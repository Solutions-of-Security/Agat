import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { AgatStore } from "../src/database.js";

const stores: AgatStore[] = [];
afterEach(() => { while (stores.length) stores.pop()!.close(); });

function setup(dimensions: number[]) {
  const store = new AgatStore(":memory:", { seedDemo: false });stores.push(store);
  const nodeId = store.registerNode({ enrollmentToken: "unused", name: "retrieval-validation", platform: "test",
    models: ["test-model"], embeddingModels: ["embeddinggemma"], maxConcurrency: 1 }).id;
  const collections = dimensions.map((size, index) => {
    const collection = store.createKnowledgeCollection({ name: `Source ${index}`, embeddingModel: "embeddinggemma", topK: 1 }) as { id: string };
    if (size) {
      store.ingestKnowledgeDocument(collection.id, { name: `Document ${index}`, content: `Recorded fact ${index}.` });
      const lease = store.leaseKnowledgeEmbedding(nodeId)!;
      store.completeKnowledgeEmbedding(nodeId, lease.leaseId, lease.chunks.map(chunk => ({ chunkId: chunk.id, embedding: Array(size).fill(1) })));
    }
    return collection.id;
  });
  const run = store.createRun({ name: "Validated retrieval", input: "Read the selected documents", approvalRequired: false,
    agentIds: ["collector"], knowledgeCollectionIds: collections });
  const lease = store.leaseNext(nodeId)!;assert.ok(lease);
  const query = (vector: number[], ids = collections, model = "embeddinggemma") => ({ embeddingModel: model, collectionIds: ids, vector, topK: 1 });
  const search = (queries: ReturnType<typeof query>[]) => store.searchKnowledge(nodeId, lease.leaseId, { queries });
  const retrieved = () => (store.getRunTrace(run.id)!.events as Array<{ type: string }>).filter(event => event.type === "knowledge.retrieved");
  return { store, collections, run, query, search, retrieved };
}

test("wrong retrieval model or query dimensions fail explicitly without a success trace, and a corrected query succeeds", () => {
  const { query, search, retrieved } = setup([3]);
  assert.throws(() => search([query([1, 1, 1], undefined, "another-model")]), /Embedding-модель retrieval/);
  assert.throws(() => search([query([1, 1])]), /Размерность retrieval/);
  assert.equal(retrieved().length, 0);
  const result = search([query([1, 1, 1])]);
  assert.equal(result.hits.length, 1);assert.equal(result.hits[0]!.marker, "K1");
  assert.equal(retrieved().length, 1);
});

test("every selected collection is validated and one incompatible query rolls the whole retrieval back", () => {
  const { collections, query, search, retrieved } = setup([2, 3]);
  assert.throws(() => search([query([1, 1])]), /Размерность retrieval/);
  assert.throws(() => search([query([1, 1], [collections[0]!]), query([1, 1], [collections[1]!])]), /Размерность retrieval/);
  assert.equal(retrieved().length, 0);
  const result = search([query([1, 1], [collections[0]!]), query([1, 1, 1], [collections[1]!])]);
  assert.equal(result.hits.length, 2);
  assert.deepEqual(new Set(result.hits.map(hit => hit.provenance.collectionId)), new Set(collections));
});

test("a selected empty optional collection remains a valid empty search, but not for the wrong model", () => {
  const { query, search } = setup([0]);
  assert.deepEqual(search([query([1, 1])]).hits, []);
  assert.throws(() => search([query([1, 1], undefined, "another-model")]), /Embedding-модель retrieval/);
});

test("damaged stored vectors are reported as an index error instead of discarded as missing evidence", () => {
  const { store, query, search, retrieved } = setup([2]);
  for (const value of ["broken", "[0,0]", "[1,1,1]"]) {
    store.db.prepare("UPDATE knowledge_chunks SET embedding_json = ?").run(value);
    assert.throws(() => search([query([1, 1])]), /Индекс embeddings/);
    assert.equal(retrieved().length, 0);
  }
  store.db.prepare("UPDATE knowledge_chunks SET embedding_json = '[1,1]'").run();
  assert.equal(search([query([1, 1])]).hits.length, 1);
});
