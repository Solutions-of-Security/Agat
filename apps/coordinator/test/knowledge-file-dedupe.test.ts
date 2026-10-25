import assert from "node:assert/strict";
import { test } from "node:test";
import { AgatStore } from "../src/database.js";
import { pdfFile, acceptFixtureExtraction } from "./knowledge-file-fixtures.js";
import { KnowledgeParserBusyError } from "../src/knowledge-files.js";

function fixture() {
  const store = new AgatStore(":memory:", { seedDemo: false });
  const collection = String(store.createKnowledgeCollection({ name: "Sources", embeddingModel: "fixture-embedding" }).id);
  return { store, collection };
}
const payload = (name: string, bytes = pdfFile(["Synthetic facts"]), sourceUri = "") => ({ name, mediaType: "application/pdf", contentBase64: bytes.toString("base64"), sourceUri });

test("renamed uploads reuse exact original bytes without resetting extraction, jobs, vectors or provenance", async () => {
  const { store, collection } = fixture();
  try {
    const bytes = pdfFile(["Synthetic facts"]);
    const first = await store.uploadKnowledgeDocument(collection, payload("Original.pdf", bytes, "agat://test/source"));
    assert.deepEqual(first.upload, { disposition: "created", requestedName: "Original.pdf" });
    const before = store.getKnowledgeDocument(String(first.id))!;
    const events = store.listEvents(0, 100).length;
    const duplicate = await store.uploadKnowledgeDocument(collection, payload("Renamed.pdf", bytes));
    assert.deepEqual(duplicate.upload, { disposition: "existing", requestedName: "Renamed.pdf" });
    assert.equal(duplicate.name, "Original.pdf"); assert.equal(duplicate.id, first.id);
    assert.equal(duplicate.sourceUri, "agat://test/source"); assert.equal(duplicate.status, "review_required");
    assert.deepEqual(store.getKnowledgeDocument(String(first.id)), before);
    assert.equal(store.listEvents(0, 100).length, events);
    acceptFixtureExtraction(store, first);
    const node = store.registerNode({ enrollmentToken: "test", name: "fixture", platform: "test", models: [], embeddingModels: ["fixture-embedding"] });
    const lease = store.leaseKnowledgeEmbedding(node.id)!;
    store.completeKnowledgeEmbedding(node.id, lease.leaseId, lease.chunks.map(chunk => ({ chunkId: chunk.id, embedding: [1, 0] })));
    const ready = store.getKnowledgeDocument(String(first.id))!;
    const again = await store.uploadKnowledgeDocument(collection, payload("Original.pdf", bytes, "agat://test/source"));
    assert.equal(again.status, "ready"); assert.equal(again.id, first.id);
    assert.deepEqual(store.getKnowledgeDocument(String(first.id)), ready);
    assert.equal(store.leaseKnowledgeEmbedding(node.id), null);
    assert.deepEqual(store.getKnowledgeDocumentFile(String(first.id))!.bytes, bytes);
    assert.equal(store.db.prepare("SELECT COUNT(*) AS count FROM knowledge_documents WHERE collection_id = ?").get(collection)!.count, 1);
  } finally { store.close(); }
});

test("different bytes, MIME, source intent and project boundaries do not silently share or overwrite a document", async () => {
  const { store, collection } = fixture();
  try {
    const bytes = pdfFile(["Synthetic facts"]);
    const original = await store.uploadKnowledgeDocument(collection, payload("Original.pdf", bytes, "agat://test/source"));
    await assert.rejects(store.uploadKnowledgeDocument(collection, payload("Renamed.pdf", bytes, "agat://other/source")), /другой источник/);
    await assert.rejects(store.uploadKnowledgeDocument(collection, { ...payload("Wrong.csv", bytes), mediaType: "text/csv" }), /другим форматом/);
    await assert.rejects(store.uploadKnowledgeDocument(collection, payload("Original.pdf", pdfFile(["Changed facts"]))), /уже существует/);
    // Equal extracted text is insufficient: another file may contain different visual material.
    const altered = await store.uploadKnowledgeDocument(collection, payload("Different bytes.pdf", Buffer.concat([bytes, Buffer.from("% different original bytes\n")])));
    assert.notEqual(altered.id, original.id); assert.equal(altered.contentSha256, original.contentSha256);
    assert.notEqual(altered.originalSha256, original.originalSha256); assert.equal(altered.status, "review_required");
    const otherCollection = String(store.createKnowledgeCollection({ name: "Other", embeddingModel: "fixture-embedding" }).id);
    assert.notEqual((await store.uploadKnowledgeDocument(otherCollection, payload("Copy.pdf", bytes))).id, original.id);
    store.createProject({ id: "foreign", name: "Foreign" });
    await assert.rejects(store.uploadKnowledgeDocument(collection, payload("Foreign.pdf", bytes), "foreign"), /не найдена/);
    const foreign = String(store.createKnowledgeCollection({ name: "Sources", embeddingModel: "fixture-embedding" }, "foreign").id);
    assert.notEqual((await store.uploadKnowledgeDocument(foreign, payload("Copy.pdf", bytes), "foreign")).id, original.id);
    assert.equal(store.getKnowledgeDocument(String(original.id))!.sourceUri, "agat://test/source");
  } finally { store.close(); }
});

