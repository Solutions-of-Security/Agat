/** Disposable, instrumented instance of the real coordinator HTTP handler. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { monitorEventLoopDelay } from "node:perf_hooks";
import { createInterface } from "node:readline";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { AgatStore } from "../apps/coordinator/src/database.js";
import { loadConfig } from "../apps/coordinator/src/config.js";
import { createCoordinatorServer } from "../apps/coordinator/src/server.js";
import { runWithPostgresSystemScope } from "../apps/coordinator/src/postgres-database.js";
import { migratePostgresSchemaAndAdmit } from "../apps/coordinator/src/postgres-schema-migrator.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
assert.equal(process.argv.length, 5);
const data = fs.realpathSync(process.argv[2]!), evidence = fs.realpathSync(process.argv[3]!);
const backend = process.argv[4]!;
assert.ok(["sqlite", "postgresql"].includes(backend));
assert.ok(path.basename(data).startsWith("agat-rag-http-") && path.dirname(data) === fs.realpathSync(os.tmpdir()));
assert.ok(evidence.startsWith(`${fs.realpathSync(path.join(root, "docs"))}${path.sep}`));
const planBytes = fs.readFileSync(path.join(evidence, "plan.json"));
const plan = JSON.parse(planBytes.toString());
const sha = (bytes: Buffer | string) => createHash("sha256").update(bytes).digest("hex");
for (const [name, expected] of Object.entries(plan.sourceSha256)) {
  assert.equal(sha(fs.readFileSync(path.join(root, name))), expected, `Changed source: ${name}`);
}
assert.equal(plan.candidates, 9716); assert.equal(plan.dimensions, 768);
assert.equal(plan.candidateLimit, 10000); assert.equal(plan.nodeVersion, process.version);
if (backend === "postgresql") {
  assert.equal(process.env.AGAT_RAG_HTTP_DISPOSABLE, "1");
  for (const key of ["AGAT_POSTGRES_URL", "AGAT_POSTGRES_TENANT_URL", "AGAT_POSTGRES_MIGRATION_URL"]) {
    const url = new URL(process.env[key]!);
    assert.equal(url.hostname, "127.0.0.1"); assert.equal(url.pathname, "/agat_rag_http"); assert.ok(url.port);
  }
  await migratePostgresSchemaAndAdmit();
}
const store = new AgatStore(backend === "sqlite" ? path.join(data, "state.sqlite") : ":postgresql:", {
  seedDemo: false, stateStoreDriver: backend as "sqlite" | "postgresql",
  artifactsDir: path.join(data, "artifacts"), knowledgeSearchMaxCandidates: plan.candidateLimit,
  region: "eu-test-1", residencyDomain: "eu-test", requireSignedWorkerReleases: false,
  ...(backend === "postgresql" ? { postgresSchemaMode: "runtime" as const,
    coordinatorInstanceId: "rag-http-probe", postgres: { systemUrl: process.env.AGAT_POSTGRES_URL!,
      tenantUrl: process.env.AGAT_POSTGRES_TENANT_URL!, roleMode: "runtime" as const,
      applicationName: "rag-http-probe", poolMax: 1, connectTimeoutMs: 5000,
      idleTimeoutMs: 30000, statementTimeoutMs: 30000, sslMode: "disable" as const } } : {}),
});
const config = { ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false,
  knowledgeSearchMaxCandidates: plan.candidateLimit,
  adminToken: "rag-http-disposable-admin", oidcEnabled: false, sandboxEnabled: false,
  a2aEnabled: false, mcpEnabled: false, siemEnabled: false, localWorkerLauncherEnabled: false };
const server = createCoordinatorServer(config, store);
const lines = createInterface({ input: process.stdin });
const lag = monitorEventLoopDelay({ resolution: 10 });
const reply = (value: unknown) => process.stdout.write(`${JSON.stringify(value)}\n`);
const round = (value: number) => Math.round(value * 1000) / 1000;
const project = "rag-http-probe", model = "rag-http-fixture";
const vector = Array<number>(plan.dimensions).fill(1 / 3);
const orthogonal = vector.map((value, index) => index % 2 ? -value : value);
const phases: unknown[] = [];
let databaseCandidates = 0;
let phase: { id: string; leases: Array<{ node: string; lease: string; run: string }> } | undefined;
try {
  store.createProject({ id: project, name: project, homeRegion: "eu-test-1", allowedRegions: ["eu-test-1"], residencyDomain: "eu-test" });
  store.updateScheduler("sequential", 4); store.updateModelRouterPolicy({ enabled: false });
  const agent = store.createAgent({ name: "HTTP load fixture", role: "diagnostic", systemPrompt: "No generation", model }, project);
  const registerNode = (name: string) => store.registerNode({ enrollmentToken: "unused", name,
    platform: "diagnostic", models: [model], embeddingModels: [model], maxConcurrency: 1,
    region: "eu-test-1", residencyDomain: "eu-test" });
  const indexer = registerNode("rag-http-indexer");
  const collection = String(store.createKnowledgeCollection({ name: "Synthetic HTTP load", embeddingModel: model,
    chunkSize: 400, chunkOverlap: 0, topK: 1 }, project).id);
  const target = store.ingestKnowledgeDocument(collection, { name: "Old exact winner", content: "Synthetic exact-match fixture.",
    sourceUri: "agat://rag-http/old-winner" }, project);
  for (let remaining = plan.candidates - 1, index = 0; remaining > 0; index++) {
    const count = Math.min(3000, remaining); remaining -= count;
    const document = store.ingestKnowledgeDocument(collection, { name: `Orthogonal filler ${index}`, content: "A".repeat(count * 400) }, project);
    assert.equal(document.chunkCount, count);
  }
  let indexed = 0;
  for (let lease; (lease = store.leaseKnowledgeEmbedding(indexer.id));) {
    store.completeKnowledgeEmbedding(indexer.id, lease.leaseId, lease.chunks.map(chunk => {
      indexed++; return { chunkId: chunk.id, embedding: lease.document.id === target.id ? vector : orthogonal };
    }));
  }
  assert.equal(indexed, plan.candidates);
  databaseCandidates = Number(store.db.prepare(`SELECT count(*) AS count FROM knowledge_chunks ch
    JOIN knowledge_documents d ON d.id = ch.document_id JOIN knowledge_collections c ON c.id = ch.collection_id
    WHERE c.project_id = ? AND c.id = ? AND c.embedding_model = ? AND ch.embedding_model = ?
      AND ch.embedding_dimensions = ? AND ch.embedding_json IS NOT NULL AND d.status = 'ready'`)
    .get(project, collection, model, model, plan.dimensions)!.count);
  assert.equal(databaseCandidates, plan.candidates);
  const nodes = Array.from({ length: 4 }, (_, index) => registerNode(`rag-http-${index}`));
  store.db.prepare("UPDATE knowledge_chunks SET embedded_at = '2020-01-01T00:00:00.000Z' WHERE document_id = ?").run(String(target.id));
  await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  const address = server.address(); assert.ok(address && typeof address === "object");
  reply({ type: "ready", port: address.port, indexed, databaseCandidates, target: target.id, nodeVersion: process.version,
    query: { queries: [{ embeddingModel: model, collectionIds: [collection], vector, topK: 1 }] } });
  for await (const line of lines) {
    assert.ok(line.length <= 2048);
    const command = JSON.parse(line);
    if (command.type === "stop") { assert.equal(phase, undefined); break; }
    if (command.type === "prepare") {
      assert.equal(phase, undefined); assert.match(command.id, /^(warmup|idle|c[124]-r[123])$/);
      assert.ok([0, 1, 2, 4].includes(command.concurrency));
      assert.ok(phases.length < 11 && !phases.some((row: any) => row.id === command.id));
      assert.equal(command.concurrency, command.id === "idle" ? 0 : command.id === "warmup" ? 1 : Number(command.id[1]));
      const leases = Array.from({ length: command.concurrency }, (_, index) => {
        const node = nodes[index]!;
        const run = store.createRun({ name: command.id, input: "Synthetic load query", agentIds: [String(agent.id)],
          approvalRequired: false, knowledgeCollectionIds: [collection] }, project);
        const lease = store.leaseNext(node.id)!; assert.equal(lease.run.id, run.id);
        return { node: node.id, token: node.token, lease: lease.leaseId, run: run.id };
      });
      phase = { id: command.id, leases };
      lag.reset(); lag.enable(); await delay(20);
      // Disposable bearer credentials only cross this private pipe; evidence excludes them.
      reply({ type: "prepared", id: command.id, leases: leases.map(({ lease, token }) => ({ lease, token })) });
    } else if (command.type === "finish") {
      assert.ok(phase); assert.equal(command.id, phase.id);
      await delay(20); lag.disable();
      const eventLoop = { maxMs: round(lag.max / 1e6), p99Ms: round(lag.percentile(99) / 1e6), samples: lag.count };
      const retrievals = [];
      for (const item of phase.leases) {
        const events = (store.getRunTrace(item.run, project)!.events as Array<{ type: string; data: any }>).filter(row => row.type === "knowledge.retrieved");
        assert.equal(events.length, 1);
        const hit = events[0]!.data.hits[0];
        assert.equal(events[0]!.data.hits.length, 1); assert.equal(hit.provenance.documentId, target.id);
        assert.equal(hit.score, 1); assert.equal(hit.marker, "K1");
        assert.equal(events[0]!.data.queries[0].candidateLimit, plan.candidateLimit);
        retrievals.push({ runId: item.run, events: events.length, documentId: hit.provenance.documentId,
          score: hit.score, marker: hit.marker, candidateLimit: events[0]!.data.queries[0].candidateLimit });
        store.completeLease(item.node, item.lease, "completed", []);
      }
      const result = { type: "finished", id: phase.id, verifiedRetrievals: phase.leases.length, retrievals, eventLoop,
        memory: process.memoryUsage(), processLifetimePeakRssBytes: process.resourceUsage().maxRSS * 1024 };
      phases.push(result); phase = undefined; reply(result);
    } else { throw new Error("Unknown probe control command"); }
  }
  assert.equal(phase, undefined);
  fs.writeFileSync(path.join(evidence, `server-${backend}.json`), `${JSON.stringify({ planSha256: sha(planBytes),
    backend, nodeVersion: process.version, indexed, databaseCandidates, target: target.id, phases }, null, 2)}\n`, { flag: "wx" });
} finally {
  lag.disable(); lines.close(); server.closeAllConnections();
  if (server.listening) await new Promise<void>(resolve => server.close(() => resolve()));
  runWithPostgresSystemScope(() => store.close());
}
