import assert from "node:assert/strict";
import test from "node:test";
import { loadConfig } from "../src/config.js";
import { AgatStore } from "../src/database.js";
import { normalizeKnowledgeSearchMaxCandidates } from "../src/knowledge.js";
import { rankKnowledgeCandidates } from "../src/knowledge-ranking.js";

test("operator candidate limit is strict, defaults to 5000 and cannot exceed 10000", () => {
  assert.equal(normalizeKnowledgeSearchMaxCandidates(undefined), 5000);
  for (const value of [1, 5000, 6001, 10000]) assert.equal(normalizeKnowledgeSearchMaxCandidates(value), value);
  for (const value of [0, -1, 10001, 2.5, Infinity, NaN, "6001", null, true]) {
    assert.throws(() => normalizeKnowledgeSearchMaxCandidates(value), /от 1 до 10000/);
    assert.throws(() => new AgatStore(":memory:", { knowledgeSearchMaxCandidates: value as number }), /от 1 до 10000/);
  }
  const previous = process.env.AGAT_KNOWLEDGE_SEARCH_MAX_CANDIDATES;
  try {
    delete process.env.AGAT_KNOWLEDGE_SEARCH_MAX_CANDIDATES;
    assert.equal(loadConfig().knowledgeSearchMaxCandidates, 5000);
    for (const value of ["1", "5000", " 6001 ", "10000"]) {
      process.env.AGAT_KNOWLEDGE_SEARCH_MAX_CANDIDATES = value;
      assert.equal(loadConfig().knowledgeSearchMaxCandidates, Number(value));
    }
    for (const value of ["", "5000x", "5e3", "0x1388", "1.5", "10001", "-1"]) {
      process.env.AGAT_KNOWLEDGE_SEARCH_MAX_CANDIDATES = value;
      assert.throws(() => loadConfig(), /AGAT_KNOWLEDGE_SEARCH_MAX_CANDIDATES/);
    }
  } finally {
    if (previous === undefined) delete process.env.AGAT_KNOWLEDGE_SEARCH_MAX_CANDIDATES;
    else process.env.AGAT_KNOWLEDGE_SEARCH_MAX_CANDIDATES = previous;
  }
});

test("shared scorer accepts its exact hard boundary and rejects the next candidate without truncation", () => {
  function* candidates(count: number) {
    for (let id = 1; id <= count; id++) yield { id, embedding_json: id === count ? "[1,0]" : "[0,1]", embedding_dimensions: 2 };
  }
  const last = rankKnowledgeCandidates(candidates(10000), { vector: [1, 0], topK: 1, maxCandidates: 10000 });
  assert.equal(last[0]!.candidate.id, 10000);
  assert.throws(() => rankKnowledgeCandidates(candidates(10001), { vector: [1, 0], topK: 1, maxCandidates: 10000 }), /retrieval.*10000/);
  assert.throws(() => rankKnowledgeCandidates(candidates(5001), { vector: [1, 0], topK: 1 }), /retrieval.*5000/);
});

test("SQLite uses only the operator budget, preserves an old match above 5000 and records the applied limit", () => {
  const store = new AgatStore(":memory:", { seedDemo: false, knowledgeSearchMaxCandidates: 6001 });
  try {
    const node = store.registerNode({ enrollmentToken: "unused", name: "search-budget", platform: "test",
      models: ["test-model"], embeddingModels: ["budget-fixture"], maxConcurrency: 1 }).id;
    const collection = (name: string) => String(store.createKnowledgeCollection({ name,
      embeddingModel: "budget-fixture", chunkSize: 400, chunkOverlap: 0 }).id);
    const main = collection("Main"), extra = collection("Extra");
    const target = store.ingestKnowledgeDocument(main, { name: "Old best", content: "Old exact source." });
    for (let index = 0; index < 2; index++) {
      store.ingestKnowledgeDocument(main, { name: `Filler ${index}`, content: "A".repeat(3000 * 400) });
    }
    store.ingestKnowledgeDocument(extra, { name: "Over budget", content: "One extra candidate." });
    for (let lease; (lease = store.leaseKnowledgeEmbedding(node));) {
      store.completeKnowledgeEmbedding(node, lease.leaseId, lease.chunks.map(chunk => ({ chunkId: chunk.id,
        embedding: lease.document.id === target.id ? [1, 0] : [0, 1] })));
    }
    store.db.prepare("UPDATE knowledge_chunks SET embedded_at = '2020-01-01T00:00:00.000Z' WHERE document_id = ?").run(String(target.id));
    const run = store.createRun({ name: "Budget", input: "Synthetic query", agentIds: ["collector"],
      approvalRequired: false, knowledgeCollectionIds: [main, extra] });
    const lease = store.leaseNext(node)!;
    const query = (ids: string[]) => ({ embeddingModel: "budget-fixture", collectionIds: ids, vector: [1, 0], topK: 1,
      maxCandidates: 10000 }); // An extra field from the worker must not override the operator.
    const result = store.searchKnowledge(node, lease.leaseId, { queries: [query([main])] });
    assert.equal(result.hits[0]!.provenance.documentId, target.id);
    assert.throws(() => store.searchKnowledge(node, lease.leaseId, { queries: [query([main]), query([main, extra])] }), /retrieval.*6001/);
    assert.equal(store.getRunKnowledgeSources(run.id)!.length, 1);
    const saved = store.db.prepare("SELECT queries_json FROM knowledge_retrievals WHERE run_id = ?").all(run.id);
    assert.equal(saved.length, 1);
    assert.equal(JSON.parse(String(saved[0]!.queries_json))[0].candidateLimit, 6001);
    assert.equal(store.searchKnowledge(node, lease.leaseId, { queries: [query([main])] }).hits[0]!.marker, "K2");
  } finally { store.close(); }
});
