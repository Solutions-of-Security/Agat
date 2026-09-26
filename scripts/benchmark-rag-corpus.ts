/** Bounded, local corpus diagnostic. Run through run-rag-corpus.py. */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { AgatStore } from "../apps/coordinator/src/database.js";
import { chunkKnowledgeText, normalizeKnowledgeDocumentInput, normalizeEmbeddingVector } from "../apps/coordinator/src/knowledge.js";
import { migratePostgresSchemaAndAdmit } from "../apps/coordinator/src/postgres-schema-migrator.js";
import { runWithPostgresSystemScope } from "../apps/coordinator/src/postgres-database.js";
import { embeddingIdentity, embeddingSettings } from "./lib/decision-rag.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sha = (value: string | Buffer) => createHash("sha256").update(value).digest("hex");
const round = (value: number) => Math.round(value * 1000) / 1000;
const args = process.argv.slice(2);
assert.equal(args.length, 4, "Expected phase, disposable directory, evidence directory, backend/ref/url argument");
const [phase, dataDirectory, evidenceDirectory, option] = args as [string, string, string, string];
const data = fs.realpathSync(dataDirectory), evidence = fs.realpathSync(evidenceDirectory);
assert.ok(path.basename(data).startsWith("agat-rag-corpus-") && path.dirname(data) === fs.realpathSync(os.tmpdir()), "Owned temporary directory required");
assert.ok(evidence.startsWith(`${fs.realpathSync(path.join(root, "docs"))}${path.sep}`), "Evidence must be inside docs");
const read = (name: string) => JSON.parse(fs.readFileSync(path.join(data, name), "utf8"));
const write = (directory: string, name: string, value: unknown) => fs.writeFileSync(path.join(directory, name), `${JSON.stringify(value, null, 2)}\n`, { flag: "wx" });
const sourceFiles = ["scripts/benchmark-rag-corpus.ts", "scripts/run-rag-corpus.py", "scripts/lib/rag_corpus.py",
  "apps/coordinator/src/database.ts", "apps/coordinator/src/knowledge.ts", "apps/coordinator/src/postgres-database.ts",
  "apps/coordinator/src/postgres-worker.ts", "apps/coordinator/src/postgres-response-buffer.ts",
  "apps/coordinator/src/sync-database.ts", "scripts/lib/decision-rag.ts", "package-lock.json"];
const sources = () => Object.fromEntries(sourceFiles.map(name => [name, sha(fs.readFileSync(path.join(root, name)))]));
const queries = [
  "Как запретить worker доступ к облачным моделям и оставлять документы в локальном контуре?",
  "Как сохранить и восстановить состояние PostgreSQL после потери региона?",
  "Как устроены bucket versioning, legal hold и удаление артефактов в S3?",
  "Как отозвать скомпрометированный credential и сохранить журнал аудита?",
  "Как включить shadow decision runtime и сохранить primary fallback при timeout?",
  "Какие условия нужны для безопасного исполнения OCI и WASI tools?",
  "Как восстановить Temporal workflow и избежать повторного выполнения внешнего действия?",
  "Как изолированы документы, очереди и права разных проектов в Fleet?",
];

async function json(url: string, body?: unknown, timeout = 30_000) {
  const response = await fetch(url, { method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(timeout), redirect: "error" });
  assert.equal(response.status, 200, `Local model HTTP ${response.status}`);
  const text = await response.text();
  assert.ok(Buffer.byteLength(text) <= 4 * 1024 * 1024, "Unexpected model response size");
  return JSON.parse(text);
}

