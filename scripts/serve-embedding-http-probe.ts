/** Own two unmodified coordinator mains and verify durable synthetic batches. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import { AgatStore } from "../apps/coordinator/src/database.js";
import { KNOWLEDGE_EMBEDDING_BATCH_SIZE } from "../apps/coordinator/src/knowledge.js";
import { migratePostgresSchemaAndAdmit } from "../apps/coordinator/src/postgres-schema-migrator.js";
import { runWithPostgresSystemScope } from "../apps/coordinator/src/postgres-database.js";
import { startMainCoordinator } from "./rag-main-coordinator.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
assert.equal(process.argv.length, 5);
const data = fs.realpathSync(process.argv[2]!), evidence = fs.realpathSync(process.argv[3]!), layout = process.argv[4]!;
assert.ok(["shared", "independent"].includes(layout));
assert.ok(path.basename(data).startsWith("agat-embedding-http-") && path.dirname(data) === fs.realpathSync(os.tmpdir()));
assert.ok(evidence.startsWith(`${fs.realpathSync(path.join(root, "docs"))}${path.sep}`));
const sha = (value: Buffer | string) => createHash("sha256").update(value).digest("hex");
const planBytes = fs.readFileSync(path.join(evidence, "plan.json")), plan = JSON.parse(planBytes.toString());
for (const [name, expected] of Object.entries(plan.sourceSha256)) assert.equal(sha(fs.readFileSync(path.join(root, name))), expected, name);
assert.equal(plan.nodeVersion, process.version); assert.equal(plan.dimensions, 768);
assert.equal(plan.chunksPerBatch, KNOWLEDGE_EMBEDDING_BATCH_SIZE); assert.equal(plan.iterations, 10);
assert.equal(process.env.AGAT_RAG_HTTP_DISPOSABLE, "1");
for (const key of ["AGAT_POSTGRES_URL", "AGAT_POSTGRES_TENANT_URL", "AGAT_POSTGRES_MIGRATION_URL"]) {
  const url = new URL(process.env[key]!);
  assert.equal(url.hostname, "127.0.0.1"); assert.equal(url.pathname, "/agat_rag_http"); assert.ok(url.port);
}
await migratePostgresSchemaAndAdmit();
const store = new AgatStore(":postgresql:", { seedDemo: false, stateStoreDriver: "postgresql", postgresSchemaMode: "runtime",
  coordinatorInstanceId: "embedding-http-fixture", region: "eu-test-1", residencyDomain: "eu-test",
  artifactsDir: path.join(data, "artifacts"), requireSignedWorkerReleases: false,
  postgres: { systemUrl: process.env.AGAT_POSTGRES_URL!, tenantUrl: process.env.AGAT_POSTGRES_TENANT_URL!,
    roleMode: "runtime", applicationName: "embedding-http-fixture", poolMax: 1, connectTimeoutMs: 5000,
    idleTimeoutMs: 30000, statementTimeoutMs: 30000, sslMode: "disable" } });
const mains: Array<Awaited<ReturnType<typeof startMainCoordinator>>> = [];
const lines = createInterface({ input: process.stdin }), phases: unknown[] = [];
const project = "embedding-http", model = "embedding-http-fixture", primaryModel = "renewal-primary";
const vector = Array<number>(plan.dimensions).fill(1 / 3), vectorSha256 = sha(JSON.stringify(vector));
const reply = (value: unknown) => process.stdout.write(`${JSON.stringify(value)}\n`);
type Job = { node: string; token: string; lease: string; document: string; collection: string; chunkIds: string[]; contentSha256: string; replica: number };
type Renewal = { kind: "stage" | "embedding"; node: string; token: string; lease: string; initialExpiry: string; run?: string; document?: string; replica: number };
let phase: { id: string; jobs: Job[]; renewals: Renewal[]; collections: string[] } | undefined;
let exits: Array<{ code: number | null; signal: string | null }> | undefined;
try {
  store.createProject({ id: project, name: project, homeRegion: "eu-test-1", allowedRegions: ["eu-test-1"],
    residencyDomain: "eu-test", maxRunningTasks: 64, maxQueuedTasks: 128 });
  store.updateScheduler("parallel", 64); store.updateModelRouterPolicy({ enabled: false });
  const agent = store.createAgent({ name: "Renewal control", role: "Synthetic control", systemPrompt: "No generation", model: primaryModel }, project);
  for (let index = 0; index < 2; index++) mains.push(await startMainCoordinator(data, "sync", plan.compiledSha256,
    { instanceId: `embedding-http-main-${index}` }));
  reply({ type: "ready", ports: mains.map(main => main.port), runtimePids: mains.map(main => main.pid), vector, vectorSha256 });
  for await (const line of lines) {
    assert.ok(line.length <= 2048);
    const command = JSON.parse(line);
    if (command.type === "stop") { assert.equal(phase, undefined); assert.equal(phases.length, plan.phases.length); break; }
    if (command.type === "prepare") {
      assert.equal(phase, undefined);
      const expected = plan.phases[phases.length]; assert.ok(expected);
      assert.equal(command.id, expected.id); assert.equal(command.concurrency, expected.concurrency);
      const id = command.id as string, count = id === "warmup" ? 2 : command.concurrency * plan.iterations;
      const collections: string[] = [];
      const collection = (name: string) => {
        const created = String(store.createKnowledgeCollection({ name: `${id}-${name}`, embeddingModel: model,
          chunkSize: 400, chunkOverlap: 0 }, project).id);
        collections.push(created); return created;
      };
      const register = (name: string, primary = false) => store.registerNode({ enrollmentToken: "unused", name: `${id}-${name}`,
        platform: "diagnostic", models: [primary ? primaryModel : "unused-primary"], embeddingModels: primary ? [] : [model],
        maxConcurrency: 1, region: "eu-test-1", residencyDomain: "eu-test", labels: { pool: id } });
      const shared = layout === "shared" && count ? collection("shared") : null;
      const jobs: Job[] = [];
      for (let index = 0; index < count; index++) {
        const collectionId = shared ?? collection(`job-${index}`);
        const content = `${id}-${index}`.padEnd(400 * plan.chunksPerBatch, "A");
        const document = store.ingestKnowledgeDocument(collectionId, { name: `Source ${index}`, content }, project);
        assert.equal(document.chunkCount, plan.chunksPerBatch);
        const worker = register(`batch-${index}`), lease = store.leaseKnowledgeEmbedding(worker.id)!;
        assert.ok(lease); assert.equal(lease.document.id, document.id); assert.equal(lease.chunks.length, plan.chunksPerBatch);
        jobs.push({ node: worker.id, token: worker.token, lease: lease.leaseId, document: String(document.id), collection: collectionId,
          chunkIds: lease.chunks.map(chunk => chunk.id), contentSha256: sha(content), replica: index % 2 });
      }
      const renewals: Renewal[] = [], controls = collection("renewals");
      for (let index = 0; index < 2; index++) {
        const stageWorker = register(`stage-control-${index}`, true);
        const run = store.createRun({ name: `${id}-control-${index}`, input: "No generation", agentIds: [String(agent.id)], approvalRequired: false }, project);
        const stageLease = store.leaseNext(stageWorker.id)!; assert.ok(stageLease); assert.equal(stageLease.run.id, run.id);
        renewals.push({ kind: "stage", node: stageWorker.id, token: stageWorker.token, lease: stageLease.leaseId,
          initialExpiry: stageLease.expiresAt, run: run.id, replica: index });
        const embeddingWorker = register(`embedding-control-${index}`);
        const document = store.ingestKnowledgeDocument(controls, { name: `Renewal ${index}`, content: `Synthetic control ${index}` }, project);
        const embeddingLease = store.leaseKnowledgeEmbedding(embeddingWorker.id)!;
        assert.ok(embeddingLease); assert.equal(embeddingLease.document.id, document.id);
        renewals.push({ kind: "embedding", node: embeddingWorker.id, token: embeddingWorker.token, lease: embeddingLease.leaseId,
          initialExpiry: embeddingLease.expiresAt, document: String(document.id), replica: index });
      }
      phase = { id, jobs, renewals, collections };
      await Promise.all(mains.map(main => main.begin(id)));
      // Disposable credentials only cross this private pipe, never evidence files.
      reply({ type: "prepared", id, jobs: jobs.map(({ token, lease, chunkIds, replica, document, collection, contentSha256 }) =>
        ({ token, lease, chunkIds, replica, document, collection, contentSha256 })),
      renewals: renewals.map(({ kind, token, lease, replica }) => ({ kind, token, lease, replica })) });
    } else if (command.type === "finish") {
      assert.ok(phase); assert.equal(command.id, phase.id);
      const metrics = await Promise.all(mains.map(main => main.finish(phase!.id)));
      const jobs = phase.jobs.map(item => {
        const document = store.db.prepare("SELECT status, embedded_count, chunk_count, content_sha256 FROM knowledge_documents WHERE id = ?").get(item.document)!;
        const job = store.db.prepare("SELECT status, node_id, lease_id, failures FROM knowledge_embedding_jobs WHERE document_id = ?").get(item.document)!;
        assert.equal(document.status, "ready"); assert.equal(document.embedded_count, plan.chunksPerBatch);
        assert.equal(document.chunk_count, plan.chunksPerBatch); assert.equal(document.content_sha256, item.contentSha256);
        assert.equal(job.status, "completed"); assert.equal(job.lease_id, null); assert.equal(job.node_id, null); assert.equal(job.failures, 0);
        const chunks = store.db.prepare("SELECT id, embedding_model, embedding_dimensions, embedding_json FROM knowledge_chunks WHERE document_id = ? ORDER BY ordinal").all(item.document);
        assert.deepEqual(chunks.map(chunk => chunk.id), item.chunkIds);
        for (const chunk of chunks) {
          assert.equal(chunk.embedding_model, model); assert.equal(chunk.embedding_dimensions, plan.dimensions);
          assert.equal(sha(String(chunk.embedding_json)), vectorSha256);
        }
        const events = store.db.prepare("SELECT data_json FROM events WHERE type = 'knowledge.document.ready' AND data_json LIKE ?").all(`%${item.document}%`);
        assert.equal(events.length, 1);
        const event = JSON.parse(String(events[0]!.data_json));
        assert.equal(event.documentId, item.document); assert.equal(event.dimensions, plan.dimensions);
        assert.equal(event.embeddedChunks, plan.chunksPerBatch); assert.equal(event.remainingChunks, 0);
        return { document: item.document, collection: item.collection, replica: item.replica, contentSha256: item.contentSha256,
          chunkIdsSha256: sha(JSON.stringify(item.chunkIds)), chunks: chunks.length, dimensions: plan.dimensions,
          vectorSha256, documentStatus: document.status, jobStatus: job.status, failures: job.failures, readyEvents: events.length };
      });
      const renewals = phase.renewals.map(item => {
        const table = item.kind === "stage" ? "stages" : "knowledge_embedding_jobs";
        const row = store.db.prepare(`SELECT status, node_id, lease_expires_at FROM ${table} WHERE lease_id = ?`).get(item.lease)!;
        assert.ok(row); assert.equal(row.status, "running"); assert.equal(row.node_id, item.node);
        assert.ok(Date.parse(String(row.lease_expires_at)) > Date.now());
        return { kind: item.kind, lease: item.lease, replica: item.replica, status: row.status,
          initialExpiry: item.initialExpiry, finalExpiry: row.lease_expires_at };
      });
      const result = { type: "finished", id: phase.id, verifiedJobs: jobs.length, jobs, renewals, metrics };
      phases.push(result);
      for (const item of phase.renewals) if (item.run) store.cancelRun(item.run, project);
      for (const id of phase.collections) assert.equal(store.deleteKnowledgeCollection(id, project), true);
      store.markWorkerPoolOffline(phase.id);
      phase = undefined; reply(result);
    } else throw new Error("Unknown embedding probe command");
  }
  assert.equal(phase, undefined); assert.equal(phases.length, plan.phases.length);
  exits = await Promise.all(mains.map(main => main.stop()));
  for (const exit of exits) assert.deepEqual(exit, { code: 0, signal: null });
  fs.writeFileSync(path.join(evidence, `server-${layout}.json`), JSON.stringify({ planSha256: sha(planBytes), layout,
    phases, mains: mains.map((main, index) => ({ pid: main.pid, ...exits![index], entry: "apps/coordinator/dist/server.js" })) }, null, 2) + "\n", { flag: "wx" });
} finally {
  lines.close(); if (!exits) await Promise.all(mains.map(main => main.stop()));
  runWithPostgresSystemScope(() => store.close());
}
