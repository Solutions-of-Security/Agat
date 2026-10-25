import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { test } from "node:test";
import { strToU8, unzipSync, zipSync } from "fflate";
import { AgatStore } from "../src/database.js";
import { parseKnowledgeFile, KNOWLEDGE_DOCX_MEDIA_TYPE } from "../src/knowledge-files.js";
import { appendKnowledgeSources } from "../src/knowledge-citations.js";
import { pdfFile, docxFile, fixtureExtractionReview, acceptFixtureExtraction } from "./knowledge-file-fixtures.js";
import type { KnowledgeExtractionReport } from "../src/knowledge-extraction.js";

function setup(filename = ":memory:") {
  const store = new AgatStore(filename, { seedDemo: false });
  store.updateModelRouterPolicy({ enabled: false });
  const collection = String(store.createKnowledgeCollection({ name: "Sources", embeddingModel: "fixture-embedding", chunkSize: 400, chunkOverlap: 0 }).id);
  const node = store.registerNode({ enrollmentToken: "test", name: "fixture", platform: "test", models: ["fixture-model"], embeddingModels: ["fixture-embedding"] }).id;
  return { store, collection, node };
}
const upload = (store: AgatStore, collection: string) => store.uploadKnowledgeDocument(collection, {
  name: "Mixed.pdf", mediaType: "application/pdf", contentBase64: pdfFile(["100 requests", "", "120 requests"]).toString("base64") });

test("coverage records physical empty PDF pages and omitted DOCX stories/features without asserting completeness", async () => {
  const pdf = await parseKnowledgeFile(pdfFile(["Evidence.", "", "More evidence."]), "application/pdf");
  assert.equal(pdf.extraction.totalPages, 3); assert.deepEqual(pdf.extraction.emptyPages, [2]);
  assert.equal(pdf.extraction.fidelity, "not_assessed"); assert.equal(pdf.extraction.reviewRequired, true);
  assert.equal(pdf.content.slice(pdf.pages[2]!.charStart, pdf.pages[2]!.charEnd), "More evidence.");
  const files = unzipSync(docxFile('<w:p><w:r><w:t>Main body.</w:t><w:drawing/><w:fldSimple/><w:ins/></w:r></w:p>'));
  files["word/header1.xml"] = strToU8("header not extracted");
  files["word/footnotes.xml"] = strToU8("footnote not extracted");
  const docx = await parseKnowledgeFile(zipSync(files), KNOWLEDGE_DOCX_MEDIA_TYPE);
  assert.equal(docx.content, "Main body."); assert.deepEqual(docx.pages, []);
  assert.deepEqual(docx.extraction.omittedParts, ["word/footnotes.xml", "word/header1.xml"]);
  assert.deepEqual(docx.extraction.features, ["embedded_content", "fields", "tracked_changes"]);
  assert.equal(docx.extraction.reviewRequired, true);
});

test("review blocks embeddings/model lease until exact source acknowledgement; snapshot and report retain limitations", async () => {
  const { store, collection, node } = setup();
  try {
    const document = await upload(store, collection);
    const preview = store.getKnowledgeDocument(String(document.id))!;
    assert.match(String(preview.content), /100 requests/); assert.equal((preview.pages as unknown[]).length, 3);
    assert.equal(document.status, "review_required"); assert.equal(store.leaseKnowledgeEmbedding(node), null);
    // A restored/stale job is not authority to expose unreviewed file text to an embedding model.
    store.db.prepare(`INSERT INTO knowledge_embedding_jobs(id, project_id, collection_id, document_id, status, created_at, updated_at)
      VALUES ('stale-fixture-job', 'default', ?, ?, 'pending', ?, ?)`)
      .run(collection, String(document.id), new Date().toISOString(), new Date().toISOString());
    assert.equal(store.leaseKnowledgeEmbedding(node), null);
    store.db.prepare("DELETE FROM knowledge_embedding_jobs WHERE id = 'stale-fixture-job'").run();
    const run = store.createRun({ name: "Source coverage fixture", input: "Compare", agentIds: ["collector"], knowledgeCollectionIds: [collection], approvalRequired: false });
    assert.equal(store.leaseNext(node), null);
    const request = fixtureExtractionReview(document);
    assert.throws(() => store.reviewKnowledgeExtraction(String(document.id), { ...request, reason: " " }), /причину/);
    assert.throws(() => store.reviewKnowledgeExtraction(String(document.id), { ...request, originalSha256: "a".repeat(64) }), /изменился/);
    store.createProject({ id: "foreign", name: "Foreign" });
    assert.equal(store.reviewKnowledgeExtraction(String(document.id), request, "foreign"), null);
    const reviewed = store.reviewKnowledgeExtraction(String(document.id), request, "default", "fixture-owner")!;
    assert.equal(reviewed.status, "pending");
    assert.deepEqual(store.reviewKnowledgeExtraction(String(document.id), request, "default", "retry-actor"), reviewed);
    assert.equal(store.db.prepare("SELECT COUNT(*) AS count FROM knowledge_embedding_jobs WHERE document_id = ?").get(String(document.id))!.count, 1);
    const event = store.listEvents(0, 100).filter(event => event.type === "knowledge.extraction.reviewed");
    assert.equal(event.length, 1); assert.ok(!JSON.stringify(event).includes(request.reason));
    const embedding = store.leaseKnowledgeEmbedding(node)!; assert.ok(embedding);
    store.completeKnowledgeEmbedding(node, embedding.leaseId, embedding.chunks.map(chunk => ({ chunkId: chunk.id, embedding: [1, 0] })));
    const stage = store.leaseNext(node)!; assert.ok(stage);
    const source = store.searchKnowledge(node, stage.leaseId, { queries: [{ embeddingModel: "fixture-embedding", collectionIds: [collection], vector: [1, 0] }] }).hits[0]!;
    assert.equal(source.provenance.extraction!.emptyPageCount, 1);
    assert.equal(source.provenance.extraction!.extractionRevision, request.extractionRevision);
    assert.equal(source.provenance.extraction!.decision, "use_extracted_text_only");
    const markdown = appendKnowledgeSources("Fixture", store.getRunKnowledgeSources(run.id)!);
    assert.match(markdown, /Визуальная полнота не подтверждена/); assert.match(markdown, /страниц без текста: 1/);
    store.cancelRun(run.id);
  } finally { store.close(); }
});

