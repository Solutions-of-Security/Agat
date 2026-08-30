import assert from "node:assert/strict";
import fs from "node:fs";
import { describe, it } from "node:test";

import { POSTGRES_SCHEMA_CONTRACT, POSTGRES_SCHEMA_VERSION } from "../src/database.js";
import {
  buildRegionLossActivationPlan,
  evaluateRegionLossDrEvidence,
  parseRegionLossDrEvidence,
  parseRegionLossDrPolicy,
  type RegionLossDatabaseSnapshot,
  type RegionLossDrEvidence,
} from "../src/region-loss-dr.js";
import { sealPostgresEvidence, sha256 } from "../src/postgres-resilience.js";

const policy = parseRegionLossDrPolicy(JSON.parse(fs.readFileSync(
  new URL("../../../deploy/k8s/production/region-loss-dr-policy.json", import.meta.url),
  "utf8",
)));
const key = Buffer.alloc(32, 7);
const nowMs = Date.parse("2026-08-31T12:00:00.000Z");

function validEvidence(): RegionLossDrEvidence {
  return parseRegionLossDrEvidence({
    schemaVersion: 1,
    incidentId: "inc-2026-0831",
    incidentDeclaredAt: "2026-08-31T11:50:00.000Z",
    observedAt: "2026-08-31T11:59:50.000Z",
    source: { cellId: "eu-primary", region: "eu-west-1", residencyDomain: "eu" },
    target: { cellId: "eu-recovery", region: "eu-central-1", residencyDomain: "eu" },
    fencing: {
      completedAt: "2026-08-31T11:56:00.000Z",
      databaseWritesDisabled: true,
      artifactWritesDisabled: true,
      inboundTrafficDisabled: true,
      workerEnrollmentDisabled: true,
      credentialsRotated: true,
      activeWriters: 0,
    },
    postgres: {
      sourceClusterId: "pg-eu-primary",
      targetClusterId: "pg-eu-recovery",
      recoveryTargetAt: "2026-08-31T11:49:10.000Z",
      restoreCompletedAt: "2026-08-31T11:57:00.000Z",
      targetReadWrite: true,
      schemaVersion: POSTGRES_SCHEMA_VERSION,
      schemaContract: POSTGRES_SCHEMA_CONTRACT,
      restoreReportSha256: "1".repeat(64),
    },
    artifacts: {
      sourceBucket: "agat-eu-primary",
      targetBucket: "agat-eu-recovery",
      observedAt: "2026-08-31T11:59:40.000Z",
      latestReplicatedAt: "2026-08-31T11:49:15.000Z",
      sourceVersioning: true,
      targetVersioning: true,
      versionIdsPreserved: true,
      replicationLagSeconds: 45,
      pendingOperations: 0,
      failedOperations: 0,
      objectCount: 0,
      totalBytes: 0,
      databaseReferenceSha256: sha256([]),
    },
    temporal: {
      sourceNamespace: "agat-eu-primary",
      targetNamespace: "agat-eu-recovery",
      recoveryTargetAt: "2026-08-31T11:49:20.000Z",
      recovered: true,
      recoveryReportSha256: "2".repeat(64),
    },
    approvals: [
      { subject: "alice", role: "incident_commander", approvedAt: "2026-08-31T11:57:30.000Z", changeId: "chg-100" },
      { subject: "bob", role: "data_owner", approvedAt: "2026-08-31T11:58:00.000Z", changeId: "chg-100" },
    ],
  });
}

function snapshot(): RegionLossDatabaseSnapshot {
  const projects = [{
    id: "default",
    homeRegion: "eu-west-1",
    residencyDomain: "eu",
    queueName: "default",
    fleetRevision: 1,
  }];
  return {
    cell: { activeRegion: "eu-west-1", residencyDomain: "eu", writeEpoch: 1, activationId: null },
    projects: { count: projects.length, sha256: sha256(projects), rows: projects },
    artifacts: { count: 0, totalBytes: 0, sha256: sha256([]), buckets: [] },
    activeRuns: 0,
  };
}

describe("residency-aware region-loss DR", () => {
  it("accepts only a fenced active/passive transition with aligned recovery points", () => {
    const evidence = validEvidence();
    const result = evaluateRegionLossDrEvidence(policy, evidence, nowMs);
    assert.equal(result.pass, true);
    assert.equal(result.rpoSeconds, 50);
    assert.equal(result.rtoSeconds, 600);
    assert.equal(result.crossSystemSkewSeconds, 10);

    const envelope = sealPostgresEvidence("region-loss-provider-evidence", evidence, key);
    const plan = buildRegionLossActivationPlan(policy, envelope, snapshot(), nowMs);
    assert.equal(plan.sourceWriteEpoch, 1);
    assert.equal(plan.sourceActivationId, null);
    assert.equal(plan.targetWriteEpoch, 2);
    assert.equal(plan.projectCount, 1);
    assert.equal(plan.targetBucket, "agat-eu-recovery");
  });

  it("fails closed for cross-residency, active writers, stale evidence and unsafe replication", () => {
    const crossResidency = validEvidence();
    crossResidency.target.residencyDomain = "us";
    assert.equal(evaluateRegionLossDrEvidence(policy, crossResidency, nowMs).pass, false);

    const activeActive = validEvidence();
    activeActive.fencing.activeWriters = 1;
    activeActive.fencing.databaseWritesDisabled = false;
    assert.match(evaluateRegionLossDrEvidence(policy, activeActive, nowMs).reasons.join("; "), /active-active/);

    const replication = validEvidence();
    replication.artifacts.versionIdsPreserved = false;
    replication.artifacts.pendingOperations = 1;
    assert.equal(evaluateRegionLossDrEvidence(policy, replication, nowMs).pass, false);

    const stale = validEvidence();
    assert.match(
      evaluateRegionLossDrEvidence(policy, stale, nowMs + 2_000_000).reasons.join("; "),
      /stale/,
    );
  });

  it("binds the sealed plan to the exact restored project and artifact snapshots", () => {
    const evidence = validEvidence();
    const envelope = sealPostgresEvidence("region-loss-provider-evidence", evidence, key);
    const changed = snapshot();
    changed.artifacts.count = 1;
    assert.throws(
      () => buildRegionLossActivationPlan(policy, envelope, changed, nowMs),
      /Artifact replication evidence/,
    );
  });
});
