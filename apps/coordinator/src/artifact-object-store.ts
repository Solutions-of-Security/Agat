import fs from "node:fs";
import { Worker } from "node:worker_threads";
import { fileURLToPath } from "node:url";

const RESPONSE_HEADER_BYTES = 16;
const DEFAULT_RESPONSE_BYTES = 4 * 1_024 * 1_024;

export interface ArtifactObjectStoreOptions {
  endpoint: string;
  region: string;
  bucket: string;
  prefix: string;
  forcePathStyle: boolean;
  accessKeyId?: string;
  secretAccessKey?: string;
  sessionToken?: string;
  requestTimeoutMs: number;
  maximumObjectBytes: number;
  serverSideEncryption: "none" | "AES256" | "aws:kms";
  kmsKeyId?: string;
  objectLockMode: "none" | "GOVERNANCE" | "COMPLIANCE";
  requireVersioning: boolean;
  responseBytes?: number;
}

export interface ArtifactObjectPutInput {
  key: string;
  body: Buffer;
  mediaType: string;
  sha256: string;
  projectHash: string;
  retentionUntil?: string;
}

export interface ArtifactObjectMetadata {
  key: string;
  sizeBytes: number;
  sha256: string;
  versionId: string | null;
  etag: string | null;
  lastModified: string | null;
  retentionUntil: string | null;
  legalHold: boolean;
}

export interface ArtifactObjectListEntry {
  key: string;
  sizeBytes: number;
  etag: string | null;
  lastModified: string | null;
}

export interface ArtifactBucketConformance {
  bucket: string;
  versioning: string;
  objectLockEnabled: boolean;
  defaultEncryption: string | null;
  lifecycleRuleIds: string[];
}

export interface ArtifactObjectStore {
  close(): void;
  put(input: ArtifactObjectPutInput): ArtifactObjectMetadata;
  get(key: string, versionId?: string | null): { metadata: ArtifactObjectMetadata; body: Buffer };
  head(key: string, versionId?: string | null): ArtifactObjectMetadata;
  setProtection(key: string, versionId: string | null, retentionUntil: string, legalHold: boolean): ArtifactObjectMetadata;
  delete(key: string, versionId?: string | null): void;
  list(prefix?: string, maximum?: number): ArtifactObjectListEntry[];
  configureLifecycle(noncurrentExpirationDays: number, abortMultipartDays: number): ArtifactBucketConformance;
  inspectBucket(): ArtifactBucketConformance;
}

interface WorkerResponse {
  ok: boolean;
  value?: unknown;
  error?: { name?: string; message?: string; code?: string; statusCode?: number };
}

function safeWorkerUrl(): URL {
  const compiled = new URL("./artifact-object-worker.js", import.meta.url);
  if (fs.existsSync(fileURLToPath(compiled))) return compiled;
  return new URL("./artifact-object-worker.ts", import.meta.url);
}

function safeKey(value: string): string {
  if (!value || value.length > 1_024 || value.startsWith("/") || value.endsWith("/")
    || value.split("/").some((segment) => !segment || segment === "." || segment === ".." || !/^[A-Za-z0-9._-]+$/.test(segment))) {
    throw new Error("S3 artifact key имеет небезопасный формат");
  }
  return value;
}

export function normalizeArtifactObjectPrefix(value: string): string {
  const normalized = value.replace(/^\/+|\/+$/g, "");
  if (!normalized || normalized.length > 256
    || normalized.split("/").some((segment) => !/^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$/.test(segment))) {
    throw new Error("S3 artifact prefix должен состоять из безопасных path segments");
  }
  return normalized;
}

export function artifactObjectKey(
  prefix: string,
  residencyDomain: string,
  projectHash: string,
  artifactId: string,
  sha256: string,
): string {
  if (!/^[a-f0-9]{64}$/.test(projectHash) || !/^[a-f0-9]{64}$/.test(sha256)) {
    throw new Error("Artifact object key требует SHA-256 identities");
  }
  if (!/^[a-z0-9][a-z0-9._-]{0,62}$/.test(residencyDomain)) {
    throw new Error("Artifact residency domain имеет небезопасный формат");
  }
  if (!/^[a-f0-9-]{36}$/.test(artifactId)) throw new Error("Artifact ID должен быть UUID");
  return safeKey(`${normalizeArtifactObjectPrefix(prefix)}/objects/v1/${residencyDomain}/${projectHash}/${artifactId}/${sha256}`);
}

export class S3ArtifactObjectStoreSync {
  private readonly worker: Worker;
  private readonly responseBytes: number;
  private readonly waitTimeoutMs: number;
  private readonly maximumObjectBytes: number;
  private closed = false;

