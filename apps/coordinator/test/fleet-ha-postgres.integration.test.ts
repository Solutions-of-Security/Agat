import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { before, describe, it } from "node:test";

import pg from "pg";
import { decisionProfileJson } from "../../../tests/fixtures/decision-shadow-profile.mjs";

import { AgatStore } from "../src/database.js";
import { migratePostgresSchemaAndAdmit } from "../src/postgres-schema-migrator.js";
import { normalizeDecisionShadowConfig } from "../src/local-decisions.js";
import { KnowledgeSearchExecutor, type KnowledgeSearchStoreOptions } from "../src/knowledge-search-executor.js";
import type { ProcessGraph } from "../src/types.js";
import { interceptRetrievalCommit } from "./postgres-commit-proxy.js";
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

function storeOptions(instanceId: string, artifactsDir: string, knowledgeSearchMaxCandidates?: number): KnowledgeSearchStoreOptions {
  return {
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
    decisionShadowEnabled: true,
    knowledgeSearchMaxCandidates,
    artifactsDir,
  };
}

function store(instanceId: string, artifactsDir: string, knowledgeSearchMaxCandidates?: number): AgatStore {
  return new AgatStore(":postgresql:", storeOptions(instanceId, artifactsDir, knowledgeSearchMaxCandidates));
}

async function startCoordinatorProcess(env: NodeJS.ProcessEnv) {
  const child = spawn(process.execPath, [fileURLToPath(new URL("../dist/server.js", import.meta.url))],
    { env, stdio: ["ignore", "pipe", "pipe"] });
  let diagnostic = "";
  child.stdout.setEncoding("utf8"); child.stderr.setEncoding("utf8");
  const closed = new Promise<{ code: number | null; signal: string | null }>(resolve => child.once("close", (code, signal) => resolve({ code, signal })));
  const stop = async () => {
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGTERM");
    let timer: NodeJS.Timeout | undefined;
    const finished = await Promise.race([closed.then(() => true), new Promise<false>(resolve => { timer = setTimeout(() => resolve(false), 5_000); })]);
    clearTimeout(timer);
    if (!finished) child.kill("SIGKILL");
    return closed;
  };
  try {
    const port = await new Promise<number>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Coordinator startup exceeded 30 seconds")), 30_000);
      const fail = () => { clearTimeout(timer); reject(new Error(`Coordinator startup failed: ${diagnostic.replace(/postgres(?:ql)?:\/\/[^\s]+/gi, "<redacted>").slice(-1000)}`)); };
      child.once("error", fail); child.once("close", fail);
      child.stderr.on("data", (chunk: string) => { diagnostic = (diagnostic + chunk).slice(-8192); });
      child.stdout.on("data", (chunk: string) => {
        diagnostic = (diagnostic + chunk).slice(-8192);
        const match = /АГАТ слушает http:\/\/127\.0\.0\.1:(\d+)/.exec(diagnostic);
        if (match) { clearTimeout(timer); resolve(Number(match[1])); }
      });
    });
    assert.ok(port > 0);
    return { port, stop, closed, signal: () => child.kill("SIGTERM"),
      diagnostic: () => diagnostic.replace(/postgres(?:ql)?:\/\/[^\s]+/gi, "<redacted>") };
  } catch (error) { await stop(); throw error; }
}

function coordinatorProcessEnvironment(instance: string, artifacts: string, overrides: NodeJS.ProcessEnv = {}): NodeJS.ProcessEnv {
  const env = Object.fromEntries(Object.entries(process.env).filter(([name]) => !name.startsWith("AGAT_")));
  return { ...env, AGAT_HOST: "127.0.0.1", AGAT_PORT: "0",
    AGAT_STATE_STORE_DRIVER: "postgresql", AGAT_ARTIFACT_STORE_DRIVER: "postgresql", AGAT_ARTIFACTS_DIR: artifacts,
    AGAT_POSTGRES_URL: systemUrl, AGAT_POSTGRES_TENANT_URL: tenantUrl, AGAT_POSTGRES_SSL_MODE: "disable",
    AGAT_POSTGRES_POOL_MAX: "2", AGAT_REGION: cellRegion, AGAT_RESIDENCY_DOMAIN: cellResidencyDomain,
    AGAT_COORDINATOR_INSTANCE_ID: instance, AGAT_KNOWLEDGE_SEARCH_EXECUTION: "isolated",
    AGAT_KNOWLEDGE_SEARCH_MAX_PENDING: "4", AGAT_KNOWLEDGE_SEARCH_TIMEOUT_MS: "1000",
    AGAT_REQUIRE_SIGNED_WORKER_RELEASES: "false", AGAT_REQUIRE_WORKER_PROVENANCE: "false",
    AGAT_REQUIRE_WORKER_RUNTIME_ATTESTATION: "false", AGAT_SERVE_WEB: "false", AGAT_SEED_DEMO: "false",
    AGAT_MCP_ENABLED: "false", AGAT_A2A_ENABLED: "false", AGAT_SANDBOX_ENABLED: "false",
    AGAT_SIEM_ENABLED: "false", AGAT_LOCAL_WORKER_LAUNCHER: "false", AGAT_TEMPORAL_ENABLED: "false", ...overrides };
}

async function eventually(check: () => Promise<boolean>, message: string): Promise<void> {
  const deadline = performance.now() + 5_000;
  while (performance.now() < deadline) { if (await check()) return; await delay(20); }
  assert.fail(message);
}

async function within<T>(promise: Promise<T>, timeoutMs: number, message: string): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([promise, new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error(message)), timeoutMs);
    })]);
  } finally { clearTimeout(timer); }
}

async function retrievalLeaseRaceFixture() {
  const suffix = randomUUID().slice(0, 8);
  const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), "agat-pg-rag-lock-"));
  const first = store(`rag-lock-main-${suffix}`, artifacts);
  const writer = new pg.Client({ connectionString: systemUrl, statement_timeout: 10_000 });
  let executor: KnowledgeSearchExecutor | undefined;
  try {
    first.updateScheduler("parallel", 10);
    const project = `rag-lock-${suffix}`, model = project;
    first.createProject({ id: project, name: project, homeRegion: cellRegion,
      allowedRegions: [cellRegion], residencyDomain: cellResidencyDomain });
    const agent = first.createAgent({ name: "Lock fixture", role: "Test", systemPrompt: "Fixture", model }, project);
    const node = first.registerNode({ enrollmentToken: "test", name: model, platform: "test", models: [model],
      embeddingModels: [model], maxConcurrency: 1, region: cellRegion, residencyDomain: cellResidencyDomain });
    const collection = String(first.createKnowledgeCollection({ name: "Lock source", embeddingModel: model }, project).id);
    first.ingestKnowledgeDocument(collection, { name: "Source", content: "Lease lock fixture." }, project);
    const embedding = first.leaseKnowledgeEmbedding(node.id)!; assert.ok(embedding);
    first.completeKnowledgeEmbedding(node.id, embedding.leaseId, embedding.chunks.map(chunk => ({ chunkId: chunk.id, embedding: [1, 0] })));
    const run = first.createRun({ name: "Lock", input: "Synthetic query", agentIds: [String(agent.id)],
      approvalRequired: false, knowledgeCollectionIds: [collection] }, project);
    const lease = first.leaseNext(node.id)!; assert.equal(lease.run.id, run.id);
    const application = `rag-lock-worker-${suffix}`;
    executor = await KnowledgeSearchExecutor.create(":postgresql:", storeOptions(application, artifacts));
    await writer.connect();
    const request = { queries: [{ embeddingModel: model, collectionIds: [collection], vector: [1, 0], topK: 1 }] };
    return { first, writer, project, run, node, lease, request, artifacts, agentId: String(agent.id),
      search: (leaseId = lease.leaseId) => executor!.search(node.token, node.id, leaseId, request),
      waitForLock: async (queryPattern = /FOR UPDATE/, applicationName = `${application}-system`) => {
        const deadline = performance.now() + 5_000;
        while (performance.now() < deadline) {
          await writer.query("SELECT pg_stat_clear_snapshot()");
          const result = await writer.query("SELECT query FROM pg_stat_activity WHERE application_name = $1 AND wait_event_type = 'Lock'", [applicationName]);
          if (result.rows.length) { assert.match(result.rows[0].query, queryPattern); return; }
          await delay(10);
        }
        assert.fail("Retrieval did not reach the SQL lock; the race was not exercised");
      },
      close: async () => {
        await writer.query("ROLLBACK"); await executor!.close(); await writer.end();
        first.cancelRun(run.id, project);
        first.close(); fs.rmSync(artifacts, { recursive: true, force: true });
      } };
  } catch (error) {
    await writer.end(); await executor?.close(); first.close(); fs.rmSync(artifacts, { recursive: true, force: true }); throw error;
  }
}

async function stageMaintenanceFixture(replicas: number) {
  const suffix = randomUUID().slice(0, 8), project = `maintenance-${suffix}`;
  const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), "agat-pg-maintenance-"));
  const first = store(`maintenance-store-${suffix}`, artifacts);
  const writer = new pg.Client({ connectionString: systemUrl, statement_timeout: 10_000 });
  const children: Array<Awaited<ReturnType<typeof startCoordinatorProcess>> & { instance: string }> = [];
  const pending: Promise<unknown>[] = [];
  try {
    await writer.connect(); first.updateScheduler("parallel", 10);
    first.createProject({ id: project, name: project, homeRegion: cellRegion,
      allowedRegions: [cellRegion], residencyDomain: cellResidencyDomain });
    const agent = first.createAgent({ name: "Primary", role: "Test", systemPrompt: "Fixture", model: project }, project);
    const worker = first.registerNode({ enrollmentToken: "test", name: project, platform: "test", models: [project],
      maxConcurrency: 1, region: cellRegion, residencyDomain: cellResidencyDomain });
    const run = first.createRun({ name: project, input: "Synthetic source", agentIds: [String(agent.id)],
      approvalRequired: false, resultDestination: "artifacts" }, project);
    const lease = first.leaseNext(worker.id)!; assert.equal(lease.run.id, run.id);
    for (let index = 0; index < replicas; index++) {
      const instance = `maintenance-${suffix}-${index}`;
      children.push({ instance, ...await startCoordinatorProcess(coordinatorProcessEnvironment(instance, artifacts,
        { AGAT_KNOWLEDGE_SEARCH_EXECUTION: "sync" })) });
    }
    const heartbeatAfter = async (index: number, timestamp: number) => {
      await delay(Math.max(0, timestamp - Date.now()));
      await eventually(async () => {
        const result = await writer.query("SELECT last_seen FROM coordinator_replicas WHERE instance_id = $1", [children[index]!.instance]);
        if (Date.parse(String(result.rows[0]?.last_seen)) >= timestamp) return true;
        // Heartbeat can precede expiry by a few ms while cleanup's later SELECT
        // already sees an expired lease and blocks. Observe that SQL directly.
        await writer.query("SELECT pg_stat_clear_snapshot()");
        const waiting = await writer.query("SELECT query FROM pg_stat_activity WHERE application_name = $1 AND wait_event_type = 'Lock'",
          [`agat-${children[index]!.instance}-system`]);
        return waiting.rows.some(row => /UPDATE stages\s+SET status/.test(String(row.query)));
      }, "The coordinator did not reach maintenance at the required timestamp");
    };
    const health = (index: number) => fetch(`http://127.0.0.1:${children[index]!.port}/api/v1/health`,
      { signal: AbortSignal.timeout(1_500) }).then(async response => { await response.text(); return { status: response.status }; },
      error => ({ error }));
    const expirationEvents = () => first.db.prepare("SELECT id FROM events WHERE run_id = ? AND type = 'lease.expired'").all(run.id);
    return { first, writer, children, pending, worker, run, lease, project, heartbeatAfter, health, expirationEvents,
      close: async () => {
        await writer.query("ROLLBACK").catch(() => {});
        await Promise.all(children.map(child => child.stop())); await Promise.allSettled(pending);
        first.cancelRun(run.id, project); await writer.end(); first.close(); fs.rmSync(artifacts, { recursive: true, force: true });
      } };
  } catch (error) {
    await writer.query("ROLLBACK").catch(() => {}); await Promise.all(children.map(child => child.stop()));
    await writer.end(); first.close(); fs.rmSync(artifacts, { recursive: true, force: true }); throw error;
  }
}

async function embeddingLeaseFixture(replicas = 1) {
  const suffix = randomUUID().slice(0, 8), project = `embedding-lease-${suffix}`;
  const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), "agat-pg-embedding-lease-"));
  const first = store(`embedding-store-${suffix}`, artifacts);
  const writer = new pg.Client({ connectionString: systemUrl, statement_timeout: 10_000 });
  const children: Array<Awaited<ReturnType<typeof startCoordinatorProcess>> & { instance: string }> = [];
  const pending: Promise<unknown>[] = [];
  try {
    await writer.connect(); first.updateScheduler("parallel", 10);
    first.createProject({ id: project, name: project, homeRegion: cellRegion,
      allowedRegions: [cellRegion], residencyDomain: cellResidencyDomain });
    const register = (name: string) => first.registerNode({ enrollmentToken: "test", name, platform: "test",
      models: [], embeddingModels: [project], maxConcurrency: 1, region: cellRegion, residencyDomain: cellResidencyDomain,
      labels: { pool: project } });
    const worker = register(`${project}-a`), other = register(`${project}-b`);
    const collection = String(first.createKnowledgeCollection({ name: project, embeddingModel: project }, project).id);
    const document = String(first.ingestKnowledgeDocument(collection, { name: "Source", content: "Synthetic index source." }, project).id);
    const lease = first.leaseKnowledgeEmbedding(worker.id)!; assert.equal(lease.document.id, document);
    const jobId = String(first.db.prepare("SELECT id FROM knowledge_embedding_jobs WHERE document_id = ?").get(document)!.id);
    for (let index = 0; index < replicas; index++) {
      const instance = `embedding-lease-${suffix}-${index}`;
      children.push({ instance, ...await startCoordinatorProcess(coordinatorProcessEnvironment(instance, artifacts,
        { AGAT_KNOWLEDGE_SEARCH_EXECUTION: "sync" })) });
    }
    const results = lease.chunks.map(chunk => ({ chunkId: chunk.id, embedding: [1, 0] }));
    const post = (action: "complete" | "fail" | "renew", index = 0) => {
      const request = fetch(`http://127.0.0.1:${children[index]!.port}/api/v1/workers/knowledge/leases/${lease.leaseId}/${action}`, {
        method: "POST", headers: { authorization: `Bearer ${worker.token}`, "content-type": "application/json" },
        body: JSON.stringify(action === "complete" ? { embeddings: results } : action === "fail" ? { error: "Fixture failure" } : {}),
        signal: AbortSignal.timeout(15_000),
      }).then(async response => ({ status: response.status, body: await response.text() })).then(value => ({ value }), error => ({ error }));
      pending.push(request); return request;
    };
    const waitForLock = (pattern: RegExp, index = 0) => eventually(async () => {
      await writer.query("SELECT pg_stat_clear_snapshot()");
      const waiting = await writer.query("SELECT query FROM pg_stat_activity WHERE application_name = $1 AND wait_event_type = 'Lock'",
        [`agat-${children[index]!.instance}-system`]);
      if (!waiting.rows.length) return false;
      assert.match(waiting.rows[0].query, pattern); return true;
    }, "Embedding request did not reach its intended SQL lock");
    const job = () => first.db.prepare("SELECT status, node_id, lease_id, lease_expires_at, failures FROM knowledge_embedding_jobs WHERE id = ?").get(jobId)!;
    const doc = () => first.db.prepare("SELECT status, embedded_count, error FROM knowledge_documents WHERE id = ?").get(document)!;
    const indexed = () => Number(first.db.prepare("SELECT COUNT(*) AS count FROM knowledge_chunks WHERE document_id = ? AND embedding_json IS NOT NULL").get(document)!.count);
    const events = () => first.db.prepare("SELECT id FROM events WHERE type IN ('knowledge.document.ready', 'knowledge.embedding.batch.completed', 'knowledge.embedding.retrying', 'knowledge.embedding.failed') AND data_json LIKE ?")
      .all(`%${document}%`).length;
    const assertReady = () => { assert.equal(job().status, "completed"); assert.equal(doc().status, "ready"); assert.equal(indexed(), results.length); };
    const recover = async () => {
      first.heartbeatNode(worker.id, {}); first.maintenanceTick();
      let replacement: ReturnType<typeof first.leaseKnowledgeEmbedding> = null;
      await eventually(async () => { replacement = first.leaseKnowledgeEmbedding(worker.id); return replacement !== null; },
        "Embedding replacement was not available after maintenance");
      assert.ok(replacement); assert.notEqual(replacement.leaseId, lease.leaseId);
      assert.equal(first.renewKnowledgeEmbeddingLease(worker.id, lease.leaseId), false);
      first.completeKnowledgeEmbedding(worker.id, replacement.leaseId, results); assertReady();
    };
    const health = (index: number) => fetch(`http://127.0.0.1:${children[index]!.port}/api/v1/health`,
      { signal: AbortSignal.timeout(1_500) }).then(async response => { await response.text(); return { status: response.status }; }, error => ({ error }));
    const maintenanceAfter = async (index: number, timestamp: number) => {
      await delay(Math.max(0, timestamp - Date.now()));
      await eventually(async () => {
        const replica = await writer.query("SELECT last_seen FROM coordinator_replicas WHERE instance_id = $1", [children[index]!.instance]);
        if (Date.parse(String(replica.rows[0]?.last_seen)) >= timestamp) return true;
        await writer.query("SELECT pg_stat_clear_snapshot()");
        const waiting = await writer.query("SELECT query FROM pg_stat_activity WHERE application_name = $1 AND wait_event_type = 'Lock'",
          [`agat-${children[index]!.instance}-system`]);
        return waiting.rows.some(row => /UPDATE knowledge_embedding_jobs/.test(String(row.query)));
      }, "Embedding maintenance did not reach the required timestamp");
    };
    return { first, writer, children, pending, project, worker, other, collection, document, lease, jobId, results,
      post, waitForLock, job, doc, indexed, events, assertReady, recover, health, maintenanceAfter,
      close: async () => {
        await writer.query("ROLLBACK").catch(() => {}); await Promise.all(children.map(child => child.stop())); await Promise.allSettled(pending);
        first.markWorkerPoolOffline(project); first.deleteKnowledgeCollection(collection, project); await writer.end(); first.close(); fs.rmSync(artifacts, { recursive: true, force: true });
      } };
  } catch (error) {
    await writer.query("ROLLBACK").catch(() => {}); await Promise.all(children.map(child => child.stop()));
    first.markWorkerPoolOffline(project); await writer.end(); first.close(); fs.rmSync(artifacts, { recursive: true, force: true }); throw error;
  }
}

