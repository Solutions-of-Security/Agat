import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

import pg, { type ClientConfig } from "pg";

import {
  AgatStore,
  POSTGRES_SCHEMA_CONTRACT,
  POSTGRES_SCHEMA_VERSION,
} from "./database.js";
import {
  postgresAdmissionOptionsFromEnv,
  runPostgresAdmission,
} from "./postgres-admission.js";
import type { PostgresDatabaseOptions } from "./postgres-database.js";

const POSTGRES_RELEASE_GATE_LOCK_ID = 867_530_904;

interface SchemaMigrationEnvironment {
  migrationUrl: string;
  runtimeUrl: string;
  tenantUrl: string;
  runtimeRole: string;
  region: string;
  residencyDomain: string;
  migrationDatabase: PostgresDatabaseOptions;
  runtimeDatabase: PostgresDatabaseOptions;
  directSsl: ClientConfig["ssl"];
}

function role(url: URL, field: string): string {
  const value = decodeURIComponent(url.username);
  if (!/^[a-z_][a-z0-9_]{0,62}$/.test(value)) throw new Error(`${field} должен быть lowercase PostgreSQL role`);
  return value;
}

function integer(value: string | undefined, fallback: number, minimum: number, maximum: number): number {
  const parsed = Number(value ?? fallback);
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error(`Некорректное bounded integer: ${value ?? fallback}`);
  }
  return parsed;
}

function file(pathname: string | undefined): string {
  return pathname ? fs.readFileSync(path.resolve(pathname), "utf8") : "";
}

function environment(): SchemaMigrationEnvironment {
  const migrationUrl = process.env.AGAT_POSTGRES_MIGRATION_URL
    ?? process.env.AGAT_MIGRATION_POSTGRES_URL
    ?? "";
  const runtimeUrl = process.env.AGAT_POSTGRES_URL ?? "";
  const tenantUrl = process.env.AGAT_POSTGRES_TENANT_URL ?? "";
  if (!migrationUrl || !runtimeUrl || !tenantUrl) {
    throw new Error("Нужны AGAT_POSTGRES_MIGRATION_URL, AGAT_POSTGRES_URL и AGAT_POSTGRES_TENANT_URL");
  }
  let migration: URL;
  let runtime: URL;
  let tenant: URL;
  try {
    migration = new URL(migrationUrl);
    runtime = new URL(runtimeUrl);
    tenant = new URL(tenantUrl);
  } catch {
    throw new Error("PostgreSQL migration/runtime/tenant URL некорректен");
  }
  for (const url of [migration, runtime, tenant]) {
    if (!url.password || (url.protocol !== "postgres:" && url.protocol !== "postgresql:")) {
      throw new Error("PostgreSQL roles требуют password credential и postgresql:// URL");
    }
    if (url.hostname !== migration.hostname || url.port !== migration.port || url.pathname !== migration.pathname) {
      throw new Error("PostgreSQL migration/runtime/tenant URLs должны указывать на одну database");
    }
  }
  const roles = [role(migration, "Migration role"), role(runtime, "Runtime role"), role(tenant, "Tenant role")];
  if (new Set(roles).size !== 3) throw new Error("PostgreSQL migration/runtime/tenant roles должны различаться");

  const sslMode = process.env.AGAT_POSTGRES_SSL_MODE ?? "disable";
  if (sslMode !== "disable" && sslMode !== "require" && sslMode !== "verify-full") {
    throw new Error("AGAT_POSTGRES_SSL_MODE: disable, require или verify-full");
  }
  const sslCa = file(process.env.AGAT_POSTGRES_CA_CERT_PATH);
  const sslCert = file(process.env.AGAT_POSTGRES_CLIENT_CERT_PATH);
  const sslKey = file(process.env.AGAT_POSTGRES_CLIENT_KEY_PATH);
  if (Boolean(sslCert) !== Boolean(sslKey)) throw new Error("PostgreSQL client certificate и key задаются вместе");
  const common = {
    tenantUrl,
    applicationName: "agat-postgres-schema-v21",
    poolMax: 1,
    connectTimeoutMs: integer(process.env.AGAT_POSTGRES_CONNECT_TIMEOUT_MS, 5_000, 500, 60_000),
    idleTimeoutMs: integer(process.env.AGAT_POSTGRES_IDLE_TIMEOUT_MS, 30_000, 1_000, 600_000),
    statementTimeoutMs: integer(process.env.AGAT_POSTGRES_MIGRATION_STATEMENT_TIMEOUT_MS, 600_000, 1_000, 3_600_000),
    sslMode,
    sslCa,
    sslCert,
    sslKey,
  } as const;
  return {
    migrationUrl,
    runtimeUrl,
    tenantUrl,
    runtimeRole: roles[1]!,
    region: process.env.AGAT_REGION ?? "local",
    residencyDomain: process.env.AGAT_RESIDENCY_DOMAIN ?? process.env.AGAT_REGION ?? "local",
    migrationDatabase: { ...common, systemUrl: migrationUrl, roleMode: "migration" },
    runtimeDatabase: { ...common, systemUrl: runtimeUrl, roleMode: "runtime", statementTimeoutMs: 30_000 },
    directSsl: sslMode === "disable" ? false : {
      rejectUnauthorized: sslMode === "verify-full",
      ...(sslCa ? { ca: sslCa } : {}),
      ...(sslCert ? { cert: sslCert, key: sslKey } : {}),
    },
  };
}

