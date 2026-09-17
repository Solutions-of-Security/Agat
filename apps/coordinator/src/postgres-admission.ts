import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { pathToFileURL } from "node:url";

import pg, { type Client, type ClientConfig } from "pg";

export interface PostgresConnectionBudgetInput {
  maxConnections: number;
  superuserReservedConnections: number;
  reservedConnections: number;
  currentConnections: number;
  expectedReplicas: number;
  poolMax: number;
  budgetPercent: number;
  externalReserve: number;
  runtimeRoleConnectionLimit: number;
  tenantRoleConnectionLimit: number;
}

export interface PostgresConnectionBudget {
  usableConnections: number;
  admittedConnections: number;
  plannedRuntimeConnections: number;
  plannedTenantConnections: number;
  plannedTotalConnections: number;
  availableForNewAgatConnections: number;
  pass: boolean;
  reasons: string[];
}

export interface PostgresAdmissionOptions {
  runtimeUrl: string;
  tenantUrl: string;
  sslMode: "disable" | "require" | "verify-full";
  sslCaPath?: string;
  sslCertPath?: string;
  sslKeyPath?: string;
  expectedReplicas: number;
  poolMax: number;
  budgetPercent: number;
  externalReserve: number;
  concurrency: number;
  durationMs: number;
  p99LimitMs: number;
  minimumOperations: number;
  reportPath?: string;
}

export interface PostgresAdmissionReport {
  schemaVersion: 1;
  startedAt: string;
  completedAt: string;
  targetIdentitySha256: string;
  budget: PostgresConnectionBudget & {
    maxConnections: number;
    superuserReservedConnections: number;
    reservedConnections: number;
    currentConnections: number;
    runtimeRoleConnectionLimit: number;
    tenantRoleConnectionLimit: number;
  };
  load: {
    concurrency: number;
    durationMs: number;
    operations: number;
    errors: number;
    errorCodes: Record<string, number>;
    p50Ms: number;
    p95Ms: number;
    p99Ms: number;
    p99LimitMs: number;
    minimumOperations: number;
    pass: boolean;
  };
  success: boolean;
  reportSha256: string;
}

interface CapacityRow {
  database_name: string;
  role_name: string;
  max_connections: string;
  superuser_reserved_connections: string;
  reserved_connections: string;
  current_connections: string;
  role_connection_limit: number;
}

function boundedInteger(value: unknown, field: string, minimum: number, maximum: number): number {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error(`${field} должен быть ${minimum}..${maximum}`);
  }
  return parsed;
}

export function evaluatePostgresConnectionBudget(input: PostgresConnectionBudgetInput): PostgresConnectionBudget {
  const usableConnections = Math.max(
    0,
    input.maxConnections - input.superuserReservedConnections - input.reservedConnections,
  );
  const admittedConnections = Math.floor(usableConnections * input.budgetPercent / 100);
  const plannedRuntimeConnections = input.expectedReplicas * input.poolMax;
  const plannedTenantConnections = input.expectedReplicas * input.poolMax;
  const plannedTotalConnections = plannedRuntimeConnections + plannedTenantConnections;
  const availableForNewAgatConnections = Math.max(
    0,
    admittedConnections - input.currentConnections - input.externalReserve,
  );
  const reasons: string[] = [];
  if (plannedTotalConnections > availableForNewAgatConnections) {
    reasons.push("planned coordinator pools exceed the admitted database connection budget");
  }
  if (input.runtimeRoleConnectionLimit >= 0 && plannedRuntimeConnections > input.runtimeRoleConnectionLimit) {
    reasons.push("runtime role CONNECTION LIMIT is below the planned runtime pools");
  }
  if (input.tenantRoleConnectionLimit >= 0 && plannedTenantConnections > input.tenantRoleConnectionLimit) {
    reasons.push("tenant role CONNECTION LIMIT is below the planned tenant pools");
  }
  return {
    usableConnections,
    admittedConnections,
    plannedRuntimeConnections,
    plannedTenantConnections,
    plannedTotalConnections,
    availableForNewAgatConnections,
    pass: reasons.length === 0,
    reasons,
  };
}

function sslConfig(options: PostgresAdmissionOptions): ClientConfig["ssl"] {
  if (options.sslMode === "disable") return false;
  return {
    rejectUnauthorized: options.sslMode === "verify-full",
    ...(options.sslCaPath ? { ca: fs.readFileSync(options.sslCaPath, "utf8") } : {}),
    ...(options.sslCertPath ? { cert: fs.readFileSync(options.sslCertPath, "utf8") } : {}),
    ...(options.sslKeyPath ? { key: fs.readFileSync(options.sslKeyPath, "utf8") } : {}),
  };
}

function client(url: string, options: PostgresAdmissionOptions, suffix: string): Client {
  return new pg.Client({
    connectionString: url,
    application_name: `agat-connection-admission-${suffix}`.slice(0, 63),
    connectionTimeoutMillis: 10_000,
    statement_timeout: 10_000,
    query_timeout: 10_000,
    ssl: sslConfig(options),
  });
}