describe("PostgreSQL Fleet/HA integration", { skip: !migrationUrl || !systemUrl || !tenantUrl }, () => {
  before(async () => {
    await migratePostgresSchemaAndAdmit();
  });

  it("reuses the response buffer across large, short, SQL-error and oversized responses", () => {
    runWithPostgresSystemScope(() => {
      const database = new PostgresDatabaseSync({ systemUrl, tenantUrl, roleMode: "runtime",
        applicationName: "response-buffer-integration", poolMax: 1, connectTimeoutMs: 5000,
        idleTimeoutMs: 5000, statementTimeoutMs: 30000, responseBytes: 1_048_576, sslMode: "disable" });
      try {
        for (let sequence = 0; sequence < 8; sequence++) {
          const content = `Данные ${sequence} 🐈`.repeat(20_000);
          const saved = database.prepare("SELECT ?::text AS content").get(content);
          assert.equal(saved?.content, content);
          assert.deepEqual(database.prepare("SELECT ?::integer AS sequence").get(sequence), { sequence });
          assert.equal(saved?.content, content, "a later response must not mutate a previously decoded value");
        }
        assert.throws(() => database.prepare("SELECT repeat('x', 1100000) AS content").get(), /exceeds bridge limit/);
        assert.deepEqual(database.prepare("SELECT 42 AS value").get(), { value: 42 });
        assert.throws(() => database.prepare("SELECT 1 / 0").get(), /division by zero/);
        assert.deepEqual(database.prepare("SELECT 43 AS value").get(), { value: 43 });
      } finally { database.close(); }
    });
  });

  it("ranks 10000 wide vectors through a 1 MiB bridge at the operator hard boundary", () => {
    runWithPostgresSystemScope(() => {
      const database = new PostgresDatabaseSync({ systemUrl, tenantUrl, roleMode: "runtime",
        applicationName: "rag-wide-budget", poolMax: 1, connectTimeoutMs: 5000,
        idleTimeoutMs: 5000, statementTimeoutMs: 30000, responseBytes: 1_048_576, sslMode: "disable" });
      try {
        const vector = Array<number>(4096).fill(1 / 3);
        const exact = JSON.stringify(vector);
        const orthogonal = JSON.stringify(vector.map((value, index) => index % 2 ? -value : value));
        assert.ok(Buffer.byteLength(exact) * 10000 > 512 * 1024 * 1024);
        database.exec("BEGIN");
        const ranked = database.prepare(`SELECT n AS ordinal, 4096 AS embedding_dimensions,
          CASE WHEN n = 10000 THEN ?::text ELSE ?::text END AS embedding_json
          FROM generate_series(1,10000) n ORDER BY n`)
          .rankKnowledgeCandidates!({ vector, topK: 1, maxCandidates: 10000 }, exact, orthogonal);
        assert.equal(ranked.length, 1);
        assert.equal(ranked[0]!.candidate.ordinal, 10000);
        assert.equal(ranked[0]!.score, 1);
        assert.equal("embedding_json" in ranked[0]!.candidate, false);
        assert.equal(Number(database.prepare("SELECT count(*) AS count FROM pg_cursors WHERE name LIKE 'agat_cursor_%'").get()!.count), 0);
        database.exec("COMMIT");
      } finally { database.close(); }
    });
  });

  it("rejects commit after the request deadline, permits rollback and restores the SQL timeout", () => {
    const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), "agat-pg-deadline-"));
    const first = store(`deadline-${randomUUID().slice(0, 8)}`, artifacts);
    try {
      const project = `deadline-${randomUUID().slice(0, 8)}`;
      first.createProject({ id: project, name: "Original", homeRegion: cellRegion,
        allowedRegions: [cellRegion], residencyDomain: cellResidencyDomain });
      const deadline = performance.now() + 500;
      runWithPostgresSystemScope(() => {
        first.db.exec("BEGIN");
        try {
          first.db.prepare("UPDATE projects SET name = 'Must roll back' WHERE id = ?").run(project);
          Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, Math.max(0, deadline - performance.now() + 20));
          assert.throws(() => first.db.exec("COMMIT"), (error: unknown) => (error as { code: string }).code === "AGAT_DEADLINE");
        } finally { first.db.exec("ROLLBACK"); }
      }, deadline);
      assert.equal(first.db.prepare("SELECT name FROM projects WHERE id = ?").get(project)!.name, "Original");
      assert.equal(first.db.prepare("SHOW statement_timeout").get()!.statement_timeout, "30s");
      assert.equal(first.db.prepare("SELECT 42 AS answer, pg_sleep(0.1)").get()!.answer, 42);
    } finally { first.close(); fs.rmSync(artifacts, { recursive: true, force: true }); }
  });

  it("applies the remaining request deadline to every cursor fetch and cleans up after SQL cancellation", () => {
    const database = new PostgresDatabaseSync({ systemUrl, tenantUrl, roleMode: "runtime", applicationName: "rag-cursor-deadline",
      poolMax: 1, connectTimeoutMs: 5000, idleTimeoutMs: 5000, statementTimeoutMs: 30000, sslMode: "disable" });
    try {
      runWithPostgresSystemScope(() => {
        database.exec("BEGIN");
        try {
          assert.throws(() => database.prepare(`SELECT n AS ordinal, 2 AS embedding_dimensions,
            '[1,0]' AS embedding_json, pg_sleep(0.003) FROM generate_series(1,130) n`)
            .rankKnowledgeCandidates!({ vector: [1, 0], topK: 1 }),
          (error: unknown) => (error as { code: string }).code === "57014", "A later FETCH must use the remaining time, not a fresh query budget");
        } finally { database.exec("ROLLBACK"); }
      }, performance.now() + 250);
      assert.equal(Number(database.prepare("SELECT count(*) AS count FROM pg_cursors WHERE name LIKE 'agat_cursor_%'").get()!.count), 0);
      assert.equal(database.prepare("SHOW statement_timeout").get()!.statement_timeout, "30s");
      assert.equal(database.prepare("SELECT 43 AS answer").get()!.answer, 43);
      const pid = database.prepare("SELECT pg_backend_pid() AS pid").get()!.pid;
      runWithPostgresSystemScope(() => {
        database.exec("BEGIN");
        assert.equal(database.prepare("SHOW statement_timeout").get()!.statement_timeout, "30s", "A long request must not extend the operator SQL timeout");
        database.exec("COMMIT");
      }, performance.now() + 60_000);
      runWithPostgresSystemScope(() => {
        database.exec("BEGIN");
        assert.notEqual(database.prepare("SHOW statement_timeout").get()!.statement_timeout, "30s");
        database.exec("COMMIT");
      }, performance.now() + 500);
      assert.equal(database.prepare("SELECT pg_backend_pid() AS pid").get()!.pid, pid, "The successful transaction reuses the same physical connection");
      assert.equal(database.prepare("SHOW statement_timeout").get()!.statement_timeout, "30s", "LOCAL must not leak into the pooled session after commit");
    } finally { database.close(); }
  });

  it("ranks all worker cursor batches against one snapshot while another connection updates the source", async () => {
    await runWithPostgresSystemScope(async () => {
      const suffix = randomUUID().slice(0, 8);
      const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), "agat-pg-rank-snapshot-"));
      const first = store(`rank-snapshot-${suffix}`, artifacts);
      const writer = new pg.Client({ connectionString: systemUrl, statement_timeout: 10_000 });
      let updating: Promise<Error | null> | undefined;
      try {
        const projectId = `rank-snapshot-${suffix}`;
        first.createProject({ id: projectId, name: "Original snapshot", homeRegion: cellRegion,
          allowedRegions: [cellRegion], residencyDomain: cellResidencyDomain });
        await writer.connect();
        const lock = Math.floor(Math.random() * 1_000_000_000) + 1;
        await writer.query("SELECT pg_advisory_lock($1::bigint)", [lock]);
        // The server waits for the ranker's first FETCH to hold a snapshot and
        // block on our lock. No timing assumption about Node scheduling is used.
        updating = writer.query(`DO $probe$
          DECLARE deadline timestamptz := clock_timestamp() + interval '5 seconds';
          BEGIN
            LOOP
              EXIT WHEN EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory'
                AND classid = 0::oid AND objid = ${lock}::oid AND objsubid = 1 AND NOT granted);
              IF clock_timestamp() >= deadline THEN RAISE EXCEPTION 'ranker never reached its snapshot gate'; END IF;
              PERFORM pg_sleep(0.01);
            END LOOP;
            UPDATE projects SET name = 'Changed by concurrent writer' WHERE id = '${projectId}';
            PERFORM pg_advisory_unlock(${lock}::bigint);
          END $probe$`).then(() => null, error => error as Error);
        first.db.exec("BEGIN");
        const ranked = first.db.prepare(`SELECT n AS ordinal, p.name AS content, 2 AS embedding_dimensions,
          CASE WHEN n = 129 THEN '[1,0]' ELSE '[0,1]' END AS embedding_json,
          CASE WHEN n = 1 THEN pg_advisory_xact_lock(?::bigint) END AS gate
          FROM projects p CROSS JOIN generate_series(1,130) n WHERE p.id = ? ORDER BY n`)
          .rankKnowledgeCandidates!({ vector: [1, 0], topK: 1 }, lock, projectId);
        assert.equal(ranked[0]!.candidate.ordinal, 129, "winner must be beyond two FETCH batches");
        assert.equal(ranked[0]!.candidate.content, "Original snapshot");
        assert.equal(Number(first.db.prepare("SELECT count(*) AS count FROM pg_cursors WHERE name LIKE 'agat_cursor_%'").get()!.count), 0);
        first.db.exec("COMMIT");
        assert.equal(await updating, null);
        assert.equal(first.db.prepare("SELECT name FROM projects WHERE id = ?").get(projectId)!.name, "Changed by concurrent writer");
      } finally {
        first.close();
        if (updating) await updating;
        await writer.end();
        fs.rmSync(artifacts, { recursive: true, force: true });
      }
    });
  });

  it("applies an operator budget above 5000 to real PostgreSQL retrieval without worker override or partial writes", () => {
    runWithPostgresSystemScope(() => {
      const suffix = randomUUID().slice(0, 8);
      const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), "agat-pg-rag-budget-"));
      const configured = store(`rag-budget-${suffix}`, artifacts, 6001);
      const standard = store(`rag-default-${suffix}`, artifacts);
      const projectId = `rag-budget-${suffix}`;
      let runId: string | undefined;
      try {
        configured.createProject({ id: projectId, name: projectId, homeRegion: cellRegion,
          allowedRegions: [cellRegion], residencyDomain: cellResidencyDomain });
        const model = `rag-budget-${suffix}`;
        const agent = configured.createAgent({ name: "Budget fixture", role: "Test", systemPrompt: "Fixture", model }, projectId);
        const node = configured.registerNode({ enrollmentToken: "test", name: model, platform: "test",
          models: [model], embeddingModels: [model], maxConcurrency: 1,
          region: cellRegion, residencyDomain: cellResidencyDomain }).id;
        const collection = (name: string) => String(configured.createKnowledgeCollection({ name,
          embeddingModel: model, chunkSize: 400, chunkOverlap: 0 }, projectId).id);
        const main = collection("Main"), extra = collection("Extra");
        const target = configured.ingestKnowledgeDocument(main, { name: "Old best", content: "Old exact source." }, projectId);
        for (let index = 0; index < 2; index++) {
          configured.ingestKnowledgeDocument(main, { name: `Filler ${index}`, content: "A".repeat(3000 * 400) }, projectId);
        }
        configured.ingestKnowledgeDocument(extra, { name: "Extra", content: "Over the operator budget." }, projectId);
        for (let lease; (lease = configured.leaseKnowledgeEmbedding(node));) {
          configured.completeKnowledgeEmbedding(node, lease.leaseId, lease.chunks.map(chunk => ({ chunkId: chunk.id,
            embedding: lease.document.id === target.id ? [1, 0] : [0, 1] })));
        }
        configured.db.prepare("UPDATE knowledge_chunks SET embedded_at = '2020-01-01T00:00:00.000Z' WHERE document_id = ?").run(String(target.id));
        const run = configured.createRun({ name: "Budget", input: "Synthetic query", agentIds: [String(agent.id)],
          approvalRequired: false, knowledgeCollectionIds: [main, extra] }, projectId);
        runId = run.id;
        const lease = configured.leaseNext(node)!;
        const query = (ids: string[]) => ({ embeddingModel: model, collectionIds: ids, vector: [1, 0], topK: 1, maxCandidates: 10000 });
        assert.throws(() => standard.searchKnowledge(node, lease.leaseId, { queries: [query([main])] }), /retrieval.*5000/);
        assert.equal(configured.getRunKnowledgeSources(run.id, projectId)!.length, 0);
        const result = configured.searchKnowledge(node, lease.leaseId, { queries: [query([main])] });
        assert.equal(result.hits[0]!.provenance.documentId, target.id);
        assert.equal(result.hits[0]!.marker, "K1");
        assert.throws(() => configured.searchKnowledge(node, lease.leaseId,
          { queries: [query([main]), query([main, extra])] }), /retrieval.*6001/);
        const saved = configured.db.prepare("SELECT queries_json FROM knowledge_retrievals WHERE run_id = ?").all(run.id);
        assert.equal(saved.length, 1);
        assert.equal(JSON.parse(String(saved[0]!.queries_json))[0].candidateLimit, 6001);
        assert.equal(configured.searchKnowledge(node, lease.leaseId, { queries: [query([main])] }).hits[0]!.marker, "K2");
        assert.equal(configured.getRunKnowledgeSources(run.id, projectId)!.length, 2);
      } finally {
        if (runId) configured.cancelRun(runId, projectId);
        configured.close(); standard.close();
        fs.rmSync(artifacts, { recursive: true, force: true });
      }
    });
  });

  it("rejects expired retrieval leases before maintenance and accepts only the replacement lease across replicas", () => {
    runWithPostgresSystemScope(() => {
      const suffix = randomUUID().slice(0, 8);
      const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), "agat-pg-rag-lease-"));
      const first = store(`rag-lease-a-${suffix}`, artifacts);
      const second = store(`rag-lease-b-${suffix}`, artifacts);
      try {
        first.updateScheduler("parallel", 10);
        const projectId = `rag-lease-${suffix}`;
        first.createProject({ id: projectId, name: projectId, homeRegion: cellRegion,
          allowedRegions: [cellRegion], residencyDomain: cellResidencyDomain });
        const model = `rag-lease-${suffix}`;
        const agent = first.createAgent({ name: "Lease fixture", role: "Test", systemPrompt: "Fixture", model }, projectId);
        const node = first.registerNode({ enrollmentToken: "test", name: model, platform: "test",
          models: [model], embeddingModels: [model], maxConcurrency: 1,
          region: cellRegion, residencyDomain: cellResidencyDomain }).id;
        const collection = String(first.createKnowledgeCollection({ name: "Lease source", embeddingModel: model }, projectId).id);
        first.ingestKnowledgeDocument(collection, { name: "Source", content: "Recorded lease fixture." }, projectId);
        const embedding = first.leaseKnowledgeEmbedding(node)!;
        assert.ok(embedding);
        first.completeKnowledgeEmbedding(node, embedding.leaseId, embedding.chunks.map(chunk => ({ chunkId: chunk.id, embedding: [1, 0] })));
        const run = first.createRun({ name: "Lease", input: "Synthetic query", agentIds: [String(agent.id)],
          approvalRequired: false, knowledgeCollectionIds: [collection] }, projectId);
        const lease = first.leaseNext(node)!;
        assert.ok(lease); assert.equal(lease.run.id, run.id);
        const request = { queries: [{ embeddingModel: model, collectionIds: [collection], vector: [1, 0], topK: 1 }] };
        first.db.prepare("UPDATE stages SET lease_expires_at = ? WHERE lease_id = ?")
          .run(new Date(Date.now() - 1000).toISOString(), lease.leaseId);
        assert.equal(second.db.prepare("SELECT status FROM stages WHERE lease_id = ?").get(lease.leaseId)!.status, "running");
        assert.equal(second.renewLease(node, lease.leaseId), false);
        assert.throws(() => second.searchKnowledge(node, lease.leaseId, request), /Активная stage-аренда не найдена/);
        assert.equal(first.getRunKnowledgeSources(run.id, projectId)!.length, 0);
        assert.equal(first.db.prepare("SELECT id FROM knowledge_retrievals WHERE run_id = ?").all(run.id).length, 0);
        assert.equal((first.getRunTrace(run.id, projectId)!.events as Array<{ type: string }>).filter(e => e.type === "knowledge.retrieved").length, 0);
        second.maintenanceTick();
        const replacement = first.leaseNext(node)!;
        assert.ok(replacement); assert.equal(replacement.run.id, run.id); assert.notEqual(replacement.leaseId, lease.leaseId);
        assert.throws(() => second.searchKnowledge(node, lease.leaseId, request), /Активная stage-аренда не найдена/);
        assert.equal(second.searchKnowledge(node, replacement.leaseId, request).hits[0]!.marker, "K1");
        assert.equal(first.getRunKnowledgeSources(run.id, projectId)!.length, 1);
        assert.equal((first.getRunTrace(run.id, projectId)!.events as Array<{ type: string }>).filter(e => e.type === "knowledge.retrieved").length, 1);
        first.completeLease(node, replacement.leaseId, "completed", []);
      } finally {
        first.close(); second.close();
        fs.rmSync(artifacts, { recursive: true, force: true });
      }
    });
  });

  it("isolates whole retrieval transactions across workers without duplicate markers or partial writes", async () => {
    await runWithPostgresSystemScope(async () => {
      const suffix = randomUUID().slice(0, 8);
      const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), "agat-pg-rag-executor-"));
      const first = store(`rag-executor-${suffix}`, artifacts);
      const executors: KnowledgeSearchExecutor[] = [];
      try {
        first.updateScheduler("parallel", 10);
        const projectId = `rag-executor-${suffix}`;
        first.createProject({ id: projectId, name: projectId, homeRegion: cellRegion,
          allowedRegions: [cellRegion], residencyDomain: cellResidencyDomain });
        const model = `rag-executor-${suffix}`;
        const agent = first.createAgent({ name: "Executor fixture", role: "Test", systemPrompt: "Fixture", model }, projectId);
        const node = first.registerNode({ enrollmentToken: "test", name: model, platform: "test",
          models: [model], embeddingModels: [model], maxConcurrency: 1,
          region: cellRegion, residencyDomain: cellResidencyDomain });
        const collection = String(first.createKnowledgeCollection({ name: "Executor source", embeddingModel: model }, projectId).id);
        const document = first.ingestKnowledgeDocument(collection, { name: "Source", content: "Concurrent executor fixture." }, projectId);
        const embedding = first.leaseKnowledgeEmbedding(node.id)!; assert.ok(embedding);
        first.completeKnowledgeEmbedding(node.id, embedding.leaseId, embedding.chunks.map(chunk => ({ chunkId: chunk.id, embedding: [1, 0] })));
        const run = first.createRun({ name: "Executor", input: "Synthetic query", agentIds: [String(agent.id)],
          approvalRequired: false, knowledgeCollectionIds: [collection] }, projectId);
        const lease = first.leaseNext(node.id)!; assert.equal(lease.run.id, run.id);
        for (let index = 0; index < 2; index++) executors.push(await KnowledgeSearchExecutor.create(":postgresql:",
          storeOptions(`rag-executor-${index}-${suffix}`, artifacts)));
        const query = { embeddingModel: model, collectionIds: [collection], vector: [1, 0], topK: 1 };
        const search = (index: number, queries = [query]) => executors[index]!.search(node.token, node.id, lease.leaseId, { queries });
        const results = await Promise.all([search(0), search(1)]);
        assert.deepEqual(results.map(row => row.hits[0]!.marker).sort(), ["K1", "K2"]);
        assert.ok(results.every(row => row.hits[0]!.provenance.documentId === document.id));
        await assert.rejects(search(0, [query, { ...query, vector: [1, 0, 0] }]), /Размерность retrieval/);
        assert.equal(first.getRunKnowledgeSources(run.id, projectId)!.length, 2);
        assert.equal((await search(1)).hits[0]!.marker, "K3");
        assert.equal((first.getRunTrace(run.id, projectId)!.events as Array<{ type: string }>).filter(e => e.type === "knowledge.retrieved").length, 3);
        first.db.prepare("UPDATE nodes SET credential_state = 'revoked' WHERE id = ?").run(node.id);
        await assert.rejects(search(0), /Токен узла недействителен/);
        assert.equal(first.getRunKnowledgeSources(run.id, projectId)!.length, 3);
        first.completeLease(node.id, lease.leaseId, "completed", []);
      } finally {
        await Promise.all(executors.map(executor => executor.close()));
        first.close(); fs.rmSync(artifacts, { recursive: true, force: true });
      }
    });
  });

  for (const table of ["knowledge_retrievals", "events"] as const) it(`rolls back retrieval when its lease expires while writing ${table}`, async context => {
    await runWithPostgresSystemScope(async () => {
      const f = await retrievalLeaseRaceFixture();
      try {
        const expires = Date.now() + 5_000;
        f.first.db.prepare("UPDATE stages SET lease_expires_at = ? WHERE lease_id = ?").run(new Date(expires).toISOString(), f.lease.leaseId);
        await f.writer.query("BEGIN");
        // SHARE permits the source SELECT but holds the later INSERT. In the
        // events case the retrieval INSERT has already completed in its tx.
        await f.writer.query(`LOCK TABLE ${table} IN SHARE MODE`);
        const pending = f.search().then(value => ({ value }), error => ({ error }));
        await f.waitForLock(new RegExp(`^\\s*INSERT INTO ${table}\\s*\\(`));
        assert.ok(Date.now() < expires, "The intended late-write wait started after lease expiry");
        await delay(Math.max(0, expires - Date.now() + 50));
        await f.writer.query("ROLLBACK");
        const result = await within(pending, 5_000, "Retrieval did not finish after the late-write lock was released");
        const rows = f.first.db.prepare("SELECT id FROM knowledge_retrievals WHERE run_id = ?").all(f.run.id);
        const events = (f.first.getRunTrace(f.run.id, f.project)!.events as Array<{ type: string }>).filter(e => e.type === "knowledge.retrieved");
        context.diagnostic(`After the expired write: ${rows.length} durable retrievals, ${events.length} retrieval events`);
        assert.ok("error" in result, "Retrieval accepted a lease that expired while its persistence was blocked");
        assert.match(result.error.message, /Активная stage-аренда не найдена/);
        assert.equal(rows.length, 0); assert.equal(events.length, 0);
        assert.equal(f.first.getRunKnowledgeSources(f.run.id, f.project)!.length, 0);
        f.first.maintenanceTick();
        const replacement = f.first.leaseNext(f.node.id)!; assert.ok(replacement);
        assert.notEqual(replacement.leaseId, f.lease.leaseId);
        assert.equal((await f.search(replacement.leaseId)).hits[0]!.marker, "K1");
        f.first.completeLease(f.node.id, replacement.leaseId, "completed", []);
      } finally { await f.close(); }
    });
  });

  it("rejects a lease that expires while retrieval waits for its run lock", async () => {
    await runWithPostgresSystemScope(async () => {
      const f = await retrievalLeaseRaceFixture();
      try {
        const expires = Date.now() + 5_000;
        f.first.db.prepare("UPDATE stages SET lease_expires_at = ? WHERE lease_id = ?").run(new Date(expires).toISOString(), f.lease.leaseId);
        await f.writer.query("BEGIN");
        await f.writer.query("SELECT id FROM runs WHERE id = $1 FOR UPDATE", [f.run.id]);
        const pending = f.search().then(value => ({ value }), error => ({ error }));
        await f.waitForLock();
        await delay(Math.max(0, expires - Date.now() + 50));
        await f.writer.query("COMMIT");
        const result = await pending;
        assert.ok("error" in result, "Retrieval accepted a lease that expired during its SQL lock wait");
        assert.match(result.error.message, /Активная stage-аренда не найдена/);
        assert.equal(f.first.getRunKnowledgeSources(f.run.id, f.project)!.length, 0);
        assert.equal(f.first.db.prepare("SELECT id FROM knowledge_retrievals WHERE run_id = ?").all(f.run.id).length, 0);
        assert.equal((f.first.getRunTrace(f.run.id, f.project)!.events as Array<{ type: string }>).filter(e => e.type === "knowledge.retrieved").length, 0);
        f.first.maintenanceTick();
        const replacement = f.first.leaseNext(f.node.id)!; assert.ok(replacement);
        assert.notEqual(replacement.leaseId, f.lease.leaseId);
        assert.equal((await f.search(replacement.leaseId)).hits[0]!.marker, "K1");
        f.first.completeLease(f.node.id, replacement.leaseId, "completed", []);
      } finally { await f.close(); }
    });
  });

  it("rejects a lease cancelled by a transaction that retrieval had to wait for", async () => {
    await runWithPostgresSystemScope(async () => {
      const f = await retrievalLeaseRaceFixture();
      try {
        await f.writer.query("BEGIN");
        await f.writer.query("UPDATE stages SET status = 'cancelled', node_id = NULL, lease_id = NULL, lease_expires_at = NULL WHERE lease_id = $1", [f.lease.leaseId]);
        await f.writer.query("UPDATE runs SET status = 'cancelled' WHERE id = $1", [f.run.id]);
        const pending = f.search().then(value => ({ value }), error => ({ error }));
        await f.waitForLock();
        await f.writer.query("COMMIT");
        const result = await pending;
        assert.ok("error" in result, "Retrieval accepted the stage snapshot from before cancellation committed");
        assert.match(result.error.message, /Активная stage-аренда не найдена/);
        assert.equal(f.first.getRunKnowledgeSources(f.run.id, f.project)!.length, 0);
        assert.equal(f.first.db.prepare("SELECT id FROM knowledge_retrievals WHERE run_id = ?").all(f.run.id).length, 0);
        assert.equal((f.first.getRunTrace(f.run.id, f.project)!.events as Array<{ type: string }>).filter(e => e.type === "knowledge.retrieved").length, 0);
      } finally { await f.close(); }
    });
  });

  it("rejects a lease that expires after validation while the knowledge index is blocked", async () => {
    await runWithPostgresSystemScope(async () => {
      const f = await retrievalLeaseRaceFixture();
      try {
        const expires = Date.now() + 5_000;
        f.first.db.prepare("UPDATE stages SET lease_expires_at = ? WHERE lease_id = ?").run(new Date(expires).toISOString(), f.lease.leaseId);
        await f.writer.query("BEGIN");
        await f.writer.query("LOCK TABLE knowledge_chunks IN ACCESS EXCLUSIVE MODE");
        const pending = f.search().then(value => ({ value }), error => ({ error }));
        await f.waitForLock(/knowledge_chunks/);
        await delay(Math.max(0, expires - Date.now() + 50));
        await f.writer.query("COMMIT");
        const result = await pending;
        assert.ok("error" in result, "Retrieval persisted after lease expiry during the index read");
        assert.match(result.error.message, /Активная stage-аренда не найдена/);
        assert.equal(f.first.db.prepare("SELECT id FROM knowledge_retrievals WHERE run_id = ?").all(f.run.id).length, 0);
        assert.equal((f.first.getRunTrace(f.run.id, f.project)!.events as Array<{ type: string }>).filter(e => e.type === "knowledge.retrieved").length, 0);
      } finally { await f.close(); }
    });
  });

  it("permits a concurrent lease renewal while retrieval is reading its index", async () => {
    await runWithPostgresSystemScope(async () => {
      const f = await retrievalLeaseRaceFixture();
      try {
        await f.writer.query("BEGIN");
        await f.writer.query("LOCK TABLE knowledge_chunks IN ACCESS EXCLUSIVE MODE");
        const pending = f.search();
        const observed = pending.then(value => ({ value }), error => ({ error }));
        await f.waitForLock(/knowledge_chunks/);
        const expires = new Date(Date.now() + 180_000).toISOString();
        const renewed = await f.writer.query(`UPDATE stages SET lease_expires_at = $1
          WHERE node_id = $2 AND lease_id = $3 AND status = 'running' AND lease_expires_at > $4`,
        [expires, f.node.id, f.lease.leaseId, new Date().toISOString()]);
        assert.equal(renewed.rowCount, 1, "Ranking must not hold a stage row lock that prevents renewal");
        await f.writer.query("COMMIT");
        const result = await observed; assert.ok("value" in result);
        assert.equal(result.value.hits[0]!.marker, "K1");
        assert.equal(f.first.db.prepare("SELECT lease_expires_at FROM stages WHERE lease_id = ?").get(f.lease.leaseId)!.lease_expires_at, expires);
        f.first.completeLease(f.node.id, f.lease.leaseId, "completed", []);
      } finally { await f.close(); }
    });
  });

  it("starts isolated retrieval from coordinator main within its pool budget and fails readiness after a deadline", async () => {
    await runWithPostgresSystemScope(async () => {
      const f = await retrievalLeaseRaceFixture();
      let child: Awaited<ReturnType<typeof startCoordinatorProcess>> | undefined;
      const instance = `rag-main-${randomUUID().slice(0, 8)}`;
      const names = [`agat-${instance}-system`, `agat-${instance}-tenant`, `agat-retrieval-${instance}-system`, `agat-retrieval-${instance}-tenant`];
      try {
        child = await startCoordinatorProcess(coordinatorProcessEnvironment(instance, f.artifacts));
        const base = `http://127.0.0.1:${child.port}/api/v1`;
        const health = await fetch(`${base}/health`); assert.equal(health.status, 200);
        const body = await health.json() as { knowledgeSearch: Record<string, unknown> };
        assert.deepEqual(body.knowledgeSearch, { execution: "isolated", active: 0, queued: 0, maxPending: 4, accepting: true });
        const connections = await f.writer.query("SELECT application_name FROM pg_stat_activity WHERE application_name = ANY($1::text[]) ORDER BY application_name", [names]);
        assert.deepEqual(connections.rows.map(row => row.application_name).sort(), names.sort(), "Each role has one main and one retrieval connection within poolMax=2");
        const search = () => fetch(`${base}/leases/${f.lease.leaseId}/knowledge/search`, { method: "POST",
          headers: { authorization: `Bearer ${f.node.token}`, "content-type": "application/json" }, body: JSON.stringify(f.request) });
        const successful = await search(); assert.equal(successful.status, 200);
        assert.equal((await successful.json() as { hits: Array<{ marker: string }> }).hits[0]!.marker, "K1");
        assert.equal(f.first.getRunKnowledgeSources(f.run.id, f.project)!.length, 1);
        await f.writer.query("BEGIN");
        await f.writer.query("SELECT id FROM runs WHERE id = $1 FOR UPDATE", [f.run.id]);
        const timedOut = await search(); assert.equal(timedOut.status, 504); await timedOut.json();
        await f.writer.query("ROLLBACK");
        const unavailable = await fetch(`${base}/health`); assert.equal(unavailable.status, 503);
        const status = await unavailable.json() as { status: string; knowledgeSearch: { accepting: boolean } };
        assert.equal(status.status, "degraded"); assert.equal(status.knowledgeSearch.accepting, false);
        const refused = await search(); assert.equal(refused.status, 503); await refused.json();
        assert.deepEqual(await child.stop(), { code: 0, signal: null });
        const remaining = await f.writer.query("SELECT count(*)::integer AS count FROM pg_stat_activity WHERE application_name = ANY($1::text[])", [names]);
        assert.equal(remaining.rows[0].count, 0, "Shutdown releases both coordinator and retrieval pools");
      } finally {
        await f.writer.query("ROLLBACK"); await child?.stop(); await f.close();
      }
    });
  });

  it("enforces the retrieval deadline while main is blocked renewing the same stage", async () => {
    await runWithPostgresSystemScope(async () => {
      const f = await retrievalLeaseRaceFixture();
      const instance = `rag-control-lock-${randomUUID().slice(0, 8)}`;
      let child: Awaited<ReturnType<typeof startCoordinatorProcess>> | undefined;
      const pending: Promise<unknown>[] = [];
      try {
        child = await startCoordinatorProcess(coordinatorProcessEnvironment(instance, f.artifacts));
        const base = `http://127.0.0.1:${child.port}/api/v1`;
        const headers = { authorization: `Bearer ${f.node.token}`, "content-type": "application/json" };
        const post = (route: string, body: unknown) => {
          const result = fetch(`${base}${route}`, { method: "POST", headers, body: JSON.stringify(body), signal: AbortSignal.timeout(10_000) })
            .then(async response => ({ status: response.status, body: await response.text() }));
          pending.push(result); return result;
        };
        await f.writer.query("BEGIN");
        await f.writer.query("SELECT id FROM runs WHERE id = $1 FOR UPDATE", [f.run.id]);
        const started = performance.now();
        const search = post(`/leases/${f.lease.leaseId}/knowledge/search`, f.request);
        await f.waitForLock(/FROM runs .*FOR UPDATE/, `agat-retrieval-${instance}-system`);
        const queued = post(`/leases/${f.lease.leaseId}/knowledge/search`, f.request);
        await eventually(async () => {
          const response = await fetch(`${base}/health`, { signal: AbortSignal.timeout(2_000) });
          const status = await response.json() as { knowledgeSearch: { queued: number } };
          return status.knowledgeSearch.queued === 1;
        }, "The second retrieval was not queued before main's lock wait");
        const renewal = post(`/leases/${f.lease.leaseId}/renew`, {});
        await f.waitForLock(/SELECT id, lease_expires_at FROM stages[\s\S]*FOR UPDATE/, `agat-${instance}-system`);
        const health = fetch(`${base}/health`, { signal: AbortSignal.timeout(10_000) }).then(async response => ({ status: response.status, body: await response.json() }));
        pending.push(health);
        // Parent is independent of main's blocked event loop. Keep the run lock
        // beyond the 1 s deadline; only cleanup may release it on failure.
        const result = await within(Promise.all([search, renewal, health, queued]), 2_500,
          "Retrieval deadline and health waited for the external run lock while main renewed its stage");
        assert.ok(performance.now() - started < 3_500);
        assert.equal(result[0].status, 504);
        assert.equal(result[1].status, 204);
        assert.equal(result[2].status, 503);
        assert.equal(result[3].status, 504);
        assert.equal((await post(`/leases/${f.lease.leaseId}/knowledge/search`, f.request)).status, 503);
        await eventually(async () => {
          await f.writer.query("SELECT pg_stat_clear_snapshot()");
          const connections = await f.writer.query("SELECT count(*)::integer AS count FROM pg_stat_activity WHERE application_name = ANY($1::text[])",
            [[`agat-retrieval-${instance}-system`, `agat-retrieval-${instance}-tenant`]]);
          return connections.rows[0].count === 0;
        }, "Expired retrieval connections survived while the external run lock was held");
        await f.writer.query("ROLLBACK");
        assert.equal(f.first.getRunKnowledgeSources(f.run.id, f.project)!.length, 0);
        assert.equal(f.first.db.prepare("SELECT id FROM knowledge_retrievals WHERE run_id = ?").all(f.run.id).length, 0);
        assert.equal((f.first.getRunTrace(f.run.id, f.project)!.events as Array<{ type: string }>).filter(e => e.type === "knowledge.retrieved").length, 0);
        assert.deepEqual(await child.stop(), { code: 0, signal: null });
        child = await startCoordinatorProcess(coordinatorProcessEnvironment(instance, f.artifacts));
        assert.equal(f.first.getRunKnowledgeSources(f.run.id, f.project)!.length, 0, "Restart must not replay either request");
        const recovered = await fetch(`http://127.0.0.1:${child.port}/api/v1/leases/${f.lease.leaseId}/knowledge/search`,
          { method: "POST", headers, body: JSON.stringify(f.request), signal: AbortSignal.timeout(5_000) });
        assert.equal(recovered.status, 200);
        assert.equal((await recovered.json() as { hits: Array<{ marker: string }> }).hits[0]!.marker, "K1");
      } finally {
        await f.writer.query("ROLLBACK"); await child?.stop(); await Promise.allSettled(pending); await f.close();
      }
    });
  });

  for (const fault of ["disconnect", "withhold"] as const) it(`fails closed when a committed retrieval loses its COMMIT acknowledgement (${fault})`, async context => {
    await runWithPostgresSystemScope(async () => {
      const f = await retrievalLeaseRaceFixture();
      const instance = `rag-commit-${randomUUID().slice(0, 8)}`;
      const proxy = await interceptRetrievalCommit(systemUrl, `agat-retrieval-${instance}-system`);
      let child: Awaited<ReturnType<typeof startCoordinatorProcess>> | undefined;
      const pending: Promise<unknown>[] = [];
      try {
        child = await startCoordinatorProcess(coordinatorProcessEnvironment(instance, f.artifacts, {
          AGAT_POSTGRES_URL: proxy.route(systemUrl), AGAT_POSTGRES_TENANT_URL: proxy.route(tenantUrl),
          AGAT_KNOWLEDGE_SEARCH_TIMEOUT_MS: fault === "disconnect" ? "10000" : "1000" }));
        const base = `http://127.0.0.1:${child.port}/api/v1`;
        const headers = { authorization: `Bearer ${f.node.token}`, "content-type": "application/json" };
        const search = () => {
          const result = fetch(`${base}/leases/${f.lease.leaseId}/knowledge/search`, { method: "POST", headers,
            body: JSON.stringify(f.request), signal: AbortSignal.timeout(15_000) })
            .then(async response => ({ status: response.status, body: await response.json() }));
          pending.push(result); return result;
        };
        const started = performance.now();
        const active = search();
        await within(Promise.race([proxy.committed, active.then(result => {
          throw new Error(`Search returned HTTP ${result.status} before the COMMIT fault was established`);
        })]), 5_000, "Fault fixture did not observe a real successful COMMIT");
        context.diagnostic(`Successful COMMIT observed after ${Math.round(performance.now() - started)} ms`);
        assert.equal(f.first.getRunKnowledgeSources(f.run.id, f.project)!.length, 1, "Independent connection must see committed K1 before its acknowledgement is lost");
        const queued = search();
        await eventually(async () => {
          const response = await fetch(`${base}/health`, { signal: AbortSignal.timeout(2_000) });
          return (await response.json() as { knowledgeSearch: { queued: number } }).knowledgeSearch.queued === 1;
        }, "Queued request was not admitted before the COMMIT connection failure");
        if (fault === "disconnect") proxy.disconnect();
        const expectedStatus = fault === "disconnect" ? 503 : 504;
        assert.equal((await within(active, 2_500, "Lost COMMIT acknowledgement did not produce a bounded operational failure")).status, expectedStatus);
        assert.equal((await queued).status, expectedStatus);
        const health = await fetch(`${base}/health`); assert.equal(health.status, 503); await health.json();
        assert.equal((await search()).status, 503);
        assert.equal(f.first.getRunKnowledgeSources(f.run.id, f.project)!.length, 1);
        assert.deepEqual(proxy.errors, []);
        assert.deepEqual(await child.stop(), { code: 0, signal: null });
        child = await startCoordinatorProcess(coordinatorProcessEnvironment(instance, f.artifacts));
        assert.equal(f.first.getRunKnowledgeSources(f.run.id, f.project)!.length, 1, "Restart must retain K1 without replaying active or queued requests");
        const explicit = await fetch(`http://127.0.0.1:${child.port}/api/v1/leases/${f.lease.leaseId}/knowledge/search`,
          { method: "POST", headers, body: JSON.stringify(f.request), signal: AbortSignal.timeout(5_000) });
        assert.equal(explicit.status, 200);
        assert.equal((await explicit.json() as { hits: Array<{ marker: string }> }).hits[0]!.marker, "K2");
      } catch (error) {
        context.diagnostic(child?.diagnostic() ?? "Main did not start");
        throw error;
      } finally {
        await proxy.close(); await child?.stop(); await Promise.allSettled(pending); await f.close();
      }
    });
  });

  it("reports an uncertain sync COMMIT as operational failure and replaces its broken pooled connection", async context => {
    await runWithPostgresSystemScope(async () => {
      const f = await retrievalLeaseRaceFixture();
      const instance = `rag-sync-commit-${randomUUID().slice(0, 8)}`;
      const proxy = await interceptRetrievalCommit(systemUrl, `agat-${instance}-system`);
      let child: Awaited<ReturnType<typeof startCoordinatorProcess>> | undefined;
      let pending: Promise<Response> | undefined;
      try {
        child = await startCoordinatorProcess(coordinatorProcessEnvironment(instance, f.artifacts, {
          AGAT_POSTGRES_URL: proxy.route(systemUrl), AGAT_POSTGRES_TENANT_URL: proxy.route(tenantUrl), AGAT_KNOWLEDGE_SEARCH_EXECUTION: "sync" }));
        const base = `http://127.0.0.1:${child.port}/api/v1`;
        const search = () => fetch(`${base}/leases/${f.lease.leaseId}/knowledge/search`, { method: "POST",
          headers: { authorization: `Bearer ${f.node.token}`, "content-type": "application/json" },
          body: JSON.stringify(f.request), signal: AbortSignal.timeout(10_000) });
        pending = search();
        await within(proxy.committed, 5_000, "Sync fault did not reach a successful COMMIT");
        assert.equal(f.first.getRunKnowledgeSources(f.run.id, f.project)!.length, 1);
        proxy.disconnect();
        const rejected = await within(pending, 2_500, "Sync main did not recover from its broken SQL connection");
        assert.equal(rejected.status, 503);
        assert.match((await rejected.json() as { error: string }).error, /COMMIT неизвестен/);
        const health = await fetch(`${base}/health`); assert.equal(health.status, 200); await health.json();
        assert.equal(f.first.getRunKnowledgeSources(f.run.id, f.project)!.length, 1);
        const explicit = await search(); assert.equal(explicit.status, 200);
        assert.equal((await explicit.json() as { hits: Array<{ marker: string }> }).hits[0]!.marker, "K2");
        assert.deepEqual(proxy.errors, []);
        assert.deepEqual(await child.stop(), { code: 0, signal: null });
      } catch (error) { context.diagnostic(child?.diagnostic() ?? "Main did not start"); throw error; }
      finally { await proxy.close(); await child?.stop(); await pending?.catch(() => {}); await f.close(); }
    });
  });

  it("rejects queued retrieval on SIGTERM, preserves active work and recovers without replay", async () => {
    await runWithPostgresSystemScope(async () => {
      const f = await retrievalLeaseRaceFixture();
      const instance = `rag-drain-${randomUUID().slice(0, 8)}`;
      const env = coordinatorProcessEnvironment(instance, f.artifacts, {
        AGAT_KNOWLEDGE_SEARCH_MAX_PENDING: "2", AGAT_KNOWLEDGE_SEARCH_TIMEOUT_MS: "10000" });
      let child: Awaited<ReturnType<typeof startCoordinatorProcess>> | undefined;
      const pending: Promise<unknown>[] = [];
      const retrievalCount = () => Number(f.first.db.prepare("SELECT COUNT(*) AS count FROM knowledge_retrievals WHERE run_id = ?").get(f.run.id)!.count);
      try {
        child = await startCoordinatorProcess(env);
        let base = `http://127.0.0.1:${child.port}/api/v1`;
        const headers = { authorization: `Bearer ${f.node.token}`, "content-type": "application/json" };
        const search = () => {
          const result = fetch(`${base}/leases/${f.lease.leaseId}/knowledge/search`, { method: "POST", headers,
            body: JSON.stringify(f.request), signal: AbortSignal.timeout(15_000) })
            .then(async response => ({ status: response.status, body: await response.json() as { hits?: Array<{ marker: string }> } }))
            .catch(error => ({ status: 0, body: { hits: undefined, error: String(error) } }));
          pending.push(result); return result;
        };
        const beforeMaintenance = String(f.first.db.prepare("SELECT last_seen FROM coordinator_replicas WHERE instance_id = ?").get(instance)!.last_seen);
        await f.writer.query("BEGIN");
        await f.writer.query("LOCK TABLE knowledge_chunks IN ACCESS EXCLUSIVE MODE");
        const active = search();
        await f.waitForLock(/knowledge_chunks/, `agat-retrieval-${instance}-system`);
        const queued = search();
        await eventually(async () => {
          const response = await fetch(`${base}/health`, { signal: AbortSignal.timeout(2_000) });
          assert.equal(response.status, 200, "A full queue must not trigger failed readiness or restart");
          const body = await response.json() as { knowledgeSearch: { active: number; queued: number; accepting: boolean } };
          return body.knowledgeSearch.active === 1 && body.knowledgeSearch.queued === 1 && body.knowledgeSearch.accepting;
        }, "Both requests were not admitted before SIGTERM");
        assert.equal((await search()).status, 429, "The third request is refused before shutdown");
        const heartbeat = await fetch(`${base}/workers/heartbeat`, { method: "POST", headers, body: "{}", signal: AbortSignal.timeout(2_000) });
        assert.equal(heartbeat.status, 204);
        const renewal = await fetch(`${base}/leases/${f.lease.leaseId}/renew`, { method: "POST", headers, body: "{}", signal: AbortSignal.timeout(2_000) });
        assert.equal(renewal.status, 204);
        await eventually(async () => String(f.first.db.prepare("SELECT last_seen FROM coordinator_replicas WHERE instance_id = ?").get(instance)!.last_seen) > beforeMaintenance,
          "Real coordinator maintenance did not continue during blocked ranking");
        assert.equal(retrievalCount(), 0);

        child.signal();
        const rejected = await within(queued, 2_500, "SIGTERM left queued retrieval waiting for the active database operation");
        assert.equal(rejected.status, 503, "Queued retrieval must be rejected before the index lock is released");
        assert.equal(retrievalCount(), 0);
        // A repeated shutdown request must not close the active store prematurely.
        child.signal();
        await f.writer.query("ROLLBACK");
        const completed = await active; assert.equal(completed.status, 200);
        assert.equal(completed.body.hits?.[0]?.marker, "K1");
        assert.deepEqual(await within(child.closed, 5_000, "Coordinator did not drain and exit"), { code: 0, signal: null });
        assert.equal(retrievalCount(), 1);

        child = await startCoordinatorProcess(env);
        base = `http://127.0.0.1:${child.port}/api/v1`;
        const recovered = await fetch(`${base}/health`); assert.equal(recovered.status, 200); await recovered.json();
        assert.equal(retrievalCount(), 1, "Restart must not replay the refused or queued requests");
        const explicit = await search(); assert.equal(explicit.status, 200);
        assert.equal(explicit.body.hits?.[0]?.marker, "K2");
        assert.equal(retrievalCount(), 2);
        assert.deepEqual(await child.stop(), { code: 0, signal: null });
      } finally {
        await f.writer.query("ROLLBACK"); await child?.stop(); await Promise.allSettled(pending); await f.close();
      }
    });
  });

  it("closes UI and A2A streams on SIGTERM without cancelling durable tasks and resumes events after restart", async () => {
    await runWithPostgresSystemScope(async () => {
      const f = await retrievalLeaseRaceFixture();
      const instance = `stream-stop-${randomUUID().slice(0, 8)}`;
      const endpoint = f.first.createA2AEndpoint({ agentId: f.agentId }, f.project);
      const endpointId = String(endpoint.endpoint.id);
      const env = coordinatorProcessEnvironment(instance, f.artifacts, {
        AGAT_A2A_ENABLED: "true", AGAT_A2A_PUBLIC_BASE_URL: "http://127.0.0.1:8787" });
      const client = new AbortController();
      let child: Awaited<ReturnType<typeof startCoordinatorProcess>> | undefined;
      const pending: Promise<unknown>[] = [];
      const a2aHeaders = { authorization: `Bearer ${endpoint.accessToken}`, "a2a-version": "1.0", "content-type": "application/a2a+json" };
      const message = (id: string, returnImmediately: boolean) => JSON.stringify({
        message: { messageId: id, role: "ROLE_USER", parts: [{ text: "Shutdown fixture", mediaType: "text/plain" }] },
        configuration: { returnImmediately } });
      try {
        child = await startCoordinatorProcess(env);
        let origin = `http://127.0.0.1:${child.port}`;
        const ui = await fetch(`${origin}/api/v1/events`, { headers: { "x-agat-project-id": f.project }, signal: client.signal });
        assert.equal(ui.status, 200);
        const uiReader = ui.body!.getReader();
        let firstEvents = "";
        while (!/^id: \d+\n/m.test(firstEvents)) {
          const part = await within(uiReader.read(), 2_000, "UI event cursor was not delivered");
          assert.equal(part.done, false); firstEvents += Buffer.from(part.value!).toString("utf8");
        }
        const cursor = Math.max(...[...firstEvents.matchAll(/^id: (\d+)\n/gm)].map(match => Number(match[1])));
        const streamed = await fetch(`${origin}/a2a/v1/endpoints/${endpointId}/message:stream`, {
          method: "POST", headers: a2aHeaders, body: message("stream-shutdown", true), signal: client.signal });
        assert.equal(streamed.status, 200);
        const a2aReader = streamed.body!.getReader();
        const firstTask = await within(a2aReader.read(), 2_000, "A2A initial task was not delivered");
        assert.equal(firstTask.done, false); assert.match(Buffer.from(firstTask.value!).toString("utf8"), /"task"/);
        const blocked = fetch(`${origin}/a2a/v1/endpoints/${endpointId}/message:send`, {
          method: "POST", headers: a2aHeaders, body: message("blocking-shutdown", false), signal: client.signal })
          .then(async response => ({ status: response.status, body: await response.json() as {
            error: { status: string; details: Array<{ reason: string; metadata: { taskId: string } }> } } }));
        pending.push(blocked);
        await eventually(async () => Number(f.first.db.prepare("SELECT COUNT(*) AS count FROM a2a_tasks WHERE endpoint_id = ?").get(endpointId)!.count) === 2,
          "The blocking request did not create its durable task before shutdown");
        const taskIds = f.first.db.prepare("SELECT id FROM a2a_tasks WHERE endpoint_id = ? ORDER BY id").all(endpointId).map(row => String(row.id));
        const endings = [uiReader, a2aReader].map(async reader => {
          let text = "";
          const decoder = new TextDecoder();
          for (;;) {
            const part = await reader.read();
            if (part.done) return text + decoder.decode();
            text += decoder.decode(part.value, { stream: true });
          }
        });
        pending.push(...endings);
        child.signal();
        const [uiEnd, a2aEnd, interrupted, exit] = await within(Promise.all([endings[0]!, endings[1]!, blocked, child.closed]),
          5_000, "SSE/A2A requests prevented coordinator shutdown");
        assert.equal(typeof uiEnd, "string");
        assert.doesNotMatch(a2aEnd, /TASK_STATE_(?:COMPLETED|FAILED|CANCELED)/, "Disconnect must not fabricate a terminal task event");
        assert.equal(interrupted.status, 503); assert.equal(interrupted.body.error.status, "UNAVAILABLE");
        assert.equal(interrupted.body.error.details[0]!.reason, "COORDINATOR_SHUTDOWN");
        assert.ok(taskIds.includes(interrupted.body.error.details[0]!.metadata.taskId));
        assert.deepEqual(exit, { code: 0, signal: null });
        for (const id of taskIds) {
          const task = f.first.getA2ATask(endpointId, id, 0, true) as { status: { state: string } };
          assert.equal(task.status.state, "TASK_STATE_SUBMITTED");
        }

        child = await startCoordinatorProcess(env);
        origin = `http://127.0.0.1:${child.port}`;
        for (const id of taskIds) {
          const response = await fetch(`${origin}/a2a/v1/endpoints/${endpointId}/tasks/${id}`, { headers: a2aHeaders });
          assert.equal(response.status, 200);
          assert.equal((await response.json() as { status: { state: string } }).status.state, "TASK_STATE_SUBMITTED");
        }
        assert.equal(Number(f.first.db.prepare("SELECT COUNT(*) AS count FROM a2a_tasks WHERE endpoint_id = ?").get(endpointId)!.count), 2);
        const expectedIds = f.first.listEvents(cursor, 200, f.project).map(event => event.id);
        assert.ok(expectedIds.length > 0, "Restart must exercise events beyond the saved client cursor");
        const resumed = await fetch(`${origin}/api/v1/events`, {
          headers: { "x-agat-project-id": f.project, "last-event-id": String(cursor) }, signal: client.signal });
        assert.equal(resumed.status, 200);
        const reader = resumed.body!.getReader();
        let later = "";
        const receivedIds = () => [...later.matchAll(/^id: (\d+)\n/gm)].map(match => Number(match[1]));
        while (receivedIds().length < expectedIds.length) {
          const part = await within(reader.read(), 2_000, "Events after the previous cursor were not resumed");
          assert.equal(part.done, false); later += Buffer.from(part.value!).toString("utf8");
        }
        assert.deepEqual(receivedIds(), expectedIds, "Resume must deliver every stored event after the cursor exactly once in order");
        await reader.cancel();
        assert.deepEqual(await child.stop(), { code: 0, signal: null });
      } finally {
        client.abort(); await child?.stop(); await Promise.allSettled(pending);
        for (const row of f.first.db.prepare("SELECT id FROM a2a_tasks WHERE endpoint_id = ?").all(endpointId)) {
          f.first.cancelA2ATask(endpointId, String(row.id));
        }
        await f.close();
      }
    });
  });

  for (const scenario of ["different dimensions", "matching dimensions", "separate collections"] as const) {
    it(`serializes embedding dimensions across jobs with ${scenario}`, async context => {
      await runWithPostgresSystemScope(async () => {
        const f = await embeddingLeaseFixture(2);
        let extraCollection: string | undefined;
        try {
          const collection = scenario === "separate collections"
            ? (extraCollection = String(f.first.createKnowledgeCollection({ name: "Independent", embeddingModel: f.project }, f.project).id))
            : f.collection;
          const document = String(f.first.ingestKnowledgeDocument(collection, { name: "Concurrent source", content: "Second synthetic source." }, f.project).id);
          const lease = f.first.leaseKnowledgeEmbedding(f.other.id)!;
          assert.ok(lease); assert.equal(lease.document.id, document);
          const vector = scenario === "matching dimensions" ? [0, 1] : [0, 0, 1];
          const embeddings = lease.chunks.map(chunk => ({ chunkId: chunk.id, embedding: vector }));
          const doc = () => f.first.db.prepare("SELECT status, embedded_count FROM knowledge_documents WHERE id = ?").get(document)!;
          const job = () => f.first.db.prepare("SELECT status, lease_id FROM knowledge_embedding_jobs WHERE document_id = ?").get(document)!;
          const events = () => f.first.db.prepare("SELECT id FROM events WHERE type = 'knowledge.document.ready' AND data_json LIKE ?").all(`%${document}%`).length;
          const indexed = () => Number(f.first.db.prepare("SELECT count(*) AS count FROM knowledge_chunks WHERE document_id = ? AND embedding_json IS NOT NULL").get(document)!.count);
          const dimensions = () => f.first.db.prepare("SELECT DISTINCT embedding_dimensions AS dimensions FROM knowledge_chunks WHERE collection_id = ? AND embedding_dimensions IS NOT NULL ORDER BY dimensions")
            .all(f.collection).map(row => row.dimensions);
          const beforeJob = job(), beforeDoc = doc();
          await f.writer.query("BEGIN"); await f.writer.query("LOCK TABLE events IN SHARE MODE");
          const firstRequest = f.post("complete");
          await f.waitForLock(/INSERT INTO events/);
          const secondRequest = fetch(`http://127.0.0.1:${f.children[1]!.port}/api/v1/workers/knowledge/leases/${lease.leaseId}/complete`, {
            method: "POST", headers: { authorization: `Bearer ${f.other.token}`, "content-type": "application/json" },
            body: JSON.stringify({ embeddings }), signal: AbortSignal.timeout(15_000),
          }).then(async response => ({ status: response.status, body: await response.text() }))
            .then(value => ({ value }), error => ({ error }));
          f.pending.push(secondRequest);
          await f.waitForLock(scenario === "separate collections" ? /INSERT INTO events/ : /INSERT INTO events|FOR NO KEY UPDATE OF c/, 1);
          await f.writer.query("ROLLBACK");
          const first = await within(firstRequest, 5_000, "First embedding completion did not finish");
          const second = await within(secondRequest, 5_000, "Second embedding completion did not finish");
          assert.ok("value" in first); assert.ok("value" in second);
          context.diagnostic(`Concurrent dimensions/${scenario}: HTTP ${first.value.status}/${second.value.status}, first collection=${dimensions()}`);
          assert.equal(first.value.status, 200); f.assertReady();
          assert.equal(second.value.status, scenario === "different dimensions" ? 400 : 200);
          assert.deepEqual(dimensions(), [2]);
          if (scenario === "different dimensions") {
            assert.match(second.value.body, /Размерность embeddings/);
            assert.deepEqual(job(), beforeJob); assert.deepEqual(doc(), beforeDoc);
            assert.equal(indexed(), 0); assert.equal(events(), 0);
            // The rejected batch is retryable with compatible output, without
            // a hidden replay, new lease or cleanup of a partially saved index.
            f.first.completeKnowledgeEmbedding(f.other.id, lease.leaseId,
              lease.chunks.map(chunk => ({ chunkId: chunk.id, embedding: [0, 1] })));
          }
          assert.equal(doc().status, "ready"); assert.equal(job().status, "completed");
          assert.equal(indexed(), lease.chunks.length); assert.equal(events(), 1);
          const actual = f.first.db.prepare("SELECT embedding_dimensions FROM knowledge_chunks WHERE document_id = ?").get(document)!;
          assert.equal(actual.embedding_dimensions, scenario === "separate collections" ? 3 : 2);
        } finally {
          await f.writer.query("ROLLBACK").catch(() => {});
          await Promise.all(f.children.map(child => child.stop())); await Promise.allSettled(f.pending);
          if (extraCollection) f.first.deleteKnowledgeCollection(extraCollection, f.project);
          await f.close();
        }
      });
    });
  }


  it("embedding completion rechecks lease expiry after waiting for its collection", async context => {
    await runWithPostgresSystemScope(async () => {
      const f = await embeddingLeaseFixture();
      try {
        const expires = Date.now() + 5_000;
        f.first.db.prepare("UPDATE knowledge_embedding_jobs SET lease_expires_at = ? WHERE id = ?")
          .run(new Date(expires).toISOString(), f.jobId);
        await f.writer.query("BEGIN");
        await f.writer.query("SELECT id FROM knowledge_collections WHERE id = $1 FOR NO KEY UPDATE", [f.collection]);
        const request = f.post("complete");
        await f.waitForLock(/FOR NO KEY UPDATE OF c/);
        assert.ok(Date.now() < expires, "Completion must wait for the collection while its lease is still live");
        await delay(Math.max(0, expires - Date.now() + 50)); await f.writer.query("ROLLBACK");
        const result = await within(request, 5_000, "Completion did not finish after releasing the collection");
        assert.ok("value" in result);
        context.diagnostic(`Completion after collection wait: HTTP ${result.value.status}, indexed=${f.indexed()}, events=${f.events()}`);
        assert.equal(result.value.status, 400); assert.match(result.value.body, /Активная embedding-аренда не найдена/);
        assert.equal(f.indexed(), 0); assert.equal(f.events(), 0);
        await f.recover();
      } finally { await f.close(); }
    });
  });


  for (const race of ["expiry", "reassignment", "renewal"] as const) {
    it(`stage renewal checks current ownership after ${race} during its SQL lock wait`, async context => {
      await runWithPostgresSystemScope(async () => {
        const f = await stageMaintenanceFixture(1);
        try {
          const other = race === "reassignment" ? f.first.registerNode({ enrollmentToken: "test", name: `${f.project}-replacement`,
            platform: "test", models: [f.project], maxConcurrency: 1, region: cellRegion, residencyDomain: cellResidencyDomain }) : null;
          const expires = Date.now() + 5_000, replacementId = randomUUID();
          f.first.db.prepare("UPDATE stages SET lease_expires_at = ? WHERE lease_id = ?").run(new Date(expires).toISOString(), f.lease.leaseId);
          await f.writer.query("BEGIN");
          await f.writer.query("SELECT id FROM stages WHERE id = $1 FOR UPDATE", [f.lease.stage.id]);
          if (other) await f.writer.query("UPDATE stages SET node_id = $1, lease_id = $2, lease_expires_at = $3 WHERE id = $4",
            [other.id, replacementId, new Date(Date.now() + 60_000).toISOString(), f.lease.stage.id]);
          if (race === "renewal") await f.writer.query("UPDATE stages SET lease_expires_at = $1 WHERE id = $2",
            [new Date(Date.now() + 60_000).toISOString(), f.lease.stage.id]);
          const pending = fetch(`http://127.0.0.1:${f.children[0]!.port}/api/v1/leases/${f.lease.leaseId}/renew`, {
            method: "POST", headers: { authorization: `Bearer ${f.worker.token}`, "content-type": "application/json" },
            body: "{}", signal: AbortSignal.timeout(15_000),
          }).then(async response => ({ status: response.status, body: await response.text() }))
            .then(value => ({ value }), error => ({ error }));
          f.pending.push(pending);
          await eventually(async () => {
            await f.writer.query("SELECT pg_stat_clear_snapshot()");
            const waiting = await f.writer.query("SELECT query FROM pg_stat_activity WHERE application_name = $1 AND wait_event_type = 'Lock'",
              [`agat-${f.children[0]!.instance}-system`]);
            if (!waiting.rows.length) return false;
            assert.match(waiting.rows[0].query, /FOR UPDATE|UPDATE stages SET lease_expires_at/); return true;
          }, "Stage renewal did not reach the ownership lock");
          assert.ok(Date.now() < expires);
          if (race !== "reassignment") await delay(Math.max(0, expires - Date.now() + 50));
          await f.writer.query(race === "expiry" ? "ROLLBACK" : "COMMIT");
          const result = await within(pending, 5_000, "Stage renewal did not finish after releasing ownership");
          assert.ok("value" in result);
          context.diagnostic(`Stage renewal/${race}: HTTP ${result.value.status}`);
          assert.equal(result.value.status, race === "renewal" ? 204 : 404);
          if (other) {
            const stage = f.first.db.prepare("SELECT node_id, lease_id FROM stages WHERE id = ?").get(f.lease.stage.id)!;
            assert.equal(stage.node_id, other.id); assert.equal(stage.lease_id, replacementId);
            f.first.completeLease(other.id, replacementId, "CURRENT OWNER");
          } else if (race === "renewal") {
            f.first.completeLease(f.worker.id, f.lease.leaseId, "RENEWED OWNER");
          } else {
            f.first.maintenanceTick(); f.first.heartbeatNode(f.worker.id, {});
            let replacement: ReturnType<typeof f.first.leaseNext> = null;
            await eventually(async () => { replacement = f.first.leaseNext(f.worker.id); return replacement !== null; },
              "Replacement stage lease remained unavailable after expiry");
            assert.ok(replacement); assert.notEqual(replacement.leaseId, f.lease.leaseId);
            assert.equal(f.first.renewLease(f.worker.id, f.lease.leaseId), false);
            f.first.completeLease(f.worker.id, replacement.leaseId, "REASSIGNED OWNER");
          }
          assert.equal(f.first.getRun(f.run.id, f.project)!.status, "completed");
        } finally { await f.close(); }
      });
    });
  }

  for (const kind of ["stage", "embedding"] as const) {
    it(`${kind} renewal rolls back when its UPDATE waits past the admitted deadline`, async context => {
      await runWithPostgresSystemScope(async () => {
        const f = kind === "stage" ? await stageMaintenanceFixture(1) : await embeddingLeaseFixture();
        try {
          const table = kind === "stage" ? "stages" : "knowledge_embedding_jobs";
          const expires = Date.now() + 5_000;
          f.first.db.prepare(`UPDATE ${table} SET lease_expires_at = ? WHERE lease_id = ?`)
            .run(new Date(expires).toISOString(), f.lease.leaseId);
          await f.writer.query("BEGIN");
          // SHARE permits the ownership SELECT FOR UPDATE (ROW SHARE), but
          // blocks the later UPDATE (ROW EXCLUSIVE). Exercise both intervals.
          await f.writer.query(`LOCK TABLE ${table} IN SHARE MODE`);
          const route = kind === "stage" ? "leases" : "workers/knowledge/leases";
          const pending = fetch(`http://127.0.0.1:${f.children[0]!.port}/api/v1/${route}/${f.lease.leaseId}/renew`, {
            method: "POST", headers: { authorization: `Bearer ${f.worker.token}`, "content-type": "application/json" },
            body: "{}", signal: AbortSignal.timeout(15_000),
          }).then(async response => ({ status: response.status, body: await response.text() }))
            .then(value => ({ value }), error => ({ error }));
          f.pending.push(pending);
          await eventually(async () => {
            await f.writer.query("SELECT pg_stat_clear_snapshot()");
            const waiting = await f.writer.query("SELECT query FROM pg_stat_activity WHERE application_name = $1 AND wait_event_type = 'Lock'",
              [`agat-${f.children[0]!.instance}-system`]);
            if (!waiting.rows.length) return false;
            assert.match(waiting.rows[0].query, new RegExp(`UPDATE ${table} SET lease_expires_at`)); return true;
          }, "Renewal did not reach the UPDATE lock");
          assert.ok(Date.now() < expires);
          await delay(Math.max(0, expires - Date.now() + 50)); await f.writer.query("ROLLBACK");
          const result = await within(pending, 5_000, "Renewal did not finish after releasing the UPDATE lock");
          assert.ok("value" in result);
          context.diagnostic(`${kind} renewal/UPDATE wait: HTTP ${result.value.status}`);
          assert.equal(result.value.status, 404);
          const old = f.first.db.prepare(`SELECT lease_expires_at FROM ${table} WHERE lease_id = ?`).get(f.lease.leaseId);
          if (old) assert.equal(old.lease_expires_at, new Date(expires).toISOString(), "Expired ownership must not be extended");
          if ("recover" in f) await f.recover();
          else {
            f.first.maintenanceTick(); f.first.heartbeatNode(f.worker.id, {});
            let replacement: ReturnType<typeof f.first.leaseNext> = null;
            await eventually(async () => { replacement = f.first.leaseNext(f.worker.id); return replacement !== null; },
              "Replacement lease remained unavailable after rejected renewal");
            assert.ok(replacement); assert.notEqual(replacement.leaseId, f.lease.leaseId);
            assert.equal(f.first.renewLease(f.worker.id, f.lease.leaseId), false);
            f.first.completeLease(f.worker.id, replacement.leaseId, "CURRENT OUTPUT");
            assert.equal(f.first.getRun(f.run.id, f.project)!.status, "completed");
          }
        } finally { await f.close(); }
      });
    });
  }

  for (const action of ["renew", "complete", "fail"] as const) {
    for (const race of ["expiry", "reassignment", "renewal"] as const) {
      it(`embedding ${action} respects ${race} while waiting for job ownership over HTTP`, async context => {
        await runWithPostgresSystemScope(async () => {
          const f = await embeddingLeaseFixture();
          try {
            const expires = Date.now() + 5_000, replacementId = randomUUID();
            f.first.db.prepare("UPDATE knowledge_embedding_jobs SET lease_expires_at = ? WHERE id = ?")
              .run(new Date(expires).toISOString(), f.jobId);
            await f.writer.query("BEGIN");
            await f.writer.query("SELECT id FROM knowledge_embedding_jobs WHERE id = $1 FOR UPDATE", [f.jobId]);
            if (race === "reassignment") await f.writer.query(`UPDATE knowledge_embedding_jobs
              SET node_id = $1, lease_id = $2, lease_expires_at = $3 WHERE id = $4`,
            [f.other.id, replacementId, new Date(Date.now() + 60_000).toISOString(), f.jobId]);
            if (race === "renewal") await f.writer.query("UPDATE knowledge_embedding_jobs SET lease_expires_at = $1 WHERE id = $2",
              [new Date(Date.now() + 60_000).toISOString(), f.jobId]);
            const pending = f.post(action);
            await f.waitForLock(/FOR UPDATE|UPDATE knowledge_embedding_jobs/);
            assert.ok(Date.now() < expires, "Request must reach the lock before the original expiry");
            if (race !== "reassignment") await delay(Math.max(0, expires - Date.now() + 50));
            await f.writer.query(race === "expiry" ? "ROLLBACK" : "COMMIT");
            const result = await within(pending, 5_000, "Embedding request did not finish after releasing ownership");
            assert.ok("value" in result);
            context.diagnostic(`Embedding ${action}/${race}: HTTP ${result.value.status}, job=${f.job().status}, document=${f.doc().status}, indexed=${f.indexed()}, events=${f.events()}`);
            assert.equal(result.value.status, race === "renewal" ? action === "renew" ? 204 : 200 : action === "renew" ? 404 : 400);
            if (race === "renewal") {
              if (action === "complete") { f.assertReady(); return; }
              if (action === "renew") {
                assert.equal(f.job().lease_id, f.lease.leaseId); assert.ok(Date.parse(String(f.job().lease_expires_at)) > Date.now());
                f.first.completeKnowledgeEmbedding(f.worker.id, f.lease.leaseId, f.results); f.assertReady(); return;
              }
              assert.equal(JSON.parse(result.value.body).retrying, true);
            } else {
              assert.match(result.value.body, /аренда/); assert.equal(f.indexed(), 0); assert.equal(f.events(), 0);
            }
            if (race === "reassignment") {
              assert.equal(f.job().status, "running"); assert.equal(f.job().node_id, f.other.id); assert.equal(f.job().lease_id, replacementId);
              f.first.completeKnowledgeEmbedding(f.other.id, replacementId, f.results); f.assertReady();
            } else await f.recover();
          } finally { await f.close(); }
        });
      });
    }
  }

  for (const action of ["complete", "fail"] as const) {
    it(`embedding ${action} rolls back when an event lock outlasts its lease over HTTP`, async context => {
      await runWithPostgresSystemScope(async () => {
        const f = await embeddingLeaseFixture();
        try {
          const expires = Date.now() + 5_000;
          f.first.db.prepare("UPDATE knowledge_embedding_jobs SET lease_expires_at = ? WHERE id = ?")
            .run(new Date(expires).toISOString(), f.jobId);
          await f.writer.query("BEGIN"); await f.writer.query("LOCK TABLE events IN SHARE MODE");
          const pending = f.post(action); await f.waitForLock(/INSERT INTO events/);
          assert.ok(Date.now() < expires);
          await delay(Math.max(0, expires - Date.now() + 50)); await f.writer.query("ROLLBACK");
          const result = await within(pending, 5_000, "Embedding write did not finish after releasing the event lock");
          assert.ok("value" in result);
          context.diagnostic(`Embedding ${action}/event lock: HTTP ${result.value.status}, indexed=${f.indexed()}, events=${f.events()}`);
          assert.equal(result.value.status, 400); assert.match(result.value.body, /Активная embedding-аренда не найдена/);
          assert.equal(f.indexed(), 0); assert.equal(f.events(), 0);
          await f.recover();
        } finally { await f.close(); }
      });
    });
  }

  it("embedding maintenance preserves a renewal that holds its job row past the old expiry", async context => {
    await runWithPostgresSystemScope(async () => {
      const f = await embeddingLeaseFixture();
      try {
        const expires = Date.now() + 2_000, renewed = new Date(Date.now() + 60_000).toISOString();
        f.first.db.prepare("UPDATE knowledge_embedding_jobs SET lease_expires_at = ? WHERE id = ?").run(new Date(expires).toISOString(), f.jobId);
        await f.writer.query("BEGIN");
        const changed = await f.writer.query(`UPDATE knowledge_embedding_jobs SET lease_expires_at = $1
          WHERE node_id = $2 AND lease_id = $3 AND status = 'running' AND lease_expires_at > $4`,
        [renewed, f.worker.id, f.lease.leaseId, new Date().toISOString()]);
        assert.equal(changed.rowCount, 1);
        await f.maintenanceAfter(0, expires); const health = await f.health(0);
        await f.writer.query("COMMIT"); assert.ok("status" in await f.health(0));
        context.diagnostic(`Embedding maintenance/renewal: health=${"status" in health ? health.status : "timeout"}, job=${f.job().status}, document=${f.doc().status}, failures=${f.job().failures}`);
        assert.equal(f.job().status, "running"); assert.equal(f.job().lease_id, f.lease.leaseId);
        assert.equal(f.job().lease_expires_at, renewed); assert.equal(f.job().failures, 0); assert.equal(f.doc().status, "indexing");
        assert.ok("status" in health); assert.equal(health.status, 200);
        f.first.completeKnowledgeEmbedding(f.worker.id, f.lease.leaseId, f.results); f.assertReady();
      } finally { await f.close(); }
    });
  });

  it("embedding maintenance stays responsive while a late completion rolls back across coordinators", async context => {
    await runWithPostgresSystemScope(async () => {
      const f = await embeddingLeaseFixture(2);
      try {
        const expires = Date.now() + 5_000;
        f.first.db.prepare("UPDATE knowledge_embedding_jobs SET lease_expires_at = ? WHERE id = ?").run(new Date(expires).toISOString(), f.jobId);
        await f.writer.query("BEGIN"); await f.writer.query("LOCK TABLE events IN SHARE MODE");
        const pending = f.post("complete"); await f.waitForLock(/INSERT INTO events/);
        assert.ok(Date.now() < expires);
        await f.maintenanceAfter(1, expires); const health = await f.health(1);
        await f.writer.query("ROLLBACK");
        const result = await within(pending, 5_000, "Embedding completion did not finish after the event lock");
        assert.ok("value" in result);
        assert.ok("status" in await f.health(1));
        context.diagnostic(`Embedding maintenance/completion: HTTP ${result.value.status}, health=${"status" in health ? health.status : "timeout"}, job=${f.job().status}, document=${f.doc().status}, indexed=${f.indexed()}, events=${f.events()}`);
        // With a final lease fence this late SQL-only completion rolls back.
        assert.equal(result.value.status, 400); assert.equal(f.indexed(), 0); assert.equal(f.events(), 0);
        assert.ok("status" in health); assert.equal(health.status, 200);
        await f.recover();
      } finally { await f.close(); }
    });
  });

  it("maintenance preserves a timely renewal while its stage row is locked", async context => {
    await runWithPostgresSystemScope(async () => {
      const f = await stageMaintenanceFixture(1);
      try {
        const expires = Date.now() + 2_000, renewed = new Date(Date.now() + 60_000).toISOString();
        f.first.db.prepare("UPDATE stages SET lease_expires_at = ? WHERE lease_id = ?").run(new Date(expires).toISOString(), f.lease.leaseId);
        await f.writer.query("BEGIN");
        const renewal = await f.writer.query(`UPDATE stages SET lease_expires_at = $1
          WHERE node_id = $2 AND lease_id = $3 AND status = 'running' AND lease_expires_at > $4`,
        [renewed, f.worker.id, f.lease.leaseId, new Date().toISOString()]);
        assert.equal(renewal.rowCount, 1, "Renewal must start while the lease is valid");
        await f.heartbeatAfter(0, expires);
        const health = await f.health(0);
        await f.writer.query("COMMIT");
        assert.ok("status" in await f.health(0), "Maintenance did not finish after releasing renewal");
        const stage = f.first.db.prepare("SELECT status, lease_id, lease_expires_at FROM stages WHERE id = ?").get(f.lease.stage.id)!;
        context.diagnostic(`Renewal after maintenance: health=${"status" in health ? health.status : "timeout"}, stage=${stage.status}, expiryEvents=${f.expirationEvents().length}`);
        assert.equal(stage.status, "running"); assert.equal(stage.lease_id, f.lease.leaseId); assert.equal(stage.lease_expires_at, renewed);
        assert.equal(f.expirationEvents().length, 0);
        assert.ok("status" in health); assert.equal(health.status, 200);
        f.first.completeLease(f.worker.id, f.lease.leaseId, "RENEWED OUTPUT");
        assert.equal(f.first.getRun(f.run.id, f.project)!.status, "completed");
      } finally { await f.close(); }
    });
  });

  it("maintenance preserves an admitted completion held past expiry by an event lock", async context => {
    await runWithPostgresSystemScope(async () => {
      const f = await stageMaintenanceFixture(2);
      try {
        const expires = Date.now() + 5_000;
        f.first.db.prepare("UPDATE stages SET lease_expires_at = ? WHERE lease_id = ?").run(new Date(expires).toISOString(), f.lease.leaseId);
        await f.writer.query("BEGIN"); await f.writer.query("LOCK TABLE events IN SHARE MODE");
        const completing = fetch(`http://127.0.0.1:${f.children[0]!.port}/api/v1/leases/${f.lease.leaseId}/complete`, {
          method: "POST", headers: { authorization: `Bearer ${f.worker.token}`, "content-type": "application/json" },
          body: JSON.stringify({ output: "ADMITTED OUTPUT", artifacts: [{ name: "admitted.txt", content: "ADMITTED ARTIFACT" }] }),
          signal: AbortSignal.timeout(15_000),
        }).then(async response => ({ status: response.status, body: await response.text() })).then(value => ({ value }), error => ({ error }));
        f.pending.push(completing);
        await eventually(async () => {
          await f.writer.query("SELECT pg_stat_clear_snapshot()");
          const waiting = await f.writer.query("SELECT query FROM pg_stat_activity WHERE application_name = $1 AND wait_event_type = 'Lock'",
            [`agat-${f.children[0]!.instance}-system`]);
          if (!waiting.rows.length) return false;
          assert.match(waiting.rows[0].query, /INSERT INTO events/); return true;
        }, "Completion did not reach the event lock while owning the stage row");
        assert.ok(Date.now() < expires);
        await f.heartbeatAfter(1, expires);
        const health = await f.health(1);
        await f.writer.query("ROLLBACK");
        const result = await within(completing, 5_000, "Admitted completion did not finish after releasing the event lock");
        assert.ok("value" in result); assert.equal(result.value.status, 200);
        assert.ok("status" in await f.health(1));
        const run = f.first.getRun(f.run.id, f.project)!;
        context.diagnostic(`Completion after maintenance: health=${"status" in health ? health.status : "timeout"}, run=${run.status}, expiryEvents=${f.expirationEvents().length}`);
        assert.equal(run.status, "completed");
        assert.equal((run.stages as Array<{ output: string }>)[0]!.output, "ADMITTED OUTPUT");
        assert.equal(f.first.listRunArtifacts(f.run.id, f.project).length, 3);
        assert.equal(f.expirationEvents().length, 0);
        assert.ok("status" in health); assert.equal(health.status, 200);
      } finally { await f.close(); }
    });
  });

  it("maintenance reclaims an expired lease once across two coordinators", async context => {
    await runWithPostgresSystemScope(async () => {
      const f = await stageMaintenanceFixture(2);
      try {
        const expires = Date.now() + 2_000;
        f.first.db.prepare("UPDATE stages SET lease_expires_at = ? WHERE lease_id = ?").run(new Date(expires).toISOString(), f.lease.leaseId);
        await f.writer.query("BEGIN"); await f.writer.query("SELECT id FROM stages WHERE id = $1 FOR UPDATE", [f.lease.stage.id]);
        await Promise.all([f.heartbeatAfter(0, expires), f.heartbeatAfter(1, expires)]);
        const health = await Promise.all([f.health(0), f.health(1)]);
        await f.writer.query("ROLLBACK"); const released = Date.now();
        await Promise.all([f.heartbeatAfter(0, released), f.heartbeatAfter(1, released)]);
        assert.ok("status" in await f.health(0)); assert.ok("status" in await f.health(1));
        context.diagnostic(`Two maintainers: healthy=${health.filter(result => "status" in result).length}, expiryEvents=${f.expirationEvents().length}`);
        assert.equal(f.expirationEvents().length, 1);
        for (const result of health) { assert.ok("status" in result); assert.equal(result.status, 200); }
        f.first.heartbeatNode(f.worker.id, {});
        const replacement = f.first.leaseNext(f.worker.id)!; assert.ok(replacement);
        assert.notEqual(replacement.leaseId, f.lease.leaseId);
        f.first.completeLease(f.worker.id, replacement.leaseId, "RECOVERED OUTPUT");
        assert.equal(f.first.getRun(f.run.id, f.project)!.status, "completed");
      } finally { await f.close(); }
    });
  });

  for (const action of ["complete", "fail"] as const) {
    for (const race of ["expiry", "reassignment", "renewal"] as const) {
      it(`checks current ownership before ${action} after a concurrent ${race} over HTTP`, async context => {
        await runWithPostgresSystemScope(async () => {
          const suffix = randomUUID().slice(0, 8);
          const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), "agat-pg-terminal-"));
          const first = store(`terminal-${suffix}`, artifacts);
          const writer = new pg.Client({ connectionString: systemUrl, statement_timeout: 10_000 });
          const instance = `terminal-http-${suffix}`;
          let child: Awaited<ReturnType<typeof startCoordinatorProcess>> | undefined;
          const pending: Promise<unknown>[] = [];
          try {
            await writer.connect(); first.updateScheduler("parallel", 10);
            const project = `terminal-${suffix}`, model = project;
            first.createProject({ id: project, name: project, homeRegion: cellRegion,
              allowedRegions: [cellRegion], residencyDomain: cellResidencyDomain });
            const agent = first.createAgent({ name: "Primary", role: "Test", systemPrompt: "Fixture", model }, project);
            const register = (name: string) => first.registerNode({ enrollmentToken: "test", name, platform: "test",
              models: [model], maxConcurrency: 1, region: cellRegion, residencyDomain: cellResidencyDomain });
            const worker = register(`${model}-a`), other = register(`${model}-b`);
            const run = first.createRun({ name: `Terminal ${suffix}`, input: "Synthetic source", agentIds: [String(agent.id)],
              approvalRequired: false, resultDestination: "artifacts" }, project);
            const lease = first.leaseNext(worker.id)!; assert.equal(lease.run.id, run.id);
            child = await startCoordinatorProcess(coordinatorProcessEnvironment(instance, artifacts, { AGAT_KNOWLEDGE_SEARCH_EXECUTION: "sync" }));
            const expires = Date.now() + 5_000;
            first.db.prepare("UPDATE stages SET lease_expires_at = ? WHERE lease_id = ?").run(new Date(expires).toISOString(), lease.leaseId);
            await writer.query("BEGIN");
            await writer.query("SELECT id FROM stages WHERE id = $1 FOR UPDATE", [lease.stage.id]);
            const replacementId = randomUUID();
            if (race === "reassignment") await writer.query(`UPDATE stages SET node_id = $1, lease_id = $2,
              lease_expires_at = $3, attempt = attempt + 1 WHERE id = $4`,
            [other.id, replacementId, new Date(Date.now() + 60_000).toISOString(), lease.stage.id]);
            if (race === "renewal") await writer.query("UPDATE stages SET lease_expires_at = $1 WHERE id = $2",
              [new Date(Date.now() + 60_000).toISOString(), lease.stage.id]);
            const payload = action === "complete" ? { output: "CURRENT OUTPUT", artifacts: [{ name: "current.txt", content: "CURRENT ARTIFACT" }] }
              : { error: "CURRENT FAILURE" };
            const responsePromise = fetch(`http://127.0.0.1:${child.port}/api/v1/leases/${lease.leaseId}/${action}`, {
              method: "POST", headers: { authorization: `Bearer ${worker.token}`, "content-type": "application/json" },
              body: JSON.stringify(payload), signal: AbortSignal.timeout(15_000),
            }).then(async response => ({ status: response.status, body: await response.json() as { error?: string; retrying?: boolean } }))
              .then(value => ({ value }), error => ({ error }));
            pending.push(responsePromise);
            let blockedAt = "";
            await eventually(async () => {
              await writer.query("SELECT pg_stat_clear_snapshot()");
              const waiting = await writer.query("SELECT query FROM pg_stat_activity WHERE application_name = $1 AND wait_event_type = 'Lock'",
                [`agat-${instance}-system`]);
              if (!waiting.rows.length) return false;
              const query = String(waiting.rows[0].query);
              assert.match(query, /FOR UPDATE|UPDATE stages/);
              blockedAt = /FOR UPDATE/.test(query) ? "stage read" : "stage update";
              return true;
            }, "The terminal HTTP request did not wait on the stage lock");
            assert.ok(Date.now() < expires, "The intended wait started after the original expiry");
            if (race !== "reassignment") await delay(Math.max(0, expires - Date.now() + 50));
            await writer.query(race === "expiry" ? "ROLLBACK" : "COMMIT");
            const result = await within(responsePromise, 5_000, "Terminal request did not finish after releasing the stage lock");
            assert.ok("value" in result);
            const stage = first.db.prepare("SELECT status, node_id, lease_id, output FROM stages WHERE id = ?").get(lease.stage.id)!;
            const records = first.listRunArtifacts(run.id, project);
            context.diagnostic(`${action}/${race}: wait=${blockedAt}, HTTP ${result.value.status}, stage=${String(stage.status)}, artifacts=${records.length}`);
            assert.equal(result.value.status, race === "renewal" ? 200 : 400);
            if (race === "renewal") {
              if (action === "complete") {
                assert.equal(stage.status, "completed"); assert.equal(stage.output, "CURRENT OUTPUT");
                assert.equal(records.length, 3);
                return;
              }
              assert.equal(result.value.body.retrying, true); assert.equal(stage.status, "queued");
            } else {
              assert.match(result.value.body.error ?? "", /Активная аренда не найдена/);
              assert.equal(stage.output, null); assert.equal(records.length, 0);
              assert.equal(first.db.prepare(`SELECT id FROM events WHERE run_id = ?
                AND type IN ('stage.completed', 'stage.retrying', 'stage.failed')`).all(run.id).length, 0);
            }
            if (race === "reassignment") {
              assert.equal(stage.status, "running"); assert.equal(stage.node_id, other.id); assert.equal(stage.lease_id, replacementId);
              first.completeLease(other.id, replacementId, "CURRENT OUTPUT", [{ name: "current.txt", content: "CURRENT ARTIFACT" }]);
            } else {
              first.maintenanceTick(); first.heartbeatNode(worker.id, {}); first.heartbeatNode(other.id, {});
              let replacement: ReturnType<typeof first.leaseNext> = null, replacementOwner = other.id;
              await eventually(async () => {
                // Retry routing can prefer the other eligible worker.
                for (const candidate of [other, worker]) {
                  replacement = first.leaseNext(candidate.id);
                  if (replacement) { replacementOwner = candidate.id; return true; }
                }
                return false;
              }, "Replacement lease remained unavailable after releasing the terminal request lock");
              assert.ok(replacement); assert.equal(replacement.run.id, run.id);
              assert.notEqual(replacement.leaseId, lease.leaseId);
              first.completeLease(replacementOwner, replacement.leaseId, "CURRENT OUTPUT", [{ name: "current.txt", content: "CURRENT ARTIFACT" }]);
            }
            assert.equal(first.getRun(run.id, project)!.status, "completed");
            assert.equal(first.listRunArtifacts(run.id, project).length, 3);
          } finally {
            await writer.query("ROLLBACK").catch(() => {}); await child?.stop(); await Promise.allSettled(pending);
            await writer.end(); first.close(); fs.rmSync(artifacts, { recursive: true, force: true });
          }
        });
      });
    }
  }

  for (const boundary of ["stage lock", "event insert", "idempotent retry"] as const) {
    it(`rejects shadow results when the lease expires during ${boundary} over HTTP`, async context => {
      await runWithPostgresSystemScope(async () => {
        const suffix = randomUUID().slice(0, 8);
        const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), "agat-pg-shadow-expiry-"));
        const first = store(`shadow-expiry-${suffix}`, artifacts);
        const writer = new pg.Client({ connectionString: systemUrl, statement_timeout: 10_000 });
        const instance = `shadow-expiry-http-${suffix}`;
        let child: Awaited<ReturnType<typeof startCoordinatorProcess>> | undefined;
        const pending: Promise<unknown>[] = [];
        const project = `shadow-expiry-${suffix}`, model = project;
        let runId: string | undefined;
        try {
          await writer.connect();
          first.updateScheduler("parallel", 10);
          first.createProject({ id: project, name: project, homeRegion: cellRegion,
            allowedRegions: [cellRegion], residencyDomain: cellResidencyDomain });
          const agent = first.createAgent({ name: "Primary", role: "Test", systemPrompt: "Fixture", model }, project);
          const worker = first.registerNode({ enrollmentToken: "test", name: model, platform: "test", models: [model],
            maxConcurrency: 1, region: cellRegion, residencyDomain: cellResidencyDomain,
            labels: { decisionShadow: "local_decision_shadow_v2", pool: project } });
          const shadow = normalizeDecisionShadowConfig({ mode: "shadow", profileJson: decisionProfileJson, timeoutMs: 1000,
            kind: "boolean", question: "Confirmed?", options: [
              { id: "no", description: "No", value: false }, { id: "yes", description: "Yes", value: true }] });
          const graph: ProcessGraph = { nodes: [
            { id: "start", name: "Start", type: "start", position: { x: 0, y: 0 }, config: {} },
            { id: "agent", name: "Agent", type: "agent", position: { x: 200, y: 0 }, config: {
              agentId: String(agent.id), decisionShadow: shadow } },
            { id: "end", name: "End", type: "end", position: { x: 400, y: 0 }, config: {} },
          ], edges: [{ id: "a", source: "start", target: "agent", branch: "default" },
            { id: "b", source: "agent", target: "end", branch: "default" }] };
          const process = first.createProcess({ name: `Shadow expiry ${suffix}`, graph }, project);
          first.publishProcess(String(process.id), project);
          const run = first.startProcess(String(process.id), { input: "Synthetic source", priority: 100 }, project)!;
          runId = String(run.runId);
          const lease = first.leaseNext(worker.id)!; assert.ok(lease?.decisionShadow);
          assert.equal(lease.run.id, runId);
          const payload = { status: "unavailable", reason: "timeout" };
          const previous = boundary === "idempotent retry" ? first.recordDecisionShadow(worker.id, lease.leaseId, payload) : undefined;
          const readObservation = () => JSON.parse(String(first.db.prepare("SELECT activity_json FROM stages WHERE id = ?")
            .get(lease.stage.id)!.activity_json)).decisionShadowObservation;
          const events = () => first.db.prepare("SELECT id FROM events WHERE run_id = ? AND type = 'decision.shadow'").all(runId);
          child = await startCoordinatorProcess(coordinatorProcessEnvironment(instance, artifacts, {
            AGAT_KNOWLEDGE_SEARCH_EXECUTION: "sync", AGAT_DECISION_SHADOW_ENABLED: "true" }));
          const expires = Date.now() + 5_000;
          first.db.prepare("UPDATE stages SET lease_expires_at = ? WHERE lease_id = ?").run(new Date(expires).toISOString(), lease.leaseId);
          await writer.query("BEGIN");
          if (boundary === "event insert") await writer.query("LOCK TABLE events IN SHARE MODE");
          else await writer.query("SELECT id FROM stages WHERE id = $1 FOR UPDATE", [lease.stage.id]);
          const responsePromise = fetch(`http://127.0.0.1:${child.port}/api/v1/leases/${lease.leaseId}/decision-shadow`, {
            method: "POST", headers: { authorization: `Bearer ${worker.token}`, "content-type": "application/json" },
            body: JSON.stringify(payload), signal: AbortSignal.timeout(15_000),
          }).then(async response => ({ status: response.status, body: await response.json() as { error?: string } }))
            .then(value => ({ value }), error => ({ error }));
          pending.push(responsePromise);
          await eventually(async () => {
            await writer.query("SELECT pg_stat_clear_snapshot()");
            const waiting = await writer.query("SELECT query FROM pg_stat_activity WHERE application_name = $1 AND wait_event_type = 'Lock'",
              [`agat-${instance}-system`]);
            if (!waiting.rows.length) return false;
            assert.match(waiting.rows[0].query, boundary === "event insert" ? /INSERT INTO events/ : /FOR UPDATE OF s/);
            return true;
          }, "The shadow HTTP request did not reach the intended PostgreSQL lock");
          assert.ok(Date.now() < expires, "The intended wait started after lease expiry");
          await delay(Math.max(0, expires - Date.now() + 50));
          await writer.query("ROLLBACK");
          const result = await within(responsePromise, 5_000, "Shadow request did not finish after releasing its SQL lock");
          assert.ok("value" in result, "The real HTTP request must return a response");
          const saved = readObservation(), eventCount = events().length;
          context.diagnostic(`After expiry at ${boundary}: HTTP ${result.value.status}, observation=${Boolean(saved)}, events=${eventCount}`);
          assert.equal(result.value.status, 400, "An expired lease must not acknowledge a shadow observation");
          assert.match(result.value.body.error ?? "", /Активная аренда не найдена/);
          assert.deepEqual(saved, previous); assert.equal(eventCount, previous ? 1 : 0);
          assert.equal(first.renewLease(worker.id, lease.leaseId), false);
          first.maintenanceTick(); first.heartbeatNode(worker.id, {});
          let replacement: ReturnType<typeof first.leaseNext> = null;
          await eventually(async () => { replacement = first.leaseNext(worker.id); return replacement !== null; },
            "Replacement shadow lease remained unavailable after maintenance");
          assert.ok(replacement);
          assert.notEqual(replacement.leaseId, lease.leaseId);
          assert.throws(() => first.recordDecisionShadow(worker.id, lease.leaseId, payload), /аренда/);
          const recovered = first.recordDecisionShadow(worker.id, replacement.leaseId, payload);
          assert.deepEqual(first.recordDecisionShadow(worker.id, replacement.leaseId, {}), recovered);
          if (previous) assert.deepEqual(recovered, previous);
          first.completeLease(worker.id, replacement.leaseId, "PRIMARY AFTER EXPIRY");
          assert.equal(first.getRun(runId, project)!.status, "completed");
          assert.equal((first.getRun(runId, project)!.stages as Array<{ output: string }>)[0]!.output, "PRIMARY AFTER EXPIRY");
          assert.equal(events().length, 1);
        } finally {
          await writer.query("ROLLBACK").catch(() => {}); await child?.stop(); await Promise.allSettled(pending);
          if (runId) first.cancelRun(runId, project);
          first.markWorkerPoolOffline(project); await writer.end(); first.close(); fs.rmSync(artifacts, { recursive: true, force: true });
        }
      });
    });
  }

  it("persists one shadow observation across replicas and restart with tenant RLS and unchanged primary output", () => {
    runWithPostgresSystemScope(() => {
      const suffix = randomUUID().slice(0, 8);
      const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), "agat-pg-shadow-"));
      let first = store(`shadow-a-${suffix}`, artifacts);
      const second = store(`shadow-b-${suffix}`, artifacts);
      try {
        const projectId = `shadow-${suffix}`;
        const foreignProjectId = `shadow-other-${suffix}`;
        for (const id of [projectId, foreignProjectId]) first.createProject({ id, name: id,
          homeRegion: cellRegion, allowedRegions: [cellRegion], residencyDomain: cellResidencyDomain });
        first.updateScheduler("parallel", 10);
        const modelName = `shadow-primary-${suffix}`;
        const agent = first.createAgent({ name: "Primary", role: "Test", systemPrompt: "Fixture",
          model: modelName }, projectId);
        const worker = first.registerNode({ enrollmentToken: "test", name: `shadow-worker-${suffix}`, platform: "test",
          models: [modelName], maxConcurrency: 1, region: cellRegion, residencyDomain: cellResidencyDomain,
          labels: { decisionShadow: "local_decision_shadow_v1" } });
        const profile = { schemaVersion: "agat.decision.v1", runtimeVersion: "test-only",
          model: { repository: "postgres-fixture", revision: "fixture", artifactSha256: "1".repeat(64),
            tokenizerSha256: "2".repeat(64), implementationSha256: "3".repeat(64), promptVersion: "fixture",
            backend: "fixture", quantization: "none" },
          policy: { id: "test", minProbability: 0.8, minMargin: 0.1, sha256: "4".repeat(64) },
          calibration: { status: "uncalibrated", temperature: 1, semantics: "softmax_over_allowed_options" } };
        const shadow = normalizeDecisionShadowConfig({ mode: "shadow", profileJson: JSON.stringify(profile), timeoutMs: 1000,
          kind: "boolean", question: "Confirmed?", options: [
            { id: "no", description: "No", value: false }, { id: "yes", description: "Yes", value: true }] });
        const graph: ProcessGraph = { nodes: [
          { id: "start", name: "Start", type: "start", position: { x: 0, y: 0 }, config: {} },
          { id: "agent", name: "Agent", type: "agent", position: { x: 200, y: 0 }, config: {
            agentId: String(agent.id), decisionShadow: shadow } },
          { id: "end", name: "End", type: "end", position: { x: 400, y: 0 }, config: {} },
        ], edges: [{ id: "a", source: "start", target: "agent", branch: "default" },
          { id: "b", source: "agent", target: "end", branch: "default" }] };
        const process = first.createProcess({ name: "Shadow PostgreSQL", graph }, projectId);
        first.publishProcess(String(process.id), projectId);
        const preflight = second.preflightProcess(String(process.id), { version: 1 }, projectId)!;
        assert.equal(preflight.runnableNow, true);
        assert.match(preflight.notices.join("\n"), /Совместимый профиль локальной проверки заявлен/);
        assert.equal(second.preflightProcess(String(process.id), { version: 1 }, foreignProjectId), null);
        const instance = first.startProcess(String(process.id), { input: "Private fixture state", priority: 100 }, projectId)!;
        const lease = second.leaseNext(worker.id)!;
        assert.ok(lease?.decisionShadow);
        assert.equal(lease.run.id, String(instance.runId));
        const probability = 1 / (1 + Math.exp(-3));
        const response = { ...profile, mode: "shadow", id: lease.stage.id, inputSha256: lease.decisionShadow.inputSha256,
          status: "ok", reason: "accepted", selectedOptionId: "no", value: false, selectedProbability: probability,
          margin: 2 * probability - 1, inputTokens: 20, generatedTokens: 0, durationMs: 1,
          distribution: [{ id: "no", probability, logit: 3 }, { id: "yes", probability: 1 - probability, logit: 0 }] };
        const saved = first.recordDecisionShadow(worker.id, lease.leaseId, { result: response });
        assert.equal(saved.status, "ok"); assert.equal(saved.result?.value, false);
        assert.deepEqual(second.recordDecisionShadow(worker.id, lease.leaseId, { result: {} }), saved);
        first.close();
        first = store(`shadow-restarted-${suffix}`, artifacts);
        assert.deepEqual(first.recordDecisionShadow(worker.id, lease.leaseId, {}), saved);
        second.completeLease(worker.id, lease.leaseId, "PRIMARY OUTPUT");
        const runId = String(instance.runId);
        const trace = first.getRunTrace(runId, projectId)!;
        assert.equal(first.getRun(runId, projectId)!.status, "completed");
        assert.deepEqual(first.preflightProcess(String(process.id), { version: 1 }, projectId)!.notices, preflight.notices);
        assert.equal((first.getRun(runId, projectId)!.stages as Array<Record<string, unknown>>)[0]!.output, "PRIMARY OUTPUT");
        assert.equal((trace.decisionObservations as unknown[]).length, 1);
        assert.equal((trace.events as Array<Record<string, unknown>>).filter((e) => e.type === "decision.shadow").length, 1);
        assert.equal(second.getRunTrace(runId, foreignProjectId), null);
        enterPostgresTenantScope(foreignProjectId);
        assert.equal(second.db.prepare("SELECT activity_json FROM stages WHERE id = ?").get(lease.stage.id), undefined);
        enterPostgresTenantScope(projectId);
        const row = second.db.prepare("SELECT activity_json FROM stages WHERE id = ?").get(lease.stage.id)!;
        assert.equal(JSON.parse(String(row.activity_json)).decisionShadowObservation.status, "ok");
      } finally {
        runWithPostgresSystemScope(() => { first.close(); second.close(); });
        fs.rmSync(artifacts, { recursive: true, force: true });
      }
    });
  });

  it("streams large valid RAG vectors, preserves cursor snapshots and rolls back failures across replicas", () => {
    runWithPostgresSystemScope(() => {
      const suffix = randomUUID().slice(0, 8);
      const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), "agat-pg-rag-stream-"));
      const first = store(`rag-stream-a-${suffix}`, artifacts);
      const second = store(`rag-stream-b-${suffix}`, artifacts);
      try {
        const projectId = `rag-stream-${suffix}`;
        const foreignProjectId = `rag-stream-other-${suffix}`;
        for (const id of [projectId, foreignProjectId]) first.createProject({ id, name: id,
          homeRegion: cellRegion, allowedRegions: [cellRegion], residencyDomain: cellResidencyDomain });
        const model = `rag-stream-fixture-${suffix}`;
        const agent = first.createAgent({ name: "RAG fixture", role: "Test", systemPrompt: "Fixture", model }, projectId);
        const node = first.registerNode({ enrollmentToken: "test", name: model, platform: "test",
          models: [model], embeddingModels: [model], maxConcurrency: 1, region: cellRegion,
          residencyDomain: cellResidencyDomain }).id;
        const collection = (name: string) => String(first.createKnowledgeCollection({ name, embeddingModel: model,
          chunkSize: 400, chunkOverlap: 0, topK: 1 }, projectId).id);
        const large = collection("Wide vectors");
        const small = collection("Small query");
        const document = first.ingestKnowledgeDocument(large, { name: "Synthetic repeated chunks",
          content: "A".repeat(500 * 400) }, projectId);
        assert.equal(document.chunkCount, 500);
        first.ingestKnowledgeDocument(small, { name: "One chunk", content: "Synthetic separate source." }, projectId);
        const vector = Array<number>(4096).fill(1 / 3);
        const embeddingJson = JSON.stringify(vector);
        assert.ok(Buffer.byteLength(embeddingJson) * 500 > 32 * 1024 * 1024,
          "the valid candidate set must exceed the bridge's single-response limit");
        for (let lease; (lease = first.leaseKnowledgeEmbedding(node));) {
          second.completeKnowledgeEmbedding(node, lease.leaseId,
            lease.chunks.map(chunk => ({ chunkId: chunk.id, embedding: vector })));
        }

        // Change a row beyond the first FETCH batch through another connection.
        // The cursor must continue to return the original SELECT snapshot.
        first.db.exec("BEGIN");
        const snapshot = first.db.prepare("SELECT ordinal, content FROM knowledge_chunks WHERE document_id = ? ORDER BY ordinal")
          .iterate(String(document.id));
        assert.equal(snapshot.next().value!.ordinal, 0);
        second.db.prepare("UPDATE knowledge_chunks SET content = 'concurrent fixture update' WHERE document_id = ? AND ordinal = 499")
          .run(String(document.id));
        const remaining = [...snapshot];
        assert.equal(remaining.length, 499);
        assert.equal(remaining[498]!.content, "A".repeat(400));
        first.db.exec("COMMIT");
        second.db.prepare("UPDATE knowledge_chunks SET content = ? WHERE document_id = ? AND ordinal = 499")
          .run("A".repeat(400), String(document.id));

        const openCursors = () => Number(first.db.prepare("SELECT COUNT(*) AS count FROM pg_cursors WHERE name LIKE 'agat_cursor_%'").get()!.count);
        const selected = first.db.prepare("SELECT * FROM knowledge_chunks WHERE collection_id = ? ORDER BY ordinal");
        assert.ok(selected.rankKnowledgeCandidates);
        assert.throws(() => selected.rankKnowledgeCandidates!({ vector, topK: 1 }, large), /открытую transaction/);
        first.db.exec("BEGIN");
        const ranked = selected.rankKnowledgeCandidates!({ vector, topK: 20 }, large);
        assert.equal(ranked.length, 20);
        assert.ok(ranked.every(hit => hit.score === 1 && !("embedding_json" in hit.candidate)));
        assert.equal(openCursors(), 0, "ranking closes its worker-owned cursor before returning metadata");
        const precise = first.db.prepare(`SELECT n AS id, 2 AS embedding_dimensions,
          CASE WHEN n = 12 THEN '[1,1]' ELSE '[1,' || (1 + n::double precision * 1e-9)::text || ']' END AS embedding_json
          FROM generate_series(1,12) n ORDER BY n`)
          .rankKnowledgeCandidates!({ vector: [1, 0], topK: 8 });
        assert.equal(precise[0]!.candidate.id, 12, "worker transport preserves the float64 winner");
        assert.ok(precise[0]!.score > precise[1]!.score);
        assert.throws(() => first.db.prepare(`SELECT n, 2 AS embedding_dimensions, '[1,0]' AS embedding_json
          FROM generate_series(1,5001) n ORDER BY n`).rankKnowledgeCandidates!({ vector: [1, 0], topK: 1 }), /Лимит локального retrieval.*5000/);
        assert.equal(openCursors(), 0, "capacity failure releases the worker cursor");
        first.db.exec("ROLLBACK");
        first.db.exec("BEGIN");
        assert.throws(() => first.db.prepare(`SELECT n, 1 / (129 - n) AS value,
          2 AS embedding_dimensions, '[1,0]' AS embedding_json FROM generate_series(1,130) n`)
          .rankKnowledgeCandidates!({ vector: [1, 0], topK: 1 }), /division by zero/);
        first.db.exec("ROLLBACK");
        assert.equal(openCursors(), 0);
        first.db.exec("BEGIN");
        for (const row of first.db.prepare("SELECT n FROM generate_series(1, 130) AS n").iterate()) {
          assert.equal(row.n, 1);
          break;
        }
        assert.equal(openCursors(), 0, "early loop exit closes the cursor before commit");
        first.db.exec("COMMIT");
        first.db.exec("BEGIN");
        assert.throws(() => [...first.db.prepare("SELECT n, 1 / (129 - n) AS value FROM generate_series(1, 130) AS n").iterate()],
          /division by zero/, "a later FETCH error must not be hidden by CLOSE on an aborted transaction");
        first.db.exec("ROLLBACK");
        assert.equal(openCursors(), 0);

        const run = first.createRun({ name: "Streamed RAG", input: "Synthetic query", agentIds: [String(agent.id)],
          approvalRequired: false, knowledgeCollectionIds: [large, small] }, projectId);
        const lease = second.leaseNext(node)!;
        assert.equal(lease.run.id, run.id);
        const query = (id: string) => ({ embeddingModel: model, collectionIds: [id], vector, topK: 1 });
        const result = first.searchKnowledge(node, lease.leaseId, { queries: [query(large)] });
        assert.equal(result.hits.length, 1);
        assert.equal(result.hits[0]!.marker, "K1");
        assert.equal(result.hits[0]!.score, 1);
        assert.equal(result.hits[0]!.provenance.documentId, document.id);
        // Corrupt a late candidate that need not belong to topK. It must still
        // be validated, and the successful first query must not be persisted.
        second.db.prepare("UPDATE knowledge_chunks SET embedding_json = 'broken', embedded_at = '2020-01-01T00:00:00.000Z' WHERE document_id = ? AND ordinal = 0")
          .run(String(document.id));
        assert.throws(() => first.searchKnowledge(node, lease.leaseId, { queries: [query(small), query(large)] }),
          /Индекс embeddings содержит повреждённый вектор/);
        assert.equal(first.getRunKnowledgeSources(run.id, projectId)!.length, 1);
        assert.equal((first.getRunTrace(run.id, projectId)!.events as Array<{ type: string }>).filter(e => e.type === "knowledge.retrieved").length, 1);
        assert.equal(openCursors(), 0);
        second.db.prepare("UPDATE knowledge_chunks SET embedding_json = ? WHERE document_id = ? AND ordinal = 0")
          .run(embeddingJson, String(document.id));
        const recovered = second.searchKnowledge(node, lease.leaseId, { queries: [query(small), query(large)] });
        assert.deepEqual(recovered.hits.map(hit => hit.marker), ["K2", "K3"]);
        assert.equal(first.getRunKnowledgeSources(run.id, projectId)!.length, 3);
        second.completeLease(node, lease.leaseId, "Synthetic primary output");

        enterPostgresTenantScope(foreignProjectId);
        first.db.exec("BEGIN");
        assert.deepEqual([...first.db.prepare("SELECT id FROM knowledge_chunks WHERE collection_id = ?").iterate(large)], []);
        assert.deepEqual(selected.rankKnowledgeCandidates!({ vector, topK: 1 }, large), []);
        first.db.exec("COMMIT");
        enterPostgresTenantScope(projectId);
        first.db.exec("BEGIN");
        assert.equal([...first.db.prepare("SELECT id FROM knowledge_chunks WHERE collection_id = ?").iterate(large)].length, 500);
        first.db.exec("COMMIT");
      } finally {
        runWithPostgresSystemScope(() => { first.close(); second.close(); });
        fs.rmSync(artifacts, { recursive: true, force: true });
      }
    });
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
      assert.equal(lease.run.id, firstRun.id, `Quota fixture unexpectedly leased ${lease.run.name}`);
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
