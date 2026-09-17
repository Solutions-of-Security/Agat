import { createHash } from "node:crypto";
import { parentPort } from "node:worker_threads";

import {
  DeleteObjectCommand,
  GetBucketEncryptionCommand,
  GetBucketLifecycleConfigurationCommand,
  GetBucketVersioningCommand,
  GetObjectCommand,
  GetObjectLockConfigurationCommand,
  HeadBucketCommand,
  HeadObjectCommand,
  ListObjectsV2Command,
  PutBucketLifecycleConfigurationCommand,
  PutObjectCommand,
  PutObjectLegalHoldCommand,
  PutObjectRetentionCommand,
  S3Client,
  type HeadObjectCommandOutput,
} from "@aws-sdk/client-s3";

import type {
  ArtifactBucketConformance,
  ArtifactObjectListEntry,
  ArtifactObjectMetadata,
  ArtifactObjectStoreOptions,
} from "./artifact-object-store.js";

const RESPONSE_HEADER_BYTES = 16;

interface WorkerRequest {
  operation: "initialize" | "close" | "put" | "get" | "head" | "setProtection" | "delete" | "list" | "configureLifecycle" | "inspectBucket";
  options?: ArtifactObjectStoreOptions;
  input?: Record<string, unknown>;
  key?: string;
  versionId?: string;
  prefix?: string;
  maximum?: number;
  noncurrentExpirationDays?: number;
  abortMultipartDays?: number;
  retentionUntil?: string;
  legalHold?: boolean;
  shared: SharedArrayBuffer;
}

let client: S3Client | null = null;
let options: ArtifactObjectStoreOptions | null = null;

function send(shared: SharedArrayBuffer, response: unknown): void {
  const header = new Int32Array(shared, 0, RESPONSE_HEADER_BYTES / Int32Array.BYTES_PER_ELEMENT);
  const target = new Uint8Array(shared, RESPONSE_HEADER_BYTES);
  let bytes = Buffer.from(JSON.stringify(response));
  if (bytes.length > target.length) {
    bytes = Buffer.from(JSON.stringify({
      ok: false,
      error: { name: "ArtifactObjectResponseTooLarge", message: "S3 response превышает shared buffer" },
    }));
  }
  target.set(bytes);
  Atomics.store(header, 1, bytes.length);
  Atomics.store(header, 0, 1);
  Atomics.notify(header, 0, 1);
}

function serializeError(error: unknown): Record<string, unknown> {
  const row = error && typeof error === "object" ? error as Record<string, unknown> : {};
  const metadata = row.$metadata && typeof row.$metadata === "object"
    ? row.$metadata as Record<string, unknown>
    : {};
  return {
    name: error instanceof Error ? error.name : "ArtifactObjectStoreError",
    message: error instanceof Error ? error.message : "S3 operation failed",
    ...(typeof row.Code === "string" ? { code: row.Code } : {}),
    ...(typeof row.code === "string" ? { code: row.code } : {}),
    ...(typeof metadata.httpStatusCode === "number" ? { statusCode: metadata.httpStatusCode } : {}),
  };
}

function configured(): { client: S3Client; options: ArtifactObjectStoreOptions } {
  if (!client || !options) throw new Error("S3 Artifact Store worker не инициализирован");
  return { client, options };
}

function metadata(key: string, output: HeadObjectCommandOutput): ArtifactObjectMetadata {
  const sizeBytes = Number(output.ContentLength ?? -1);
  const sha256 = output.Metadata?.["agat-sha256"] ?? "";
  if (!Number.isSafeInteger(sizeBytes) || sizeBytes < 0 || !/^[a-f0-9]{64}$/.test(sha256)) {
    throw new Error("S3 object metadata hash/size отсутствует или повреждён");
  }
  return {
    key,
    sizeBytes,
    sha256,
    versionId: output.VersionId ?? null,
    etag: output.ETag ?? null,
    lastModified: output.LastModified?.toISOString() ?? null,
    retentionUntil: output.ObjectLockRetainUntilDate?.toISOString() ?? null,
    legalHold: output.ObjectLockLegalHoldStatus === "ON",
  };
}

