import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { describe, it } from "node:test";

import pg from "pg";

import { AgatStore, POSTGRES_SCHEMA_CONTRACT, POSTGRES_SCHEMA_VERSION } from "../src/database.js";
import type { ArtifactObjectStore } from "../src/artifact-object-store.js";
import { migratePostgresSchemaAndAdmit } from "../src/postgres-schema-migrator.js";
import {
  applyRegionLossActivation,
  buildRegionLossActivationPlan,
  parseRegionLossDrEvidence,
  parseRegionLossDrPolicy,
  readRegionLossDatabaseSnapshot,
  verifyRegionLossActivation,
} from "../src/region-loss-dr.js";
import { runWithPostgresSystemScope } from "../src/postgres-database.js";
import { sealPostgresEvidence } from "../src/postgres-resilience.js";

const migrationUrl = process.env.AGAT_POSTGRES_MIGRATION_URL ?? "";
const runtimeUrl = process.env.AGAT_POSTGRES_URL ?? "";
const tenantUrl = process.env.AGAT_POSTGRES_TENANT_URL ?? "";
const enabled = Boolean(migrationUrl && runtimeUrl && tenantUrl);
const unusedObjectStore: ArtifactObjectStore = {
  close() {},
  put() { throw new Error("not used"); },
  get() { throw new Error("not used"); },
  head() { throw new Error("not used"); },
  setProtection() { throw new Error("not used"); },
  delete() { throw new Error("not used"); },
  list() { throw new Error("not used"); },
  configureLifecycle() { throw new Error("not used"); },
  inspectBucket() { throw new Error("not used"); },
};

function databaseOptions(applicationName: string) {
  return {
    systemUrl: runtimeUrl,
    tenantUrl,
    roleMode: "runtime" as const,
    applicationName,
    poolMax: 2,
    connectTimeoutMs: 5_000,
    idleTimeoutMs: 5_000,
    statementTimeoutMs: 30_000,
    sslMode: "disable" as const,
  };
}