async function acquireMigrationGate(config: SchemaMigrationEnvironment): Promise<pg.Client> {
  const connection = new pg.Client({
    connectionString: config.migrationUrl,
    application_name: "agat-schema-active-replica-preflight",
    ssl: config.directSsl,
  });
  await connection.connect();
  try {
    await connection.query("SELECT pg_advisory_lock($1)", [POSTGRES_RELEASE_GATE_LOCK_ID]);
    const presence = await connection.query<{ present: boolean }>(`
      SELECT to_regclass(format('%I.%I', current_schema(), 'coordinator_replicas')) IS NOT NULL AS present
    `);
    if (presence.rows[0]?.present) {
      const active = await connection.query<{ count: string }>(`
        SELECT COUNT(*)::text AS count FROM coordinator_replicas
        WHERE status = 'ready' AND last_seen::timestamptz >= CURRENT_TIMESTAMP - INTERVAL '3 minutes'
      `);
      if (Number(active.rows[0]?.count ?? 0) > 0) {
        throw new Error("Schema migration запрещена при active coordinator replicas; scale runtime to zero");
      }
    }
    return connection;
  } catch (error) {
    await connection.end();
    throw error;
  }
}

function validateRuntime(config: SchemaMigrationEnvironment, requireAdmission: boolean): void {
  const store = new AgatStore(":postgresql:", {
    stateStoreDriver: "postgresql",
    postgres: config.runtimeDatabase,
    postgresSchemaMode: "runtime",
    postgresRuntimeRole: config.runtimeRole,
    schemaOnly: true,
    requirePostgresAdmission: requireAdmission,
    region: config.region,
    residencyDomain: config.residencyDomain,
    coordinatorInstanceId: "schema-runtime-validation",
  });
  store.close();
}

async function markAdmissionPassed(config: SchemaMigrationEnvironment, reportSha256: string): Promise<void> {
  const connection = new pg.Client({
    connectionString: config.migrationUrl,
    application_name: "agat-schema-admission-finalize",
    ssl: config.directSsl,
  });
  await connection.connect();
  try {
    await connection.query("BEGIN");
    await connection.query("SELECT pg_advisory_xact_lock($1)", [867_530_901]);
    const updated = await connection.query(`
      UPDATE agat_schema_migrations SET
        admission_status = 'passed',
        admission_report_sha256 = $1,
        admission_checked_at = CURRENT_TIMESTAMP::text
      WHERE version = $2 AND contract_id = $3
    `, [reportSha256, POSTGRES_SCHEMA_VERSION, POSTGRES_SCHEMA_CONTRACT]);
    if (updated.rowCount !== 1) throw new Error("Schema admission marker не найден");
    await connection.query("COMMIT");
  } catch (error) {
    await connection.query("ROLLBACK").catch(() => undefined);
    throw error;
  } finally {
    await connection.end();
  }
}

export async function migratePostgresSchemaAndAdmit(): Promise<{
  schemaVersion: number;
  contractId: string;
  admissionReportSha256: string;
  admissionOperations: number;
  admissionP99Ms: number;
}> {
  const config = environment();
  const gate = await acquireMigrationGate(config);
  try {
    const migrator = new AgatStore(":postgresql:", {
      stateStoreDriver: "postgresql",
      postgres: config.migrationDatabase,
      postgresSchemaMode: "migration",
      postgresRuntimeRole: config.runtimeRole,
      schemaOnly: true,
      requirePostgresAdmission: false,
      region: config.region,
      residencyDomain: config.residencyDomain,
      coordinatorInstanceId: "schema-migration-v21",
    });
    migrator.close();

    validateRuntime(config, false);
    const admission = await runPostgresAdmission(postgresAdmissionOptionsFromEnv());
    if (!admission.success) {
      throw new Error(`PostgreSQL connection admission failed: ${admission.reportSha256}`);
    }
    await markAdmissionPassed(config, admission.reportSha256);
    validateRuntime(config, true);
    return {
      schemaVersion: POSTGRES_SCHEMA_VERSION,
      contractId: POSTGRES_SCHEMA_CONTRACT,
      admissionReportSha256: admission.reportSha256,
      admissionOperations: admission.load.operations,
      admissionP99Ms: admission.load.p99Ms,
    };
  } finally {
    await gate.query("SELECT pg_advisory_unlock($1)", [POSTGRES_RELEASE_GATE_LOCK_ID]).catch(() => undefined);
    await gate.end().catch(() => undefined);
  }
}

async function main(): Promise<void> {
  process.stdout.write(`${JSON.stringify(await migratePostgresSchemaAndAdmit())}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  void main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : "PostgreSQL schema migration failed"}\n`);
    process.exitCode = 1;
  });
}
