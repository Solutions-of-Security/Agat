import assert from "node:assert/strict";
import test from "node:test";
import { AgatStore } from "../src/database.js";
import { KNOWLEDGE_MAX_SEARCH_CANDIDATES } from "../src/knowledge.js";

test("full candidate boundary preserves an old exact match, rejects truncation across collections, and rolls back multi-query search", () => {
  const store = new AgatStore(":memory:", { seedDemo: false });
  try {
    const node = store.registerNode({ enrollmentToken: "unused", name: "retrieval-capacity", platform: "test",
      models: ["test-model"], embeddingModels: ["capacity-fixture"], maxConcurrency: 1 }).id;
    const collection = (name: string) => (store.createKnowledgeCollection({ name, embeddingModel: "capacity-fixture",
      chunkSize: 400, chunkOverlap: 0, topK: 1 }) as { id: string }).id;
    const targetCollection = collection("Old exact source");
    const fillerCollection = collection("Recent orthogonal sources");
    const extraCollection = collection("Newest extra source");
    const target = store.ingestKnowledgeDocument(targetCollection, { name: "Exact target", content: "Synthetic exact target." }) as { id: string };
    const fillers = store.ingestKnowledgeDocument(fillerCollection, { name: "Orthogonal chunks",
      content: "A".repeat((KNOWLEDGE_MAX_SEARCH_CANDIDATES - 1) * 400) }) as { chunkCount: number };
    assert.equal(fillers.chunkCount, KNOWLEDGE_MAX_SEARCH_CANDIDATES - 1);
    store.ingestKnowledgeDocument(extraCollection, { name: "Extra", content: "One additional synthetic orthogonal chunk." });
    // Actual ingestion and embedding-completion paths, with declared 2D fixture vectors.
    for (let lease; (lease = store.leaseKnowledgeEmbedding(node));) {
      store.completeKnowledgeEmbedding(node, lease.leaseId, lease.chunks.map(chunk => ({ chunkId: chunk.id,
        embedding: lease.document.id === target.id ? [1, 0] : [0, 1] })));
    }
    store.db.prepare("UPDATE knowledge_chunks SET embedded_at = '2020-01-01T00:00:00.000Z' WHERE document_id = ?").run(target.id);
    const run = store.createRun({ name: "Capacity boundary", input: "Synthetic exact query", agentIds: ["collector"],
      approvalRequired: false, knowledgeCollectionIds: [targetCollection, fillerCollection, extraCollection] });
    const lease = store.leaseNext(node)!;
    const query = (collectionIds: string[]) => ({ embeddingModel: "capacity-fixture", collectionIds, vector: [1, 0], topK: 1 });
    const search = (queries: ReturnType<typeof query>[]) => store.searchKnowledge(node, lease.leaseId, { queries });
    const retrieved = () => (store.getRunTrace(run.id)!.events as Array<{ type: string }>).filter(event => event.type === "knowledge.retrieved");
    const within = query([targetCollection, fillerCollection]);
    const over = query([targetCollection, fillerCollection, extraCollection]);
    const first = search([within]);
    assert.equal(first.hits.length, 1);assert.equal(first.hits[0]!.score, 1);
    assert.equal(first.hits[0]!.provenance.documentId, target.id);assert.equal(first.hits[0]!.marker, "K1");
    assert.throws(() => search([over]), /Лимит локального retrieval.*5000/);
    assert.equal(retrieved().length, 1);
    assert.throws(() => search([query([targetCollection]), over]), /Лимит локального retrieval.*5000/);
    assert.equal(retrieved().length, 1);assert.equal(store.getRunKnowledgeSources(run.id)!.length, 1);
    const recovered = search([query([targetCollection])]);
    assert.equal(recovered.hits[0]!.provenance.documentId, target.id);assert.equal(recovered.hits[0]!.marker, "K2");
    assert.equal(retrieved().length, 2);
  } finally { store.close(); }
});

test("topK ranks close cosine scores before display rounding", () => {
  const store = new AgatStore(":memory:", { seedDemo: false });
  try {
    const node = store.registerNode({ enrollmentToken: "unused", name: "ranking-precision", platform: "test",
      models: ["test-model"], embeddingModels: ["precision-fixture"], maxConcurrency: 1 }).id;
    const collection = store.createKnowledgeCollection({ name: "Close vectors", embeddingModel: "precision-fixture", topK: 1 }) as { id: string };
    const exact = store.ingestKnowledgeDocument(collection.id, { name: "Old exact", content: "Synthetic exact source." }) as { id: string };
    const nearby = store.ingestKnowledgeDocument(collection.id, { name: "New nearby", content: "Synthetic nearby source." }) as { id: string };
    for (let lease; (lease = store.leaseKnowledgeEmbedding(node));) {
      const vector = lease.document.id === exact.id ? [1, 0] : [1, 0.0005];
      store.completeKnowledgeEmbedding(node, lease.leaseId, lease.chunks.map(chunk => ({ chunkId: chunk.id, embedding: vector })));
    }
    store.db.prepare("UPDATE knowledge_chunks SET embedded_at = '2020-01-01T00:00:00.000Z' WHERE document_id = ?").run(exact.id);
    store.createRun({ name: "Precise topK", input: "Synthetic vector query", agentIds: ["collector"], approvalRequired: false,
      knowledgeCollectionIds: [collection.id] });
    const lease = store.leaseNext(node)!;
    const search = (vector: number[], topK: number) => store.searchKnowledge(node, lease.leaseId,
      { queries: [{ embeddingModel: "precision-fixture", collectionIds: [collection.id], topK, vector }] });
    const result = search([1, 0], 1);
    assert.equal(result.hits[0]!.provenance.documentId, exact.id);
    assert.equal(result.hits[0]!.score, 1);
    const both = search([1, 0], 2);
    assert.deepEqual(both.hits.map(hit => hit.provenance.documentId), [exact.id, nearby.id]);
    assert.deepEqual(both.hits.map(hit => hit.score), [1, 1], "public scores retain six-place display precision");
    assert.equal(search([1, 0.0005], 1).hits[0]!.provenance.documentId, nearby.id);
  } finally { store.close(); }
});
