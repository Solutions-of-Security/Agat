import { AsyncLocalStorage } from "node:async_hooks";
import fs from "node:fs";
import { Worker } from "node:worker_threads";
import { fileURLToPath } from "node:url";

import type { DatabaseValue, SyncDatabase, SyncStatement } from "./sync-database.js";

const RESPONSE_HEADER_BYTES = 16;
const DEFAULT_RESPONSE_BYTES = 32 * 1_024 * 1_024;

export interface PostgresAccessScope {
  kind: "system" | "tenant";
  projectId?: string;
}

const postgresAccess = new AsyncLocalStorage<PostgresAccessScope>();

export function enterPostgresTenantScope(projectId: string): void {
  postgresAccess.enterWith({ kind: "tenant", projectId });
}

export function runWithPostgresSystemScope<T>(callback: () => T): T {
  return postgresAccess.run({ kind: "system" }, callback);
}

export interface PostgresDatabaseOptions {
  systemUrl: string;
  tenantUrl: string;
  applicationName: string;
  poolMax: number;
  connectTimeoutMs: number;
  idleTimeoutMs: number;
  statementTimeoutMs: number;
  responseBytes?: number;
  sslMode: "disable" | "require" | "verify-full";
  sslCa?: string;
  sslCert?: string;
  sslKey?: string;
}

interface WorkerResponse {
  ok: boolean;
  value?: unknown;
  error?: {
    name?: string;
    message?: string;
    code?: string;
    detail?: string;
    constraint?: string;
  };
}

function encodeValue(value: DatabaseValue): unknown {
  if (typeof value === "bigint") return { __agatBigInt: value.toString() };
  if (value instanceof Uint8Array) {
    return { __agatBytes: Buffer.from(value).toString("base64") };
  }
  return value;
}

function decodeValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(decodeValue);
  if (!value || typeof value !== "object") return value;
  const record = value as Record<string, unknown>;
  if (typeof record.__agatBigInt === "string") return BigInt(record.__agatBigInt);
  if (typeof record.__agatBytes === "string") return Buffer.from(record.__agatBytes, "base64");
  return Object.fromEntries(Object.entries(record).map(([key, nested]) => [key, decodeValue(nested)]));
}

function safeWorkerUrl(): URL {
  const compiled = new URL("./postgres-worker.js", import.meta.url);
  if (fs.existsSync(fileURLToPath(compiled))) return compiled;
  return new URL("./postgres-worker.ts", import.meta.url);
}

class PostgresStatement implements SyncStatement {
  constructor(
    private readonly database: PostgresDatabaseSync,
    private readonly sql: string,
  ) {}

  all(...params: DatabaseValue[]): Record<string, unknown>[] {
    const value = this.database.request("all", this.sql, params);
    return value as Record<string, unknown>[];
  }

  get(...params: DatabaseValue[]): Record<string, unknown> | undefined {
    const value = this.database.request("get", this.sql, params);
    return value === null ? undefined : value as Record<string, unknown>;
  }

  run(...params: DatabaseValue[]): { changes: number | bigint; lastInsertRowid: number | bigint } {
    return this.database.request("run", this.sql, params) as {
      changes: number | bigint;
      lastInsertRowid: number | bigint;
    };
  }
}

export class PostgresDatabaseSync implements SyncDatabase {
  readonly dialect = "postgresql" as const;
  private readonly worker: Worker;
  private readonly responseBytes: number;
  private readonly waitTimeoutMs: number;
  private closed = false;

  constructor(options: PostgresDatabaseOptions) {
    this.responseBytes = Math.max(1_048_576, options.responseBytes ?? DEFAULT_RESPONSE_BYTES);
    this.waitTimeoutMs = Math.max(
      5_000,
      options.connectTimeoutMs + options.statementTimeoutMs + 5_000,
    );
    const workerUrl = safeWorkerUrl();
    this.worker = new Worker(workerUrl, {
      execArgv: workerUrl.pathname.endsWith(".ts") ? ["--import", "tsx"] : [],
    });
    try {
      this.call({
        operation: "initialize",
        options: {
          ...options,
          sslCa: options.sslCa ?? "",
          sslCert: options.sslCert ?? "",
          sslKey: options.sslKey ?? "",
        },
      });
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

  exec(sql: string): void {
    this.request("exec", sql, []);
  }

  prepare(sql: string): SyncStatement {
    if (this.closed) throw new Error("PostgreSQL state store уже закрыт");
    return new PostgresStatement(this, sql);
  }

  request(operation: "all" | "get" | "run" | "exec", sql: string, params: DatabaseValue[]): unknown {
    if (this.closed) throw new Error("PostgreSQL state store уже закрыт");
    const scope = postgresAccess.getStore() ?? { kind: "system" as const };
    if (scope.kind === "tenant" && !scope.projectId) {
      throw new Error("Tenant PostgreSQL scope не содержит project ID");
    }
    return this.call({
      operation,
      sql,
      params: params.map(encodeValue),
      scope,
    });
  }

  private call(payload: Record<string, unknown>): unknown {
    const shared = new SharedArrayBuffer(RESPONSE_HEADER_BYTES + this.responseBytes);
    const header = new Int32Array(shared, 0, RESPONSE_HEADER_BYTES / Int32Array.BYTES_PER_ELEMENT);
    this.worker.postMessage({ ...payload, shared });
    const waited = Atomics.wait(header, 0, 0, this.waitTimeoutMs);
    if (waited === "timed-out") {
      void this.worker.terminate();
      this.closed = true;
      throw new Error(`PostgreSQL state store не ответил за ${this.waitTimeoutMs} ms`);
    }
    const length = Atomics.load(header, 1);
    if (length < 0 || length > this.responseBytes) {
      throw new Error("PostgreSQL state store вернул повреждённый ответ");
    }
    const bytes = new Uint8Array(shared, RESPONSE_HEADER_BYTES, length);
    const response = JSON.parse(Buffer.from(bytes).toString("utf8")) as WorkerResponse;
    if (!response.ok) {
      const error = new Error(response.error?.message ?? "PostgreSQL query завершился ошибкой");
      error.name = response.error?.name ?? "PostgresError";
      Object.assign(error, {
        code: response.error?.code,
        detail: response.error?.detail,
        constraint: response.error?.constraint,
      });
      throw error;
    }
    return decodeValue(response.value);
  }
}