test("restart preserves acknowledgement; reindex of identical bytes invalidates the prior revision and keeps history append-only", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "agat-extraction-review-")); const filename = path.join(root, "state.sqlite");
  let { store, collection, node } = setup(filename);
  try {
    const document = await upload(store, collection); const request = fixtureExtractionReview(document);
    const old = acceptFixtureExtraction(store, document)!;
    store.close(); store = new AgatStore(filename, { seedDemo: false });
    assert.deepEqual(store.getKnowledgeDocument(String(document.id))!.extraction, old.extraction);
    const next = (await store.reindexKnowledgeDocument(String(document.id)))!;
    assert.equal(next.originalSha256, document.originalSha256); assert.equal(next.contentSha256, document.contentSha256);
    assert.equal(next.status, "review_required"); assert.equal(store.leaseKnowledgeEmbedding(node), null);
    assert.throws(() => store.reviewKnowledgeExtraction(String(document.id), request), /изменился/);
    acceptFixtureExtraction(store, next);
    assert.equal(store.listEvents(0, 200).filter(event => event.type === "knowledge.extraction.reviewed").length, 2);
  } finally { store.close(); rmSync(root, { recursive: true, force: true }); }
});

test("legacy file coverage remains unknown and cannot reuse ready vectors or invent a review", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "agat-extraction-legacy-")); const filename = path.join(root, "state.sqlite");
  let { store, collection, node } = setup(filename);
  try {
    const document = await upload(store, collection); const request = fixtureExtractionReview(document);
    acceptFixtureExtraction(store, document);
    const lease = store.leaseKnowledgeEmbedding(node)!;
    store.completeKnowledgeEmbedding(node, lease.leaseId, lease.chunks.map(chunk => ({ chunkId: chunk.id, embedding: [1, 0] })));
    store.close();
    const legacy = new DatabaseSync(filename);
    for (const name of ["extraction_state", "extraction_report_json", "extraction_review_json", "extraction_revision"]) legacy.exec(`ALTER TABLE knowledge_documents DROP COLUMN ${name}`);
    legacy.close(); store = new AgatStore(filename, { seedDemo: false });
    const migrated = store.getKnowledgeDocument(String(document.id))!;
    assert.equal(migrated.status, "review_required");
    assert.equal((migrated.extraction as { state: string }).state, "legacy_unknown");
    assert.equal((migrated.extraction as { report: KnowledgeExtractionReport | null }).report, null);
    assert.match(String(migrated.content), /120 requests/); assert.equal(store.leaseKnowledgeEmbedding(node), null);
    assert.throws(() => store.reviewKnowledgeExtraction(String(document.id), request), /неизвестного/);
    const repaired = (await store.reindexKnowledgeDocument(String(document.id)))!;
    acceptFixtureExtraction(store, repaired);
    assert.ok(store.leaseKnowledgeEmbedding(node));
  } finally { store.close(); rmSync(root, { recursive: true, force: true }); }
});