describe("region-loss DR PostgreSQL activation", { skip: !enabled }, () => {
  it("increments the write epoch, relocates the cell atomically and gates runtime startup", async () => {
    await migratePostgresSchemaAndAdmit();
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "agat-region-loss-"));
    const source = new AgatStore(":postgresql:", {
      stateStoreDriver: "postgresql",
      postgres: databaseOptions("region-loss-source"),
      postgresSchemaMode: "runtime",
      postgresRuntimeRole: "agat_system",
      coordinatorInstanceId: "source-coordinator",
      region: "eu-west-1",
      residencyDomain: "eu",
      artifactsDir: path.join(root, "artifacts"),
      artifactStoreDriver: "postgresql",
      requireSignedWorkerReleases: false,
    });
    let queuedRunId = "";
    let artifactId = "";
    let nodeId = "";
    try {
      source.updateModelRouterPolicy({ enabled: false });
      source.createProject({
        id: "dr-project",
        name: "DR project",
        homeRegion: "eu-west-1",
        allowedRegions: ["eu-west-1"],
        residencyDomain: "eu",
      });
      const node = source.registerNode({
        enrollmentToken: "test",
        name: "dr-source-node",
        platform: "Linux",
        models: [],
        maxConcurrency: 1,
        region: "eu-west-1",
        residencyDomain: "eu",
      });
      nodeId = node.id;
      const artifactRun = source.createRun({
        name: "Artifact before region loss",
        input: "preserve exact version",
        agentIds: ["collector"],
        approvalRequired: false,
        resultDestination: "artifacts",
      }, "dr-project");
      const lease = source.leaseNext(node.id);
      assert.ok(lease);
      source.completeLease(node.id, lease.leaseId, "complete", [
        { name: "evidence.txt", mediaType: "text/plain", content: "region-loss-artifact" },
      ]);
      const artifact = source.listRunArtifacts(artifactRun.id)[0];
      assert.ok(artifact);
      artifactId = String(artifact.id);
      runWithPostgresSystemScope(() => source.db.prepare(`
        UPDATE artifacts SET storage_backend = 's3', content_blob = NULL,
          object_bucket = 'agat-eu-primary', object_key = 'agat/objects/v1/eu/project/artifact/version',
          object_version_id = 'version-source-1', retention_until = ?, project_id = 'dr-project'
        WHERE id = ?
      `).run(new Date(Date.now() + 86_400_000).toISOString(), artifactId));
      queuedRunId = source.createRun({
        name: "Queued across region loss",
        input: "resume in target",
        agentIds: ["collector"],
        approvalRequired: false,
        resultDestination: "history",
      }, "dr-project").id;
      runWithPostgresSystemScope(() => source.db.prepare(`
        INSERT INTO artifact_storage_outbox(
          id, dedupe_key, artifact_id, project_id, operation,
          object_bucket, object_key, object_version_id,
          status, attempts, available_at, created_at, updated_at
        ) VALUES (
          'dr-outbox', 'dr-outbox', ?, 'dr-project', 'delete',
          'agat-eu-primary', 'agat/objects/v1/eu/project/artifact/version', 'version-source-1',
          'delivering', 1, ?, ?, ?
        )
      `).run(artifactId, new Date().toISOString(), new Date().toISOString(), new Date().toISOString()));
    } finally {
      runWithPostgresSystemScope(() => source.close());
    }

    const migration = new pg.Client({ connectionString: migrationUrl, ssl: false });
    await migration.connect();
    const evidenceKey = Buffer.alloc(32, 9);
    try {
      const snapshot = await readRegionLossDatabaseSnapshot(migration);
      const baseMs = Date.now();
      const iso = (seconds: number) => new Date(baseMs + seconds * 1_000).toISOString();
      const evidence = parseRegionLossDrEvidence({
        schemaVersion: 1,
        incidentId: `incident-${randomUUID()}`,
        incidentDeclaredAt: iso(-600),
        observedAt: iso(-10),
        source: { cellId: "eu-primary", region: "eu-west-1", residencyDomain: "eu" },
        target: { cellId: "eu-recovery", region: "eu-central-1", residencyDomain: "eu" },
        fencing: {
          completedAt: iso(-200),
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
          recoveryTargetAt: iso(-650),
          restoreCompletedAt: iso(-100),
          targetReadWrite: true,
          schemaVersion: POSTGRES_SCHEMA_VERSION,
          schemaContract: POSTGRES_SCHEMA_CONTRACT,
          restoreReportSha256: "3".repeat(64),
        },
        artifacts: {
          sourceBucket: "agat-eu-primary",
          targetBucket: "agat-eu-recovery",
          observedAt: iso(-20),
          latestReplicatedAt: iso(-645),
          sourceVersioning: true,
          targetVersioning: true,
          versionIdsPreserved: true,
          replicationLagSeconds: 40,
          pendingOperations: 0,
          failedOperations: 0,
          objectCount: snapshot.artifacts.count,
          totalBytes: snapshot.artifacts.totalBytes,
          databaseReferenceSha256: snapshot.artifacts.sha256,
        },
        temporal: {
          sourceNamespace: "agat-eu-primary",
          targetNamespace: "agat-eu-recovery",
          recoveryTargetAt: iso(-640),
          recovered: true,
          recoveryReportSha256: "4".repeat(64),
        },
        approvals: [
          { subject: "alice", role: "incident_commander", approvedAt: iso(-150), changeId: "chg-dr-1" },
          { subject: "bob", role: "data_owner", approvedAt: iso(-140), changeId: "chg-dr-1" },
        ],
      });
      const policy = parseRegionLossDrPolicy(JSON.parse(fs.readFileSync(
        new URL("../../../deploy/k8s/production/region-loss-dr-policy.json", import.meta.url),
        "utf8",
      )));
      const evidenceEnvelope = sealPostgresEvidence("region-loss-provider-evidence", evidence, evidenceKey);
      const plan = buildRegionLossActivationPlan(policy, evidenceEnvelope, snapshot, baseMs);
      const planEnvelope = sealPostgresEvidence("region-loss-activation-plan", plan, evidenceKey);
      const activated = await applyRegionLossActivation(
        migration,
        policy,
        evidenceEnvelope,
        planEnvelope,
        "incident-commander",
        "agat-eu-recovery",
        baseMs,
      );
      assert.equal(activated.success, true);
      assert.equal(activated.idempotent, false);
      const verified = await verifyRegionLossActivation(migration, plan);
      assert.equal(verified.success, true);

      const state = await migration.query<{
        project_region: string;
        run_region: string;
        artifact_bucket: string;
        node_state: string;
        outbox_bucket: string;
        outbox_status: string;
      }>(`
        SELECT
          (SELECT home_region FROM projects WHERE id = 'dr-project') AS project_region,
          (SELECT region FROM runs WHERE id = $1) AS run_region,
          (SELECT object_bucket FROM artifacts WHERE id = $2) AS artifact_bucket,
          (SELECT credential_state FROM nodes WHERE id = $3) AS node_state,
          (SELECT object_bucket FROM artifact_storage_outbox WHERE id = 'dr-outbox') AS outbox_bucket,
          (SELECT status FROM artifact_storage_outbox WHERE id = 'dr-outbox') AS outbox_status
      `, [queuedRunId, artifactId, nodeId]);
      assert.deepEqual(state.rows[0], {
        project_region: "eu-central-1",
        run_region: "eu-central-1",
        artifact_bucket: "agat-eu-recovery",
        node_state: "revoked",
        outbox_bucket: "agat-eu-recovery",
        outbox_status: "pending",
      });
      const repeated = await applyRegionLossActivation(
        migration,
        policy,
        evidenceEnvelope,
        planEnvelope,
        "incident-commander",
        "agat-eu-recovery",
        baseMs,
      );
      assert.equal(repeated.idempotent, true);

      assert.throws(() => new AgatStore(":postgresql:", {
        stateStoreDriver: "postgresql",
        postgres: databaseOptions("wrong-source-runtime"),
        postgresSchemaMode: "runtime",
        postgresRuntimeRole: "agat_system",
        schemaOnly: true,
        region: "eu-west-1",
        residencyDomain: "eu",
        artifactStoreDriver: "postgresql",
      }), /cell runtime marker/);
      assert.throws(() => new AgatStore(":postgresql:", {
        stateStoreDriver: "postgresql",
        postgres: databaseOptions("missing-activation-runtime"),
        postgresSchemaMode: "runtime",
        postgresRuntimeRole: "agat_system",
        schemaOnly: true,
        region: "eu-central-1",
        residencyDomain: "eu",
        artifactStoreDriver: "postgresql",
      }), /write epoch|activation ID/);
      assert.throws(() => new AgatStore(":postgresql:", {
        stateStoreDriver: "postgresql",
        postgres: databaseOptions("target-runtime"),
        postgresSchemaMode: "runtime",
        postgresRuntimeRole: "agat_system",
        schemaOnly: true,
        region: "eu-central-1",
        residencyDomain: "eu",
        regionLossDrActivationId: plan.activationId,
        regionLossDrWriteEpoch: plan.targetWriteEpoch,
        artifactStoreDriver: "postgresql",
      }), /S3 Artifact Store/);
      const target = new AgatStore(":postgresql:", {
        stateStoreDriver: "postgresql",
        postgres: databaseOptions("target-runtime"),
        postgresSchemaMode: "runtime",
        postgresRuntimeRole: "agat_system",
        schemaOnly: true,
        region: "eu-central-1",
        residencyDomain: "eu",
        regionLossDrActivationId: plan.activationId,
        regionLossDrWriteEpoch: plan.targetWriteEpoch,
        artifactStoreDriver: "s3",
        artifactRetentionDays: 30,
        artifactS3: {
          endpoint: "https://s3.invalid",
          region: "eu-central-1",
          bucket: "agat-eu-recovery",
          prefix: "agat",
          forcePathStyle: false,
          requestTimeoutMs: 5_000,
          maximumObjectBytes: 1_048_576,
          serverSideEncryption: "AES256",
          objectLockMode: "none",
          requireVersioning: true,
        },
        artifactObjectStore: unusedObjectStore,
      });
      target.close();
    } finally {
      await migration.end();
      fs.rmSync(root, { recursive: true, force: true });
    }

    const tenant = new pg.Client({ connectionString: tenantUrl, ssl: false });
    await tenant.connect();
    try {
      await assert.rejects(tenant.query("SELECT * FROM region_loss_dr_activations"), /permission denied/i);
    } finally {
      await tenant.end();
    }
  });
});
