import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { AgatStore } from "../src/database.js";
import { chunkKnowledgeText, cosineSimilarity, normalizeEmbeddingVector } from "../src/knowledge.js";

const stores: AgatStore[] = [];

afterEach(() => {
  while (stores.length) stores.pop()?.close();
});

function createStore(): AgatStore {
  const store = new AgatStore(":memory:", { seedDemo: false });
  stores.push(store);
  return store;
}

function createEmbeddingNode(store: AgatStore, name = "rag-node"): string {
  return store.registerNode({
    enrollmentToken: "unused-at-store-layer",
    name,
    platform: "test",
    models: ["test-model"],
    embeddingModels: ["embeddinggemma"],
    maxConcurrency: 1,
  }).id;
}

describe("local knowledge and memory", () => {
  it("indexes locally, retrieves with provenance and records an auditable trace", () => {
    const store = createStore();
    const nodeId = createEmbeddingNode(store);
    const collection = store.createKnowledgeCollection({
      name: "Product handbook",
      description: "Internal product facts",
      embeddingModel: "embeddinggemma",
      chunkSize: 400,
      chunkOverlap: 40,
      topK: 3,
    }) as { id: string };
    const document = store.ingestKnowledgeDocument(collection.id, {
      name: "Pricing policy",
      sourceUri: "agat://handbook/pricing",
      mediaType: "text/markdown",
      content: "Enterprise pricing requires an annual contract. All customer data remains on the local deployment.",
    }) as { id: string; status: string; chunkCount: number };
    assert.equal(document.status, "pending");
    assert.equal(document.chunkCount, 1);

    const embeddingLease = store.leaseKnowledgeEmbedding(nodeId);
    assert.ok(embeddingLease);
    assert.equal(embeddingLease.collection.id, collection.id);
    assert.equal(embeddingLease.collection.embeddingModel, "embeddinggemma");
    assert.equal(embeddingLease.chunks.length, 1);
    assert.deepEqual(
      store.completeKnowledgeEmbedding(nodeId, embeddingLease.leaseId, [{
        chunkId: embeddingLease.chunks[0]!.id,
        embedding: [1, 0, 0],
      }]),
      { completed: true, remainingChunks: 0 },
    );

    const memory = store.saveMemory({
      kind: "episodic",
      agentId: "collector",
      content: "The operator explicitly approved using the 2026 product handbook.",
    }) as { id: string; kind: string; explicitlySaved: boolean };
    assert.equal(memory.kind, "episodic");
    assert.equal(memory.explicitlySaved, true);

    const run = store.createRun({
      name: "Answer from handbook",
      input: "Where is customer data stored?",
      approvalRequired: false,
      agentIds: ["collector"],
      knowledgeCollectionIds: [collection.id],
    });
    const stageLease = store.leaseNext(nodeId)!;
    assert.deepEqual(stageLease.knowledge.groups, [{
      embeddingModel: "embeddinggemma",
      collectionIds: [collection.id],
      topK: 3,
    }]);
    assert.equal(stageLease.knowledge.memory.some((entry) => entry.id === memory.id), true);
    assert.throws(
      () => store.deleteKnowledgeCollection(collection.id),
      /активный запуск/,
    );

    const search = store.searchKnowledge(nodeId, stageLease.leaseId, {
      queries: [{
        embeddingModel: "embeddinggemma",
        collectionIds: [collection.id],
        topK: 3,
        vector: [1, 0, 0],
      }],
    });
    assert.equal(search.hits.length, 1);
    assert.equal(search.hits[0]?.marker, "K1");
    assert.equal(search.hits[0]?.provenance.documentId, document.id);
    assert.equal(search.hits[0]?.provenance.sourceUri, "agat://handbook/pricing");
    assert.equal(search.hits[0]?.provenance.chunkSha256.length, 64);

    const trace = store.getRunTrace(run.id)!;
    const events = trace.events as Array<{ type: string; data: Record<string, unknown> | null }>;
    const retrieval = events.find((event) => event.type === "knowledge.retrieved");
    assert.equal(retrieval?.data?.kind, "knowledge");
    assert.equal(Array.isArray(retrieval?.data?.hits), true);

    const snapshot = store.getKnowledgeSnapshot() as {
      documents: Array<{ id: string; status: string; embeddedCount: number }>;
      counts: { embeddedChunks: number; pendingJobs: number; activeMemory: number };
    };
    assert.equal(snapshot.documents.find((item) => item.id === document.id)?.status, "ready");
    assert.equal(snapshot.counts.embeddedChunks, 1);
    assert.equal(snapshot.counts.pendingJobs, 0);
    assert.equal(snapshot.counts.activeMemory, 1);

    const exported = store.exportKnowledge() as {
      schemaVersion: number;
      collections: Array<{ documents: Array<{ content: string; chunks: Array<{ contentSha256: string }> }> }>;
      retrievals: Array<{ runId: string }>;
    };
    assert.equal(exported.schemaVersion, 1);
    assert.equal(exported.collections[0]?.documents[0]?.content, "Enterprise pricing requires an annual contract. All customer data remains on the local deployment.");
    assert.equal(exported.collections[0]?.documents[0]?.chunks[0]?.contentSha256.length, 64);
    assert.equal(exported.retrievals[0]?.runId, run.id);
  });

  it("enforces project snapshots and embedding dimensions", () => {
    const store = createStore();
    store.createProject({ id: "research", name: "Research" });
    const nodeId = createEmbeddingNode(store, "isolation-node");
    const collection = store.createKnowledgeCollection({
      name: "Research only",
      embeddingModel: "embeddinggemma",
    }, "research") as { id: string };
    store.ingestKnowledgeDocument(collection.id, {
      name: "First",
      content: "A project-scoped fact.",
    }, "research");
    const firstLease = store.leaseKnowledgeEmbedding(nodeId)!;
    store.completeKnowledgeEmbedding(nodeId, firstLease.leaseId, [{
      chunkId: firstLease.chunks[0]!.id,
      embedding: [1, 0],
    }]);

    store.ingestKnowledgeDocument(collection.id, {
      name: "Second",
      content: "Another project-scoped fact.",
    }, "research");
    const secondLease = store.leaseKnowledgeEmbedding(nodeId)!;
    assert.throws(() => store.completeKnowledgeEmbedding(nodeId, secondLease.leaseId, [{
      chunkId: secondLease.chunks[0]!.id,
      embedding: [1, 0, 0],
    }]), /Размерность embeddings/);

    assert.throws(() => store.createRun({
      name: "Cross-project run",
      input: "Must fail",
      approvalRequired: false,
      knowledgeCollectionIds: [collection.id],
    }), /неизвестную knowledge collection/);
    assert.equal(store.deleteKnowledgeCollection(collection.id), false);
    assert.equal((store.getKnowledgeSnapshot() as { collections: unknown[] }).collections.length, 0);
    assert.equal((store.getKnowledgeSnapshot("research") as { collections: unknown[] }).collections.length, 1);
  });

  it("accounts embedding work in the same global scheduler budget", () => {
    const store = createStore();
    store.updateModelRouterPolicy({ enabled: false });
    const stageNodeId = store.registerNode({
      enrollmentToken: "unused",
      name: "stage-node",
      platform: "test",
      models: ["test-model"],
    }).id;
    const embeddingNodeId = createEmbeddingNode(store, "embedding-node");
    const collection = store.createKnowledgeCollection({
      name: "Queued knowledge",
      embeddingModel: "embeddinggemma",
    }) as { id: string };
    store.ingestKnowledgeDocument(collection.id, {
      name: "Queued document",
      content: "A fact waiting for an embedding slot.",
    });
    store.createRun({
      name: "Busy stage",
      input: "Keep the sequential slot busy",
      approvalRequired: false,
      agentIds: ["collector"],
    });

    assert.ok(store.leaseNext(stageNodeId));
    assert.equal(store.leaseKnowledgeEmbedding(embeddingNodeId), null);

    store.updateScheduler("parallel");
    assert.ok(store.leaseKnowledgeEmbedding(embeddingNodeId));
  });

  it("chunks at readable boundaries and rejects unsafe vectors", () => {
    const chunks = chunkKnowledgeText(
      `${"A".repeat(260)}. ${"B".repeat(260)}. ${"C".repeat(260)}`,
      400,
      40,
    );
    assert.equal(chunks.length >= 2, true);
    assert.equal(chunks[0]!.charEnd > chunks[0]!.charStart, true);
    assert.equal(cosineSimilarity([1, 0], [1, 0]), 1);
    assert.equal(cosineSimilarity([1, 0], [0, 1]), 0);
    assert.equal(cosineSimilarity([1], [1, 0]), null);
    assert.throws(() => normalizeEmbeddingVector([0, 0]), /нулевым/);
    assert.throws(() => normalizeEmbeddingVector([Number.NaN]), /некорректное/);
  });
});
