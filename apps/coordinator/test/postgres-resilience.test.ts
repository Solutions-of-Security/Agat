import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";

import {
  evaluateManagedPostgresSnapshot,
  evaluatePostgresSlo,
  parseManagedPostgresSnapshot,
  parsePostgresResiliencePolicy,
  parsePostgresSloMetrics,
  sealPostgresEvidence,
  sha256,
  verifyPostgresEvidence,
  type ManagedPostgresSnapshot,
  type PostgresResiliencePolicy,
} from "../src/postgres-resilience.js";

const nowMs = Date.parse("2026-08-30T12:00:00.000Z");

function policy(): PostgresResiliencePolicy {
  return parsePostgresResiliencePolicy({
    schemaVersion: 1,
    policyId: "agat-production-postgresql-v1",
    engineMajor: 17,
    topology: {
      minimumZones: 2,
      minimumSynchronousStandbys: 1,
      requireAutomaticFailover: true,
    },
    backup: {
      requirePitr: true,
      minimumRetentionHours: 168,
      maximumRestoreLagSeconds: 60,
      maximumBackupAgeSeconds: 86_400,
    },
    objectives: {
      rpoSeconds: 60,
      failoverRtoSeconds: 300,
      restoreRtoSeconds: 14_400,
      monthlyAvailabilityPercent: 99.9,
      minimumSloWindowHours: 672,
    },
    evidence: {
      maximumTopologyAgeSeconds: 900,
      maximumDrillAgeDays: 90,
      requiredDistinctApprovers: 2,
      requiredApprovalRoles: ["service-owner", "platform-operations"],
    },
  });
}

function snapshot(targetPolicy: PostgresResiliencePolicy): ManagedPostgresSnapshot {
  return parseManagedPostgresSnapshot({
    schemaVersion: 1,
    observedAt: new Date(nowMs - 30_000).toISOString(),
    provider: "example-managed-cloud",
    service: "managed-postgresql",
    clusterId: "agat-eu-prod",
    primaryInstanceId: "pg-a",
    primaryZone: "eu-1a",
    zones: ["eu-1a", "eu-1b"],
    region: "eu-prod",
    residencyDomain: "eu",
    engine: { name: "postgresql", major: 17 },
    ha: { automaticFailover: true, synchronousStandby: true, healthyStandbys: 1 },
    backup: {
      pitrEnabled: true,
      retentionHours: 192,
      oldestRestorableAt: new Date(nowMs - 192 * 3_600_000).toISOString(),
      latestRestorableAt: new Date(nowMs - 20_000).toISOString(),
      lastSuccessfulBackupAt: new Date(nowMs - 3_600_000).toISOString(),
    },
    security: { tlsMode: "verify-full", encryptionAtRest: true, publicEndpoint: false },
    sloApproval: {
      policySha256: sha256(targetPolicy),
      approvedAt: new Date(nowMs - 86_400_000).toISOString(),
      changeId: "CHG-2026-0042",
      approvers: [
        { subject: "oidc:alice", role: "service-owner" },
        { subject: "oidc:bob", role: "platform-operations" },
      ],
    },
  });
}

describe("managed PostgreSQL resilience contract", () => {
  it("keeps the deployed production policy parseable and fail-closed", () => {
    const deployed = parsePostgresResiliencePolicy(JSON.parse(fs.readFileSync(
      path.resolve(import.meta.dirname, "../../../deploy/k8s/production/postgres-resilience-policy.json"),
      "utf8",
    )));
    assert.equal(deployed.topology.minimumZones >= 2, true);
    assert.equal(deployed.backup.requirePitr, true);
    assert.equal(deployed.evidence.requiredDistinctApprovers >= 2, true);
  });

  it("accepts fresh multi-AZ/PITR evidence with distinct SLO approvers", () => {
    const targetPolicy = policy();
    const result = evaluateManagedPostgresSnapshot(targetPolicy, snapshot(targetPolicy), {
      region: "eu-prod",
      residencyDomain: "eu",
      nowMs,
    });
    assert.equal(result.pass, true);
    assert.deepEqual(result.reasons, []);
  });

  it("fails closed for stale, single-zone and incorrectly approved evidence", () => {
    const targetPolicy = policy();
    const invalid = snapshot(targetPolicy);
    invalid.observedAt = new Date(nowMs - 3_600_000).toISOString();
    invalid.zones = ["eu-1a"];
    invalid.ha.healthyStandbys = 0;
    invalid.backup.pitrEnabled = false;
    invalid.sloApproval.approvers = [
      { subject: "oidc:alice", role: "service-owner" },
      { subject: "oidc:alice", role: "platform-operations" },
    ];
    const result = evaluateManagedPostgresSnapshot(targetPolicy, invalid, {
      region: "eu-prod",
      residencyDomain: "eu",
      nowMs,
    });
    assert.equal(result.pass, false);
    assert.match(result.reasons.join("\n"), /stale|multi-AZ|standby|PITR|approvers/);
  });

  it("detects tampering in HMAC-sealed operational evidence", () => {
    const key = Buffer.alloc(32, 7);
    const sealed = sealPostgresEvidence("postgres-dr-checkpoint", { checkpointId: "one" }, key);
    assert.deepEqual(
      verifyPostgresEvidence(sealed, "postgres-dr-checkpoint", key).payload,
      { checkpointId: "one" },
    );
    const tampered = structuredClone(sealed);
    tampered.payload = { checkpointId: "two" };
    assert.throws(
      () => verifyPostgresEvidence(tampered, "postgres-dr-checkpoint", key),
      /digest не совпадает/,
    );
  });

  it("evaluates a measured monthly SLO only with fresh passing DR reports", () => {
    const targetPolicy = policy();
    const key = Buffer.alloc(32, 9);
    const completedAt = new Date(nowMs - 86_400_000).toISOString();
    const failover = sealPostgresEvidence("postgres-dr-verification", {
      reportType: "failover" as const,
      completedAt,
      success: true,
    }, key);
    const restore = sealPostgresEvidence("postgres-dr-verification", {
      reportType: "restore" as const,
      completedAt,
      success: true,
    }, key);
    const windowSeconds = 28 * 86_400;
    const metrics = parsePostgresSloMetrics({
      schemaVersion: 1,
      windowStart: new Date(nowMs - windowSeconds * 1_000).toISOString(),
      windowEnd: new Date(nowMs).toISOString(),
      eligibleSeconds: windowSeconds,
      unavailableSeconds: 120,
      requestsTotal: 1_000_000,
      requestsFailed: 10,
      failoverReportSha256: failover.payloadSha256,
      restoreReportSha256: restore.payloadSha256,
    });
    const result = evaluatePostgresSlo(targetPolicy, metrics, failover as never, restore as never, nowMs);
    assert.equal(result.success, true);
    assert.equal(result.availabilityPercent > 99.9, true);
    assert.equal(result.requestSuccessPercent, 99.999);

    metrics.unavailableSeconds = 10_000;
    assert.equal(evaluatePostgresSlo(targetPolicy, metrics, failover as never, restore as never, nowMs).success, false);
  });
});
