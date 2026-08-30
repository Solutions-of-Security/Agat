import { parentPort } from "node:worker_threads";

import pg, { type Pool, type PoolClient, type QueryResult } from "pg";

const RESPONSE_HEADER_BYTES = 16;

interface AccessScope {
  kind: "system" | "tenant";
  projectId?: string;
}

interface PostgresWorkerOptions {
  systemUrl: string;
  tenantUrl: string;
  roleMode: "migration" | "runtime";
  applicationName: string;
  poolMax: number;
  connectTimeoutMs: number;
  idleTimeoutMs: number;
  statementTimeoutMs: number;
  sslMode: "disable" | "require" | "verify-full";
  sslCa: string;
  sslCert: string;
  sslKey: string;
}

interface WorkerRequest {
  operation: "initialize" | "close" | "all" | "get" | "run" | "exec";
  options?: PostgresWorkerOptions;
  sql?: string;
  params?: unknown[];
  scope?: AccessScope;
  shared: SharedArrayBuffer;
}

let systemPool: Pool | null = null;
let tenantPool: Pool | null = null;
let transactionClient: PoolClient | null = null;
let transactionScope: AccessScope | null = null;

function sslOptions(options: PostgresWorkerOptions): false | {
  rejectUnauthorized: boolean;
  ca?: string;
  cert?: string;
  key?: string;
} {
  if (options.sslMode === "disable") return false;
  return {
    rejectUnauthorized: options.sslMode === "verify-full",
    ...(options.sslCa ? { ca: options.sslCa } : {}),
    ...(options.sslCert ? { cert: options.sslCert } : {}),
    ...(options.sslKey ? { key: options.sslKey } : {}),
  };
}

function createPool(connectionString: string, options: PostgresWorkerOptions, suffix: string): Pool {
  const pool = new pg.Pool({
    connectionString,
    application_name: `${options.applicationName}-${suffix}`.slice(0, 63),
    max: options.poolMax,
    connectionTimeoutMillis: options.connectTimeoutMs,
    idleTimeoutMillis: options.idleTimeoutMs,
    statement_timeout: options.statementTimeoutMs,
    query_timeout: options.statementTimeoutMs,
    allowExitOnIdle: false,
    ssl: sslOptions(options),
  });
  pool.on("error", () => {
    // The next checked-out query returns the actionable connection error.
  });
  return pool;
}

interface RoleSecurityProfile {
  role_name: string;
  rolsuper: boolean;
  rolbypassrls: boolean;
  rolcreaterole: boolean;
  rolcreatedb: boolean;
  rolreplication: boolean;
  powerful_membership: boolean;
  reserved_connection_membership: boolean;
  database_create: boolean;
  database_temporary: boolean;
  schema_create: boolean;
  owns_schema_objects: boolean;
}

async function roleSecurityProfile(pool: Pool): Promise<RoleSecurityProfile> {
  const result = await pool.query<RoleSecurityProfile>(`
    SELECT current_user AS role_name,
      current_role_row.rolsuper,
      current_role_row.rolbypassrls,
      current_role_row.rolcreaterole,
      current_role_row.rolcreatedb,
      current_role_row.rolreplication,
      EXISTS (
        SELECT 1 FROM pg_roles inherited
        WHERE inherited.oid <> current_role_row.oid
          AND (inherited.rolsuper OR inherited.rolbypassrls OR inherited.rolcreaterole
            OR inherited.rolcreatedb OR inherited.rolreplication)
          AND pg_has_role(current_user, inherited.oid, 'MEMBER')
      ) AS powerful_membership,
      pg_has_role(current_user, 'pg_use_reserved_connections', 'MEMBER') AS reserved_connection_membership,
      has_database_privilege(current_user, current_database(), 'CREATE') AS database_create,
      has_database_privilege(current_user, current_database(), 'TEMPORARY') AS database_temporary,
      has_schema_privilege(current_user, current_schema(), 'CREATE') AS schema_create,
      (
        EXISTS (
          SELECT 1 FROM pg_namespace namespace_row
          WHERE namespace_row.nspname = current_schema()
            AND namespace_row.nspowner = current_role_row.oid
        ) OR EXISTS (
          SELECT 1 FROM pg_class class_row
          JOIN pg_namespace namespace_row ON namespace_row.oid = class_row.relnamespace
          WHERE namespace_row.nspname = current_schema()
            AND class_row.relowner = current_role_row.oid
        ) OR EXISTS (
          SELECT 1 FROM pg_proc procedure_row
          JOIN pg_namespace namespace_row ON namespace_row.oid = procedure_row.pronamespace
          WHERE namespace_row.nspname = current_schema()
            AND procedure_row.proowner = current_role_row.oid
        )
      ) AS owns_schema_objects
    FROM pg_roles current_role_row
    WHERE current_role_row.rolname = current_user
  `);
  const profile = result.rows[0];
  if (!profile) throw new Error("PostgreSQL не вернул security profile текущей роли");
  return profile;
}

