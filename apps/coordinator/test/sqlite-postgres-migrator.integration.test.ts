import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, it } from "node:test";

import pg from "pg";

import { AgatStore } from "../src/database.js";
import { migrateSqliteToPostgres } from "../src/sqlite-postgres-migrator.js";

const systemUrl = process.env.AGAT_TEST_MIGRATION_POSTGRES_URL ?? "";
const tenantUrl = process.env.AGAT_TEST_MIGRATION_POSTGRES_TENANT_URL ?? "";
const rehearsalSystemUrl = process.env.AGAT_TEST_REHEARSAL_POSTGRES_URL ?? "";
const rehearsalTenantUrl = process.env.AGAT_TEST_REHEARSAL_POSTGRES_TENANT_URL ?? "";

describe("SQLite to PostgreSQL migration integration", {
  skip: (!systemUrl || !tenantUrl) && (!rehearsalSystemUrl || !rehearsalTenantUrl),
}, () => {
  it("copies a quiescent cell, reconciles artifacts and supports verify-only", { skip: !systemUrl || !tenantUrl }, async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "agat-state-migration-"));
    const sourcePath = path.join(root, "source.sqlite");
    const artifactsDir = path.join(root, "artifacts");
    const applyReportPath = path.join(root, "apply-report.json");
    const verifyReportPath = path.join(root, "verify-report.json");
    const source = new AgatStore(sourcePath, {
      region: "eu-migration-test",
      residencyDomain: "eu-test",
      artifactsDir,
      coordinatorInstanceId: "sqlite-source",
    });
    try {
      const node = source.registerNode({
        enrollmentToken: "test",
        name: "migration-node",
        platform: "Linux",
        models: [],
        maxConcurrency: 1,
      });
      const run = source.createRun({
        name: "Migrated artifact",
        input: "preserve me",
        agentIds: ["collector"],
        approvalRequired: false,
        resultDestination: "artifacts",
        artifactPath: "migration",
      });
      const lease = source.leaseNext(node.id);
      assert.ok(lease);
      source.completeLease(node.id, lease.leaseId, "migration result", [
        { name: "evidence.txt", mediaType: "text/plain", content: "artifact bytes" },
      ]);
      assert.equal(source.getRun(run.id)?.status, "completed");

      const process = source.createProcess({
        name: "Self-reference migration",
        graph: {
          nodes: [
            { id: "start", type: "start", name: "Start", position: { x: 0, y: 0 }, config: {} },
            { id: "end", type: "end", name: "End", position: { x: 200, y: 0 }, config: {} },
          ],
          edges: [{ id: "start-end", source: "start", target: "end", branch: "default" }],
        },
      });
      source.publishProcess(String(process.id));
      const instance = source.startProcess(String(process.id), { input: "dependency order" });
      assert.ok(instance);
      const insertToken = source.db.prepare(`
        INSERT INTO process_tokens (
          id, instance_id, parent_token_id, status, created_at, updated_at
        ) VALUES (?, ?, ?, 'completed', ?, ?)
      `);
      const timestamp = new Date().toISOString();
      insertToken.run("z-parent-token", String(instance.id), null, timestamp, timestamp);
      for (let index = 0; index < 300; index += 1) {
        insertToken.run(
          `a-child-token-${String(index).padStart(3, "0")}`,
          String(instance.id),
          "z-parent-token",
          timestamp,
          timestamp,
        );
      }
    } finally {
      source.close();
    }

    const base = {
      sourcePath,
      artifactsDir,
      targetUrl: systemUrl,
      targetTenantUrl: tenantUrl,
      sslMode: "disable" as const,
      batchRows: 50,
      offlineConfirmation: "SOURCE_AND_WRITERS_STOPPED",
    };
    try {
      const applied = await migrateSqliteToPostgres({
        ...base,
        mode: "apply",
        reportPath: applyReportPath,
      });
      assert.equal(applied.success, true);
      assert.equal(applied.tables.every((table) => table.match), true);
      assert.equal(applied.artifacts.verified >= 3, true);
      assert.equal(applied.reportSha256.length, 64);
      assert.equal(fs.existsSync(applyReportPath), true);

      const client = new pg.Client({ connectionString: systemUrl, ssl: false });
      await client.connect();
      try {
        const rows = await client.query<{ count: string }>("SELECT COUNT(*)::text AS count FROM artifacts WHERE content_blob IS NOT NULL");
        assert.equal(Number(rows.rows[0]?.count ?? 0) >= 3, true);
        const tokens = await client.query<{ count: string }>(`
          SELECT COUNT(*)::text AS count FROM process_tokens
          WHERE id = 'z-parent-token' OR parent_token_id = 'z-parent-token'
        `);
        assert.equal(tokens.rows[0]?.count, "301");
      } finally {
        await client.end();
      }

      const verified = await migrateSqliteToPostgres({
        ...base,
        mode: "verify",
        reportPath: verifyReportPath,
      });
      assert.equal(verified.success, true);
      assert.equal(verified.tables.every((table) => table.match), true);
      assert.equal(verified.artifacts.verified >= 3, true);

      const cyclic = new DatabaseSync(sourcePath);
      cyclic.exec("PRAGMA foreign_keys = OFF");
      const instance = cyclic.prepare("SELECT id FROM process_instances ORDER BY id LIMIT 1").get() as { id: string };
      const timestamp = new Date().toISOString();
      const insertCycle = cyclic.prepare(`
        INSERT INTO process_tokens (
          id, instance_id, parent_token_id, status, created_at, updated_at
        ) VALUES (?, ?, ?, 'completed', ?, ?)
      `);
      insertCycle.run("cycle-left", instance.id, "cycle-right", timestamp, timestamp);
      insertCycle.run("cycle-right", instance.id, "cycle-left", timestamp, timestamp);
      cyclic.close();
      await assert.rejects(
        migrateSqliteToPostgres({
          ...base,
          mode: "verify",
          reportPath: path.join(root, "cycle-report.json"),
        }),
        /Self-reference cycle.*process_tokens/,
      );
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  it("proves that a rehearsal import rolls back to its bootstrap baseline", { skip: !rehearsalSystemUrl || !rehearsalTenantUrl }, async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "agat-state-rehearsal-"));
    const sourcePath = path.join(root, "source.sqlite");
    const artifactsDir = path.join(root, "artifacts");
    const reportPath = path.join(root, "rehearsal-report.json");
    const source = new AgatStore(sourcePath, {
      region: "eu-rehearsal-test",
      residencyDomain: "eu-test",
      artifactsDir,
      coordinatorInstanceId: "sqlite-rehearsal-source",
    });
    source.createAgent({
      name: "Rehearsal-only agent",
      role: "Must disappear with rollback",
      systemPrompt: "TEST",
    });
    source.close();
    try {
      const report = await migrateSqliteToPostgres({
        sourcePath,
        artifactsDir,
        reportPath,
        mode: "rehearse",
        targetUrl: rehearsalSystemUrl,
        targetTenantUrl: rehearsalTenantUrl,
        sslMode: "disable",
        batchRows: 50,
        offlineConfirmation: "SOURCE_AND_WRITERS_STOPPED",
      });
      assert.equal(report.rollback.requested, true);
      assert.equal(report.rollback.verified, true);
      assert.equal(report.rollback.baselineSha256, report.rollback.restoredSha256);

      const client = new pg.Client({ connectionString: rehearsalSystemUrl, ssl: false });
      await client.connect();
      try {
        const result = await client.query<{ count: string }>(`
          SELECT COUNT(*)::text AS count FROM agents WHERE name = 'Rehearsal-only agent'
        `);
        assert.equal(result.rows[0]?.count, "0");
      } finally {
        await client.end();
      }
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });
});