test("concurrent cold CSV uploads create one document, embedding job and queued event", async () => {
  const { store, collection } = fixture();
  try {
    const input = { name: "One.csv", mediaType: "text/csv", contentBase64: Buffer.from("period,total\n2026-09,120\n").toString("base64") };
    const attempts = await Promise.allSettled([store.uploadKnowledgeDocument(collection, input), store.uploadKnowledgeDocument(collection, { ...input, name: "Two.csv" }), store.uploadKnowledgeDocument(collection, { ...input, name: "Three.csv" })]);
    const documents = attempts.filter((result): result is PromiseFulfilledResult<Record<string, unknown>> => result.status === "fulfilled").map(result => result.value);
    const rejected = attempts.filter((result): result is PromiseRejectedResult => result.status === "rejected");
    assert.equal(rejected.length, 1); assert.ok(rejected[0]!.reason instanceof KnowledgeParserBusyError);
    assert.equal(documents[0]!.id, documents[1]!.id);
    assert.deepEqual(documents.map(doc => (doc.upload as { disposition: string }).disposition).sort(), ["created", "existing"]);
    assert.equal(store.db.prepare("SELECT COUNT(*) AS count FROM knowledge_embedding_jobs WHERE collection_id = ?").get(collection)!.count, 1);
    assert.equal(store.listEvents(0, 100).filter(event => event.type === "knowledge.document.queued").length, 1);
  } finally { store.close(); }
});

test("failed parses and legacy duplicate rows remain inspectable; reuse never implies indexing or review", async () => {
  const { store, collection } = fixture();
  try {
    const bad = payload("Broken.pdf", Buffer.from("broken"));
    const failed = await store.uploadKnowledgeDocument(collection, bad);
    const repeated = await store.uploadKnowledgeDocument(collection, { ...bad, name: "Renamed broken.pdf" });
    assert.equal(repeated.id, failed.id); assert.equal(repeated.status, "failed"); assert.equal(repeated.chunkCount, 0);
    const text = store.ingestKnowledgeDocument(collection, { name: "Same text", content: "Synthetic facts" });
    const valid = await store.uploadKnowledgeDocument(collection, payload("Valid.pdf"));
    assert.notEqual(valid.id, text.id);
    store.db.prepare(`INSERT INTO knowledge_documents(id, collection_id, name, content_sha256, content, created_at, updated_at, original_sha256, original_base64, media_type)
      SELECT 'legacy-copy', collection_id, 'Legacy copy', content_sha256, content, created_at, updated_at, original_sha256, original_base64, media_type
      FROM knowledge_documents WHERE id = ?`).run(String(valid.id));
    store.db.prepare("UPDATE knowledge_documents SET extraction_state = 'legacy_unknown', extraction_report_json = NULL, extraction_review_json = NULL, extraction_revision = NULL, status = 'review_required' WHERE id = ?").run(String(valid.id));
    const unknown = await store.uploadKnowledgeDocument(collection, payload("Legacy renamed.pdf"));
    assert.equal(unknown.id, valid.id); assert.equal(unknown.status, "review_required");
    assert.equal((unknown.extraction as { state: string }).state, "legacy_unknown");
    assert.equal(store.db.prepare("SELECT COUNT(*) AS count FROM knowledge_documents WHERE collection_id = ? AND original_sha256 = ?").get(collection, String(valid.originalSha256))!.count, 2);
  } finally { store.close(); }
});

test("temporary parser saturation during reindex preserves the current text and exact review", async () => {
  const { store, collection } = fixture();
  try {
    const document = await store.uploadKnowledgeDocument(collection, payload("Reviewed.pdf"));
    acceptFixtureExtraction(store, document);
    const before = store.getKnowledgeDocument(String(document.id));
    const pending = [1, 2].map(value => store.uploadKnowledgeDocument(collection, { name: `Load ${value}.csv`, mediaType: "text/csv",
      contentBase64: Buffer.from(`period,total\n2026-09,${value}\n`).toString("base64") }));
    try {
      await assert.rejects(store.reindexKnowledgeDocument(String(document.id)), KnowledgeParserBusyError);
      assert.deepEqual(store.getKnowledgeDocument(String(document.id)), before);
    } finally { await Promise.all(pending); }
    assert.deepEqual(store.getKnowledgeDocument(String(document.id)), before);
  } finally { store.close(); }
});