async function capacity(connection: Client): Promise<CapacityRow> {
  const result = await connection.query<CapacityRow>(`
    SELECT
      current_database() AS database_name,
      current_user AS role_name,
      current_setting('max_connections') AS max_connections,
      current_setting('superuser_reserved_connections') AS superuser_reserved_connections,
      current_setting('reserved_connections') AS reserved_connections,
      (SELECT COUNT(*)::text FROM pg_stat_activity WHERE backend_type = 'client backend') AS current_connections,
      role_row.rolconnlimit AS role_connection_limit
    FROM pg_roles AS role_row
    WHERE role_row.rolname = current_user
  `);
  const row = result.rows[0];
  if (!row) throw new Error("PostgreSQL capacity profile отсутствует");
  return row;
}

function percentile(values: number[], fraction: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil(sorted.length * fraction) - 1));
  return Number(sorted[index]!.toFixed(3));
}

async function writeReport(reportPath: string, report: PostgresAdmissionReport): Promise<void> {
  const resolved = path.resolve(reportPath);
  await fs.promises.mkdir(path.dirname(resolved), { recursive: true });
  const temporary = `${resolved}.${process.pid}.tmp`;
  await fs.promises.writeFile(temporary, `${JSON.stringify(report, null, 2)}\n`, { mode: 0o600 });
  await fs.promises.rename(temporary, resolved);
}

export async function runPostgresAdmission(options: PostgresAdmissionOptions): Promise<PostgresAdmissionReport> {
  const startedAt = new Date().toISOString();
  const runtime = client(options.runtimeUrl, options, "capacity-runtime");
  const tenant = client(options.tenantUrl, options, "capacity-tenant");
  let runtimeCapacity: CapacityRow;
  let tenantCapacity: CapacityRow;
  try {
    await Promise.all([runtime.connect(), tenant.connect()]);
    [runtimeCapacity, tenantCapacity] = await Promise.all([capacity(runtime), capacity(tenant)]);
  } finally {
    await Promise.all([
      runtime.end().catch(() => undefined),
      tenant.end().catch(() => undefined),
    ]);
  }
  if (runtimeCapacity.database_name !== tenantCapacity.database_name) {
    throw new Error("Runtime и tenant admission URLs указывают на разные databases");
  }
  if (runtimeCapacity.role_name === tenantCapacity.role_name) {
    throw new Error("Runtime и tenant admission URLs используют одну роль");
  }
  const maxConnections = Number(runtimeCapacity.max_connections);
  const superuserReservedConnections = Number(runtimeCapacity.superuser_reserved_connections);
  const reservedConnections = Number(runtimeCapacity.reserved_connections);
  const currentConnections = Number(runtimeCapacity.current_connections);
  const budget = evaluatePostgresConnectionBudget({
    maxConnections,
    superuserReservedConnections,
    reservedConnections,
    currentConnections,
    expectedReplicas: options.expectedReplicas,
    poolMax: options.poolMax,
    budgetPercent: options.budgetPercent,
    externalReserve: options.externalReserve,
    runtimeRoleConnectionLimit: runtimeCapacity.role_connection_limit,
    tenantRoleConnectionLimit: tenantCapacity.role_connection_limit,
  });

  const latencies: number[] = [];
  const errorCodes = new Map<string, number>();
  if (budget.pass) {
    const connections = Array.from({ length: options.concurrency }, (_, index) => (
      client(index % 2 === 0 ? options.runtimeUrl : options.tenantUrl, options, `load-${index}`)
    ));
    try {
      await Promise.all(connections.map((connection) => connection.connect()));
      const deadline = performance.now() + options.durationMs;
      await Promise.all(connections.map(async (connection) => {
        while (performance.now() < deadline) {
          const operationStarted = performance.now();
          try {
            await connection.query("SELECT 1");
            latencies.push(performance.now() - operationStarted);
          } catch (error) {
            const code = error && typeof error === "object" && typeof (error as { code?: unknown }).code === "string"
              ? (error as { code: string }).code
              : "QUERY_ERROR";
            errorCodes.set(code, (errorCodes.get(code) ?? 0) + 1);
          }
        }
      }));
    } catch (error) {
      const code = error && typeof error === "object" && typeof (error as { code?: unknown }).code === "string"
        ? (error as { code: string }).code
        : "CONNECT_ERROR";
      errorCodes.set(code, (errorCodes.get(code) ?? 0) + 1);
    } finally {
      await Promise.all(connections.map((connection) => connection.end().catch(() => undefined)));
    }
  }

  const errors = [...errorCodes.values()].reduce((total, count) => total + count, 0);
  const p99Ms = percentile(latencies, 0.99);
  const loadPass = budget.pass
    && errors === 0
    && latencies.length >= options.minimumOperations
    && p99Ms <= options.p99LimitMs;
  const withoutDigest: Omit<PostgresAdmissionReport, "reportSha256"> = {
    schemaVersion: 1,
    startedAt,
    completedAt: new Date().toISOString(),
    targetIdentitySha256: createHash("sha256")
      .update(JSON.stringify({
        database: runtimeCapacity.database_name,
        host: new URL(options.runtimeUrl).hostname,
        port: new URL(options.runtimeUrl).port || "5432",
      }))
      .digest("hex"),
    budget: {
      ...budget,
      maxConnections,
      superuserReservedConnections,
      reservedConnections,
      currentConnections,
      runtimeRoleConnectionLimit: runtimeCapacity.role_connection_limit,
      tenantRoleConnectionLimit: tenantCapacity.role_connection_limit,
    },
    load: {
      concurrency: options.concurrency,
      durationMs: options.durationMs,
      operations: latencies.length,
      errors,
      errorCodes: Object.fromEntries([...errorCodes.entries()].sort(([left], [right]) => left.localeCompare(right))),
      p50Ms: percentile(latencies, 0.5),
      p95Ms: percentile(latencies, 0.95),
      p99Ms,
      p99LimitMs: options.p99LimitMs,
      minimumOperations: options.minimumOperations,
      pass: loadPass,
    },
    success: budget.pass && loadPass,
  };
  const report = {
    ...withoutDigest,
    reportSha256: createHash("sha256").update(JSON.stringify(withoutDigest)).digest("hex"),
  };
  if (options.reportPath) await writeReport(options.reportPath, report);
  return report;
}