async function head(key: string, versionId?: string): Promise<ArtifactObjectMetadata> {
  const current = configured();
  const output = await current.client.send(new HeadObjectCommand({
    Bucket: current.options.bucket,
    Key: key,
    ...(versionId ? { VersionId: versionId } : {}),
    ChecksumMode: "ENABLED",
  }));
  return metadata(key, output);
}

function assertExact(actual: ArtifactObjectMetadata, expected: { sizeBytes: number; sha256: string }): void {
  if (actual.sizeBytes !== expected.sizeBytes || actual.sha256 !== expected.sha256) {
    throw new Error("S3 object не совпадает с artifact hash/size");
  }
}

async function put(input: Record<string, unknown>): Promise<ArtifactObjectMetadata> {
  const current = configured();
  const key = String(input.key ?? "");
  const bodyBase64 = String(input.bodyBase64 ?? "");
  const body = Buffer.from(bodyBase64, "base64");
  if (body.toString("base64") !== bodyBase64 || body.byteLength > current.options.maximumObjectBytes) {
    throw new Error("S3 artifact body повреждён или превышает limit");
  }
  const sha256 = String(input.sha256 ?? "");
  if (createHash("sha256").update(body).digest("hex") !== sha256) throw new Error("S3 artifact body hash не совпадает");
  const checksum = createHash("sha256").update(body).digest("base64");
  const retentionUntil = typeof input.retentionUntil === "string" && input.retentionUntil
    ? new Date(input.retentionUntil)
    : undefined;
  try {
    const output = await current.client.send(new PutObjectCommand({
      Bucket: current.options.bucket,
      Key: key,
      Body: body,
      ContentLength: body.byteLength,
      ContentType: String(input.mediaType ?? "application/octet-stream"),
      ChecksumSHA256: checksum,
      IfNoneMatch: "*",
      Metadata: {
        "agat-sha256": sha256,
        "agat-size": String(body.byteLength),
        "agat-project-hash": String(input.projectHash ?? ""),
      },
      Tagging: "agat-managed=true",
      ...(current.options.serverSideEncryption === "none" ? {} : {
        ServerSideEncryption: current.options.serverSideEncryption,
      }),
      ...(current.options.serverSideEncryption === "aws:kms" && current.options.kmsKeyId
        ? { SSEKMSKeyId: current.options.kmsKeyId }
        : {}),
      ...(current.options.objectLockMode !== "none" && retentionUntil ? {
        ObjectLockMode: current.options.objectLockMode,
        ObjectLockRetainUntilDate: retentionUntil,
      } : {}),
    }));
    const actual = await head(key, output.VersionId);
    assertExact(actual, { sizeBytes: body.byteLength, sha256 });
    return actual;
  } catch (error) {
    const row = error && typeof error === "object" ? error as Record<string, unknown> : {};
    const metadata = row.$metadata && typeof row.$metadata === "object" ? row.$metadata as Record<string, unknown> : {};
    if (metadata.httpStatusCode !== 412 && row.name !== "PreconditionFailed") throw error;
    const actual = await head(key);
    assertExact(actual, { sizeBytes: body.byteLength, sha256 });
    return actual;
  }
}

async function get(key: string, versionId?: string): Promise<{ metadata: ArtifactObjectMetadata; bodyBase64: string }> {
  const current = configured();
  const output = await current.client.send(new GetObjectCommand({
    Bucket: current.options.bucket,
    Key: key,
    ...(versionId ? { VersionId: versionId } : {}),
    ChecksumMode: "ENABLED",
  }));
  if (!output.Body) throw new Error("S3 GetObject не вернул body");
  const bytes = Buffer.from(await output.Body.transformToByteArray());
  if (bytes.byteLength > current.options.maximumObjectBytes) throw new Error("S3 object превышает maximumObjectBytes");
  const actual = metadata(key, output);
  assertExact(actual, { sizeBytes: bytes.byteLength, sha256: createHash("sha256").update(bytes).digest("hex") });
  return { metadata: actual, bodyBase64: bytes.toString("base64") };
}

