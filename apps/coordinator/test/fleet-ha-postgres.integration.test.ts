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

import { AgatStore } from "../src/database.js";
import { migratePostgresSchemaAndAdmit } from "../src/postgres-schema-migrator.js";
import { normalizeDecisionShadowConfig } from "../src/local-decisions.js";
import { KnowledgeSearchExecutor, type KnowledgeSearchStoreOptions } from "../src/knowledge-search-executor.js";
import type { ProcessGraph } from "../src/types.js";
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
    return { port, stop, closed, signal: () => child.kill("SIGTERM") };
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
      try {
        const projectId = `rag-budget-${suffix}`;
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