async function validateRoleSeparation(
  system: Pool,
  tenant: Pool,
  roleMode: PostgresWorkerOptions["roleMode"],
): Promise<void> {
  const [systemProfile, tenantProfile] = await Promise.all([
    roleSecurityProfile(system),
    roleSecurityProfile(tenant),
  ]);
  if (systemProfile.role_name === tenantProfile.role_name) {
    throw new Error("PostgreSQL system и tenant connections используют одну фактическую роль");
  }
  if (
    systemProfile.rolsuper
    || !systemProfile.rolbypassrls
    || systemProfile.rolcreaterole
    || systemProfile.rolcreatedb
    || systemProfile.rolreplication
    || systemProfile.powerful_membership
    || systemProfile.reserved_connection_membership
    || systemProfile.database_create
    || systemProfile.database_temporary
  ) {
    throw new Error("PostgreSQL system role имеет запрещённые elevated attributes, membership, database CREATE/TEMPORARY");
  }
  if (roleMode === "migration" && (!systemProfile.schema_create || !systemProfile.owns_schema_objects)) {
    throw new Error("PostgreSQL migration role должна владеть schema objects и иметь schema CREATE");
  }
  if (roleMode === "runtime" && (systemProfile.schema_create || systemProfile.owns_schema_objects)) {
    throw new Error("PostgreSQL runtime system role должна быть DDL-free и не владеть schema objects");
  }
  if (
    tenantProfile.rolsuper
    || tenantProfile.rolbypassrls
    || tenantProfile.rolcreaterole
    || tenantProfile.rolcreatedb
    || tenantProfile.rolreplication
    || tenantProfile.powerful_membership
    || tenantProfile.reserved_connection_membership
    || tenantProfile.database_create
    || tenantProfile.database_temporary
    || tenantProfile.schema_create
  ) {
    throw new Error("PostgreSQL tenant role имеет опасные attributes, membership или CREATE privilege");
  }
}

function encodeJson(_key: string, value: unknown): unknown {
  if (typeof value === "bigint") return { __agatBigInt: value.toString() };
  if (Buffer.isBuffer(value)) return { __agatBytes: value.toString("base64") };
  if (
    value
    && typeof value === "object"
    && (value as { type?: unknown }).type === "Buffer"
    && Array.isArray((value as { data?: unknown }).data)
  ) {
    return { __agatBytes: Buffer.from((value as { data: number[] }).data).toString("base64") };
  }
  return value;
}

function decodeParam(value: unknown): unknown {
  if (!value || typeof value !== "object") return value;
  const record = value as Record<string, unknown>;
  if (typeof record.__agatBigInt === "string") return BigInt(record.__agatBigInt);
  if (typeof record.__agatBytes === "string") return Buffer.from(record.__agatBytes, "base64");
  return value;
}

function send(shared: SharedArrayBuffer, response: unknown): void {
  const header = new Int32Array(shared, 0, RESPONSE_HEADER_BYTES / Int32Array.BYTES_PER_ELEMENT);
  const target = new Uint8Array(shared, RESPONSE_HEADER_BYTES);
  let bytes = Buffer.from(JSON.stringify(response, encodeJson));
  if (bytes.length > target.length) {
    bytes = Buffer.from(JSON.stringify({
      ok: false,
      error: {
        name: "PostgresResponseTooLarge",
        message: `PostgreSQL query response ${bytes.length} bytes exceeds bridge limit ${target.length}`,
      },
    }));
  }
  target.set(bytes);
  Atomics.store(header, 1, bytes.length);
  Atomics.store(header, 0, 1);
  Atomics.notify(header, 0, 1);
}