  constructor(private readonly options: ArtifactObjectStoreOptions) {
    const encodedMaximum = Math.ceil(options.maximumObjectBytes * 4 / 3) + 64 * 1_024;
    this.responseBytes = Math.max(DEFAULT_RESPONSE_BYTES, encodedMaximum, options.responseBytes ?? 0);
    this.maximumObjectBytes = options.maximumObjectBytes;
    this.waitTimeoutMs = Math.max(5_000, options.requestTimeoutMs + 5_000);
    const workerUrl = safeWorkerUrl();
    this.worker = new Worker(workerUrl, {
      execArgv: workerUrl.pathname.endsWith(".ts") ? ["--import", "tsx"] : [],
    });
    try {
      this.call({ operation: "initialize", options });
    } catch (error) {
      this.closed = true;
      void this.worker.terminate();
      throw error;
    }
  }

  close(): void {
    if (this.closed) return;
    try {
      this.call({ operation: "close" });
    } finally {
      this.closed = true;
      void this.worker.terminate();
    }
  }

  put(input: ArtifactObjectPutInput): ArtifactObjectMetadata {
    if (input.body.byteLength > this.maximumObjectBytes) throw new Error("Artifact превышает S3 maximumObjectBytes");
    safeKey(input.key);
    if (!/^[a-f0-9]{64}$/.test(input.sha256) || !/^[a-f0-9]{64}$/.test(input.projectHash)) {
      throw new Error("Artifact object metadata требует SHA-256");
    }
    return this.call({
      operation: "put",
      input: { ...input, bodyBase64: input.body.toString("base64"), body: undefined },
    }) as ArtifactObjectMetadata;
  }

  get(key: string, versionId?: string | null): { metadata: ArtifactObjectMetadata; body: Buffer } {
    const value = this.call({ operation: "get", key: safeKey(key), versionId: versionId ?? undefined }) as {
      metadata: ArtifactObjectMetadata;
      bodyBase64: string;
    };
    const body = Buffer.from(value.bodyBase64, "base64");
    if (body.byteLength > this.maximumObjectBytes) throw new Error("S3 artifact response превышает maximumObjectBytes");
    return { metadata: value.metadata, body };
  }

  head(key: string, versionId?: string | null): ArtifactObjectMetadata {
    return this.call({ operation: "head", key: safeKey(key), versionId: versionId ?? undefined }) as ArtifactObjectMetadata;
  }

  setProtection(key: string, versionId: string | null, retentionUntil: string, legalHold: boolean): ArtifactObjectMetadata {
    if (!Number.isFinite(Date.parse(retentionUntil))) throw new Error("Artifact retentionUntil должен быть ISO timestamp");
    return this.call({
      operation: "setProtection",
      key: safeKey(key),
      versionId: versionId ?? undefined,
      retentionUntil: new Date(retentionUntil).toISOString(),
      legalHold,
    }) as ArtifactObjectMetadata;
  }

  delete(key: string, versionId?: string | null): void {
    this.call({ operation: "delete", key: safeKey(key), versionId: versionId ?? undefined });
  }

  list(prefix = `${normalizeArtifactObjectPrefix(this.options.prefix)}/objects/`, maximum = 10_000): ArtifactObjectListEntry[] {
    safeKey(prefix.endsWith("/") ? `${prefix}placeholder` : prefix);
    if (!Number.isInteger(maximum) || maximum < 1 || maximum > 100_000) throw new Error("S3 list maximum должен быть 1..100000");
    return this.call({ operation: "list", prefix, maximum }) as ArtifactObjectListEntry[];
  }

  configureLifecycle(noncurrentExpirationDays: number, abortMultipartDays: number): ArtifactBucketConformance {
    return this.call({
      operation: "configureLifecycle",
      noncurrentExpirationDays,
      abortMultipartDays,
    }) as ArtifactBucketConformance;
  }

  inspectBucket(): ArtifactBucketConformance {
    return this.call({ operation: "inspectBucket" }) as ArtifactBucketConformance;
  }

  private call(payload: Record<string, unknown>): unknown {
    if (this.closed) throw new Error("S3 Artifact Store уже закрыт");
    const shared = new SharedArrayBuffer(RESPONSE_HEADER_BYTES + this.responseBytes);
    const header = new Int32Array(shared, 0, RESPONSE_HEADER_BYTES / Int32Array.BYTES_PER_ELEMENT);
    this.worker.postMessage({ ...payload, shared });
    const waited = Atomics.wait(header, 0, 0, this.waitTimeoutMs);
    if (waited === "timed-out") {
      this.closed = true;
      void this.worker.terminate();
      throw new Error(`S3 Artifact Store не ответил за ${this.waitTimeoutMs} ms`);
    }
    const length = Atomics.load(header, 1);
    if (length < 0 || length > this.responseBytes) throw new Error("S3 Artifact Store вернул повреждённый ответ");
    const bytes = new Uint8Array(shared, RESPONSE_HEADER_BYTES, length);
    const response = JSON.parse(Buffer.from(bytes).toString("utf8")) as WorkerResponse;
    if (!response.ok) {
      const error = new Error(response.error?.message ?? "S3 Artifact Store operation завершилась ошибкой");
      error.name = response.error?.name ?? "ArtifactObjectStoreError";
      Object.assign(error, { code: response.error?.code, statusCode: response.error?.statusCode });
      throw error;
    }
    return response.value;
  }
}
