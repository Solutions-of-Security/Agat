import fs from "node:fs";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { pathToFileURL } from "node:url";

import pg, { type Client, type ClientConfig } from "pg";

import { POSTGRES_SCHEMA_CONTRACT, POSTGRES_SCHEMA_VERSION } from "./database.js";
import {
  canonicalJson,
  sealPostgresEvidence,
  sha256,
  verifyPostgresEvidence,
  type SignedPostgresEvidence,
} from "./postgres-resilience.js";

const REGION_LOSS_LOCK_ID = 867_530_906;

export interface RegionLossDrPolicy {
  schemaVersion: 1;
  policyId: string;
  transitions: Array<{
    residencyDomain: string;
    sourceRegion: string;
    targetRegion: string;
  }>;
  objectives: {
    rpoSeconds: number;
    rtoSeconds: number;
    maximumCrossSystemSkewSeconds: number;
  };
  artifacts: {
    requireVersioning: boolean;
    requireVersionIdPreservation: boolean;
    maximumReplicationLagSeconds: number;
  };
  evidence: {
    maximumAgeSeconds: number;
    requiredDistinctApprovers: number;
    requiredApprovalRoles: string[];
  };
}

export interface RegionLossDrEvidence {
  schemaVersion: 1;
  incidentId: string;
  incidentDeclaredAt: string;
  observedAt: string;
  source: { cellId: string; region: string; residencyDomain: string };
  target: { cellId: string; region: string; residencyDomain: string };
  fencing: {
    completedAt: string;
    databaseWritesDisabled: boolean;
    artifactWritesDisabled: boolean;
    inboundTrafficDisabled: boolean;
    workerEnrollmentDisabled: boolean;
    credentialsRotated: boolean;
    activeWriters: number;
  };
  postgres: {
    sourceClusterId: string;
    targetClusterId: string;
    recoveryTargetAt: string;
    restoreCompletedAt: string;
    targetReadWrite: boolean;
    schemaVersion: number;
    schemaContract: string;
    restoreReportSha256: string;
  };
  artifacts: {
    sourceBucket: string;
    targetBucket: string;
    observedAt: string;
    latestReplicatedAt: string;
    sourceVersioning: boolean;
    targetVersioning: boolean;
    versionIdsPreserved: boolean;
    replicationLagSeconds: number;
    pendingOperations: number;
    failedOperations: number;
    objectCount: number;
    totalBytes: number;
    databaseReferenceSha256: string;
  };
  temporal: {
    sourceNamespace: string;
    targetNamespace: string;
    recoveryTargetAt: string;
    recovered: boolean;
    recoveryReportSha256: string;
  };
  approvals: Array<{
    subject: string;
    role: string;
    approvedAt: string;
    changeId: string;
  }>;
}

export interface RegionLossDatabaseSnapshot {
  cell: {
    activeRegion: string;
    residencyDomain: string;
    writeEpoch: number;
    activationId: string | null;
  };
  projects: {
    count: number;
    sha256: string;
    rows: Array<{
      id: string;
      homeRegion: string;
      residencyDomain: string;
      queueName: string;
      fleetRevision: number;
    }>;
  };
  artifacts: {
    count: number;
    totalBytes: number;
    sha256: string;
    buckets: string[];
  };
  activeRuns: number;
}

export interface RegionLossActivationPlan {
  schemaVersion: 1;
  planId: string;
  activationId: string;
  incidentId: string;
  createdAt: string;
  policyId: string;
  policySha256: string;
  evidenceSha256: string;
  source: RegionLossDrEvidence["source"];
  target: RegionLossDrEvidence["target"];
  sourceClusterId: string;
  targetClusterId: string;
  sourceBucket: string;
  targetBucket: string;
  recoveryTargetAt: string;
  sourceFencedAt: string;
  sourceWriteEpoch: number;
  sourceActivationId: string | null;
  targetWriteEpoch: number;
  projectCount: number;
  projectSnapshotSha256: string;
  artifactCount: number;
  artifactBytes: number;
  artifactSnapshotSha256: string;
  approvals: RegionLossDrEvidence["approvals"];
}