function serializeError(error: unknown): Record<string, unknown> {
  const value = error && typeof error === "object" ? error as Record<string, unknown> : {};
  return {
    name: error instanceof Error ? error.name : "PostgresError",
    message: error instanceof Error ? error.message : "PostgreSQL query завершился ошибкой",
    ...(typeof value.code === "string" ? { code: value.code } : {}),
    ...(typeof value.detail === "string" ? { detail: value.detail } : {}),
    ...(typeof value.constraint === "string" ? { constraint: value.constraint } : {}),
  };
}

function transformQuestionMarks(sql: string): string {
  let result = "";
  let parameter = 0;
  let index = 0;
  let state: "normal" | "single" | "double" | "line" | "block" | "dollar" = "normal";
  let dollarTag = "";
  while (index < sql.length) {
    const char = sql[index]!;
    const next = sql[index + 1] ?? "";
    if (state === "normal") {
      if (char === "'") state = "single";
      else if (char === '"') state = "double";
      else if (char === "-" && next === "-") state = "line";
      else if (char === "/" && next === "*") state = "block";
      else if (char === "$") {
        const match = /^\$[A-Za-z_][A-Za-z0-9_]*\$|^\$\$/.exec(sql.slice(index));
        if (match) {
          dollarTag = match[0];
          state = "dollar";
          result += dollarTag;
          index += dollarTag.length;
          continue;
        }
      } else if (char === "?") {
        parameter += 1;
        result += `$${parameter}`;
        index += 1;
        continue;
      }
    } else if (state === "single" && char === "'") {
      if (next === "'") {
        result += "''";
        index += 2;
        continue;
      }
      state = "normal";
    } else if (state === "double" && char === '"') {
      if (next === '"') {
        result += '""';
        index += 2;
        continue;
      }
      state = "normal";
    } else if (state === "line" && (char === "\n" || char === "\r")) {
      state = "normal";
    } else if (state === "block" && char === "*" && next === "/") {
      result += "*/";
      index += 2;
      state = "normal";
      continue;
    } else if (state === "dollar" && sql.startsWith(dollarTag, index)) {
      result += dollarTag;
      index += dollarTag.length;
      state = "normal";
      continue;
    }
    result += char;
    index += 1;
  }
  return result;
}

