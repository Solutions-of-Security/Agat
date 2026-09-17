import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { before, describe, it } from "node:test";

import pg from "pg";

import { AgatStore } from "../src/database.js";
import { migratePostgresSchemaAndAdmit } from "../src/postgres-schema-migrator.js";
import {
  enterPostgresTenantScope,
  PostgresDatabaseSync,
  runWithPostgresSystemScope,
} from "../src/postgres-database.js";

const systemUrl = process.env.AGAT_TEST_POSTGRES_URL ?? "";
const migrationUrl = process.env.AGAT_POSTGRES_MIGRATION_URL ?? "";
const tenantUrl = process.env.AGAT_TEST_POSTGRES_TENANT_URL ?? "";
const cellRegion = process.env.AGAT_REGION ?? "local";
const cellResidencyDomain = process.env.AGAT_RESIDENCY_DOMAIN ?? cellRegion;

function store(instanceId: string, artifactsDir: string): AgatStore {
  return new AgatStore(":postgresql:", {
    stateStoreDriver: "postgresql",
    postgres: {
      systemUrl,
      tenantUrl,
      roleMode: "runtime",
      applicationName: instanceId,
      poolMax: 2,
      connectTimeoutMs: 5_000,
      idleTimeoutMs: 30_000,
      statementTimeoutMs: 30_000,
      sslMode: "disable",
    },
    postgresSchemaMode: "runtime",
    coordinatorInstanceId: instanceId,
    region: cellRegion,
    residencyDomain: cellResidencyDomain,
    requireSignedWorkerReleases: false,
    artifactsDir,
  });
}

