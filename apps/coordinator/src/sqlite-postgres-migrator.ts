import { createHash, randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { DatabaseSync, type StatementSync } from "node:sqlite";
import { pathToFileURL } from "node:url";

import pg, { type Client, type ClientConfig } from "pg";

import { AgatStore } from "./database.js";

const REQUIRED_SQLITE_SCHEMA_VERSION = 23;
const DEFAULT_BATCH_ROWS = 250;
const MAX_POSTGRES_PARAMETERS = 60_000;
const MIGRATION_LOCK_ID = 867_530_902;
const EPHEMERAL_TABLES = new Set(["coordinator_replicas"]);

type MigrationMode = "apply" | "rehearse" | "verify";
type SqliteValue = null | string | number | bigint | Uint8Array;
type MigrationRow = Record<string, SqliteValue>;

interface SqliteColumn {
  name: string;
  type: string;
  primaryKeyPosition: number;
}

interface SqliteSelfReference {
  from: string;
  to: string;
}

interface SqliteTable {
  name: string;
  columns: SqliteColumn[];
  primaryKey: string[];
  dependencies: string[];
  selfReferences?: SqliteSelfReference[];
}

interface TableDigest {
  rows: number;
  sha256: string;
}

interface TargetFingerprint {
  sha256: string;
  tables: Record<string, TableDigest>;
}

export interface SqlitePostgresMigrationOptions {
  sourcePath: string;
  artifactsDir: string;
  reportPath: string;
  mode: MigrationMode;
  targetUrl: string;
  targetRuntimeUrl: string;
  targetTenantUrl: string;
  sslMode: "disable" | "require" | "verify-full";
  sslCaPath?: string;
  sslCertPath?: string;
  sslKeyPath?: string;
  batchRows: number;
  offlineConfirmation: string;
}

export interface MigrationTableReport {
  table: string;
  policy: "copied" | "runtime_ephemeral_reset";
  sourceRows: number;
  targetRows: number;
  sourceSha256: string;
  targetSha256: string;
  match: boolean;
}

export interface SqlitePostgresMigrationReport {
  schemaVersion: 1;
  migrationId: string;
  mode: MigrationMode;
  startedAt: string;
  completedAt: string;
  source: {
    sqliteSchemaVersion: number;
    databaseSha256: string;
    cell: { region: string; residencyDomain: string };
    foreignKeyViolations: number;
  };
  target: {
    identitySha256: string;
    unvalidatedForeignKeys: number;
  };
  artifacts: {
    verified: number;
    backfilledFromFilesystem: number;
    bytes: number;
  };
  tables: MigrationTableReport[];
  rollback: {
    requested: boolean;
    verified: boolean;
    baselineSha256: string | null;
    restoredSha256: string | null;
  };
  success: boolean;
  reportSha256: string;
}

interface ArtifactStats {
  verified: number;
  backfilledFromFilesystem: number;
  bytes: number;
}

function quoteIdentifier(value: string): string {
  return `"${value.replaceAll('"', '""')}"`;
}

function hasTextAffinity(column: SqliteColumn): boolean {
  return /CHAR|CLOB|TEXT/i.test(column.type);
}

function sqliteOrderExpression(column: SqliteColumn, alias?: string): string {
  const identifier = `${alias ? `${quoteIdentifier(alias)}.` : ""}${quoteIdentifier(column.name)}`;
  return hasTextAffinity(column) ? `${identifier} COLLATE BINARY` : identifier;
}

function postgresOrderExpression(column: SqliteColumn): string {
  const identifier = quoteIdentifier(column.name);
  return hasTextAffinity(column) ? `${identifier} COLLATE "C"` : identifier;
}

function canonicalValue(column: SqliteColumn, value: unknown): unknown {
  if (value === null || value === undefined) return null;
  if (Buffer.isBuffer(value) || value instanceof Uint8Array) {
    return { bytesBase64: Buffer.from(value).toString("base64") };
  }
  if (/INT/i.test(column.type)) return { integer: String(value) };
  if (typeof value === "bigint") return { integer: value.toString() };
  if (value instanceof Date) return value.toISOString();
  if (typeof value === "number" && !Number.isFinite(value)) {
    throw new Error(`Неконечное число в ${column.name} нельзя канонизировать`);
  }
  return value;
}

export function canonicalMigrationRow(columns: SqliteColumn[], row: Record<string, unknown>): string {
  return JSON.stringify(columns.map((column) => [column.name, canonicalValue(column, row[column.name])]));
}

function finishDigest(hash: ReturnType<typeof createHash>, rows: number): TableDigest {
  return { rows, sha256: hash.digest("hex") };
}

function statementWithBigInts(statement: StatementSync): StatementSync {
  statement.setReadBigInts(true);
  return statement;
}

function sqliteTables(database: DatabaseSync): SqliteTable[] {
  const names = database.prepare(`
    SELECT name FROM sqlite_master
    WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
    ORDER BY name
  `).all().map((row) => String(row.name));
  const known = new Set(names);
  return names.map((name) => {
    const columns = database.prepare(`PRAGMA table_info(${quoteIdentifier(name)})`).all().map((row) => ({
      name: String(row.name),
      type: String(row.type ?? ""),
      primaryKeyPosition: Number(row.pk ?? 0),
    }));
    if (columns.length === 0) throw new Error(`SQLite table ${name} не имеет columns`);
    const primaryKey = columns
      .filter((column) => column.primaryKeyPosition > 0)
      .sort((left, right) => left.primaryKeyPosition - right.primaryKeyPosition)
      .map((column) => column.name);
    if (primaryKey.length === 0) throw new Error(`SQLite table ${name} не имеет primary key`);
    const foreignKeys = database.prepare(`PRAGMA foreign_key_list(${quoteIdentifier(name)})`).all();
    const dependencies = [...new Set(foreignKeys
      .map((row) => String(row.table))
      .filter((dependency) => dependency !== name && known.has(dependency)))].sort();
    const selfReferences = foreignKeys
      .filter((row) => String(row.table) === name)
      .map((row) => ({ from: String(row.from), to: String(row.to) }))
      .sort((left, right) => left.from.localeCompare(right.from));
    return { name, columns, primaryKey, dependencies, selfReferences };
  });
}

export function migrationTableOrder(tables: SqliteTable[]): string[] {
  const pending = new Map(tables.map((table) => [table.name, new Set(table.dependencies)]));
  const ordered: string[] = [];
  while (pending.size > 0) {
    const ready = [...pending.entries()]
      .filter(([, dependencies]) => [...dependencies].every((dependency) => !pending.has(dependency)))
      .map(([name]) => name)
      .sort();
    if (ready.length === 0) {
      const cycle = [...pending.entries()].map(([name, dependencies]) => `${name}->${[...dependencies].join(",")}`).join("; ");
      throw new Error(`Циклические межтабличные foreign keys не поддерживаются offline migrator: ${cycle}`);
    }
    for (const name of ready) {
      ordered.push(name);
      pending.delete(name);
    }
  }
  return ordered;
}

function sourceCell(database: DatabaseSync): { region: string; residencyDomain: string } {
  const rows = database.prepare(`
    SELECT DISTINCT home_region, residency_domain FROM projects
    ORDER BY home_region, residency_domain
  `).all();
  if (rows.length !== 1) {
    throw new Error(`SQLite source должен содержать ровно одну HA-cell; найдено ${rows.length}`);
  }
  return {
    region: String(rows[0]!.home_region),
    residencyDomain: String(rows[0]!.residency_domain),
  };
}

function sourcePreflight(database: DatabaseSync): number {
  const version = Number((database.prepare("PRAGMA user_version").get() as { user_version?: unknown }).user_version ?? 0);
  if (version !== REQUIRED_SQLITE_SCHEMA_VERSION) {
    throw new Error(`SQLite schema v${version} не поддерживается; нужен v${REQUIRED_SQLITE_SCHEMA_VERSION}`);
  }
  const foreignKeyViolations = database.prepare("PRAGMA foreign_key_check").all();
  if (foreignKeyViolations.length > 0) {
    throw new Error(`SQLite foreign_key_check обнаружил ${foreignKeyViolations.length} нарушений`);
  }
  const unsafeChecks = [
    ["stages", "status = 'running'"],
    ["mcp_tool_calls", "status = 'executing'"],
    ["knowledge_embedding_jobs", "status = 'running'"],
    ["audit_export_outbox", "locked_by IS NOT NULL"],
  ] as const;
  for (const [table, predicate] of unsafeChecks) {
    const count = Number((database.prepare(`SELECT COUNT(*) AS count FROM ${quoteIdentifier(table)} WHERE ${predicate}`).get() as { count: number | bigint }).count);
    if (count > 0) throw new Error(`Offline cutover запрещён: ${table} содержит ${count} активных/locked rows`);
  }
  return foreignKeyViolations.length;
}

async function hashFiles(files: string[]): Promise<string> {
  const hash = createHash("sha256");
  for (const file of files) {
    if (!fs.existsSync(file)) continue;
    hash.update(path.basename(file));
    for await (const chunk of fs.createReadStream(file)) hash.update(chunk as Buffer);
  }
  return hash.digest("hex");
}

function sslConfig(options: SqlitePostgresMigrationOptions): ClientConfig["ssl"] {
  if (options.sslMode === "disable") return false;
  return {
    rejectUnauthorized: options.sslMode === "verify-full",
    ...(options.sslCaPath ? { ca: fs.readFileSync(options.sslCaPath, "utf8") } : {}),
    ...(options.sslCertPath ? { cert: fs.readFileSync(options.sslCertPath, "utf8") } : {}),
    ...(options.sslKeyPath ? { key: fs.readFileSync(options.sslKeyPath, "utf8") } : {}),
  };
}

function postgresClient(url: string, options: SqlitePostgresMigrationOptions, applicationName: string): Client {
  return new pg.Client({
    connectionString: url,
    application_name: applicationName.slice(0, 63),
    ssl: sslConfig(options),
    connectionTimeoutMillis: 10_000,
    statement_timeout: 0,
    query_timeout: 0,
  });
}

async function assertPristineTarget(client: Client): Promise<void> {
  const result = await client.query<{ present: boolean }>(`
    SELECT EXISTS (
      SELECT 1 FROM information_schema.tables
      WHERE table_schema = current_schema() AND table_name = 'projects'
    ) AS present
  `);
  if (result.rows[0]?.present) {
    throw new Error("Target уже содержит Agat schema; apply/rehearse требуют новую disposable database");
  }
}

function validateTargetUrls(options: SqlitePostgresMigrationOptions): void {
  let migration: URL;
  let runtime: URL;
  let tenant: URL;
  try {
    migration = new URL(options.targetUrl);
    runtime = new URL(options.targetRuntimeUrl);
    tenant = new URL(options.targetTenantUrl);
  } catch {
    throw new Error("PostgreSQL migration URLs некорректны");
  }
  for (const url of [migration, runtime, tenant]) {
    if (url.protocol !== "postgres:" && url.protocol !== "postgresql:") {
      throw new Error("Target URLs должны использовать PostgreSQL");
    }
  }
  if ([runtime, tenant].some((url) => (
    migration.hostname !== url.hostname || migration.port !== url.port || migration.pathname !== url.pathname
  ))) {
    throw new Error("Migration, runtime и tenant URLs должны указывать на одну database");
  }
  if (new Set([migration.username, runtime.username, tenant.username]).size !== 3) {
    throw new Error("Migration, runtime и tenant roles должны различаться");
  }
}

function bootstrapTarget(options: SqlitePostgresMigrationOptions, cell: { region: string; residencyDomain: string }, migrationId: string): void {
  const store = new AgatStore(":postgresql:", {
    stateStoreDriver: "postgresql",
    postgres: {
      systemUrl: options.targetUrl,
      tenantUrl: options.targetTenantUrl,
      roleMode: "migration",
      applicationName: `agat-sqlite-migration-${migrationId}`,
      poolMax: 1,
      connectTimeoutMs: 10_000,
      idleTimeoutMs: 10_000,
      statementTimeoutMs: 600_000,
      sslMode: options.sslMode,
      sslCa: options.sslCaPath ? fs.readFileSync(options.sslCaPath, "utf8") : "",
      sslCert: options.sslCertPath ? fs.readFileSync(options.sslCertPath, "utf8") : "",
      sslKey: options.sslKeyPath ? fs.readFileSync(options.sslKeyPath, "utf8") : "",
    },
    postgresSchemaMode: "migration",
    postgresRuntimeRole: new URL(options.targetRuntimeUrl).username,
    schemaOnly: true,
    coordinatorInstanceId: `migration-bootstrap-${migrationId}`,
    region: cell.region,
    residencyDomain: cell.residencyDomain,
    artifactsDir: options.artifactsDir,
    requireSignedWorkerReleases: false,
  });
  store.close();
}

async function assertTargetShape(client: Client, tables: SqliteTable[]): Promise<void> {
  const tableResult = await client.query<{ table_name: string }>(`
    SELECT table_name FROM information_schema.tables
    WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'
    ORDER BY table_name
  `);
  const actualTables = tableResult.rows.map((row) => row.table_name);
  const expectedTables = [
    ...tables.map((table) => table.name),
    "agat_dr_canaries",
    "agat_schema_migrations",
  ].sort();
  if (actualTables.join("\u0000") !== expectedTables.join("\u0000")) {
    throw new Error("Schema drift: набор SQLite и PostgreSQL tables различается");
  }
  for (const table of tables) {
    const result = await client.query<{ column_name: string }>(`
      SELECT column_name FROM information_schema.columns
      WHERE table_schema = current_schema() AND table_name = $1
      ORDER BY ordinal_position
    `, [table.name]);
    const target = result.rows.map((row) => row.column_name);
    const source = table.columns.map((column) => column.name);
    if (target.length === 0) throw new Error(`Target table ${table.name} отсутствует`);
    if (target.join("\u0000") !== source.join("\u0000")) {
      throw new Error(`Schema drift в ${table.name}: SQLite и PostgreSQL columns различаются`);
    }
  }
}

function resolvedArtifactPath(root: string, relativePath: string): string {
  const resolvedRoot = path.resolve(root);
  const resolved = path.resolve(resolvedRoot, relativePath);
  if (resolved !== resolvedRoot && !resolved.startsWith(`${resolvedRoot}${path.sep}`)) {
    throw new Error(`Artifact path выходит за AGAT_ARTIFACTS_DIR: ${relativePath}`);
  }
  return resolved;
}

function transformArtifact(row: MigrationRow, options: SqlitePostgresMigrationOptions, stats: ArtifactStats): MigrationRow {
  if (row.storage_backend !== "filesystem" || row.storage_state !== "ready") {
    throw new Error(`SQLite artifact ${row.id} имеет неподдерживаемое storage state`);
  }
  let content = row.content_blob;
  if (content === null || content === undefined) {
    const artifactPath = resolvedArtifactPath(options.artifactsDir, String(row.relative_path));
    if (!fs.existsSync(artifactPath)) throw new Error(`Artifact file отсутствует: ${row.relative_path}`);
    content = fs.readFileSync(artifactPath);
    stats.backfilledFromFilesystem += 1;
  }
  const bytes = Buffer.from(content as Uint8Array);
  const expectedSize = BigInt(String(row.size_bytes));
  if (BigInt(bytes.length) !== expectedSize) throw new Error(`Artifact size mismatch: ${row.id}`);
  const digest = createHash("sha256").update(bytes).digest("hex");
  if (digest !== String(row.sha256).toLowerCase()) throw new Error(`Artifact SHA-256 mismatch: ${row.id}`);
  stats.verified += 1;
  stats.bytes += bytes.length;
  return {
    ...row,
    content_blob: bytes,
    storage_backend: "postgresql",
    storage_state: "ready",
    object_bucket: null,
    object_key: null,
    object_version_id: null,
    retention_until: null,
    legal_hold: 0,
    deleted_at: null,
    last_storage_error: null,
  };
}

function primaryKeyColumns(table: SqliteTable): SqliteColumn[] {
  return table.primaryKey.map((name) => {
    const column = table.columns.find((candidate) => candidate.name === name);
    if (!column) throw new Error(`Primary key column ${table.name}.${name} отсутствует`);
    return column;
  });
}

function selfReference(table: SqliteTable): SqliteSelfReference | null {
  const references = table.selfReferences ?? [];
  if (references.length === 0) return null;
  if (references.length !== 1 || table.primaryKey.length !== 1 || references[0]!.to !== table.primaryKey[0]) {
    throw new Error(`Self-references ${table.name} не поддерживаются безопасным dependency order`);
  }
  return references[0]!;
}

function selfReferenceCte(table: SqliteTable, reference: SqliteSelfReference): string {
  const tableName = quoteIdentifier(table.name);
  const primaryKey = quoteIdentifier(table.primaryKey[0]!);
  const parentKey = quoteIdentifier(reference.from);
  return `
    WITH RECURSIVE migration_order(migration_key, migration_depth) AS (
      SELECT ${primaryKey}, 0 FROM ${tableName} WHERE ${parentKey} IS NULL
      UNION ALL
      SELECT child.${primaryKey}, parent.migration_depth + 1
      FROM ${tableName} AS child
      JOIN migration_order AS parent ON child.${parentKey} = parent.migration_key
    )
  `;
}

function assertSelfReferencesAcyclic(database: DatabaseSync, table: SqliteTable): void {
  const reference = selfReference(table);
  if (!reference) return;
  const tableName = quoteIdentifier(table.name);
  const primaryKey = quoteIdentifier(table.primaryKey[0]!);
  const unreachable = statementWithBigInts(database.prepare(`
    ${selfReferenceCte(table, reference)}
    SELECT COUNT(*) AS count
    FROM ${tableName} AS source
    LEFT JOIN migration_order ON source.${primaryKey} = migration_order.migration_key
    WHERE migration_order.migration_key IS NULL
  `)).get() as { count: bigint };
  if (unreachable.count > 0n) {
    throw new Error(`Self-reference cycle в ${table.name}: ${unreachable.count} rows не достижимы от root`);
  }
}

function sourceRows(database: DatabaseSync, table: SqliteTable, dependencyOrder = false): Iterable<MigrationRow> {
  const columns = table.columns.map((column) => quoteIdentifier(column.name)).join(", ");
  const order = primaryKeyColumns(table).map((column) => sqliteOrderExpression(column)).join(", ");
  const reference = dependencyOrder ? selfReference(table) : null;
  if (!reference) {
    return statementWithBigInts(database.prepare(`SELECT ${columns} FROM ${quoteIdentifier(table.name)} ORDER BY ${order}`))
      .iterate() as Iterable<MigrationRow>;
  }

  const tableName = quoteIdentifier(table.name);
  const primaryKey = quoteIdentifier(table.primaryKey[0]!);
  const projectedColumns = table.columns
    .map((column) => `source.${quoteIdentifier(column.name)}`)
    .join(", ");
  const dependencyOrderBy = primaryKeyColumns(table)
    .map((column) => sqliteOrderExpression(column, "source"))
    .join(", ");
  return statementWithBigInts(database.prepare(`
    ${selfReferenceCte(table, reference)}
    SELECT ${projectedColumns}
    FROM ${tableName} AS source
    JOIN migration_order ON source.${primaryKey} = migration_order.migration_key
    ORDER BY migration_order.migration_depth, ${dependencyOrderBy}
  `))
    .iterate() as Iterable<MigrationRow>;
}

async function insertBatch(client: Client, table: SqliteTable, rows: MigrationRow[]): Promise<void> {
  if (rows.length === 0) return;
  const columns = table.columns.map((column) => quoteIdentifier(column.name)).join(", ");
  const params: unknown[] = [];
  const values = rows.map((row) => {
    const placeholders = table.columns.map((column) => {
      params.push(row[column.name]);
      return `$${params.length}`;
    });
    return `(${placeholders.join(", ")})`;
  });
  await client.query(`INSERT INTO ${quoteIdentifier(table.name)} (${columns}) VALUES ${values.join(", ")}`, params);
}

async function importTable(
  database: DatabaseSync,
  client: Client,
  table: SqliteTable,
  options: SqlitePostgresMigrationOptions,
  artifactStats: ArtifactStats,
): Promise<TableDigest> {
  const hash = createHash("sha256");
  let count = 0;
  let batch: MigrationRow[] = [];
  const batchRows = Math.max(1, Math.min(options.batchRows, Math.floor(MAX_POSTGRES_PARAMETERS / table.columns.length)));
  const requiresDependencyOrder = (table.selfReferences?.length ?? 0) > 0;
  for (const original of sourceRows(database, table, requiresDependencyOrder)) {
    const row = table.name === "artifacts" ? transformArtifact(original, options, artifactStats) : original;
    if (!requiresDependencyOrder) {
      hash.update(canonicalMigrationRow(table.columns, row));
      hash.update("\n");
    }
    count += 1;
    batch.push(row);
    if (batch.length >= batchRows) {
      await insertBatch(client, table, batch);
      batch = [];
    }
  }
  await insertBatch(client, table, batch);
  if (requiresDependencyOrder) return digestSourceTable(database, table);
  return finishDigest(hash, count);
}

function digestSourceTable(
  database: DatabaseSync,
  table: SqliteTable,
  options?: SqlitePostgresMigrationOptions,
  artifactStats?: ArtifactStats,
): TableDigest {
  const hash = createHash("sha256");
  let count = 0;
  for (const original of sourceRows(database, table)) {
    const row = table.name === "artifacts" && options && artifactStats
      ? transformArtifact(original, options, artifactStats)
      : original;
    hash.update(canonicalMigrationRow(table.columns, row));
    hash.update("\n");
    count += 1;
  }
  return finishDigest(hash, count);
}

async function digestTargetTable(client: Client, table: SqliteTable, pageSize: number): Promise<TableDigest> {
  const hash = createHash("sha256");
  const columns = table.columns.map((column) => quoteIdentifier(column.name)).join(", ");
  const orderExpressions = primaryKeyColumns(table).map(postgresOrderExpression);
  const order = orderExpressions.join(", ");
  let lastKey: unknown[] | null = null;
  let count = 0;
  while (true) {
    const params: unknown[] = [];
    let where = "";
    if (lastKey) {
      const placeholders = lastKey.map((value) => {
        params.push(value);
        return `$${params.length}`;
      });
      where = `WHERE (${orderExpressions.join(", ")}) > (${placeholders.join(", ")})`;
    }
    params.push(pageSize);
    const result = await client.query<Record<string, unknown>>(`
      SELECT ${columns} FROM ${quoteIdentifier(table.name)}
      ${where} ORDER BY ${order} LIMIT $${params.length}
    `, params);
    if (result.rows.length === 0) break;
    for (const row of result.rows) {
      hash.update(canonicalMigrationRow(table.columns, row));
      hash.update("\n");
      count += 1;
    }
    const finalRow = result.rows.at(-1)!;
    lastKey = table.primaryKey.map((key) => finalRow[key]);
    if (result.rows.length < pageSize) break;
  }
  return finishDigest(hash, count);
}

async function targetFingerprint(client: Client, tables: SqliteTable[], pageSize: number): Promise<TargetFingerprint> {
  const tableDigests: Record<string, TableDigest> = {};
  const overall = createHash("sha256");
  for (const table of [...tables].sort((left, right) => left.name.localeCompare(right.name))) {
    const digest = await digestTargetTable(client, table, pageSize);
    tableDigests[table.name] = digest;
    overall.update(`${table.name}:${digest.rows}:${digest.sha256}\n`);
  }
  return { sha256: overall.digest("hex"), tables: tableDigests };
}

async function targetIdentity(client: Client): Promise<string> {
  const result = await client.query<{ database_name: string; schema_name: string; role_name: string }>(`
    SELECT current_database() AS database_name, current_schema() AS schema_name, current_user AS role_name
  `);
  return createHash("sha256").update(JSON.stringify(result.rows[0] ?? {})).digest("hex");
}

async function unvalidatedForeignKeys(client: Client): Promise<number> {
  const result = await client.query<{ count: string }>(`
    SELECT COUNT(*)::text AS count FROM pg_constraint
    WHERE contype = 'f' AND connamespace = current_schema()::regnamespace AND NOT convalidated
  `);
  return Number(result.rows[0]?.count ?? 0);
}

async function resetSerialSequences(client: Client): Promise<void> {
  await client.query(`
    SELECT setval(
      pg_get_serial_sequence(format('%I.%I', current_schema(), 'events'), 'id'),
      COALESCE((SELECT MAX(id) FROM events), 1),
      EXISTS (SELECT 1 FROM events)
    )
  `);
}

function reportDigest(report: Omit<SqlitePostgresMigrationReport, "reportSha256">): string {
  return createHash("sha256").update(JSON.stringify(report)).digest("hex");
}

async function writeReport(reportPath: string, report: SqlitePostgresMigrationReport): Promise<void> {
  const resolved = path.resolve(reportPath);
  await fs.promises.mkdir(path.dirname(resolved), { recursive: true });
  const temporary = `${resolved}.${process.pid}.tmp`;
  await fs.promises.writeFile(temporary, `${JSON.stringify(report, null, 2)}\n`, { mode: 0o600 });
  await fs.promises.rename(temporary, resolved);
}

function targetTableReport(
  table: SqliteTable,
  source: TableDigest,
  target: TableDigest,
): MigrationTableReport {
  const ephemeral = EPHEMERAL_TABLES.has(table.name);
  return {
    table: table.name,
    policy: ephemeral ? "runtime_ephemeral_reset" : "copied",
    sourceRows: source.rows,
    targetRows: target.rows,
    sourceSha256: source.sha256,
    targetSha256: target.sha256,
    match: ephemeral ? target.rows === 0 : source.rows === target.rows && source.sha256 === target.sha256,
  };
}

export async function migrateSqliteToPostgres(options: SqlitePostgresMigrationOptions): Promise<SqlitePostgresMigrationReport> {
  if (options.offlineConfirmation !== "SOURCE_AND_WRITERS_STOPPED") {
    throw new Error("Нужен --confirm-offline SOURCE_AND_WRITERS_STOPPED");
  }
  validateTargetUrls(options);
  const sourcePath = path.resolve(options.sourcePath);
  if (!fs.existsSync(sourcePath) || !fs.statSync(sourcePath).isFile()) throw new Error("SQLite source file не найден");
  const migrationId = randomUUID();
  const startedAt = new Date().toISOString();
  const source = new DatabaseSync(sourcePath);
  let sourceLocked = false;
  let target = postgresClient(options.targetUrl, options, `agat-migration-${migrationId}`);
  let targetConnected = false;
  let targetTransaction = false;
  try {
    source.exec("PRAGMA busy_timeout = 1000; BEGIN IMMEDIATE;");
    sourceLocked = true;
    source.exec("PRAGMA query_only = ON;");
    const foreignKeyViolations = sourcePreflight(source);
    const tables = sqliteTables(source);
    for (const table of tables) assertSelfReferencesAcyclic(source, table);
    const tableByName = new Map(tables.map((table) => [table.name, table]));
    const order = migrationTableOrder(tables);
    const cell = sourceCell(source);
    const databaseSha256 = await hashFiles([sourcePath, `${sourcePath}-wal`]);

    await target.connect();
    targetConnected = true;
    if (options.mode !== "verify") {
      await assertPristineTarget(target);
      await target.end();
      targetConnected = false;
      bootstrapTarget(options, cell, migrationId);
      target = postgresClient(options.targetUrl, options, `agat-migration-${migrationId}`);
      await target.connect();
      targetConnected = true;
    }
    await assertTargetShape(target, tables);
    const identitySha256 = await targetIdentity(target);
    const baseline = options.mode === "rehearse"
      ? await targetFingerprint(target, tables, options.batchRows)
      : null;
    const artifactStats: ArtifactStats = { verified: 0, backfilledFromFilesystem: 0, bytes: 0 };
    const sourceDigests = new Map<string, TableDigest>();

    if (options.mode !== "verify") {
      await target.query("BEGIN");
      targetTransaction = true;
      await target.query("SELECT pg_advisory_xact_lock($1)", [MIGRATION_LOCK_ID]);
      await target.query("ALTER TABLE events DISABLE TRIGGER agat_events_audit_outbox");
      const targetNames = tables.map((table) => quoteIdentifier(table.name)).join(", ");
      await target.query(`TRUNCATE TABLE ${targetNames} RESTART IDENTITY CASCADE`);
      for (const name of order) {
        const table = tableByName.get(name)!;
        if (EPHEMERAL_TABLES.has(name)) {
          sourceDigests.set(name, digestSourceTable(source, table));
          continue;
        }
        sourceDigests.set(name, await importTable(source, target, table, options, artifactStats));
      }
      await resetSerialSequences(target);
      await target.query("ALTER TABLE events ENABLE TRIGGER agat_events_audit_outbox");
      await target.query("SET CONSTRAINTS ALL IMMEDIATE");
    } else {
      await target.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");
      targetTransaction = true;
      for (const table of tables) {
        sourceDigests.set(table.name, digestSourceTable(source, table, options, artifactStats));
      }
    }

    const tableReports: MigrationTableReport[] = [];
    for (const table of [...tables].sort((left, right) => left.name.localeCompare(right.name))) {
      const targetDigest = await digestTargetTable(target, table, options.batchRows);
      tableReports.push(targetTableReport(table, sourceDigests.get(table.name)!, targetDigest));
    }
    const invalidForeignKeys = await unvalidatedForeignKeys(target);
    const matched = tableReports.every((table) => table.match) && invalidForeignKeys === 0;
    if (!matched) throw new Error("Reconciliation не совпал; target transaction будет отменена");

    let rollbackVerified = false;
    let restored: TargetFingerprint | null = null;
    if (options.mode === "rehearse") {
      await target.query("ROLLBACK");
      targetTransaction = false;
      restored = await targetFingerprint(target, tables, options.batchRows);
      rollbackVerified = baseline?.sha256 === restored.sha256;
      if (!rollbackVerified) throw new Error("Rehearsal rollback не восстановил baseline target");
    } else if (options.mode === "apply") {
      await target.query("COMMIT");
      targetTransaction = false;
    } else {
      await target.query("ROLLBACK");
      targetTransaction = false;
    }

    const withoutDigest: Omit<SqlitePostgresMigrationReport, "reportSha256"> = {
      schemaVersion: 1,
      migrationId,
      mode: options.mode,
      startedAt,
      completedAt: new Date().toISOString(),
      source: {
        sqliteSchemaVersion: REQUIRED_SQLITE_SCHEMA_VERSION,
        databaseSha256,
        cell,
        foreignKeyViolations,
      },
      target: {
        identitySha256,
        unvalidatedForeignKeys: invalidForeignKeys,
      },
      artifacts: artifactStats,
      tables: tableReports,
      rollback: {
        requested: options.mode === "rehearse",
        verified: rollbackVerified,
        baselineSha256: baseline?.sha256 ?? null,
        restoredSha256: restored?.sha256 ?? null,
      },
      success: true,
    };
    const report = { ...withoutDigest, reportSha256: reportDigest(withoutDigest) };
    await writeReport(options.reportPath, report);
    return report;
  } finally {
    if (targetTransaction) {
      try {
        await target.query("ROLLBACK");
      } catch {
        // Preserve the original failure.
      }
    }
    if (targetConnected) await target.end().catch(() => undefined);
    if (sourceLocked) {
      try {
        source.exec("ROLLBACK");
      } catch {
        // Preserve the original failure.
      }
    }
    source.close();
  }
}

function argument(name: string): string | undefined {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

function cliOptions(): SqlitePostgresMigrationOptions {
  const mode = argument("--mode") ?? "rehearse";
  if (mode !== "apply" && mode !== "rehearse" && mode !== "verify") throw new Error("--mode: apply, rehearse или verify");
  const targetUrl = process.env.AGAT_POSTGRES_MIGRATION_URL
    ?? process.env.AGAT_MIGRATION_POSTGRES_URL
    ?? "";
  const targetRuntimeUrl = process.env.AGAT_POSTGRES_URL ?? "";
  const targetTenantUrl = process.env.AGAT_MIGRATION_POSTGRES_TENANT_URL ?? process.env.AGAT_POSTGRES_TENANT_URL ?? "";
  if (!targetUrl || !targetRuntimeUrl || !targetTenantUrl) {
    throw new Error("Нужны AGAT_POSTGRES_MIGRATION_URL, AGAT_POSTGRES_URL и AGAT_POSTGRES_TENANT_URL");
  }
  const sourcePath = argument("--source");
  const reportPath = argument("--report");
  if (!sourcePath || !reportPath) throw new Error("Нужны --source и --report");
  const batchRows = Number(argument("--batch-rows") ?? DEFAULT_BATCH_ROWS);
  if (!Number.isInteger(batchRows) || batchRows < 1 || batchRows > 5_000) throw new Error("--batch-rows должен быть 1..5000");
  const sslMode = process.env.AGAT_POSTGRES_SSL_MODE ?? "disable";
  if (sslMode !== "disable" && sslMode !== "require" && sslMode !== "verify-full") {
    throw new Error("AGAT_POSTGRES_SSL_MODE: disable, require или verify-full");
  }
  return {
    sourcePath,
    artifactsDir: argument("--artifacts-dir") ?? "./data/artifacts",
    reportPath,
    mode,
    targetUrl,
    targetRuntimeUrl,
    targetTenantUrl,
    sslMode,
    sslCaPath: process.env.AGAT_POSTGRES_CA_CERT_PATH,
    sslCertPath: process.env.AGAT_POSTGRES_CLIENT_CERT_PATH,
    sslKeyPath: process.env.AGAT_POSTGRES_CLIENT_KEY_PATH,
    batchRows,
    offlineConfirmation: argument("--confirm-offline") ?? "",
  };
}

async function main(): Promise<void> {
  const report = await migrateSqliteToPostgres(cliOptions());
  process.stdout.write(`${JSON.stringify({
    migrationId: report.migrationId,
    mode: report.mode,
    success: report.success,
    reportSha256: report.reportSha256,
    reportPath: path.resolve(argument("--report")!),
  })}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  void main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : "Migration failed"}\n`);
    process.exitCode = 1;
  });
}