if (phase === "plan") {
  assert.match(option, /^[a-f0-9]{40}$/);
  const git = (...command: string[]) => execFileSync("git", command, { cwd: root, encoding: "utf8", maxBuffer: 4 * 1024 * 1024 });
  assert.equal(git("rev-parse", `${option}^{commit}`).trim(), option);
  const names = git("ls-tree", "--name-only", `${option}:docs`).trim().split("\n").filter(name => /^[^/]+\.md$/.test(name)).sort();
  assert.ok(names.length >= 20 && names.length <= 100);
  const documents = names.map(name => {
    const raw = git("show", `${option}:docs/${name}`);
    const normalized = normalizeKnowledgeDocumentInput({ name, sourceUri: `agat://rag-corpus/docs/${name}`, content: raw, mediaType: "text/markdown" });
    return { ...normalized, path: `docs/${name}`, rawSha256: sha(raw), contentSha256: sha(normalized.content),
      chunks: chunkKnowledgeText(normalized.content, 400, 40).map(chunk => ({ ...chunk, sha256: sha(chunk.content) })) };
  });
  const chunks = documents.reduce((sum, document) => sum + document.chunks.length, 0);
  assert.ok(chunks >= 1000 && chunks <= 5000, "Corpus must fit the stated exact-search boundary");
  write(data, "corpus.json", documents);
  const plan = { schemaVersion: "agat.rag.corpus-plan.v1", createdAt: new Date().toISOString(), sourceCommit: option,
    corpusSha256: sha(fs.readFileSync(path.join(data, "corpus.json"))),
    model: "embeddinggemma:latest", modelDigest: "85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1",
    embeddingSettings, dimensions: 768, chunkSize: 400, chunkOverlap: 40, topK: 8, passes: 5, queries,
    documentCount: documents.length, chunkCount: chunks,
    documents: documents.map(({ content: _content, chunks: parts, ...document }) => ({ ...document, chunks: parts.length })),
    sourceSha256: sources(), nodeVersion: process.version,
    design: "One first pass plus four repeats; one fresh search process per backend; no model inference during retrieval",
    scope: "Repository technical documents; direct AgatStore calls, no HTTP, worker scheduling, answer generation or independent semantic labels" };
  write(evidence, "plan.json", plan);
  console.log(`plan: ${documents.length} documents, ${chunks} chunks`);
} else {
  const planBytes = fs.readFileSync(path.join(evidence, "plan.json"));
  const plan = JSON.parse(planBytes.toString());
  assert.deepEqual(sources(), plan.sourceSha256, "Implementation changed after the plan was frozen");
  const planSha256 = sha(planBytes);
  const model = plan.model as string;
  if (phase === "embed") {
    const identity = await embeddingIdentity(option, model, plan.modelDigest, json);
    assert.equal(sha(fs.readFileSync(path.join(data, "corpus.json"))), plan.corpusSha256);
    const documents = read("corpus.json");
    const unique = new Map<string, string>();
    for (const document of documents) for (const chunk of document.chunks) unique.set(chunk.sha256, chunk.content);
    const inputs = [...unique.entries()];
    const vectors: Array<{ contentSha256: string; vector: number[] }> = [];
    const batches = [];
    for (let start = 0; start < inputs.length; start += 16) {
      const batch = inputs.slice(start, start + 16), began = performance.now();
      const result = await json(`${option}/api/embed`, { model, input: batch.map(([, content]) => content), ...embeddingSettings }, 60_000);
      assert.equal(result.model, model);assert.equal(result.embeddings.length, batch.length);
      for (let index = 0; index < batch.length; index++) {
        const vector = normalizeEmbeddingVector(result.embeddings[index]);assert.equal(vector.length, plan.dimensions);
        vectors.push({ contentSha256: batch[index]![0], vector });
      }
      batches.push({ items: batch.length, wallMs: round(performance.now() - began), inputTokens: result.prompt_eval_count,
        nativeTotalMs: result.total_duration / 1e6, nativeLoadMs: result.load_duration / 1e6 });
      if (start % 256 === 0) console.log(`embedding: ${Math.min(start + 16, inputs.length)}/${inputs.length}`);
    }
    const queryResult = await json(`${option}/api/embed`, { model, input: plan.queries, ...embeddingSettings }, 60_000);
    assert.equal(queryResult.model, model);assert.equal(queryResult.embeddings.length, plan.queries.length);
    const queryVectors = queryResult.embeddings.map((value: unknown) => { const vector = normalizeEmbeddingVector(value);assert.equal(vector.length, plan.dimensions);return vector; });
    assert.deepEqual(await embeddingIdentity(option, model, plan.modelDigest, json), identity);
    write(data, "vectors.json", vectors);write(data, "queries.json", queryVectors);
    write(evidence, "embedding.json", { planSha256, identity, uniqueChunks: inputs.length, batches,
      queryInputTokens: queryResult.prompt_eval_count, queryVectors, queryVectorsSha256: sha(JSON.stringify(queryVectors)),
      vectorsSha256: sha(fs.readFileSync(path.join(data, "vectors.json"))),
      vectorDigests: vectors.map(({ contentSha256, vector }) => ({ contentSha256, vectorSha256: sha(JSON.stringify(vector)) })) });
    console.log(`embedding: complete (${inputs.length} unique chunks)`);
  } else {
    assert.ok(phase === "ingest" || phase === "search");assert.ok(option === "sqlite" || option === "postgresql");
    if (option === "postgresql") {
      for (const key of ["AGAT_POSTGRES_URL", "AGAT_POSTGRES_TENANT_URL", "AGAT_POSTGRES_MIGRATION_URL"]) {
        const url = new URL(process.env[key] ?? "");
        assert.equal(url.hostname, "127.0.0.1");assert.equal(url.pathname, "/agat_rag_corpus");assert.ok(url.port);
      }
      assert.equal(process.env.AGAT_RAG_CORPUS_DISPOSABLE, "1");
      if (phase === "ingest") await migratePostgresSchemaAndAdmit();
    }
    const store = new AgatStore(option === "sqlite" ? path.join(data, "state.db") : ":postgresql:", {
      seedDemo: false, stateStoreDriver: option, artifactsDir: path.join(data, `artifacts-${option}`),
      region: "eu-test-1", residencyDomain: "eu-test", requireSignedWorkerReleases: false,
      ...(option === "postgresql" ? { postgresSchemaMode: "runtime" as const,
        coordinatorInstanceId: `rag-corpus-${phase}`, postgres: { systemUrl: process.env.AGAT_POSTGRES_URL!, tenantUrl: process.env.AGAT_POSTGRES_TENANT_URL!,
          roleMode: "runtime" as const, applicationName: "rag-corpus", poolMax: 1, connectTimeoutMs: 5000, idleTimeoutMs: 30000,
          statementTimeoutMs: 30000, sslMode: "disable" as const } } : {}) });
    try {
      const project = "rag-corpus";
      if (phase === "ingest") {
        assert.equal(sha(fs.readFileSync(path.join(data, "corpus.json"))), plan.corpusSha256);
        const embedding = JSON.parse(fs.readFileSync(path.join(evidence, "embedding.json"), "utf8"));
        assert.equal(sha(fs.readFileSync(path.join(data, "vectors.json"))), embedding.vectorsSha256);
        const documents = read("corpus.json");
        const vectors = new Map<string, number[]>(read("vectors.json").map((item: any) => [item.contentSha256, item.vector]));
        store.createProject({ id: project, name: project, homeRegion: "eu-test-1", allowedRegions: ["eu-test-1"], residencyDomain: "eu-test" });
        const agent = store.createAgent({ name: "Corpus query", role: "diagnostic", systemPrompt: "No generation", model: "corpus-no-generation" }, project);
        store.updateModelRouterPolicy({ enabled: false });
        const node = store.registerNode({ enrollmentToken: "unused", name: "corpus", platform: "diagnostic", models: ["corpus-no-generation"],
          embeddingModels: [model], maxConcurrency: 1, region: "eu-test-1", residencyDomain: "eu-test" }).id;
        const collection = String(store.createKnowledgeCollection({ name: "Pinned technical corpus", embeddingModel: model,
          chunkSize: plan.chunkSize, chunkOverlap: plan.chunkOverlap, topK: plan.topK }, project).id);
        for (const document of documents) {
          const ingested = store.ingestKnowledgeDocument(collection, document, project);
          assert.equal(ingested.chunkCount, document.chunks.length);
        }
        let indexed = 0;
        for (let lease; (lease = store.leaseKnowledgeEmbedding(node));) {
          store.completeKnowledgeEmbedding(node, lease.leaseId, lease.chunks.map(chunk => {
            const vector = vectors.get(sha(chunk.content));assert.ok(vector, "Unknown chunk content");
            indexed++;return { chunkId: chunk.id, embedding: vector };
          }));
        }
        assert.equal(indexed, plan.chunkCount);
        const actual = store.db.prepare("SELECT COUNT(*) AS count FROM knowledge_chunks WHERE embedding_json IS NOT NULL").get();
        assert.equal(Number(actual?.count), indexed);
        write(data, `state-${option}.json`, { node, collection, agent: String(agent.id), indexed });
        write(evidence, `ingest-${option}.json`, { planSha256, backend: option, indexed, documents: documents.length });
        console.log(`ingest ${option}: ${indexed} chunks`);
      } else {
        // This fresh process never reads corpus texts or the full vector cache.
        const state = read(`state-${option}.json`), queryVectors: number[][] = read("queries.json");
        const embedding = JSON.parse(fs.readFileSync(path.join(evidence, "embedding.json"), "utf8"));
        assert.equal(sha(JSON.stringify(queryVectors)), embedding.queryVectorsSha256);
        const oracle = JSON.parse(fs.readFileSync(path.join(evidence, "oracle.json"), "utf8"));
        assert.equal(oracle.planSha256, planSha256);
        const baseline = { memory: process.memoryUsage(), peakRssBytes: process.resourceUsage().maxRSS * 1024 };
        const attempts = [];
        for (let pass = 0; pass < plan.passes; pass++) for (let query = 0; query < plan.queries.length; query++) {
          const run = store.createRun({ name: `Corpus ${pass}/${query}`, input: plan.queries[query], agentIds: [state.agent],
            approvalRequired: false, knowledgeCollectionIds: [state.collection] }, project);
          const lease = store.leaseNext(state.node)!;assert.equal(lease.run.id, run.id);
          const cpu = process.cpuUsage(), before = performance.now();
          const result = store.searchKnowledge(state.node, lease.leaseId, { queries: [{ collectionIds: [state.collection],
            embeddingModel: model, vector: queryVectors[query]!, topK: plan.topK }] });
          const wallMs = round(performance.now() - before), cpuUsed = process.cpuUsage(cpu);
          const memory = process.memoryUsage(), peakRssBytes = process.resourceUsage().maxRSS * 1024;
          const hits = result.hits.map(hit => ({ key: `${hit.provenance.documentName}:${hit.provenance.chunkOrdinal}`,
            contentSha256: sha(hit.content), score: hit.score, documentSha256: hit.provenance.documentSha256, marker: hit.marker }));
          const expected = oracle.queries[query];
          assert.equal(hits.length, plan.topK);assert.equal(new Set(hits.map(hit => hit.key)).size, plan.topK);
          for (const hit of hits) {
            const reference = expected.eligible.find((item: any) => item.key === hit.key);assert.ok(reference, "Incorrect topK member");
            assert.equal(hit.contentSha256, reference.contentSha256);assert.equal(hit.documentSha256, reference.documentSha256);
            assert.ok(Math.abs(hit.score - reference.score) <= 0.00000050001, "Incorrect cosine score");
          }
          assert.ok(expected.required.every((key: string) => hits.some(hit => hit.key === key)), "Missing strictly better candidate");
          const events = (store.getRunTrace(run.id, project)!.events as Array<{ type: string }>).filter(event => event.type === "knowledge.retrieved");
          assert.equal(events.length, 1);assert.deepEqual(hits.map(hit => hit.marker), hits.map((_, i) => `K${i + 1}`));
          store.completeLease(state.node, lease.leaseId, "completed", []);
          attempts.push({ pass, query, wallMs, cpu: cpuUsed, memory, peakRssBytes, hits, retrievalEvents: events.length });
          console.log(`search ${option}: ${pass}/${query} ${wallMs}ms`);
        }
        write(evidence, `search-${option}.json`, { schemaVersion: "agat.rag.corpus-search.v1", planSha256, backend: option,
          nodeVersion: process.version, pid: process.pid, baseline, attempts, finalPeakRssBytes: process.resourceUsage().maxRSS * 1024,
          limit: "RSS includes all threads in this Node process; excludes PostgreSQL server and Ollama. First pass is not a cold filesystem cache." });
      }
    } finally { runWithPostgresSystemScope(() => store.close()); }
  }
}