describe("PostgreSQL Fleet/HA integration", { skip: !migrationUrl || !systemUrl || !tenantUrl }, () => {
  before(async () => {
    await migratePostgresSchemaAndAdmit();
  });

  it("rejects a tenant connection that resolves to the system BYPASSRLS role", () => {
    assert.throws(() => new PostgresDatabaseSync({
      systemUrl,
      tenantUrl: systemUrl,
      roleMode: "runtime",
      applicationName: "agat-role-separation-negative-test",
      poolMax: 1,
      connectTimeoutMs: 5_000,
      idleTimeoutMs: 5_000,
      statementTimeoutMs: 5_000,
      sslMode: "disable",
    }), /same actual role|одну фактическую роль/i);
  });

  it("keeps the runtime role DDL-free after a separate migration gate", () => {
    const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), "agat-pg-ddl-free-"));
    const runtime = store(`ddl-free-${randomUUID().slice(0, 8)}`, artifacts);
    try {
      const profile = runtime.db.prepare(`
        SELECT has_schema_privilege(current_user, current_schema(), 'CREATE') AS schema_create
      `).get();
      assert.equal(profile?.schema_create, false);
      assert.throws(
        () => runtime.db.exec("CREATE TABLE forbidden_runtime_ddl(id TEXT PRIMARY KEY)"),
        /permission denied/i,
      );
      assert.throws(
        () => runtime.db.exec("CREATE TEMP TABLE forbidden_runtime_temp_ddl(id TEXT PRIMARY KEY)"),
        /permission denied/i,
      );
    } finally {
      runtime.close();
      fs.rmSync(artifacts, { recursive: true, force: true });
    }
  });

  it("rejects a schema Job while a coordinator replica is active", async () => {
    const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), "agat-pg-active-migration-"));
    const runtime = store(`active-migration-${randomUUID().slice(0, 8)}`, artifacts);
    try {
      await assert.rejects(
        migratePostgresSchemaAndAdmit(),
        /active coordinator replicas/,
      );
    } finally {
      runtime.close();
      fs.rmSync(artifacts, { recursive: true, force: true });
    }
  });

  it("rejects runtime startup after out-of-band schema drift", async () => {
    const connection = new pg.Client({ connectionString: migrationUrl, ssl: false });
    await connection.connect();
    try {
      await connection.query("CREATE TABLE schema_drift_probe(id TEXT PRIMARY KEY)");
      const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), "agat-pg-schema-drift-"));
      try {
        assert.throws(
          () => store(`schema-drift-${randomUUID().slice(0, 8)}`, artifacts),
          /schema manifest drift/i,
        );
      } finally {
        fs.rmSync(artifacts, { recursive: true, force: true });
      }
    } finally {
      await connection.query("DROP TABLE IF EXISTS schema_drift_probe");
      await connection.end();
    }
  });

  it("shares state between replicas, serializes project quota and enforces RLS", () => {
    const suffix = randomUUID().slice(0, 8);
    const region = cellRegion;
    const residencyDomain = cellResidencyDomain;
    const firstArtifacts = fs.mkdtempSync(path.join(os.tmpdir(), "agat-pg-a-"));
    const secondArtifacts = fs.mkdtempSync(path.join(os.tmpdir(), "agat-pg-b-"));
    const first = store(`integration-a-${suffix}`, firstArtifacts);
    const second = store(`integration-b-${suffix}`, secondArtifacts);
    try {
      const projectId = `fleet-${suffix}`;
      const foreignProjectId = `foreign-${suffix}`;
      first.createProject({
        id: projectId,
        name: `Fleet ${suffix}`,
        homeRegion: region,
        allowedRegions: [region],
        residencyDomain,
        queueName: `queue-${suffix}`,
        maxQueuedTasks: 2,
        maxRunningTasks: 1,
      });
      first.createProject({
        id: foreignProjectId,
        name: `Foreign ${suffix}`,
        homeRegion: region,
        allowedRegions: [region],
        residencyDomain,
      });
      first.updateScheduler("parallel", 10);
      const firstRun = first.createRun({
        name: "replica-visible-a",
        input: "one",
        agentIds: ["collector"],
        approvalRequired: false,
        resultDestination: "artifacts",
      }, projectId);
      const secondRun = second.createRun({
        name: "replica-visible-b",
        input: "two",
        agentIds: ["collector"],
        approvalRequired: false,
      }, projectId);
      assert.ok(second.getRun(firstRun.id, projectId));
      assert.ok(first.getRun(secondRun.id, projectId));

      assert.throws(() => first.createRun({
        name: "quota-overflow",
        input: "three",
        agentIds: ["collector"],
        approvalRequired: false,
      }, projectId), /queue quota exceeded/i);

      const nodeA = first.registerNode({
        enrollmentToken: "test",
        name: `node-a-${suffix}`,
        platform: "Linux",
        models: [],
        maxConcurrency: 1,
        region,
        residencyDomain,
      });
      const nodeB = second.registerNode({
        enrollmentToken: "test",
        name: `node-b-${suffix}`,
        platform: "Linux",
        models: [],
        maxConcurrency: 1,
        region,
        residencyDomain,
      });
      const lease = first.leaseNext(nodeA.id);
      assert.ok(lease);
      assert.equal(second.leaseNext(nodeB.id), null, "project maxRunningTasks must hold across replicas");
      first.completeLease(nodeA.id, lease.leaseId, "replica artifact", [
        { name: "evidence.txt", mediaType: "text/plain", content: "persisted in PostgreSQL" },
      ]);
      const trace = second.getRunTrace(firstRun.id, projectId) as { artifacts?: Array<{ id: string }> };
      const artifact = trace.artifacts?.find((item) => item.id);
      assert.ok(artifact);
      const download = second.getArtifactDownload(artifact.id, projectId);
      assert.ok(download);
      assert.equal(download.filePath.startsWith(secondArtifacts), true);
      assert.equal(fs.readFileSync(download.filePath, "utf8").length > 0, true);

      const snapshot = first.getFleetSnapshot(projectId);
      assert.equal((snapshot.cell as Record<string, unknown>).haReady, true);

      enterPostgresTenantScope(projectId);
      const visibleProjects = first.db.prepare("SELECT id FROM projects ORDER BY id").all();
      assert.deepEqual(visibleProjects.map((row) => row.id), [projectId]);
      const crossTenantUpdate = first.db.prepare("UPDATE projects SET name = ? WHERE id = ?")
        .run("forbidden", foreignProjectId);
      assert.equal(crossTenantUpdate.changes, 0);
      assert.equal(first.db.prepare("UPDATE projects SET name = name WHERE id = ?").run(projectId).changes, 1);
      assert.throws(
        () => first.db.prepare("UPDATE settings SET value = ? WHERE key = ?").run("unsafe", "scheduler_mode"),
        /permission denied/i,
        "tenant role must not mutate global control-plane tables",
      );
      for (const globalTrustTable of [
        "audit_export_outbox",
        "worker_runtime_attestation_challenges",
        "audit_export_dead_letters",
        "audit_export_retention_state",
      ]) {
        assert.throws(
          () => first.db.prepare(`SELECT * FROM ${globalTrustTable} LIMIT 1`).all(),
          /permission denied/i,
          `tenant role must not read ${globalTrustTable}`,
        );
      }
      runWithPostgresSystemScope(() => {
        const foreign = first.db.prepare("SELECT name FROM projects WHERE id = ?").get(foreignProjectId);
        assert.equal(foreign?.name, `Foreign ${suffix}`);
      });

      runWithPostgresSystemScope(() => {
        const claimedByFirst = first.claimAuditExportBatch(100);
        const claimedBySecond = second.claimAuditExportBatch(100);
        const firstIds = new Set(claimedByFirst.map((event) => Number(event.id)));
        assert.equal(claimedBySecond.some((event) => firstIds.has(Number(event.id))), false);
        first.completeAuditExport([...firstIds], null);
        second.completeAuditExport(claimedBySecond.map((event) => Number(event.id)), null);
      });
    } finally {
      runWithPostgresSystemScope(() => {
        first.close();
        second.close();
      });
      fs.rmSync(firstArtifacts, { recursive: true, force: true });
      fs.rmSync(secondArtifacts, { recursive: true, force: true });
    }
  });
});