function sslMode(): PostgresAdmissionOptions["sslMode"] {
  const value = process.env.AGAT_POSTGRES_SSL_MODE ?? "disable";
  if (value !== "disable" && value !== "require" && value !== "verify-full") {
    throw new Error("AGAT_POSTGRES_SSL_MODE: disable, require или verify-full");
  }
  return value;
}

export function postgresAdmissionOptionsFromEnv(): PostgresAdmissionOptions {
  const runtimeUrl = process.env.AGAT_POSTGRES_URL ?? "";
  const tenantUrl = process.env.AGAT_POSTGRES_TENANT_URL ?? "";
  if (!runtimeUrl || !tenantUrl) throw new Error("Нужны AGAT_POSTGRES_URL и AGAT_POSTGRES_TENANT_URL");
  const expectedReplicas = boundedInteger(process.env.AGAT_POSTGRES_EXPECTED_REPLICAS ?? "2", "AGAT_POSTGRES_EXPECTED_REPLICAS", 1, 64);
  const poolMax = boundedInteger(process.env.AGAT_POSTGRES_POOL_MAX ?? "4", "AGAT_POSTGRES_POOL_MAX", 1, 32);
  const plannedConnections = expectedReplicas * poolMax * 2;
  return {
    runtimeUrl,
    tenantUrl,
    sslMode: sslMode(),
    sslCaPath: process.env.AGAT_POSTGRES_CA_CERT_PATH,
    sslCertPath: process.env.AGAT_POSTGRES_CLIENT_CERT_PATH,
    sslKeyPath: process.env.AGAT_POSTGRES_CLIENT_KEY_PATH,
    expectedReplicas,
    poolMax,
    budgetPercent: boundedInteger(process.env.AGAT_POSTGRES_CONNECTION_BUDGET_PERCENT ?? "80", "AGAT_POSTGRES_CONNECTION_BUDGET_PERCENT", 10, 95),
    externalReserve: boundedInteger(process.env.AGAT_POSTGRES_EXTERNAL_CONNECTION_RESERVE ?? "5", "AGAT_POSTGRES_EXTERNAL_CONNECTION_RESERVE", 0, 10_000),
    concurrency: boundedInteger(process.env.AGAT_POSTGRES_ADMISSION_CONCURRENCY ?? String(Math.min(plannedConnections, 128)), "AGAT_POSTGRES_ADMISSION_CONCURRENCY", 1, 128),
    durationMs: boundedInteger(process.env.AGAT_POSTGRES_ADMISSION_DURATION_MS ?? "5000", "AGAT_POSTGRES_ADMISSION_DURATION_MS", 500, 300_000),
    p99LimitMs: boundedInteger(process.env.AGAT_POSTGRES_ADMISSION_P99_MS ?? "250", "AGAT_POSTGRES_ADMISSION_P99_MS", 1, 60_000),
    minimumOperations: boundedInteger(process.env.AGAT_POSTGRES_ADMISSION_MIN_OPERATIONS ?? String(Math.max(100, plannedConnections * 10)), "AGAT_POSTGRES_ADMISSION_MIN_OPERATIONS", 1, 10_000_000),
    reportPath: process.env.AGAT_POSTGRES_ADMISSION_REPORT_PATH,
  };
}

async function main(): Promise<void> {
  const report = await runPostgresAdmission(postgresAdmissionOptionsFromEnv());
  process.stdout.write(`${JSON.stringify(report)}\n`);
  if (!report.success) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  void main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : "PostgreSQL admission failed"}\n`);
    process.exitCode = 1;
  });
}
