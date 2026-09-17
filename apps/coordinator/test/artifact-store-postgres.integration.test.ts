import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, it } from "node:test";

import {
  CreateBucketCommand,
  HeadObjectCommand,
  PutBucketVersioningCommand,
  S3Client,
} from "@aws-sdk/client-s3";

import { AgatStore } from "../src/database.js";
import { migratePostgresSchemaAndAdmit } from "../src/postgres-schema-migrator.js";
import { enterPostgresTenantScope, runWithPostgresSystemScope } from "../src/postgres-database.js";

const endpoint = process.env.AGAT_TEST_S3_ENDPOINT ?? "";
const accessKeyId = process.env.AGAT_TEST_S3_ACCESS_KEY_ID ?? "";
const secretAccessKey = process.env.AGAT_TEST_S3_SECRET_ACCESS_KEY ?? "";
const bucket = process.env.AGAT_TEST_S3_BUCKET ?? "";
const systemUrl = process.env.AGAT_POSTGRES_URL ?? "";
const tenantUrl = process.env.AGAT_POSTGRES_TENANT_URL ?? "";

const enabled = Boolean(endpoint && accessKeyId && secretAccessKey && bucket && systemUrl && tenantUrl
  && process.env.AGAT_POSTGRES_MIGRATION_URL);

function database(instanceId: string, artifactsDir: string, artifactStoreDriver: "postgresql" | "s3"): AgatStore {
  return new AgatStore(":postgresql:", {
    stateStoreDriver: "postgresql",
    postgres: {
      systemUrl,
      tenantUrl,
      roleMode: "runtime",
      applicationName: instanceId,
      poolMax: 2,
      connectTimeoutMs: 5_000,
      idleTimeoutMs: 5_000,
      statementTimeoutMs: 30_000,
      sslMode: "disable",
    },
    postgresSchemaMode: "runtime",
    postgresRuntimeRole: "agat_system",
    coordinatorInstanceId: instanceId,
    region: "local",
    residencyDomain: "local",
    artifactsDir,
    requireSignedWorkerReleases: false,
    artifactStoreDriver,
    artifactRetentionDays: 30,
    ...(artifactStoreDriver === "s3" ? {
      artifactS3: {
        endpoint,
        region: "us-east-1",
        bucket,
        prefix: "agat-test",
        forcePathStyle: true,
        accessKeyId,
        secretAccessKey,
        requestTimeoutMs: 10_000,
        maximumObjectBytes: 1_048_576,
        serverSideEncryption: "none" as const,
        objectLockMode: "none" as const,
        requireVersioning: true,
      },
    } : {}),
  });
}

function createArtifact(store: AgatStore, suffix: string): { projectId: string; runId: string; artifactId: string; content: string } {
  const projectId = `artifact-${suffix}`;
  store.createProject({
    id: projectId,
    name: `Artifact ${suffix}`,
    homeRegion: "local",
    allowedRegions: ["local"],
    residencyDomain: "local",
  });
  const node = store.registerNode({
    enrollmentToken: "test",
    name: `artifact-node-${suffix}`,
    platform: "Linux",
    models: [],
    maxConcurrency: 1,
    region: "local",
    residencyDomain: "local",
  });
  const run = store.createRun({
    name: `Artifact run ${suffix}`,
    input: "artifact contract",
    agentIds: ["collector"],
    approvalRequired: false,
    resultDestination: "artifacts",
  }, projectId);
  const lease = store.leaseNext(node.id);
  assert.ok(lease);
  const content = `content-${suffix}-${randomUUID()}`;
  store.completeLease(node.id, lease.leaseId, "completed", [
    { name: "evidence.txt", mediaType: "text/plain", content },
  ]);
  const artifacts = store.listRunArtifacts(run.id);
  const artifact = artifacts.find((candidate) => candidate.name === "evidence.txt");
  assert.ok(artifact);
  return { projectId, runId: run.id, artifactId: String(artifact.id), content };
}

