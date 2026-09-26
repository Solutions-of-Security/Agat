import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { before, describe, it } from "node:test";

import pg from "pg";

import { AgatStore } from "../src/database.js";
import { migratePostgresSchemaAndAdmit } from "../src/postgres-schema-migrator.js";
import { normalizeDecisionShadowConfig } from "../src/local-decisions.js";
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
    decisionShadowEnabled: true,
    artifactsDir,
  });
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