function postgresSql(sql: string): string {
  const trimmed = sql.trim();
  const tableInfo = /^PRAGMA\s+table_info\(([^)]+)\)\s*;?$/i.exec(trimmed);
  if (tableInfo?.[1]) {
    const tableName = tableInfo[1].trim().replace(/^['"]|['"]$/g, "");
    return `SELECT column_name AS name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = '${tableName.replaceAll("'", "''")}' ORDER BY ordinal_position`;
  }
  if (/^PRAGMA\s+(foreign_keys|busy_timeout|journal_mode|user_version)/i.test(trimmed)) {
    return "SELECT 1";
  }
  let normalized = sql
    .replace(/BEGIN\s+IMMEDIATE/gi, "BEGIN")
    .replace(/INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT/gi, "BIGSERIAL PRIMARY KEY")
    .replace(/\bBLOB\b/gi, "BYTEA")
    .replace(/datetime\s*\(\s*'now'\s*\)/gi, "CURRENT_TIMESTAMP")
    .replace(/([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*\?\s+COLLATE\s+NOCASE/gi, "LOWER($1) = LOWER(?)")
    .replace(/\s+COLLATE\s+NOCASE/gi, "")
    .replace(/\browid\b/gi, "created_at");
  normalized = transformQuestionMarks(normalized);
  return normalized;
}

function sameScope(left: AccessScope | null, right: AccessScope): boolean {
  return left?.kind === right.kind && left?.projectId === right.projectId;
}

async function setTenantScope(client: PoolClient, scope: AccessScope): Promise<void> {
  if (scope.kind !== "tenant" || !scope.projectId) return;
  await client.query("SELECT set_config('agat.current_project_id', $1, true)", [scope.projectId]);
}

async function executeScoped(
  scope: AccessScope,
  sql: string,
  params: unknown[],
): Promise<QueryResult | QueryResult[]> {
  if (!systemPool || !tenantPool) throw new Error("PostgreSQL worker не инициализирован");
  const normalized = postgresSql(sql);
  const transactionControl = /^(BEGIN|COMMIT|ROLLBACK)\b/i.exec(normalized.trim())?.[1]?.toUpperCase();
  if (transactionControl === "BEGIN") {
    if (transactionClient) throw new Error("Nested PostgreSQL transaction запрещена");
    const pool = scope.kind === "tenant" ? tenantPool : systemPool;
    transactionClient = await pool.connect();
    transactionScope = scope;
    try {
      const result = await transactionClient.query("BEGIN");
      await setTenantScope(transactionClient, scope);
      return result;
    } catch (error) {
      transactionClient.release(true);
      transactionClient = null;
      transactionScope = null;
      throw error;
    }
  }
  if (transactionControl === "COMMIT" || transactionControl === "ROLLBACK") {
    if (!transactionClient) throw new Error("PostgreSQL transaction не открыта");
    if (!sameScope(transactionScope, scope)) throw new Error("PostgreSQL transaction scope изменился");
    const client = transactionClient;
    transactionClient = null;
    transactionScope = null;
    try {
      return await client.query(transactionControl);
    } finally {
      client.release(transactionControl === "ROLLBACK");
    }
  }
  if (transactionClient) {
    if (!sameScope(transactionScope, scope)) throw new Error("PostgreSQL transaction scope изменился");
    return transactionClient.query(normalized, params);
  }
  if (scope.kind === "system") return systemPool.query(normalized, params);
  const client = await tenantPool.connect();
  try {
    await client.query("BEGIN READ WRITE");
    await setTenantScope(client, scope);
    const result = await client.query(normalized, params);
    await client.query("COMMIT");
    return result;
  } catch (error) {
    try {
      await client.query("ROLLBACK");
    } catch {
      // Preserve the original query error.
    }
    throw error;
  } finally {
    client.release();
  }
}

function finalResult(result: QueryResult | QueryResult[]): QueryResult {
  return Array.isArray(result) ? result.at(-1) ?? {
    command: "",
    oid: 0,
    fields: [],
    rows: [],
    rowCount: 0,
  } : result;
}

async function handle(request: WorkerRequest): Promise<unknown> {
  if (request.operation === "initialize") {
    if (!request.options) throw new Error("PostgreSQL worker options отсутствуют");
    systemPool = createPool(request.options.systemUrl, request.options, "system");
    tenantPool = createPool(request.options.tenantUrl || request.options.systemUrl, request.options, "tenant");
    await systemPool.query("SELECT 1");
    await tenantPool.query("SELECT 1");
    await validateRoleSeparation(systemPool, tenantPool, request.options.roleMode);
    return { connected: true };
  }
  if (request.operation === "close") {
    if (transactionClient) {
      try {
        await transactionClient.query("ROLLBACK");
      } finally {
        transactionClient.release(true);
        transactionClient = null;
        transactionScope = null;
      }
    }
    await Promise.all([systemPool?.end(), tenantPool && tenantPool !== systemPool ? tenantPool.end() : undefined]);
    systemPool = null;
    tenantPool = null;
    return { closed: true };
  }
  const scope = request.scope ?? { kind: "system" };
  const result = finalResult(await executeScoped(
    scope,
    request.sql ?? "",
    (request.params ?? []).map(decodeParam),
  ));
  if (request.operation === "all") return result.rows;
  if (request.operation === "get") return result.rows[0] ?? null;
  if (request.operation === "run") {
    const insertedId = result.rows[0] && "id" in result.rows[0] ? result.rows[0].id : 0;
    return { changes: result.rowCount ?? 0, lastInsertRowid: insertedId ?? 0 };
  }
  return null;
}

if (!parentPort) throw new Error("PostgreSQL bridge должен выполняться в worker thread");

parentPort.on("message", (request: WorkerRequest) => {
  void handle(request)
    .then((value) => send(request.shared, { ok: true, value }))
    .catch((error) => send(request.shared, { ok: false, error: serializeError(error) }));
});