async function setProtection(
  key: string,
  versionId: string | undefined,
  retentionUntil: string,
  legalHold: boolean,
): Promise<ArtifactObjectMetadata> {
  const current = configured();
  if (current.options.objectLockMode === "none") {
    throw new Error("S3 Object Lock не включён для bucket profile");
  }
  const retainUntilDate = new Date(retentionUntil);
  if (!Number.isFinite(retainUntilDate.getTime())) throw new Error("Artifact retentionUntil повреждён");
  await current.client.send(new PutObjectRetentionCommand({
    Bucket: current.options.bucket,
    Key: key,
    ...(versionId ? { VersionId: versionId } : {}),
    Retention: { Mode: current.options.objectLockMode, RetainUntilDate: retainUntilDate },
  }));
  await current.client.send(new PutObjectLegalHoldCommand({
    Bucket: current.options.bucket,
    Key: key,
    ...(versionId ? { VersionId: versionId } : {}),
    LegalHold: { Status: legalHold ? "ON" : "OFF" },
  }));
  return head(key, versionId);
}

async function list(prefix: string, maximum: number): Promise<ArtifactObjectListEntry[]> {
  const current = configured();
  const rows: ArtifactObjectListEntry[] = [];
  let continuationToken: string | undefined;
  while (rows.length < maximum) {
    const output = await current.client.send(new ListObjectsV2Command({
      Bucket: current.options.bucket,
      Prefix: prefix,
      ContinuationToken: continuationToken,
      MaxKeys: Math.min(1_000, maximum - rows.length),
    }));
    for (const object of output.Contents ?? []) {
      if (!object.Key) continue;
      rows.push({
        key: object.Key,
        sizeBytes: Number(object.Size ?? 0),
        etag: object.ETag ?? null,
        lastModified: object.LastModified?.toISOString() ?? null,
      });
    }
    continuationToken = output.NextContinuationToken;
    if (!output.IsTruncated || !continuationToken) break;
  }
  return rows;
}

async function inspectBucket(): Promise<ArtifactBucketConformance> {
  const current = configured();
  const [versioning, lifecycle, encryption, lock] = await Promise.all([
    current.client.send(new GetBucketVersioningCommand({ Bucket: current.options.bucket })),
    current.client.send(new GetBucketLifecycleConfigurationCommand({ Bucket: current.options.bucket }))
      .catch((error: unknown) => absentConfiguration(error, ["NoSuchLifecycleConfiguration"]) ? { Rules: [] } : Promise.reject(error)),
    current.client.send(new GetBucketEncryptionCommand({ Bucket: current.options.bucket }))
      .catch((error: unknown) => absentConfiguration(error, ["ServerSideEncryptionConfigurationNotFoundError"])
        ? { ServerSideEncryptionConfiguration: undefined }
        : Promise.reject(error)),
    current.client.send(new GetObjectLockConfigurationCommand({ Bucket: current.options.bucket }))
      .catch((error: unknown) => absentConfiguration(error, ["ObjectLockConfigurationNotFoundError", "NoSuchObjectLockConfiguration"])
        ? { ObjectLockConfiguration: undefined }
        : Promise.reject(error)),
  ]);
  const encryptionRule = encryption.ServerSideEncryptionConfiguration?.Rules?.[0]?.ApplyServerSideEncryptionByDefault;
  return {
    bucket: current.options.bucket,
    versioning: versioning.Status ?? "Disabled",
    objectLockEnabled: lock.ObjectLockConfiguration?.ObjectLockEnabled === "Enabled",
    defaultEncryption: encryptionRule?.SSEAlgorithm ?? null,
    lifecycleRuleIds: (lifecycle.Rules ?? []).map((rule) => rule.ID ?? "").filter(Boolean).sort(),
  };
}

function absentConfiguration(error: unknown, names: string[]): boolean {
  const row = error && typeof error === "object" ? error as Record<string, unknown> : {};
  const metadata = row.$metadata && typeof row.$metadata === "object"
    ? row.$metadata as Record<string, unknown>
    : {};
  return names.includes(String(row.name ?? row.Code ?? row.code ?? "")) || metadata.httpStatusCode === 404;
}