describe("S3-compatible Artifact Store integration", { skip: !enabled }, () => {
  it("migrates BYTEA, verifies cross-replica cache, isolates outbox and deletes by version", async () => {
    const s3 = new S3Client({
      endpoint,
      region: "us-east-1",
      forcePathStyle: true,
      credentials: { accessKeyId, secretAccessKey },
    });
    await s3.send(new CreateBucketCommand({ Bucket: bucket }));
    await s3.send(new PutBucketVersioningCommand({
      Bucket: bucket,
      VersioningConfiguration: { Status: "Enabled" },
    }));
    await migratePostgresSchemaAndAdmit();

    const root = fs.mkdtempSync(path.join(os.tmpdir(), "agat-artifact-s3-"));
    const bytea = database(`bytea-${randomUUID().slice(0, 8)}`, path.join(root, "bytea"), "postgresql");
    // Artifact semantics must not depend on which same-capability worker wins Model Router ordering.
    bytea.updateModelRouterPolicy({ enabled: false });
    let legacy: ReturnType<typeof createArtifact>;
    try {
      legacy = createArtifact(bytea, randomUUID().slice(0, 8));
      const metadata = runWithPostgresSystemScope(() => bytea.db.prepare("SELECT storage_backend, content_blob FROM artifacts WHERE id = ?")
        .get(legacy.artifactId));
      assert.equal(metadata?.storage_backend, "postgresql");
      assert.ok(metadata?.content_blob instanceof Uint8Array);
    } finally {
      runWithPostgresSystemScope(() => bytea.close());
    }

    const first = database(`s3-a-${randomUUID().slice(0, 8)}`, path.join(root, "first"), "s3");
    const second = database(`s3-b-${randomUUID().slice(0, 8)}`, path.join(root, "second"), "s3");
    try {
      const conformance = first.inspectArtifactBucket();
      assert.equal(conformance.versioning, "Enabled");
      const lifecycle = first.configureArtifactBucketLifecycle(30, 1);
      assert.deepEqual(lifecycle.lifecycleRuleIds, ["agat-storage-maintenance"]);

      const migration = first.migratePostgresArtifactsToS3(10);
      assert.equal(migration.selected, migration.migrated);
      assert.equal(migration.migrated >= 1, true);
      assert.equal(migration.bytes >= Buffer.byteLength(legacy!.content), true);
      const migrated = runWithPostgresSystemScope(() => first.db.prepare(`
        SELECT storage_backend, storage_state, content_blob, object_key, object_version_id
        FROM artifacts WHERE id = ?
      `).get(legacy!.artifactId));
      assert.equal(migrated?.storage_backend, "s3");
      assert.equal(migrated?.storage_state, "ready");
      assert.equal(migrated?.content_blob, null);
      assert.equal(typeof migrated?.object_key, "string");
      assert.equal(typeof migrated?.object_version_id, "string");

      const firstDownload = first.getArtifactDownload(legacy!.artifactId, legacy!.projectId);
      const secondDownload = second.getArtifactDownload(legacy!.artifactId, legacy!.projectId);
      assert.ok(firstDownload);
      assert.ok(secondDownload);
      assert.equal(fs.readFileSync(secondDownload.filePath, "utf8"), legacy!.content);
      fs.writeFileSync(secondDownload.filePath, "corrupt cache", { mode: 0o600 });
      const repaired = second.getArtifactDownload(legacy!.artifactId, legacy!.projectId);
      assert.ok(repaired);
      assert.equal(fs.readFileSync(repaired.filePath, "utf8"), legacy!.content);

      enterPostgresTenantScope(legacy!.projectId);
      assert.throws(
        () => first.db.prepare("SELECT id FROM artifact_storage_outbox").all(),
        /permission denied/i,
      );
      runWithPostgresSystemScope(() => {
        first.setArtifactRetention(
          legacy!.artifactId,
          legacy!.projectId,
          new Date(Date.now() + 86_400_000).toISOString(),
          true,
          "integration-test",
        );
        first.db.prepare(`
          UPDATE artifacts SET retention_until = ? WHERE id = ?
        `).run(new Date(Date.now() - 60_000).toISOString(), legacy!.artifactId);
      });
      const heldLifecycle = runWithPostgresSystemScope(() => first.runArtifactLifecycle(10));
      assert.deepEqual(heldLifecycle, { staged: 0, delivered: 0, retried: 0, dead: 0 });
      const heldOutbox = runWithPostgresSystemScope(() => first.db.prepare(`
        SELECT COUNT(*) AS count FROM artifact_storage_outbox WHERE artifact_id = ?
      `).get(legacy!.artifactId));
      assert.equal(Number(heldOutbox?.count), 0);
      runWithPostgresSystemScope(() => first.db.prepare(`
        UPDATE artifacts SET legal_hold = 0 WHERE id = ? AND storage_state = 'ready'
      `).run(legacy!.artifactId));
      const lifecycleResult = runWithPostgresSystemScope(() => first.runArtifactLifecycle(10));
      assert.deepEqual(lifecycleResult, { staged: 1, delivered: 1, retried: 0, dead: 0 });
      const deleted = runWithPostgresSystemScope(() => first.db.prepare(`
        SELECT storage_state, deleted_at FROM artifacts WHERE id = ?
      `).get(legacy!.artifactId));
      assert.equal(deleted?.storage_state, "deleted");
      assert.equal(typeof deleted?.deleted_at, "string");
      assert.equal(second.getArtifactDownload(legacy!.artifactId, legacy!.projectId), null);
      await assert.rejects(
        s3.send(new HeadObjectCommand({
          Bucket: bucket,
          Key: String(migrated?.object_key),
          VersionId: String(migrated?.object_version_id),
        })),
        /not.?found|specified key|specified version/i,
      );
      const delivered = runWithPostgresSystemScope(() => first.db.prepare(`
        SELECT status, attempts FROM artifact_storage_outbox WHERE artifact_id = ?
      `).get(legacy!.artifactId));
      assert.equal(delivered?.status, "delivered");
      assert.equal(Number(delivered?.attempts), 1);

      const cascaded = runWithPostgresSystemScope(() => createArtifact(first, randomUUID().slice(0, 8)));
      enterPostgresTenantScope(cascaded.projectId);
      assert.equal(first.db.prepare("DELETE FROM runs WHERE id = ?").run(cascaded.runId).changes, 1);
      const cascadeOutbox = runWithPostgresSystemScope(() => first.db.prepare(`
        SELECT COUNT(*) AS count FROM artifact_storage_outbox
        WHERE project_id = ? AND status = 'pending'
      `).get(cascaded.projectId));
      assert.equal(Number(cascadeOutbox?.count) >= 1, true);
      const cascadeLifecycle = runWithPostgresSystemScope(() => first.runArtifactLifecycle(20));
      assert.equal(cascadeLifecycle.delivered >= 1, true);
    } finally {
      runWithPostgresSystemScope(() => {
        first.close();
        second.close();
      });
      s3.destroy();
      fs.rmSync(root, { recursive: true, force: true });
    }
  });
});