function record(value: unknown, field: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${field} должен быть object`);
  return value as Record<string, unknown>;
}

function boundedText(value: unknown, field: string, maximum = 256): string {
  if (typeof value !== "string" || value.length === 0 || value.length > maximum || /[\u0000-\u001f]/u.test(value)) {
    throw new Error(`${field} должен быть непустым bounded string`);
  }
  return value;
}

function identifier(value: unknown, field: string): string {
  const parsed = boundedText(value, field, 128);
  if (!/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/.test(parsed)) throw new Error(`${field} имеет небезопасный формат`);
  return parsed;
}

function timestamp(value: unknown, field: string): string {
  const parsed = boundedText(value, field, 64);
  if (!Number.isFinite(Date.parse(parsed))) throw new Error(`${field} должен быть ISO timestamp`);
  return new Date(parsed).toISOString();
}

function integer(value: unknown, field: string, minimum: number, maximum: number): number {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error(`${field} должен быть integer ${minimum}..${maximum}`);
  }
  return parsed;
}

function booleanValue(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") throw new Error(`${field} должен быть boolean`);
  return value;
}

function digest(value: unknown, field: string): string {
  const parsed = boundedText(value, field, 64);
  if (!/^[a-f0-9]{64}$/.test(parsed)) throw new Error(`${field} должен быть SHA-256 hex`);
  return parsed;
}

function parseCell(value: unknown, field: string): RegionLossDrEvidence["source"] {
  const row = record(value, field);
  return {
    cellId: identifier(row.cellId, `${field}.cellId`),
    region: identifier(row.region, `${field}.region`),
    residencyDomain: identifier(row.residencyDomain, `${field}.residencyDomain`),
  };
}

function parseApprovals(value: unknown, field: string): RegionLossDrEvidence["approvals"] {
  if (!Array.isArray(value) || value.length === 0 || value.length > 16) {
    throw new Error(`${field} должен быть bounded non-empty array`);
  }
  return value.map((entry, index) => {
    const approval = record(entry, `${field}[${index}]`);
    return {
      subject: identifier(approval.subject, `${field}[${index}].subject`),
      role: identifier(approval.role, `${field}[${index}].role`),
      approvedAt: timestamp(approval.approvedAt, `${field}[${index}].approvedAt`),
      changeId: identifier(approval.changeId, `${field}[${index}].changeId`),
    };
  });
}

export function parseRegionLossDrPolicy(value: unknown): RegionLossDrPolicy {
  const row = record(value, "Region-loss DR policy");
  const objectives = record(row.objectives, "policy.objectives");
  const artifacts = record(row.artifacts, "policy.artifacts");
  const evidence = record(row.evidence, "policy.evidence");
  if (row.schemaVersion !== 1) throw new Error("Region-loss DR policy schemaVersion должен быть 1");
  if (!Array.isArray(row.transitions) || row.transitions.length === 0 || row.transitions.length > 64) {
    throw new Error("policy.transitions должен быть bounded non-empty array");
  }
  if (!Array.isArray(evidence.requiredApprovalRoles)
    || evidence.requiredApprovalRoles.length === 0
    || evidence.requiredApprovalRoles.length > 16) {
    throw new Error("policy.evidence.requiredApprovalRoles должен быть bounded non-empty array");
  }
  const transitions = row.transitions.map((entry, index) => {
    const transition = record(entry, `transitions[${index}]`);
    const parsed = {
      residencyDomain: identifier(transition.residencyDomain, `transitions[${index}].residencyDomain`),
      sourceRegion: identifier(transition.sourceRegion, `transitions[${index}].sourceRegion`),
      targetRegion: identifier(transition.targetRegion, `transitions[${index}].targetRegion`),
    };
    if (parsed.sourceRegion === parsed.targetRegion) throw new Error("Region-loss transition требует разные source/target regions");
    return parsed;
  });
  const transitionKeys = transitions.map((entry) => `${entry.residencyDomain}\0${entry.sourceRegion}\0${entry.targetRegion}`);
  if (new Set(transitionKeys).size !== transitionKeys.length) throw new Error("policy.transitions содержит duplicate");
  const requiredApprovalRoles = evidence.requiredApprovalRoles.map((entry, index) => (
    identifier(entry, `requiredApprovalRoles[${index}]`)
  ));
  if (new Set(requiredApprovalRoles).size !== requiredApprovalRoles.length) {
    throw new Error("requiredApprovalRoles содержит duplicate");
  }
  return {
    schemaVersion: 1,
    policyId: identifier(row.policyId, "policyId"),
    transitions,
    objectives: {
      rpoSeconds: integer(objectives.rpoSeconds, "rpoSeconds", 0, 86_400),
      rtoSeconds: integer(objectives.rtoSeconds, "rtoSeconds", 1, 604_800),
      maximumCrossSystemSkewSeconds: integer(
        objectives.maximumCrossSystemSkewSeconds,
        "maximumCrossSystemSkewSeconds",
        0,
        86_400,
      ),
    },
    artifacts: {
      requireVersioning: booleanValue(artifacts.requireVersioning, "requireVersioning"),
      requireVersionIdPreservation: booleanValue(
        artifacts.requireVersionIdPreservation,
        "requireVersionIdPreservation",
      ),
      maximumReplicationLagSeconds: integer(
        artifacts.maximumReplicationLagSeconds,
        "maximumReplicationLagSeconds",
        0,
        86_400,
      ),
    },
    evidence: {
      maximumAgeSeconds: integer(evidence.maximumAgeSeconds, "maximumAgeSeconds", 30, 86_400),
      requiredDistinctApprovers: integer(evidence.requiredDistinctApprovers, "requiredDistinctApprovers", 2, 16),
      requiredApprovalRoles,
    },
  };
}

export function parseRegionLossDrEvidence(value: unknown): RegionLossDrEvidence {
  const row = record(value, "Region-loss DR evidence");
  if (row.schemaVersion !== 1) throw new Error("Region-loss DR evidence schemaVersion должен быть 1");
  const fencing = record(row.fencing, "evidence.fencing");
  const postgres = record(row.postgres, "evidence.postgres");
  const artifacts = record(row.artifacts, "evidence.artifacts");
  const temporal = record(row.temporal, "evidence.temporal");
  const approvals = parseApprovals(row.approvals, "evidence.approvals");
  return {
    schemaVersion: 1,
    incidentId: identifier(row.incidentId, "incidentId"),
    incidentDeclaredAt: timestamp(row.incidentDeclaredAt, "incidentDeclaredAt"),
    observedAt: timestamp(row.observedAt, "observedAt"),
    source: parseCell(row.source, "source"),
    target: parseCell(row.target, "target"),
    fencing: {
      completedAt: timestamp(fencing.completedAt, "fencing.completedAt"),
      databaseWritesDisabled: booleanValue(fencing.databaseWritesDisabled, "databaseWritesDisabled"),
      artifactWritesDisabled: booleanValue(fencing.artifactWritesDisabled, "artifactWritesDisabled"),
      inboundTrafficDisabled: booleanValue(fencing.inboundTrafficDisabled, "inboundTrafficDisabled"),
      workerEnrollmentDisabled: booleanValue(fencing.workerEnrollmentDisabled, "workerEnrollmentDisabled"),
      credentialsRotated: booleanValue(fencing.credentialsRotated, "credentialsRotated"),
      activeWriters: integer(fencing.activeWriters, "activeWriters", 0, 1_000_000),
    },
    postgres: {
      sourceClusterId: identifier(postgres.sourceClusterId, "postgres.sourceClusterId"),
      targetClusterId: identifier(postgres.targetClusterId, "postgres.targetClusterId"),
      recoveryTargetAt: timestamp(postgres.recoveryTargetAt, "postgres.recoveryTargetAt"),
      restoreCompletedAt: timestamp(postgres.restoreCompletedAt, "postgres.restoreCompletedAt"),
      targetReadWrite: booleanValue(postgres.targetReadWrite, "postgres.targetReadWrite"),
      schemaVersion: integer(postgres.schemaVersion, "postgres.schemaVersion", 1, 1_000_000),
      schemaContract: identifier(postgres.schemaContract, "postgres.schemaContract"),
      restoreReportSha256: digest(postgres.restoreReportSha256, "postgres.restoreReportSha256"),
    },
    artifacts: {
      sourceBucket: identifier(artifacts.sourceBucket, "artifacts.sourceBucket"),
      targetBucket: identifier(artifacts.targetBucket, "artifacts.targetBucket"),
      observedAt: timestamp(artifacts.observedAt, "artifacts.observedAt"),
      latestReplicatedAt: timestamp(artifacts.latestReplicatedAt, "artifacts.latestReplicatedAt"),
      sourceVersioning: booleanValue(artifacts.sourceVersioning, "sourceVersioning"),
      targetVersioning: booleanValue(artifacts.targetVersioning, "targetVersioning"),
      versionIdsPreserved: booleanValue(artifacts.versionIdsPreserved, "versionIdsPreserved"),
      replicationLagSeconds: integer(artifacts.replicationLagSeconds, "replicationLagSeconds", 0, 86_400),
      pendingOperations: integer(artifacts.pendingOperations, "pendingOperations", 0, Number.MAX_SAFE_INTEGER),
      failedOperations: integer(artifacts.failedOperations, "failedOperations", 0, Number.MAX_SAFE_INTEGER),
      objectCount: integer(artifacts.objectCount, "objectCount", 0, Number.MAX_SAFE_INTEGER),
      totalBytes: integer(artifacts.totalBytes, "totalBytes", 0, Number.MAX_SAFE_INTEGER),
      databaseReferenceSha256: digest(artifacts.databaseReferenceSha256, "databaseReferenceSha256"),
    },
    temporal: {
      sourceNamespace: identifier(temporal.sourceNamespace, "temporal.sourceNamespace"),
      targetNamespace: identifier(temporal.targetNamespace, "temporal.targetNamespace"),
      recoveryTargetAt: timestamp(temporal.recoveryTargetAt, "temporal.recoveryTargetAt"),
      recovered: booleanValue(temporal.recovered, "temporal.recovered"),
      recoveryReportSha256: digest(temporal.recoveryReportSha256, "temporal.recoveryReportSha256"),
    },
    approvals,
  };
}

export function evaluateRegionLossDrEvidence(
  policy: RegionLossDrPolicy,
  evidence: RegionLossDrEvidence,
  nowMs = Date.now(),
): {
  pass: boolean;
  reasons: string[];
  rpoSeconds: number;
  rtoSeconds: number;
  crossSystemSkewSeconds: number;
  policySha256: string;
} {
  const reasons: string[] = [];
  const transitionAllowed = policy.transitions.some((transition) => (
    transition.residencyDomain === evidence.source.residencyDomain
      && transition.sourceRegion === evidence.source.region
      && transition.targetRegion === evidence.target.region
  ));
  const observedMs = Date.parse(evidence.observedAt);
  const incidentMs = Date.parse(evidence.incidentDeclaredAt);
  const postgresTargetMs = Date.parse(evidence.postgres.recoveryTargetAt);
  const temporalTargetMs = Date.parse(evidence.temporal.recoveryTargetAt);
  const artifactObservedMs = Date.parse(evidence.artifacts.observedAt);
  const artifactTargetMs = Date.parse(evidence.artifacts.latestReplicatedAt);
  const oldestRecoveredMs = Math.min(postgresTargetMs, temporalTargetMs, artifactTargetMs);
  const newestRecoveredMs = Math.max(postgresTargetMs, temporalTargetMs, artifactTargetMs);
  const rpoSeconds = Math.max(0, (incidentMs - oldestRecoveredMs) / 1_000);
  // RTO is evaluated at each plan/apply decision, not frozen at evidence collection time.
  const rtoSeconds = Math.max(0, (nowMs - incidentMs) / 1_000);
  const crossSystemSkewSeconds = Math.max(0, (newestRecoveredMs - oldestRecoveredMs) / 1_000);
  const subjects = new Set(evidence.approvals.map((approval) => approval.subject));
  const roles = new Set(evidence.approvals.map((approval) => approval.role));
  if (!transitionAllowed) reasons.push("source/target transition is not approved by residency policy");
  if (evidence.source.region === evidence.target.region || evidence.source.cellId === evidence.target.cellId) {
    reasons.push("region-loss recovery requires a distinct target region and cell");
  }
  if (evidence.source.residencyDomain !== evidence.target.residencyDomain) {
    reasons.push("target cell crosses the residency boundary");
  }
  const ageSeconds = (nowMs - observedMs) / 1_000;
  if (ageSeconds < -30 || ageSeconds > policy.evidence.maximumAgeSeconds) reasons.push("region-loss evidence is stale or from the future");
  if (observedMs < incidentMs
    || Date.parse(evidence.fencing.completedAt) < incidentMs
    || Date.parse(evidence.fencing.completedAt) > observedMs
    || postgresTargetMs > observedMs
    || temporalTargetMs > observedMs) reasons.push("incident/fencing/evidence timeline is invalid");
  if (!evidence.fencing.databaseWritesDisabled
    || !evidence.fencing.artifactWritesDisabled
    || !evidence.fencing.inboundTrafficDisabled
    || !evidence.fencing.workerEnrollmentDisabled
    || !evidence.fencing.credentialsRotated
    || evidence.fencing.activeWriters !== 0) {
    reasons.push("source cell is not completely fenced; active-active writes are forbidden");
  }
  if (evidence.postgres.sourceClusterId === evidence.postgres.targetClusterId
    || !evidence.postgres.targetReadWrite
    || evidence.postgres.schemaVersion !== POSTGRES_SCHEMA_VERSION
    || evidence.postgres.schemaContract !== POSTGRES_SCHEMA_CONTRACT) {
    reasons.push("isolated PostgreSQL restore does not match the current writable schema contract");
  }
  if (Date.parse(evidence.postgres.restoreCompletedAt) < postgresTargetMs
    || Date.parse(evidence.postgres.restoreCompletedAt) > observedMs) {
    reasons.push("PostgreSQL restore timeline is invalid");
  }
  if (evidence.artifacts.sourceBucket === evidence.artifacts.targetBucket) reasons.push("artifact target bucket is not isolated from source");
  if (policy.artifacts.requireVersioning
    && (!evidence.artifacts.sourceVersioning || !evidence.artifacts.targetVersioning)) {
    reasons.push("artifact source/target versioning is incomplete");
  }
  if (policy.artifacts.requireVersionIdPreservation && !evidence.artifacts.versionIdsPreserved) {
    reasons.push("artifact replication does not preserve exact version IDs");
  }
  if (evidence.artifacts.pendingOperations !== 0
    || evidence.artifacts.failedOperations !== 0
    || evidence.artifacts.replicationLagSeconds > policy.artifacts.maximumReplicationLagSeconds) {
    reasons.push("artifact replication is incomplete or outside lag policy");
  }
  if (artifactTargetMs > artifactObservedMs
    || artifactObservedMs > observedMs
    || (observedMs - artifactObservedMs) / 1_000 > policy.evidence.maximumAgeSeconds) {
    reasons.push("artifact replication evidence timeline is invalid");
  }
  if (!evidence.temporal.recovered) reasons.push("Temporal workflow history was not recovered");
  if (rpoSeconds > policy.objectives.rpoSeconds) reasons.push("measured cross-system RPO exceeds policy");
  if (rtoSeconds > policy.objectives.rtoSeconds) reasons.push("measured recovery RTO exceeds policy");
  if (crossSystemSkewSeconds > policy.objectives.maximumCrossSystemSkewSeconds) {
    reasons.push("PostgreSQL, artifact and Temporal recovery points are not sufficiently aligned");
  }
  if (subjects.size < policy.evidence.requiredDistinctApprovers) reasons.push("region-loss activation lacks distinct approvers");
  for (const role of policy.evidence.requiredApprovalRoles) {
    if (!roles.has(role)) reasons.push(`region-loss approval role ${role} is missing`);
  }
  if (evidence.approvals.some((approval) => (
    Date.parse(approval.approvedAt) < incidentMs || Date.parse(approval.approvedAt) > observedMs + 30_000
  ))) reasons.push("region-loss approval timestamp is outside the incident window");
  return {
    pass: reasons.length === 0,
    reasons,
    rpoSeconds,
    rtoSeconds,
    crossSystemSkewSeconds,
    policySha256: sha256(policy),
  };
}

export async function readRegionLossDatabaseSnapshot(client: Client): Promise<RegionLossDatabaseSnapshot> {
  const marker = await client.query<{
    active_region: string;
    residency_domain: string;
    write_epoch: string;
    activation_id: string | null;
  }>("SELECT active_region, residency_domain, write_epoch::text, activation_id FROM agat_cell_runtime WHERE id = 'current'");
  const cell = marker.rows[0];
  if (!cell) throw new Error("PostgreSQL cell runtime marker отсутствует");
  const projects = await client.query<{
    id: string;
    home_region: string;
    residency_domain: string;
    queue_name: string;
    fleet_revision: string;
  }>(`
    SELECT id, home_region, residency_domain, queue_name, fleet_revision::text
    FROM projects ORDER BY id
  `);
  const projectRows = projects.rows.map((row) => ({
    id: row.id,
    homeRegion: row.home_region,
    residencyDomain: row.residency_domain,
    queueName: row.queue_name,
    fleetRevision: Number(row.fleet_revision),
  }));
  const artifacts = await client.query<{
    id: string;
    object_bucket: string;
    object_key: string;
    object_version_id: string | null;
    sha256: string;
    size_bytes: string;
    storage_state: string;
  }>(`
    SELECT id, object_bucket, object_key, object_version_id, sha256, size_bytes::text, storage_state
    FROM artifacts
    WHERE storage_backend = 's3' AND storage_state <> 'deleted'
      AND object_bucket IS NOT NULL AND object_key IS NOT NULL
    ORDER BY id
  `);
  const artifactRows = artifacts.rows.map((row) => ({
    id: row.id,
    bucket: row.object_bucket,
    key: row.object_key,
    versionId: row.object_version_id,
    sha256: row.sha256,
    sizeBytes: Number(row.size_bytes),
    storageState: row.storage_state,
  }));
  const activeRuns = await client.query<{ count: string }>(`
    SELECT COUNT(*)::text AS count FROM runs
    WHERE status IN ('queued', 'running', 'waiting_approval', 'waiting_external', 'compensating')
  `);
  return {
    cell: {
      activeRegion: cell.active_region,
      residencyDomain: cell.residency_domain,
      writeEpoch: Number(cell.write_epoch),
      activationId: cell.activation_id,
    },
    projects: { count: projectRows.length, sha256: sha256(projectRows), rows: projectRows },
    artifacts: {
      count: artifactRows.length,
      totalBytes: artifactRows.reduce((sum, row) => sum + row.sizeBytes, 0),
      sha256: sha256(artifactRows),
      buckets: [...new Set(artifactRows.map((row) => row.bucket))].sort(),
    },
    activeRuns: Number(activeRuns.rows[0]?.count ?? 0),
  };
}

export function buildRegionLossActivationPlan(
  policy: RegionLossDrPolicy,
  evidenceEnvelope: SignedPostgresEvidence<RegionLossDrEvidence>,
  snapshot: RegionLossDatabaseSnapshot,
  nowMs = Date.now(),
): RegionLossActivationPlan {
  const evaluation = evaluateRegionLossDrEvidence(policy, evidenceEnvelope.payload, nowMs);
  if (!evaluation.pass) throw new Error(`Region-loss evidence failed: ${evaluation.reasons.join("; ")}`);
  const evidence = evidenceEnvelope.payload;
  if (snapshot.cell.activeRegion !== evidence.source.region
    || snapshot.cell.residencyDomain !== evidence.source.residencyDomain) {
    throw new Error("Restored database marker does not match the source cell");
  }
  if (snapshot.cell.writeEpoch < 1) throw new Error("Source cell write epoch is invalid");
  if (snapshot.projects.rows.some((project) => (
    project.homeRegion !== evidence.source.region || project.residencyDomain !== evidence.source.residencyDomain
  ))) throw new Error("Database contains projects outside the source HA-cell");
  if (snapshot.artifacts.buckets.some((bucket) => bucket !== evidence.artifacts.sourceBucket)) {
    throw new Error("Artifact metadata references a bucket outside the source evidence");
  }
  if (snapshot.artifacts.count !== evidence.artifacts.objectCount
    || snapshot.artifacts.totalBytes !== evidence.artifacts.totalBytes
    || snapshot.artifacts.sha256 !== evidence.artifacts.databaseReferenceSha256) {
    throw new Error("Artifact replication evidence does not match restored PostgreSQL references");
  }
  return {
    schemaVersion: 1,
    planId: randomUUID(),
    activationId: randomUUID(),
    incidentId: evidence.incidentId,
    createdAt: new Date(nowMs).toISOString(),
    policyId: policy.policyId,
    policySha256: evaluation.policySha256,
    evidenceSha256: evidenceEnvelope.payloadSha256,
    source: evidence.source,
    target: evidence.target,
    sourceClusterId: evidence.postgres.sourceClusterId,
    targetClusterId: evidence.postgres.targetClusterId,
    sourceBucket: evidence.artifacts.sourceBucket,
    targetBucket: evidence.artifacts.targetBucket,
    recoveryTargetAt: evidence.postgres.recoveryTargetAt,
    sourceFencedAt: evidence.fencing.completedAt,
    sourceWriteEpoch: snapshot.cell.writeEpoch,
    sourceActivationId: snapshot.cell.activationId,
    targetWriteEpoch: snapshot.cell.writeEpoch + 1,
    projectCount: snapshot.projects.count,
    projectSnapshotSha256: snapshot.projects.sha256,
    artifactCount: snapshot.artifacts.count,
    artifactBytes: snapshot.artifacts.totalBytes,
    artifactSnapshotSha256: snapshot.artifacts.sha256,
    approvals: evidence.approvals,
  };
}

function parsePlan(value: unknown): RegionLossActivationPlan {
  const row = record(value, "Region-loss activation plan");
  if (row.schemaVersion !== 1) throw new Error("Region-loss activation plan schemaVersion должен быть 1");
  const source = parseCell(row.source, "plan.source");
  const target = parseCell(row.target, "plan.target");
  const approvals = parseApprovals(row.approvals, "plan.approvals");
  return {
    schemaVersion: 1,
    planId: identifier(row.planId, "planId"),
    activationId: identifier(row.activationId, "activationId"),
    incidentId: identifier(row.incidentId, "incidentId"),
    createdAt: timestamp(row.createdAt, "createdAt"),
    policyId: identifier(row.policyId, "policyId"),
    policySha256: digest(row.policySha256, "policySha256"),
    evidenceSha256: digest(row.evidenceSha256, "evidenceSha256"),
    source,
    target,
    sourceClusterId: identifier(row.sourceClusterId, "sourceClusterId"),
    targetClusterId: identifier(row.targetClusterId, "targetClusterId"),
    sourceBucket: identifier(row.sourceBucket, "sourceBucket"),
    targetBucket: identifier(row.targetBucket, "targetBucket"),
    recoveryTargetAt: timestamp(row.recoveryTargetAt, "recoveryTargetAt"),
    sourceFencedAt: timestamp(row.sourceFencedAt, "sourceFencedAt"),
    sourceWriteEpoch: integer(row.sourceWriteEpoch, "sourceWriteEpoch", 1, 1_000_000_000),
    sourceActivationId: row.sourceActivationId === null
      ? null
      : identifier(row.sourceActivationId, "sourceActivationId"),
    targetWriteEpoch: integer(row.targetWriteEpoch, "targetWriteEpoch", 2, 1_000_000_000),
    projectCount: integer(row.projectCount, "projectCount", 1, Number.MAX_SAFE_INTEGER),
    projectSnapshotSha256: digest(row.projectSnapshotSha256, "projectSnapshotSha256"),
    artifactCount: integer(row.artifactCount, "artifactCount", 0, Number.MAX_SAFE_INTEGER),
    artifactBytes: integer(row.artifactBytes, "artifactBytes", 0, Number.MAX_SAFE_INTEGER),
    artifactSnapshotSha256: digest(row.artifactSnapshotSha256, "artifactSnapshotSha256"),
    approvals,
  };
}

export async function applyRegionLossActivation(
  client: Client,
  policy: RegionLossDrPolicy,
  evidenceEnvelope: SignedPostgresEvidence<RegionLossDrEvidence>,
  planEnvelope: SignedPostgresEvidence<RegionLossActivationPlan>,
  actor: string,
  configuredTargetBucket: string,
  nowMs = Date.now(),
): Promise<Record<string, unknown>> {
  const plan = parsePlan(planEnvelope.payload);
  const evidence = evidenceEnvelope.payload;
  const evaluation = evaluateRegionLossDrEvidence(policy, evidence, nowMs);
  if (!evaluation.pass) throw new Error(`Region-loss evidence failed: ${evaluation.reasons.join("; ")}`);
  const normalizedActor = identifier(actor, "activation actor");
  if (plan.policyId !== policy.policyId
    || plan.policySha256 !== evaluation.policySha256
    || plan.evidenceSha256 !== evidenceEnvelope.payloadSha256
    || planEnvelope.payloadSha256 !== sha256(planEnvelope.payload)
    || plan.incidentId !== evidence.incidentId
    || canonicalJson(plan.source) !== canonicalJson(evidence.source)
    || canonicalJson(plan.target) !== canonicalJson(evidence.target)
    || plan.sourceClusterId !== evidence.postgres.sourceClusterId
    || plan.targetClusterId !== evidence.postgres.targetClusterId
    || plan.sourceBucket !== evidence.artifacts.sourceBucket
    || plan.targetBucket !== evidence.artifacts.targetBucket
    || plan.recoveryTargetAt !== evidence.postgres.recoveryTargetAt
    || plan.sourceFencedAt !== evidence.fencing.completedAt
    || canonicalJson(plan.approvals) !== canonicalJson(evidence.approvals)) {
    throw new Error("Region-loss plan is not bound to current policy/evidence");
  }
  if (plan.targetWriteEpoch !== plan.sourceWriteEpoch + 1) throw new Error("Region-loss write epoch is not monotonic");
  if (configuredTargetBucket !== plan.targetBucket || evidence.artifacts.targetBucket !== plan.targetBucket) {
    throw new Error("Configured target bucket does not match region-loss plan");
  }
  await client.query("BEGIN ISOLATION LEVEL SERIALIZABLE");
  try {
    await client.query("SELECT pg_advisory_xact_lock($1)", [REGION_LOSS_LOCK_ID]);
    const existing = await client.query<{ status: string }>(`
      SELECT status FROM region_loss_dr_activations WHERE id = $1 AND plan_sha256 = $2
    `, [plan.activationId, planEnvelope.payloadSha256]);
    const currentMarker = await client.query<{
      active_region: string;
      residency_domain: string;
      write_epoch: string;
      activation_id: string | null;
    }>(`
      SELECT active_region, residency_domain, write_epoch::text, activation_id
      FROM agat_cell_runtime WHERE id = 'current' FOR UPDATE
    `);
    const marker = currentMarker.rows[0];
    if (!marker) throw new Error("PostgreSQL cell runtime marker отсутствует");
    if (existing.rows[0]?.status === "active"
      && marker.activation_id === plan.activationId
      && marker.active_region === plan.target.region
      && Number(marker.write_epoch) === plan.targetWriteEpoch) {
      await client.query("COMMIT");
      return { activationId: plan.activationId, writeEpoch: plan.targetWriteEpoch, idempotent: true, success: true };
    }
    if (marker.active_region !== plan.source.region
      || marker.residency_domain !== plan.source.residencyDomain
      || Number(marker.write_epoch) !== plan.sourceWriteEpoch
      || marker.activation_id !== plan.sourceActivationId) {
      throw new Error("Cell marker changed after region-loss plan generation");
    }
    const freshTargetReplicas = await client.query<{ count: string }>(`
      SELECT COUNT(*)::text AS count FROM coordinator_replicas
      WHERE region = $1 AND status = 'ready'
        AND last_seen::timestamptz >= CURRENT_TIMESTAMP - INTERVAL '3 minutes'
    `, [plan.target.region]);
    if (Number(freshTargetReplicas.rows[0]?.count ?? 0) !== 0) {
      throw new Error("Target coordinator replicas must be scaled to zero during activation");
    }
    const snapshot = await readRegionLossDatabaseSnapshot(client);
    if (snapshot.projects.count !== plan.projectCount
      || snapshot.projects.sha256 !== plan.projectSnapshotSha256
      || snapshot.artifacts.count !== plan.artifactCount
      || snapshot.artifacts.totalBytes !== plan.artifactBytes
      || snapshot.artifacts.sha256 !== plan.artifactSnapshotSha256) {
      throw new Error("Database changed after region-loss plan generation");
    }
    await client.query("UPDATE region_loss_dr_activations SET status = 'superseded' WHERE status = 'active'");
    const activatedAt = new Date(nowMs).toISOString();
    await client.query(`
      INSERT INTO region_loss_dr_activations(
        id, incident_id, plan_sha256, policy_sha256, evidence_sha256,
        source_region, target_region, residency_domain,
        source_cluster_id, target_cluster_id, source_bucket, target_bucket,
        write_epoch, recovery_target_at, source_fenced_at,
        project_snapshot_sha256, artifact_snapshot_sha256, approvers_json,
        status, activated_at, activated_by
      ) VALUES (
        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
        $13, $14, $15, $16, $17, $18, 'active', $19, $20
      )
    `, [
      plan.activationId, plan.incidentId, planEnvelope.payloadSha256, plan.policySha256,
      plan.evidenceSha256, plan.source.region, plan.target.region, plan.target.residencyDomain,
      plan.sourceClusterId, plan.targetClusterId, plan.sourceBucket, plan.targetBucket,
      plan.targetWriteEpoch, plan.recoveryTargetAt, plan.sourceFencedAt,
      plan.projectSnapshotSha256, plan.artifactSnapshotSha256, JSON.stringify(plan.approvals),
      activatedAt, normalizedActor,
    ]);
    const projects = await client.query(`
      UPDATE projects SET home_region = $1, allowed_regions_json = $2,
        fleet_revision = fleet_revision + 1, updated_at = $3
      WHERE home_region = $4 AND residency_domain = $5
    `, [plan.target.region, JSON.stringify([plan.target.region]), activatedAt, plan.source.region, plan.source.residencyDomain]);
    if (projects.rowCount !== plan.projectCount) throw new Error("Project relocation count does not match the sealed plan");
    await client.query(`
      UPDATE runs SET region = $1, updated_at = $2
      WHERE region = $3 AND residency_domain = $4
        AND status IN ('queued', 'running', 'waiting_approval', 'waiting_external', 'compensating')
    `, [plan.target.region, activatedAt, plan.source.region, plan.source.residencyDomain]);
    await client.query(`
      UPDATE worker_rollouts SET region = $1, revision = revision + 1, updated_at = $2
      WHERE region = $3
    `, [plan.target.region, activatedAt, plan.source.region]);
    await client.query(`
      UPDATE nodes SET status = 'offline',
        credential_state = CASE WHEN credential_state = 'active' THEN 'revoked' ELSE credential_state END,
        revoked_at = CASE WHEN credential_state = 'active' THEN $1 ELSE revoked_at END,
        updated_at = $1
      WHERE region = $2 AND residency_domain = $3
    `, [activatedAt, plan.source.region, plan.source.residencyDomain]);
    await client.query(`
      UPDATE stages SET lease_expires_at = '1970-01-01T00:00:00.000Z', updated_at = $1
      WHERE status = 'running'
    `, [activatedAt]);
    await client.query(`
      UPDATE knowledge_embedding_jobs SET lease_expires_at = '1970-01-01T00:00:00.000Z', updated_at = $1
      WHERE status = 'running'
    `, [activatedAt]);
    await client.query(`
      UPDATE audit_export_outbox SET status = 'pending', locked_by = NULL,
        lock_expires_at = NULL, available_at = $1, updated_at = $1
      WHERE status = 'delivering'
    `, [activatedAt]);
    await client.query(`
      UPDATE artifact_storage_outbox SET
        object_bucket = CASE WHEN object_bucket = $1 THEN $2 ELSE object_bucket END,
        status = CASE WHEN status = 'delivering' THEN 'pending' ELSE status END,
        locked_by = NULL, lock_expires_at = NULL,
        available_at = CASE WHEN status = 'delivering' THEN $3 ELSE available_at END,
        updated_at = $3
      WHERE (object_bucket = $1 AND status <> 'delivered') OR status = 'delivering'
    `, [plan.sourceBucket, plan.targetBucket, activatedAt]);
    const artifacts = await client.query(`
      UPDATE artifacts SET object_bucket = $1
      WHERE storage_backend = 's3' AND storage_state <> 'deleted' AND object_bucket = $2
    `, [plan.targetBucket, plan.sourceBucket]);
    if (artifacts.rowCount !== plan.artifactCount) throw new Error("Artifact bucket relocation count does not match the sealed plan");
    await client.query("UPDATE a2a_push_deliveries SET status = 'pending' WHERE status = 'delivering'");
    await client.query(`
      UPDATE coordinator_replicas SET status = 'stopped', updated_at = $1 WHERE status <> 'stopped'
    `, [activatedAt]);
    await client.query(`
      UPDATE agat_cell_runtime SET active_region = $1, residency_domain = $2,
        write_epoch = $3, activation_id = $4, updated_at = $5, updated_by = $6
      WHERE id = 'current'
    `, [plan.target.region, plan.target.residencyDomain, plan.targetWriteEpoch, plan.activationId, activatedAt, normalizedActor]);
    await client.query(`
      INSERT INTO events(run_id, stage_id, node_id, level, type, message, data_json, project_id, created_at)
      VALUES (NULL, NULL, NULL, 'warn', 'region_loss.activated',
        'HA-cell activated after fenced region loss', $1, 'global', $2)
    `, [JSON.stringify({
      kind: "region_loss_dr",
      activationId: plan.activationId,
      incidentId: plan.incidentId,
      sourceRegion: plan.source.region,
      targetRegion: plan.target.region,
      residencyDomain: plan.target.residencyDomain,
      writeEpoch: plan.targetWriteEpoch,
      planSha256: planEnvelope.payloadSha256,
    }), activatedAt]);
    await client.query("COMMIT");
    return {
      activationId: plan.activationId,
      incidentId: plan.incidentId,
      sourceRegion: plan.source.region,
      targetRegion: plan.target.region,
      residencyDomain: plan.target.residencyDomain,
      writeEpoch: plan.targetWriteEpoch,
      projects: plan.projectCount,
      artifacts: plan.artifactCount,
      activeRunsAtPlan: snapshot.activeRuns,
      idempotent: false,
      activatedAt,
      success: true,
    };
  } catch (error) {
    await client.query("ROLLBACK").catch(() => undefined);
    throw error;
  }
}

export async function verifyRegionLossActivation(
  client: Client,
  plan: RegionLossActivationPlan,
): Promise<{ checks: Record<string, boolean>; success: boolean }> {
  const result = await client.query<Record<string, unknown>>(`
    SELECT
      c.active_region = $1 AS active_region,
      c.residency_domain = $2 AS residency_domain,
      c.write_epoch = $3 AS write_epoch,
      c.activation_id = $4 AS activation_id,
      a.status = 'active' AS activation_active,
      (SELECT COUNT(*) = 0 FROM projects WHERE home_region <> $1 OR residency_domain <> $2) AS projects_relocated,
      (SELECT COUNT(*) = 0 FROM runs
        WHERE status IN ('queued', 'running', 'waiting_approval', 'waiting_external', 'compensating')
          AND region <> $1) AS active_runs_relocated,
      (SELECT COUNT(*) = 0 FROM artifacts
        WHERE storage_backend = 's3' AND storage_state <> 'deleted' AND object_bucket <> $5) AS artifacts_relocated,
      (SELECT COUNT(*) = 0 FROM nodes
        WHERE region = $6 AND residency_domain = $2 AND credential_state = 'active') AS source_workers_revoked
    FROM agat_cell_runtime c
    JOIN region_loss_dr_activations a ON a.id = c.activation_id
    WHERE c.id = 'current'
  `, [
    plan.target.region,
    plan.target.residencyDomain,
    plan.targetWriteEpoch,
    plan.activationId,
    plan.targetBucket,
    plan.source.region,
  ]);
  const row = result.rows[0] ?? {};
  const checks = Object.fromEntries(Object.entries(row).map(([key, value]) => [key, value === true]));
  return { checks, success: Object.values(checks).length > 0 && Object.values(checks).every(Boolean) };
}

function readJson(pathname: string): unknown {
  const resolved = path.resolve(pathname);
  const stat = fs.statSync(resolved);
  if (!stat.isFile() || stat.size > 2_097_152) throw new Error("DR JSON должен быть regular file не больше 2 MiB");
  return JSON.parse(fs.readFileSync(resolved, "utf8"));
}

function evidenceKey(): Buffer {
  const pathname = process.env.AGAT_DR_EVIDENCE_KEY_PATH ?? "";
  if (!pathname) throw new Error("AGAT_DR_EVIDENCE_KEY_PATH обязателен");
  const resolved = path.resolve(pathname);
  const stat = fs.statSync(resolved);
  if (!stat.isFile() || (stat.mode & 0o007) !== 0) {
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
  await fs.promises.mkdir(path.dirname(resolved), { recursive: true, mode: 0o700 });
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

function sslConfig(connectionUrl: string): ClientConfig["ssl"] {
  const mode = process.env.AGAT_POSTGRES_SSL_MODE ?? "verify-full";
  const hostname = new URL(connectionUrl).hostname.toLowerCase();
  const local = process.env.AGAT_DR_ALLOW_INSECURE_LOCAL === "true"
    && ["127.0.0.1", "::1", "localhost"].includes(hostname);
  if (mode !== "verify-full" && !local) throw new Error("Region-loss DR требует PostgreSQL TLS verify-full");
  if (mode === "disable") return false;
  const caPath = process.env.AGAT_POSTGRES_CA_CERT_PATH;
  return {
    rejectUnauthorized: mode === "verify-full",
    ...(caPath ? { ca: fs.readFileSync(path.resolve(caPath), "utf8") } : {}),
  };
}

async function connect(migration: boolean): Promise<Client> {
  const url = migration
    ? process.env.AGAT_POSTGRES_MIGRATION_URL ?? ""
    : process.env.AGAT_POSTGRES_URL ?? "";
  if (!url) throw new Error(migration ? "AGAT_POSTGRES_MIGRATION_URL обязателен" : "AGAT_POSTGRES_URL обязателен");
  const client = new pg.Client({
    connectionString: url,
    application_name: migration ? "agat-region-loss-activation" : "agat-region-loss-verification",
    connectionTimeoutMillis: 10_000,
    statement_timeout: 120_000,
    query_timeout: 120_000,
    ssl: sslConfig(url),
  });
  await client.connect();
  return client;
}

function loadEvidence(pathname: string, key: Buffer): SignedPostgresEvidence<RegionLossDrEvidence> {
  const envelope = verifyPostgresEvidence<unknown>(readJson(pathname), "region-loss-provider-evidence", key);
  return { ...envelope, payload: parseRegionLossDrEvidence(envelope.payload) };
}

function loadPlan(pathname: string, key: Buffer): SignedPostgresEvidence<RegionLossActivationPlan> {
  const envelope = verifyPostgresEvidence<unknown>(readJson(pathname), "region-loss-activation-plan", key);
  return { ...envelope, payload: parsePlan(envelope.payload) };
}

async function main(): Promise<void> {
  const command = process.argv[2] ?? "";
  const key = evidenceKey();
  if (command === "policy-digest") {
    const policy = parseRegionLossDrPolicy(readJson(requiredArgument("--policy")));
    await writeJson(argument("--report"), { schemaVersion: 1, policyId: policy.policyId, policySha256: sha256(policy) });
    return;
  }
  if (command === "seal-evidence") {
    const evidence = parseRegionLossDrEvidence(readJson(requiredArgument("--input")));
    await writeJson(argument("--report"), sealPostgresEvidence("region-loss-provider-evidence", evidence, key));
    return;
  }
  const policy = parseRegionLossDrPolicy(readJson(requiredArgument("--policy")));
  const evidenceEnvelope = loadEvidence(requiredArgument("--evidence"), key);
  if (command === "plan") {
    const client = await connect(true);
    try {
      const snapshot = await readRegionLossDatabaseSnapshot(client);
      const plan = buildRegionLossActivationPlan(policy, evidenceEnvelope, snapshot);
      await writeJson(argument("--report"), sealPostgresEvidence("region-loss-activation-plan", plan, key));
    } finally {
      await client.end().catch(() => undefined);
    }
    return;
  }
  const planEnvelope = loadPlan(requiredArgument("--plan"), key);
  if (command === "activate") {
    if (argument("--confirm") !== "SOURCE_FENCED_TARGET_ISOLATED") {
      throw new Error("Activation требует --confirm SOURCE_FENCED_TARGET_ISOLATED");
    }
    const actor = process.env.AGAT_DR_ACTOR ?? "";
    const targetBucket = process.env.AGAT_ARTIFACT_S3_BUCKET ?? "";
    const client = await connect(true);
    try {
      const result = await applyRegionLossActivation(
        client,
        policy,
        evidenceEnvelope,
        planEnvelope,
        actor,
        targetBucket,
      );
      await writeJson(argument("--report"), sealPostgresEvidence("region-loss-activation-report", {
        ...result,
        planSha256: planEnvelope.payloadSha256,
        evidenceSha256: evidenceEnvelope.payloadSha256,
      }, key));
    } finally {
      await client.end().catch(() => undefined);
    }
    return;
  }
  if (command === "verify") {
    const client = await connect(false);
    try {
      const result = await verifyRegionLossActivation(client, planEnvelope.payload);
      await writeJson(argument("--report"), sealPostgresEvidence("region-loss-verification", {
        completedAt: new Date().toISOString(),
        activationId: planEnvelope.payload.activationId,
        planSha256: planEnvelope.payloadSha256,
        ...result,
      }, key));
      if (!result.success) process.exitCode = 1;
    } finally {
      await client.end().catch(() => undefined);
    }
    return;
  }
  throw new Error("Команда: policy-digest, seal-evidence, plan, activate или verify");
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  void main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : "Region-loss DR command failed"}\n`);
    process.exitCode = 1;
  });
}