async function configureLifecycle(noncurrentExpirationDays: number, abortMultipartDays: number): Promise<ArtifactBucketConformance> {
  const current = configured();
  if (!Number.isInteger(noncurrentExpirationDays) || noncurrentExpirationDays < 1 || noncurrentExpirationDays > 3650
    || !Number.isInteger(abortMultipartDays) || abortMultipartDays < 1 || abortMultipartDays > 30) {
    throw new Error("S3 lifecycle days вне bounded range");
  }
  const existing = await current.client.send(new GetBucketLifecycleConfigurationCommand({ Bucket: current.options.bucket }))
    .catch((error: unknown) => absentConfiguration(error, ["NoSuchLifecycleConfiguration"]) ? { Rules: [] } : Promise.reject(error));
  const managedRuleIds = new Set([
    "agat-storage-maintenance",
    "agat-abort-incomplete-multipart",
    "agat-expire-deleted-versions",
  ]);
  const preservedRules = (existing.Rules ?? []).filter((rule) => !managedRuleIds.has(rule.ID ?? ""));
  await current.client.send(new PutBucketLifecycleConfigurationCommand({
    Bucket: current.options.bucket,
    LifecycleConfiguration: {
      Rules: [
        ...preservedRules,
        {
          ID: "agat-storage-maintenance",
          Status: "Enabled",
          Filter: { Prefix: `${current.options.prefix}/objects/` },
          AbortIncompleteMultipartUpload: { DaysAfterInitiation: abortMultipartDays },
          NoncurrentVersionExpiration: { NoncurrentDays: noncurrentExpirationDays },
        },
      ],
    },
  }));
  return inspectBucket();
}

parentPort?.on("message", async (request: WorkerRequest) => {
  try {
    let value: unknown = null;
    if (request.operation === "initialize") {
      if (!request.options) throw new Error("S3 worker options отсутствуют");
      options = request.options;
      client = new S3Client({
        endpoint: options.endpoint,
        region: options.region,
        forcePathStyle: options.forcePathStyle,
        maxAttempts: 3,
        ...(options.accessKeyId && options.secretAccessKey ? {
          credentials: {
            accessKeyId: options.accessKeyId,
            secretAccessKey: options.secretAccessKey,
            ...(options.sessionToken ? { sessionToken: options.sessionToken } : {}),
          },
        } : {}),
      });
      await client.send(new HeadBucketCommand({ Bucket: options.bucket }));
      const bucket = await inspectBucket();
      if (options.requireVersioning && bucket.versioning !== "Enabled") {
        throw new Error("S3 bucket versioning не включён");
      }
      value = bucket;
    } else if (request.operation === "close") {
      client?.destroy();
      client = null;
      options = null;
    } else if (request.operation === "put") {
      value = await put(request.input ?? {});
    } else if (request.operation === "get") {
      value = await get(request.key ?? "", request.versionId);
    } else if (request.operation === "head") {
      value = await head(request.key ?? "", request.versionId);
    } else if (request.operation === "setProtection") {
      value = await setProtection(
        request.key ?? "",
        request.versionId,
        request.retentionUntil ?? "",
        request.legalHold === true,
      );
    } else if (request.operation === "delete") {
      const current = configured();
      await current.client.send(new DeleteObjectCommand({
        Bucket: current.options.bucket,
        Key: request.key ?? "",
        ...(request.versionId ? { VersionId: request.versionId } : {}),
      }));
    } else if (request.operation === "list") {
      value = await list(request.prefix ?? "", request.maximum ?? 10_000);
    } else if (request.operation === "inspectBucket") {
      value = await inspectBucket();
    } else if (request.operation === "configureLifecycle") {
      value = await configureLifecycle(request.noncurrentExpirationDays ?? 30, request.abortMultipartDays ?? 1);
    }
    send(request.shared, { ok: true, value });
  } catch (error) {
    send(request.shared, { ok: false, error: serializeError(error) });
  }
});
