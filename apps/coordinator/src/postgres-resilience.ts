import { createHash, createHmac, randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

import pg, { type Client, type ClientConfig } from "pg";

import { POSTGRES_SCHEMA_CONTRACT, POSTGRES_SCHEMA_VERSION } from "./database.js";

export type PostgresDrCheckpointKind =
  | "heartbeat"
  | "failover-before"
  | "failover-after"
  | "restore-before"
  | "restore-after"
  | "restore-verified";

export interface PostgresResiliencePolicy {
  schemaVersion: 1;
  policyId: string;
  engineMajor: number;
  topology: {
    minimumZones: number;
    minimumSynchronousStandbys: number;
    requireAutomaticFailover: boolean;
  };
  backup: {
    requirePitr: boolean;
    minimumRetentionHours: number;
    maximumRestoreLagSeconds: number;
    maximumBackupAgeSeconds: number;
  };
  objectives: {
    rpoSeconds: number;
    failoverRtoSeconds: number;
    restoreRtoSeconds: number;
    monthlyAvailabilityPercent: number;
    minimumSloWindowHours: number;
  };
  evidence: {
    maximumTopologyAgeSeconds: number;
    maximumDrillAgeDays: number;
    requiredDistinctApprovers: number;
    requiredApprovalRoles: string[];
  };
}

export interface ManagedPostgresOperationEvidence {
  type: "failover" | "restore";
  operationId: string;
  startedAt: string;
  completedAt: string;
  sourceClusterId: string;
  sourcePrimaryInstanceId: string;
  targetClusterId: string;
  targetPrimaryInstanceId: string;
  recoveryTargetAt?: string;
}

export interface ManagedPostgresSnapshot {
  schemaVersion: 1;
  observedAt: string;
  provider: string;
  service: string;
  clusterId: string;
  primaryInstanceId: string;
  primaryZone: string;
  zones: string[];
  region: string;
  residencyDomain: string;
  engine: { name: "postgresql"; major: number };
  ha: {
    automaticFailover: boolean;
    synchronousStandby: boolean;
    healthyStandbys: number;
  };
  backup: {
    pitrEnabled: boolean;
    retentionHours: number;
    oldestRestorableAt: string;
    latestRestorableAt: string;
    lastSuccessfulBackupAt: string;
  };
  security: {
    tlsMode: "verify-full";
    encryptionAtRest: boolean;
    publicEndpoint: boolean;
  };
  sloApproval: {
    policySha256: string;
    approvedAt: string;
    changeId: string;
    approvers: Array<{ subject: string; role: string }>;
  };
  operation?: ManagedPostgresOperationEvidence;
}

export interface SignedPostgresEvidence<T = unknown> {
  schemaVersion: 1;
  kind: string;
  payload: T;
  payloadSha256: string;
  hmacSha256: string;
}

export interface PostgresDrCheckpoint {
  checkpointId: string;
  checkpointKind: PostgresDrCheckpointKind;
  issuedAt: string;
  region: string;
  residencyDomain: string;
  payloadSha256: string;
  providerClusterId: string;
  providerPrimaryInstanceId: string;
  databaseServerAddress: string;
  postmasterStartedAt: string;
  schemaVersion: number;
  schemaContract: string;
}

export interface PostgresSloMetrics {
  schemaVersion: 1;
  windowStart: string;
  windowEnd: string;
  eligibleSeconds: number;
  unavailableSeconds: number;
  requestsTotal: number;
  requestsFailed: number;
  failoverReportSha256: string;
  restoreReportSha256: string;
}

interface DatabaseObservation {
  observedAt: string;
  databaseName: string;
  roleName: string;
  serverVersionNum: number;
  serverAddress: string;
  postmasterStartedAt: string;
  inRecovery: boolean;
  transactionReadOnly: boolean;
  tls: boolean;
  dataChecksums: boolean;
  walLevel: string;
  synchronousCommit: string;
  runtimeDdlFree: boolean;
  canaryTablePresent: boolean;
  schemaVersion: number;
  schemaContract: string;
  admissionStatus: string;
}

interface PostgresDrVerificationReport {
  reportType: "failover" | "restore";
  operationId: string;
  completedAt: string;
  providerEvidenceSha256: string;
  checkpointEvidenceSha256: string[];
  rpoSeconds: number;
  rtoSeconds: number;
  rpoObjectiveSeconds: number;
  rtoObjectiveSeconds: number;
  sourceClusterId: string;
  targetClusterId: string;
  sourcePrimaryInstanceId: string;
  targetPrimaryInstanceId: string;
  databaseObservation: DatabaseObservation;
  checks: Record<string, boolean>;
  success: boolean;
}

function object(value: unknown, field: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${field} должен быть object`);
  return value as Record<string, unknown>;
}

function textValue(value: unknown, field: string, maximum = 256): string {
  if (typeof value !== "string" || value.length === 0 || value.length > maximum || /[\u0000-\u001f]/.test(value)) {
    throw new Error(`${field} должен быть непустой bounded string`);
  }
  return value;
}

function identifier(value: unknown, field: string): string {
  const parsed = textValue(value, field, 128);
  if (!/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/.test(parsed)) throw new Error(`${field} имеет небезопасный формат`);
  return parsed;
}

function integer(value: unknown, field: string, minimum: number, maximum: number): number {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error(`${field} должен быть ${minimum}..${maximum}`);
  }
  return parsed;
}

function finite(value: unknown, field: string, minimum: number, maximum: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error(`${field} должен быть ${minimum}..${maximum}`);
  }
  return parsed;
}

function booleanValue(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") throw new Error(`${field} должен быть boolean`);
  return value;
}

function timestamp(value: unknown, field: string): string {
  const parsed = textValue(value, field, 64);
  if (!Number.isFinite(Date.parse(parsed))) throw new Error(`${field} должен быть ISO timestamp`);
  return new Date(parsed).toISOString();
}

function sha256Value(value: unknown, field: string): string {
  const parsed = textValue(value, field, 64);
  if (!/^[a-f0-9]{64}$/.test(parsed)) throw new Error(`${field} должен быть SHA-256 hex`);
  return parsed;
}

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .filter(([, entry]) => entry !== undefined)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, entry]) => [key, canonicalize(entry)]),
    );
  }
  return value;
}

export function canonicalJson(value: unknown): string {
  return JSON.stringify(canonicalize(value));
}

export function sha256(value: unknown): string {
  return createHash("sha256").update(typeof value === "string" ? value : canonicalJson(value)).digest("hex");
}

export function sealPostgresEvidence<T>(kind: string, payload: T, key: Buffer): SignedPostgresEvidence<T> {
  if (key.length < 32) throw new Error("DR evidence HMAC key должен содержать минимум 32 bytes");
  const normalizedKind = identifier(kind, "Evidence kind");
  const payloadSha256 = sha256(payload);
  const hmacSha256 = createHmac("sha256", key)
    .update(`${normalizedKind}\n${payloadSha256}\n${canonicalJson(payload)}`)
    .digest("hex");
  return { schemaVersion: 1, kind: normalizedKind, payload, payloadSha256, hmacSha256 };
}

export function verifyPostgresEvidence<T>(
  value: unknown,
  expectedKind: string,
  key: Buffer,
): SignedPostgresEvidence<T> {
  const row = object(value, "Signed evidence");
  if (row.schemaVersion !== 1 || row.kind !== expectedKind) throw new Error(`Ожидался signed evidence ${expectedKind}`);
  const payloadSha256 = sha256Value(row.payloadSha256, "payloadSha256");
  const hmacSha256 = sha256Value(row.hmacSha256, "hmacSha256");
  if (sha256(row.payload) !== payloadSha256) throw new Error("Signed evidence payload digest не совпадает");
  const expected = sealPostgresEvidence(expectedKind, row.payload, key).hmacSha256;
  if (!timingSafeEqual(Buffer.from(hmacSha256, "hex"), Buffer.from(expected, "hex"))) {
    throw new Error("Signed evidence HMAC не совпадает");
  }
  return row as unknown as SignedPostgresEvidence<T>;
}

export function parsePostgresResiliencePolicy(value: unknown): PostgresResiliencePolicy {
  const row = object(value, "PostgreSQL resilience policy");
  const topology = object(row.topology, "policy.topology");
  const backup = object(row.backup, "policy.backup");
  const objectives = object(row.objectives, "policy.objectives");
  const evidence = object(row.evidence, "policy.evidence");
  if (row.schemaVersion !== 1) throw new Error("PostgreSQL resilience policy schemaVersion должен быть 1");
  if (!Array.isArray(evidence.requiredApprovalRoles) || evidence.requiredApprovalRoles.length === 0) {
    throw new Error("policy.evidence.requiredApprovalRoles должен быть непустым array");
  }
  const requiredApprovalRoles = evidence.requiredApprovalRoles.map((entry, index) => (
    identifier(entry, `requiredApprovalRoles[${index}]`)
  ));
  if (new Set(requiredApprovalRoles).size !== requiredApprovalRoles.length) {
    throw new Error("requiredApprovalRoles не должен содержать duplicates");
  }
  return {
    schemaVersion: 1,
    policyId: identifier(row.policyId, "policyId"),
    engineMajor: integer(row.engineMajor, "engineMajor", 17, 99),
    topology: {
      minimumZones: integer(topology.minimumZones, "minimumZones", 2, 16),
      minimumSynchronousStandbys: integer(topology.minimumSynchronousStandbys, "minimumSynchronousStandbys", 1, 15),
      requireAutomaticFailover: booleanValue(topology.requireAutomaticFailover, "requireAutomaticFailover"),
    },
    backup: {
      requirePitr: booleanValue(backup.requirePitr, "requirePitr"),
      minimumRetentionHours: integer(backup.minimumRetentionHours, "minimumRetentionHours", 24, 24 * 365),
      maximumRestoreLagSeconds: integer(backup.maximumRestoreLagSeconds, "maximumRestoreLagSeconds", 1, 86_400),
      maximumBackupAgeSeconds: integer(backup.maximumBackupAgeSeconds, "maximumBackupAgeSeconds", 60, 604_800),
    },
    objectives: {
      rpoSeconds: integer(objectives.rpoSeconds, "rpoSeconds", 0, 86_400),
      failoverRtoSeconds: integer(objectives.failoverRtoSeconds, "failoverRtoSeconds", 1, 86_400),
      restoreRtoSeconds: integer(objectives.restoreRtoSeconds, "restoreRtoSeconds", 1, 604_800),
      monthlyAvailabilityPercent: finite(objectives.monthlyAvailabilityPercent, "monthlyAvailabilityPercent", 90, 100),
      minimumSloWindowHours: integer(objectives.minimumSloWindowHours, "minimumSloWindowHours", 24, 24 * 366),
    },
    evidence: {
      maximumTopologyAgeSeconds: integer(evidence.maximumTopologyAgeSeconds, "maximumTopologyAgeSeconds", 30, 86_400),
      maximumDrillAgeDays: integer(evidence.maximumDrillAgeDays, "maximumDrillAgeDays", 1, 365),
      requiredDistinctApprovers: integer(evidence.requiredDistinctApprovers, "requiredDistinctApprovers", 2, 16),
      requiredApprovalRoles,
    },
  };
}

function parseOperation(value: unknown): ManagedPostgresOperationEvidence {
  const row = object(value, "provider.operation");
  if (row.type !== "failover" && row.type !== "restore") throw new Error("operation.type должен быть failover или restore");
  return {
    type: row.type,
    operationId: identifier(row.operationId, "operationId"),
    startedAt: timestamp(row.startedAt, "operation.startedAt"),
    completedAt: timestamp(row.completedAt, "operation.completedAt"),
    sourceClusterId: identifier(row.sourceClusterId, "sourceClusterId"),
    sourcePrimaryInstanceId: identifier(row.sourcePrimaryInstanceId, "sourcePrimaryInstanceId"),
    targetClusterId: identifier(row.targetClusterId, "targetClusterId"),
    targetPrimaryInstanceId: identifier(row.targetPrimaryInstanceId, "targetPrimaryInstanceId"),
    ...(row.recoveryTargetAt === undefined ? {} : { recoveryTargetAt: timestamp(row.recoveryTargetAt, "recoveryTargetAt") }),
  };
}

export function parseManagedPostgresSnapshot(value: unknown): ManagedPostgresSnapshot {
  const row = object(value, "Managed PostgreSQL snapshot");
  const engine = object(row.engine, "provider.engine");
  const ha = object(row.ha, "provider.ha");
  const backup = object(row.backup, "provider.backup");
  const security = object(row.security, "provider.security");
  const approval = object(row.sloApproval, "provider.sloApproval");
  if (row.schemaVersion !== 1) throw new Error("Managed PostgreSQL snapshot schemaVersion должен быть 1");
  if (!Array.isArray(row.zones) || row.zones.length === 0 || row.zones.length > 16) {
    throw new Error("provider.zones должен быть bounded array");
  }
  if (!Array.isArray(approval.approvers) || approval.approvers.length === 0 || approval.approvers.length > 16) {
    throw new Error("sloApproval.approvers должен быть bounded array");
  }
  const approvers = approval.approvers.map((entry, index) => {
    const approver = object(entry, `approvers[${index}]`);
    return {
      subject: identifier(approver.subject, `approvers[${index}].subject`),
      role: identifier(approver.role, `approvers[${index}].role`),
    };
  });
  if (engine.name !== "postgresql") throw new Error("provider.engine.name должен быть postgresql");
  if (security.tlsMode !== "verify-full") throw new Error("provider.security.tlsMode должен быть verify-full");
  return {
    schemaVersion: 1,
    observedAt: timestamp(row.observedAt, "provider.observedAt"),
    provider: identifier(row.provider, "provider"),
    service: identifier(row.service, "service"),
    clusterId: identifier(row.clusterId, "clusterId"),
    primaryInstanceId: identifier(row.primaryInstanceId, "primaryInstanceId"),
    primaryZone: identifier(row.primaryZone, "primaryZone"),
    zones: row.zones.map((entry, index) => identifier(entry, `zones[${index}]`)),
    region: identifier(row.region, "region"),
    residencyDomain: identifier(row.residencyDomain, "residencyDomain"),
    engine: { name: "postgresql", major: integer(engine.major, "engine.major", 17, 99) },
    ha: {
      automaticFailover: booleanValue(ha.automaticFailover, "automaticFailover"),
      synchronousStandby: booleanValue(ha.synchronousStandby, "synchronousStandby"),
      healthyStandbys: integer(ha.healthyStandbys, "healthyStandbys", 0, 15),
    },
    backup: {
      pitrEnabled: booleanValue(backup.pitrEnabled, "pitrEnabled"),
      retentionHours: integer(backup.retentionHours, "retentionHours", 0, 24 * 365),
      oldestRestorableAt: timestamp(backup.oldestRestorableAt, "oldestRestorableAt"),
      latestRestorableAt: timestamp(backup.latestRestorableAt, "latestRestorableAt"),
      lastSuccessfulBackupAt: timestamp(backup.lastSuccessfulBackupAt, "lastSuccessfulBackupAt"),
    },
    security: {
      tlsMode: "verify-full",
      encryptionAtRest: booleanValue(security.encryptionAtRest, "encryptionAtRest"),
      publicEndpoint: booleanValue(security.publicEndpoint, "publicEndpoint"),
    },
    sloApproval: {
      policySha256: sha256Value(approval.policySha256, "sloApproval.policySha256"),
      approvedAt: timestamp(approval.approvedAt, "sloApproval.approvedAt"),
      changeId: identifier(approval.changeId, "sloApproval.changeId"),
      approvers,
    },
    ...(row.operation === undefined ? {} : { operation: parseOperation(row.operation) }),
  };
}

export function evaluateManagedPostgresSnapshot(
  policy: PostgresResiliencePolicy,
  snapshot: ManagedPostgresSnapshot,
  expected: { region: string; residencyDomain: string; nowMs?: number },
): { pass: boolean; reasons: string[]; policySha256: string } {
  const nowMs = expected.nowMs ?? Date.now();
  const reasons: string[] = [];
  const policySha256 = sha256(policy);
  const zones = new Set(snapshot.zones);
  const approverSubjects = new Set(snapshot.sloApproval.approvers.map((entry) => entry.subject));
  const approverRoles = new Set(snapshot.sloApproval.approvers.map((entry) => entry.role));
  const ageSeconds = (nowMs - Date.parse(snapshot.observedAt)) / 1_000;
  const latestRestoreLagSeconds = (nowMs - Date.parse(snapshot.backup.latestRestorableAt)) / 1_000;
  const backupAgeSeconds = (nowMs - Date.parse(snapshot.backup.lastSuccessfulBackupAt)) / 1_000;
  const retainedHours = (nowMs - Date.parse(snapshot.backup.oldestRestorableAt)) / 3_600_000;
  if (ageSeconds < -30 || ageSeconds > policy.evidence.maximumTopologyAgeSeconds) reasons.push("provider topology evidence is stale or from the future");
  if (snapshot.region !== expected.region || snapshot.residencyDomain !== expected.residencyDomain) reasons.push("provider region/residency does not match the HA-cell");
  if (snapshot.engine.major !== policy.engineMajor) reasons.push("PostgreSQL engine major does not match policy");
  if (zones.size < policy.topology.minimumZones || !zones.has(snapshot.primaryZone)) reasons.push("multi-AZ topology is incomplete");
  if (policy.topology.requireAutomaticFailover && !snapshot.ha.automaticFailover) reasons.push("automatic failover is disabled");
  if (!snapshot.ha.synchronousStandby || snapshot.ha.healthyStandbys < policy.topology.minimumSynchronousStandbys) reasons.push("healthy synchronous standby requirement is not met");
  if (policy.backup.requirePitr && !snapshot.backup.pitrEnabled) reasons.push("PITR is disabled");
  if (snapshot.backup.retentionHours < policy.backup.minimumRetentionHours || retainedHours + 1 < policy.backup.minimumRetentionHours) reasons.push("PITR retention window is below policy");
  if (Date.parse(snapshot.backup.oldestRestorableAt) > Date.parse(snapshot.backup.latestRestorableAt)) reasons.push("PITR restorable window is inverted");
  if (latestRestoreLagSeconds < -30 || latestRestoreLagSeconds > policy.backup.maximumRestoreLagSeconds) reasons.push("latest restorable point is outside the RPO envelope");
  if (backupAgeSeconds < -30 || backupAgeSeconds > policy.backup.maximumBackupAgeSeconds) reasons.push("last successful backup is stale");
  if (!snapshot.security.encryptionAtRest || snapshot.security.publicEndpoint) reasons.push("managed database security boundary is not private and encrypted");
  if (snapshot.sloApproval.policySha256 !== policySha256) reasons.push("SLO approval references another policy digest");
  if (approverSubjects.size < policy.evidence.requiredDistinctApprovers) reasons.push("SLO approval lacks distinct approvers");
  for (const requiredRole of policy.evidence.requiredApprovalRoles) {
    if (!approverRoles.has(requiredRole)) reasons.push(`SLO approval role ${requiredRole} is missing`);
  }
  if (Date.parse(snapshot.sloApproval.approvedAt) > nowMs + 30_000) reasons.push("SLO approval is from the future");
  return { pass: reasons.length === 0, reasons, policySha256 };
}

function sslConfigFromEnv(connectionUrl: string): ClientConfig["ssl"] {
  const mode = process.env.AGAT_POSTGRES_SSL_MODE ?? "verify-full";
  if (mode !== "disable" && mode !== "require" && mode !== "verify-full") {
    throw new Error("AGAT_POSTGRES_SSL_MODE: disable, require или verify-full");
  }
  let hostname = "";
  try {
    hostname = new URL(connectionUrl).hostname.toLowerCase();
  } catch {
    throw new Error("AGAT_POSTGRES_URL некорректен");
  }
  const localOverride = process.env.AGAT_DR_ALLOW_INSECURE_LOCAL === "true"
    && ["127.0.0.1", "::1", "localhost"].includes(hostname);
  if (mode !== "verify-full" && !localOverride) {
    throw new Error("Production resilience gate требует AGAT_POSTGRES_SSL_MODE=verify-full");
  }
  if (mode === "disable") return false;
  const read = (pathname: string | undefined): string | undefined => (
    pathname ? fs.readFileSync(path.resolve(pathname), "utf8") : undefined
  );
  const ca = read(process.env.AGAT_POSTGRES_CA_CERT_PATH);
  const cert = read(process.env.AGAT_POSTGRES_CLIENT_CERT_PATH);
  const key = read(process.env.AGAT_POSTGRES_CLIENT_KEY_PATH);
  if (Boolean(cert) !== Boolean(key)) throw new Error("PostgreSQL client certificate и key задаются вместе");
  return {
    rejectUnauthorized: mode === "verify-full",
    ...(ca ? { ca } : {}),
    ...(cert && key ? { cert, key } : {}),
  };
}

async function connectRuntime(): Promise<Client> {
  const url = process.env.AGAT_POSTGRES_URL ?? "";
  if (!url) throw new Error("AGAT_POSTGRES_URL обязателен");
  const connection = new pg.Client({
    connectionString: url,
    application_name: "agat-postgres-resilience",
    connectionTimeoutMillis: 10_000,
    statement_timeout: 30_000,
    query_timeout: 30_000,
    ssl: sslConfigFromEnv(url),
  });
  await connection.connect();
  return connection;
}

async function observeDatabase(connection: Client): Promise<DatabaseObservation> {
  const result = await connection.query<Record<string, unknown>>(`
    SELECT
      CURRENT_TIMESTAMP::text AS observed_at,
      current_database() AS database_name,
      current_user AS role_name,
      current_setting('server_version_num')::integer AS server_version_num,
      COALESCE(inet_server_addr()::text, 'local-socket') AS server_address,
      pg_postmaster_start_time()::text AS postmaster_started_at,
      pg_is_in_recovery() AS in_recovery,
      current_setting('transaction_read_only') = 'on' AS transaction_read_only,
      COALESCE((SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()), false) AS tls,
      current_setting('data_checksums') = 'on' AS data_checksums,
      current_setting('wal_level') AS wal_level,
      current_setting('synchronous_commit') AS synchronous_commit,
      NOT has_schema_privilege(current_user, current_schema(), 'CREATE')
        AND NOT has_database_privilege(current_user, current_database(), 'CREATE')
        AND NOT has_database_privilege(current_user, current_database(), 'TEMPORARY') AS runtime_ddl_free,
      to_regclass(format('%I.%I', current_schema(), 'agat_dr_canaries')) IS NOT NULL AS canary_table_present,
      COALESCE((SELECT version FROM agat_schema_migrations WHERE version = $1), 0) AS schema_version,
      COALESCE((SELECT contract_id FROM agat_schema_migrations WHERE version = $1), '') AS schema_contract,
      COALESCE((SELECT admission_status FROM agat_schema_migrations WHERE version = $1), '') AS admission_status
  `, [POSTGRES_SCHEMA_VERSION]);
  const row = result.rows[0];
  if (!row) throw new Error("PostgreSQL observation отсутствует");
  return {
    observedAt: timestamp(row.observed_at, "database.observedAt"),
    databaseName: textValue(row.database_name, "databaseName", 63),
    roleName: textValue(row.role_name, "roleName", 63),
    serverVersionNum: Number(row.server_version_num),
    serverAddress: textValue(row.server_address, "serverAddress", 128),
    postmasterStartedAt: timestamp(row.postmaster_started_at, "postmasterStartedAt"),
    inRecovery: row.in_recovery === true,
    transactionReadOnly: row.transaction_read_only === true,
    tls: row.tls === true,
    dataChecksums: row.data_checksums === true,
    walLevel: String(row.wal_level ?? ""),
    synchronousCommit: String(row.synchronous_commit ?? ""),
    runtimeDdlFree: row.runtime_ddl_free === true,
    canaryTablePresent: row.canary_table_present === true,
    schemaVersion: Number(row.schema_version),
    schemaContract: String(row.schema_contract ?? ""),
    admissionStatus: String(row.admission_status ?? ""),
  };
}

function databaseChecks(policy: PostgresResiliencePolicy, observation: DatabaseObservation): Record<string, boolean> {
  const allowInsecureLocal = process.env.AGAT_DR_ALLOW_INSECURE_LOCAL === "true";
  return {
    primaryReadWrite: !observation.inRecovery && !observation.transactionReadOnly,
    transportAccepted: observation.tls || allowInsecureLocal,
    dataChecksums: observation.dataChecksums,
    engineMajor: Math.trunc(observation.serverVersionNum / 10_000) === policy.engineMajor,
    walDurable: observation.walLevel === "replica" || observation.walLevel === "logical",
    synchronousCommit: ["on", "remote_write", "remote_apply"].includes(observation.synchronousCommit),
    ddlFreeRuntime: observation.runtimeDdlFree,
    drCanaryTable: observation.canaryTablePresent,
    schemaContract: observation.schemaVersion === POSTGRES_SCHEMA_VERSION
      && observation.schemaContract === POSTGRES_SCHEMA_CONTRACT
      && observation.admissionStatus === "passed",
  };
}

async function insertCheckpoint(
  connection: Client,
  snapshot: ManagedPostgresSnapshot,
  checkpointKind: PostgresDrCheckpointKind,
  region: string,
  residencyDomain: string,
): Promise<PostgresDrCheckpoint> {
  const observation = await observeDatabase(connection);
  const checkpointId = randomUUID();
  const issuedAt = new Date().toISOString();
  const payloadSha256 = createHash("sha256").update(randomBytes(32)).digest("hex");
  await connection.query(`
    INSERT INTO agat_dr_canaries(
      id, kind, region, residency_domain, issued_at, payload_sha256, created_by
    ) VALUES ($1, $2, $3, $4, $5, $6, current_user)
  `, [checkpointId, checkpointKind, region, residencyDomain, issuedAt, payloadSha256]);
  return {
    checkpointId,
    checkpointKind,
    issuedAt,
    region,
    residencyDomain,
    payloadSha256,
    providerClusterId: snapshot.clusterId,
    providerPrimaryInstanceId: snapshot.primaryInstanceId,
    databaseServerAddress: observation.serverAddress,
    postmasterStartedAt: observation.postmasterStartedAt,
    schemaVersion: observation.schemaVersion,
    schemaContract: observation.schemaContract,
  };
}

async function checkpointPresence(connection: Client, checkpoint: PostgresDrCheckpoint): Promise<boolean> {
  const result = await connection.query<{ count: string }>(`
    SELECT COUNT(*)::text AS count FROM agat_dr_canaries
    WHERE id = $1 AND kind = $2 AND issued_at = $3 AND payload_sha256 = $4
  `, [checkpoint.checkpointId, checkpoint.checkpointKind, checkpoint.issuedAt, checkpoint.payloadSha256]);
  return Number(result.rows[0]?.count ?? 0) === 1;
}

function checkpointPayload(value: unknown): PostgresDrCheckpoint {
  const row = object(value, "DR checkpoint");
  const checkpointKind = row.checkpointKind;
  if (!["heartbeat", "failover-before", "failover-after", "restore-before", "restore-after", "restore-verified"].includes(String(checkpointKind))) {
    throw new Error("Некорректный checkpointKind");
  }
  return {
    checkpointId: identifier(row.checkpointId, "checkpointId"),
    checkpointKind: checkpointKind as PostgresDrCheckpointKind,
    issuedAt: timestamp(row.issuedAt, "checkpoint.issuedAt"),
    region: identifier(row.region, "checkpoint.region"),
    residencyDomain: identifier(row.residencyDomain, "checkpoint.residencyDomain"),
    payloadSha256: sha256Value(row.payloadSha256, "checkpoint.payloadSha256"),
    providerClusterId: identifier(row.providerClusterId, "providerClusterId"),
    providerPrimaryInstanceId: identifier(row.providerPrimaryInstanceId, "providerPrimaryInstanceId"),
    databaseServerAddress: textValue(row.databaseServerAddress, "databaseServerAddress", 128),
    postmasterStartedAt: timestamp(row.postmasterStartedAt, "checkpoint.postmasterStartedAt"),
    schemaVersion: integer(row.schemaVersion, "checkpoint.schemaVersion", 1, 1_000_000),
    schemaContract: identifier(row.schemaContract, "checkpoint.schemaContract"),
  };
}

export function parsePostgresSloMetrics(value: unknown): PostgresSloMetrics {
  const row = object(value, "PostgreSQL SLO metrics");
  if (row.schemaVersion !== 1) throw new Error("SLO metrics schemaVersion должен быть 1");
  const requestsTotal = integer(row.requestsTotal, "requestsTotal", 1, Number.MAX_SAFE_INTEGER);
  const requestsFailed = integer(row.requestsFailed, "requestsFailed", 0, requestsTotal);
  return {
    schemaVersion: 1,
    windowStart: timestamp(row.windowStart, "windowStart"),
    windowEnd: timestamp(row.windowEnd, "windowEnd"),
    eligibleSeconds: integer(row.eligibleSeconds, "eligibleSeconds", 1, Number.MAX_SAFE_INTEGER),
    unavailableSeconds: integer(row.unavailableSeconds, "unavailableSeconds", 0, Number.MAX_SAFE_INTEGER),
    requestsTotal,
    requestsFailed,
    failoverReportSha256: sha256Value(row.failoverReportSha256, "failoverReportSha256"),
    restoreReportSha256: sha256Value(row.restoreReportSha256, "restoreReportSha256"),
  };
}

export function evaluatePostgresSlo(
  policy: PostgresResiliencePolicy,
  metrics: PostgresSloMetrics,
  failover: SignedPostgresEvidence<PostgresDrVerificationReport>,
  restore: SignedPostgresEvidence<PostgresDrVerificationReport>,
  nowMs = Date.now(),
): {
  availabilityPercent: number;
  requestSuccessPercent: number;
  checks: Record<string, boolean>;
  success: boolean;
} {
  const windowSeconds = (Date.parse(metrics.windowEnd) - Date.parse(metrics.windowStart)) / 1_000;
  const availabilityPercent = Number((100 * (1 - metrics.unavailableSeconds / metrics.eligibleSeconds)).toFixed(6));
  const requestSuccessPercent = Number((100 * (1 - metrics.requestsFailed / metrics.requestsTotal)).toFixed(6));
  const maxDrillAgeMs = policy.evidence.maximumDrillAgeDays * 86_400_000;
  const checks = {
    windowOrder: windowSeconds > 0,
    minimumWindow: windowSeconds >= policy.objectives.minimumSloWindowHours * 3_600,
    eligibleWithinWindow: metrics.eligibleSeconds <= windowSeconds && metrics.unavailableSeconds <= metrics.eligibleSeconds,
    availability: availabilityPercent >= policy.objectives.monthlyAvailabilityPercent,
    failoverReference: metrics.failoverReportSha256 === failover.payloadSha256,
    restoreReference: metrics.restoreReportSha256 === restore.payloadSha256,
    failoverFresh: nowMs - Date.parse(failover.payload.completedAt) <= maxDrillAgeMs,
    restoreFresh: nowMs - Date.parse(restore.payload.completedAt) <= maxDrillAgeMs,
    failoverNotFuture: Date.parse(failover.payload.completedAt) <= nowMs + 30_000,
    restoreNotFuture: Date.parse(restore.payload.completedAt) <= nowMs + 30_000,
    failoverPassed: failover.payload.reportType === "failover" && failover.payload.success,
    restorePassed: restore.payload.reportType === "restore" && restore.payload.success,
  };
  return { availabilityPercent, requestSuccessPercent, checks, success: Object.values(checks).every(Boolean) };
}

function readJson(pathname: string): unknown {
  const resolved = path.resolve(pathname);
  const stat = fs.statSync(resolved);
  if (!stat.isFile() || stat.size > 1_048_576) throw new Error("Evidence JSON должен быть regular file не больше 1 MiB");
  return JSON.parse(fs.readFileSync(resolved, "utf8"));
}

function evidenceKey(): Buffer {
  const pathname = process.env.AGAT_DR_EVIDENCE_KEY_PATH ?? "";
  if (!pathname) throw new Error("AGAT_DR_EVIDENCE_KEY_PATH обязателен");
  const resolved = path.resolve(pathname);
  const realParent = fs.realpathSync(path.dirname(resolved));
  const realPath = fs.realpathSync(resolved);
  const stat = fs.statSync(resolved);
  if ((!realPath.startsWith(`${realParent}${path.sep}`) && realPath !== realParent)
    || !stat.isFile()
    || (stat.mode & 0o007) !== 0) {
    throw new Error("DR evidence key должен быть regular file без world permissions");
  }
  const key = fs.readFileSync(resolved);
  if (key.length < 32) throw new Error("DR evidence key должен содержать минимум 32 bytes");
  return key;
}

async function writeJson(pathname: string | undefined, value: unknown): Promise<void> {
  if (!pathname) {
    process.stdout.write(`${JSON.stringify(value)}\n`);
    return;
  }
  const resolved = path.resolve(pathname);
  await fs.promises.mkdir(path.dirname(resolved), { recursive: true });
  const temporary = `${resolved}.${process.pid}.tmp`;
  await fs.promises.writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600, flag: "wx" });
  try {
    await fs.promises.link(temporary, resolved);
  } finally {
    await fs.promises.unlink(temporary).catch(() => undefined);
  }
}

function argument(name: string): string | undefined {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

function requiredArgument(name: string): string {
  const value = argument(name);
  if (!value) throw new Error(`${name} обязателен`);
  return value;
}

function runtimeCell(): { region: string; residencyDomain: string } {
  const region = identifier(process.env.AGAT_REGION ?? "", "AGAT_REGION");
  const residencyDomain = identifier(process.env.AGAT_RESIDENCY_DOMAIN ?? "", "AGAT_RESIDENCY_DOMAIN");
  return { region, residencyDomain };
}

function loadPolicyAndSnapshot(): {
  policy: PostgresResiliencePolicy;
  snapshot: ManagedPostgresSnapshot;
  providerEvidenceSha256: string;
  evaluation: ReturnType<typeof evaluateManagedPostgresSnapshot>;
} {
  const policy = parsePostgresResiliencePolicy(readJson(requiredArgument("--policy")));
  const snapshot = parseManagedPostgresSnapshot(readJson(requiredArgument("--provider-evidence")));
  const cell = runtimeCell();
  return {
    policy,
    snapshot,
    providerEvidenceSha256: sha256(snapshot),
    evaluation: evaluateManagedPostgresSnapshot(policy, snapshot, cell),
  };
}

async function preflight(): Promise<boolean> {
  const key = evidenceKey();
  const { policy, snapshot, providerEvidenceSha256, evaluation } = loadPolicyAndSnapshot();
  const connection = await connectRuntime();
  try {
    const databaseObservation = await observeDatabase(connection);
    const checks = databaseChecks(policy, databaseObservation);
    const payload = {
      reportType: "preflight",
      completedAt: new Date().toISOString(),
      policyId: policy.policyId,
      policySha256: evaluation.policySha256,
      providerEvidenceSha256,
      providerReasons: evaluation.reasons,
      databaseObservation,
      checks,
      success: evaluation.pass && Object.values(checks).every(Boolean),
    };
    await writeJson(argument("--report"), sealPostgresEvidence("postgres-resilience-preflight", payload, key));
    return payload.success;
  } finally {
    await connection.end().catch(() => undefined);
  }
}

async function checkpoint(): Promise<void> {
  const key = evidenceKey();
  const kind = requiredArgument("--kind") as PostgresDrCheckpointKind;
  if (!["heartbeat", "failover-before", "failover-after", "restore-before", "restore-after", "restore-verified"].includes(kind)) {
    throw new Error("--kind имеет некорректное значение");
  }
  const { policy, snapshot, evaluation } = loadPolicyAndSnapshot();
  if (!evaluation.pass) throw new Error(`Managed PostgreSQL preflight failed: ${evaluation.reasons.join("; ")}`);
  const connection = await connectRuntime();
  try {
    const observation = await observeDatabase(connection);
    const checks = databaseChecks(policy, observation);
    if (!Object.values(checks).every(Boolean)) throw new Error("Database resilience checks не пройдены");
    const cell = runtimeCell();
    const payload = await insertCheckpoint(connection, snapshot, kind, cell.region, cell.residencyDomain);
    await writeJson(argument("--report"), sealPostgresEvidence("postgres-dr-checkpoint", payload, key));
  } finally {
    await connection.end().catch(() => undefined);
  }
}

function loadCheckpoint(pathname: string, key: Buffer): SignedPostgresEvidence<PostgresDrCheckpoint> {
  const envelope = verifyPostgresEvidence<unknown>(readJson(pathname), "postgres-dr-checkpoint", key);
  return { ...envelope, payload: checkpointPayload(envelope.payload) };
}

async function verifyFailover(): Promise<boolean> {
  const key = evidenceKey();
  const { policy, snapshot, providerEvidenceSha256, evaluation } = loadPolicyAndSnapshot();
  const operation = snapshot.operation;
  if (!operation || operation.type !== "failover") throw new Error("Provider evidence должна содержать failover operation");
  const before = loadCheckpoint(requiredArgument("--must-exist"), key);
  const connection = await connectRuntime();
  try {
    const observation = await observeDatabase(connection);
    const exists = await checkpointPresence(connection, before.payload);
    const cell = runtimeCell();
    const checks = {
      ...databaseChecks(policy, observation),
      providerSnapshot: evaluation.pass,
      beforeCanaryPresent: exists,
      checkpointKind: before.payload.checkpointKind === "failover-before",
      checkpointCell: before.payload.region === snapshot.region
        && before.payload.residencyDomain === snapshot.residencyDomain,
      sameCluster: operation.sourceClusterId === operation.targetClusterId
        && operation.sourceClusterId === before.payload.providerClusterId
        && operation.targetClusterId === snapshot.clusterId,
      primaryChanged: operation.sourcePrimaryInstanceId === before.payload.providerPrimaryInstanceId
        && operation.targetPrimaryInstanceId === snapshot.primaryInstanceId
        && operation.sourcePrimaryInstanceId !== operation.targetPrimaryInstanceId,
      operationOrder: Date.parse(operation.completedAt) >= Date.parse(operation.startedAt),
      checkpointBeforeIncident: Date.parse(operation.startedAt) >= Date.parse(before.payload.issuedAt),
      providerObservedAfterOperation: Date.parse(snapshot.observedAt) >= Date.parse(operation.completedAt)
        && Date.parse(operation.completedAt) <= Date.now() + 30_000,
    };
    const rtoSeconds = Math.max(0, (Date.parse(operation.completedAt) - Date.parse(operation.startedAt)) / 1_000);
    const rpoSeconds = exists
      ? Math.max(0, (Date.parse(operation.startedAt) - Date.parse(before.payload.issuedAt)) / 1_000)
      : policy.objectives.rpoSeconds + 1;
    const verificationCanary = Object.values(checks).every(Boolean)
      ? await insertCheckpoint(connection, snapshot, "failover-after", cell.region, cell.residencyDomain)
      : null;
    const success = Object.values(checks).every(Boolean)
      && rpoSeconds <= policy.objectives.rpoSeconds
      && rtoSeconds <= policy.objectives.failoverRtoSeconds
      && verificationCanary !== null;
    const payload: PostgresDrVerificationReport & { verificationCheckpoint: PostgresDrCheckpoint | null } = {
      reportType: "failover",
      operationId: operation.operationId,
      completedAt: new Date().toISOString(),
      providerEvidenceSha256,
      checkpointEvidenceSha256: [before.payloadSha256],
      rpoSeconds,
      rtoSeconds,
      rpoObjectiveSeconds: policy.objectives.rpoSeconds,
      rtoObjectiveSeconds: policy.objectives.failoverRtoSeconds,
      sourceClusterId: operation.sourceClusterId,
      targetClusterId: operation.targetClusterId,
      sourcePrimaryInstanceId: operation.sourcePrimaryInstanceId,
      targetPrimaryInstanceId: operation.targetPrimaryInstanceId,
      databaseObservation: observation,
      checks,
      success,
      verificationCheckpoint: verificationCanary,
    };
    await writeJson(argument("--report"), sealPostgresEvidence("postgres-dr-verification", payload, key));
    return success;
  } finally {
    await connection.end().catch(() => undefined);
  }
}

async function verifyRestore(): Promise<boolean> {
  const key = evidenceKey();
  const { policy, snapshot, providerEvidenceSha256, evaluation } = loadPolicyAndSnapshot();
  const operation = snapshot.operation;
  if (!operation || operation.type !== "restore" || !operation.recoveryTargetAt) {
    throw new Error("Provider evidence должна содержать restore operation и recoveryTargetAt");
  }
  const before = loadCheckpoint(requiredArgument("--must-exist"), key);
  const after = loadCheckpoint(requiredArgument("--must-not-exist"), key);
  const connection = await connectRuntime();
  try {
    const observation = await observeDatabase(connection);
    const [beforeExists, afterExists] = await Promise.all([
      checkpointPresence(connection, before.payload),
      checkpointPresence(connection, after.payload),
    ]);
    const targetMs = Date.parse(operation.recoveryTargetAt);
    const cell = runtimeCell();
    const checks = {
      ...databaseChecks(policy, observation),
      providerSnapshot: evaluation.pass,
      beforeCanaryPresent: beforeExists,
      afterCanaryAbsent: !afterExists,
      checkpointKinds: before.payload.checkpointKind === "restore-before"
        && after.payload.checkpointKind === "restore-after",
      checkpointCell: before.payload.region === snapshot.region
        && after.payload.region === snapshot.region
        && before.payload.residencyDomain === snapshot.residencyDomain
        && after.payload.residencyDomain === snapshot.residencyDomain,
      isolatedClone: operation.sourceClusterId === before.payload.providerClusterId
        && operation.sourceClusterId === after.payload.providerClusterId
        && operation.sourcePrimaryInstanceId === before.payload.providerPrimaryInstanceId
        && operation.sourcePrimaryInstanceId === after.payload.providerPrimaryInstanceId
        && operation.targetClusterId === snapshot.clusterId
        && operation.sourceClusterId !== operation.targetClusterId,
      restorePrimaryMatches: operation.targetPrimaryInstanceId === snapshot.primaryInstanceId,
      exactPitrBoundary: targetMs >= Date.parse(before.payload.issuedAt)
        && targetMs < Date.parse(after.payload.issuedAt),
      operationOrder: Date.parse(operation.completedAt) >= Date.parse(operation.startedAt),
      checkpointsBeforeRestore: Date.parse(operation.startedAt) >= Date.parse(after.payload.issuedAt),
      providerObservedAfterOperation: Date.parse(snapshot.observedAt) >= Date.parse(operation.completedAt)
        && Date.parse(operation.completedAt) <= Date.now() + 30_000,
    };
    const rpoSeconds = Math.max(0, (targetMs - Date.parse(before.payload.issuedAt)) / 1_000);
    const rtoSeconds = Math.max(0, (Date.parse(operation.completedAt) - Date.parse(operation.startedAt)) / 1_000);
    const verificationCanary = Object.values(checks).every(Boolean)
      ? await insertCheckpoint(connection, snapshot, "restore-verified", cell.region, cell.residencyDomain)
      : null;
    const success = Object.values(checks).every(Boolean)
      && rpoSeconds <= policy.objectives.rpoSeconds
      && rtoSeconds <= policy.objectives.restoreRtoSeconds
      && verificationCanary !== null;
    const payload: PostgresDrVerificationReport & { verificationCheckpoint: PostgresDrCheckpoint | null } = {
      reportType: "restore",
      operationId: operation.operationId,
      completedAt: new Date().toISOString(),
      providerEvidenceSha256,
      checkpointEvidenceSha256: [before.payloadSha256, after.payloadSha256],
      rpoSeconds,
      rtoSeconds,
      rpoObjectiveSeconds: policy.objectives.rpoSeconds,
      rtoObjectiveSeconds: policy.objectives.restoreRtoSeconds,
      sourceClusterId: operation.sourceClusterId,
      targetClusterId: operation.targetClusterId,
      sourcePrimaryInstanceId: operation.sourcePrimaryInstanceId,
      targetPrimaryInstanceId: operation.targetPrimaryInstanceId,
      databaseObservation: observation,
      checks,
      success,
      verificationCheckpoint: verificationCanary,
    };
    await writeJson(argument("--report"), sealPostgresEvidence("postgres-dr-verification", payload, key));
    return success;
  } finally {
    await connection.end().catch(() => undefined);
  }
}

async function sealMetrics(): Promise<void> {
  const key = evidenceKey();
  const payload = parsePostgresSloMetrics(readJson(requiredArgument("--input")));
  await writeJson(argument("--report"), sealPostgresEvidence("postgres-slo-metrics", payload, key));
}

async function policyDigest(): Promise<void> {
  const policy = parsePostgresResiliencePolicy(readJson(requiredArgument("--policy")));
  await writeJson(argument("--report"), {
    schemaVersion: 1,
    policyId: policy.policyId,
    policySha256: sha256(policy),
  });
}

async function evaluateSlo(): Promise<boolean> {
  const key = evidenceKey();
  const policy = parsePostgresResiliencePolicy(readJson(requiredArgument("--policy")));
  const metricsEnvelope = verifyPostgresEvidence<unknown>(
    readJson(requiredArgument("--metrics")),
    "postgres-slo-metrics",
    key,
  );
  const metrics = parsePostgresSloMetrics(metricsEnvelope.payload);
  const failover = verifyPostgresEvidence<PostgresDrVerificationReport>(
    readJson(requiredArgument("--failover-report")),
    "postgres-dr-verification",
    key,
  );
  const restore = verifyPostgresEvidence<PostgresDrVerificationReport>(
    readJson(requiredArgument("--restore-report")),
    "postgres-dr-verification",
    key,
  );
  const evaluation = evaluatePostgresSlo(policy, metrics, failover, restore);
  const payload = {
    reportType: "slo-window",
    completedAt: new Date().toISOString(),
    policyId: policy.policyId,
    policySha256: sha256(policy),
    metricsEvidenceSha256: metricsEnvelope.payloadSha256,
    failoverReportSha256: failover.payloadSha256,
    restoreReportSha256: restore.payloadSha256,
    ...evaluation,
  };
  await writeJson(argument("--report"), sealPostgresEvidence("postgres-slo-evaluation", payload, key));
  return evaluation.success;
}

async function main(): Promise<void> {
  const command = process.argv[2] ?? "";
  let success = true;
  if (command === "preflight") success = await preflight();
  else if (command === "checkpoint") await checkpoint();
  else if (command === "verify-failover") success = await verifyFailover();
  else if (command === "verify-restore") success = await verifyRestore();
  else if (command === "policy-digest") await policyDigest();
  else if (command === "seal-metrics") await sealMetrics();
  else if (command === "evaluate-slo") success = await evaluateSlo();
  else throw new Error("Команда: policy-digest, preflight, checkpoint, verify-failover, verify-restore, seal-metrics или evaluate-slo");
  if (!success) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  void main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : "PostgreSQL resilience command failed"}\n`);
    process.exitCode = 1;
  });
}
