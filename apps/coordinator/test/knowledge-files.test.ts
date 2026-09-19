import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { AgatStore } from "../src/database.js";
import { KNOWLEDGE_DOCX_MEDIA_TYPE, KNOWLEDGE_MAX_FILE_BYTES, normalizeKnowledgeUpload, parseKnowledgeFile } from "../src/knowledge-files.js";
import { appendKnowledgeSources } from "../src/knowledge-citations.js";
import { pdfFile, docxFile } from "./knowledge-file-fixtures.js";

function setup() {
  const store = new AgatStore(":memory:", { seedDemo: false });
  const collection = store.createKnowledgeCollection({ name: "Files", embeddingModel: "embeddinggemma", chunkSize: 400, chunkOverlap: 40 });
  const node = store.registerNode({ enrollmentToken: "unused", name: "local", platform: "test", models: ["test-model"], embeddingModels: ["embeddinggemma"], maxConcurrency: 1 });
  return { store, collectionId: String(collection.id), nodeId: node.id };
}

test("PDF extraction retains physical page numbers, DOCX paragraphs, tables and Unicode", async () => {
  const pdf = await parseKnowledgeFile(pdfFile(["", "Second page evidence.", "Last page."]), "application/pdf");
  assert.equal(pdf.pages.length, 3);
  assert.equal(pdf.pages[0]!.charEnd, 0);
  assert.equal(pdf.content.slice(pdf.pages[1]!.charStart, pdf.pages[1]!.charEnd), "Second page evidence.");
  const docx = await parseKnowledgeFile(docxFile(), KNOWLEDGE_DOCX_MEDIA_TYPE);
  assert.equal(docx.content, "Проверенный факт & источник\n\nТаблица: 42");
  assert.deepEqual(docx.pages, []);
});

test("parser rejects corrupt, empty and oversized documents and XML entities", async () => {
  await assert.rejects(parseKnowledgeFile(Buffer.from("bad"), "application/pdf"), /сигнатура/);
  await assert.rejects(parseKnowledgeFile(Buffer.from("%PDF-1.7 broken"), "application/pdf"));
  await assert.rejects(parseKnowledgeFile(pdfFile([""]), "application/pdf"), /OCR/);
  await assert.rejects(parseKnowledgeFile(docxFile(""), KNOWLEDGE_DOCX_MEDIA_TYPE), /не содержит/);
  await assert.rejects(parseKnowledgeFile(docxFile("<w:p><w:r><w:t>&external;</w:t></w:r></w:p>"), KNOWLEDGE_DOCX_MEDIA_TYPE));
  await assert.rejects(parseKnowledgeFile(docxFile(`<w:p><w:r><w:t>${"x".repeat(8 * 1024 * 1024)}</w:t></w:r></w:p>`), KNOWLEDGE_DOCX_MEDIA_TYPE), /8 МиБ/);
  assert.throws(() => normalizeKnowledgeUpload({ name: "bad.pdf", mediaType: "application/pdf", contentBase64: "%%%%" }), /base64/);
  assert.throws(() => normalizeKnowledgeUpload({ name: "big.pdf", mediaType: "application/pdf", contentBase64: Buffer.alloc(KNOWLEDGE_MAX_FILE_BYTES + 1).toString("base64") }), /5 МиБ/);
  assert.throws(() => normalizeKnowledgeUpload({ name: "x.exe", mediaType: "application/x-executable", contentBase64: "YQ==" }), /PDF и DOCX/);
});

test("upload, failed parse, preview, reindex leases and project boundaries persist correctly", async () => {
  const { store, collectionId, nodeId } = setup();
  try {
    const bytes = pdfFile();
    const document = await store.uploadKnowledgeDocument(collectionId, { name: "Policy.pdf", mediaType: "application/pdf", contentBase64: bytes.toString("base64") });
    const id = String(document.id);
    assert.equal(document.status, "pending");
    assert.equal(document.pageCount, 2);
    const preview = store.getKnowledgeDocument(id)!;
    const chunks = preview.chunks as Array<{ id: string; pageNumber: number; content: string; charStart: number; charEnd: number }>;
    assert.deepEqual(chunks.map((chunk) => chunk.pageNumber), [1, 2]);
    for (const chunk of chunks) assert.equal(String(preview.content).slice(chunk.charStart, chunk.charEnd), chunk.content);
    assert.deepEqual(store.getKnowledgeDocumentFile(id)!.bytes, bytes);
    const oldLease = store.leaseKnowledgeEmbedding(nodeId)!;
    assert.ok(oldLease);
    const reindexed = await store.reindexKnowledgeDocument(id);
    assert.equal(reindexed!.id, id);
    assert.equal(reindexed!.embeddedCount, 0);
    assert.deepEqual((store.getKnowledgeDocument(id)!.chunks as Array<{ id: string }>).map((chunk) => chunk.id), chunks.map((chunk) => chunk.id));
    assert.throws(() => store.completeKnowledgeEmbedding(nodeId, oldLease.leaseId, oldLease.chunks.map((chunk) => ({ chunkId: chunk.id, embedding: [1, 0] }))), /аренда/);
    const lease = store.leaseKnowledgeEmbedding(nodeId)!;
    store.completeKnowledgeEmbedding(nodeId, lease.leaseId, lease.chunks.map((chunk) => ({ chunkId: chunk.id, embedding: [1, 0] })));
    assert.equal(store.getKnowledgeDocument(id)!.status, "ready");
    store.createProject({ id: "other", name: "Other" });
    assert.equal(store.getKnowledgeDocument(id, "other"), null);
    assert.equal(store.getKnowledgeDocumentFile(id, "other"), null);
    assert.equal(await store.reindexKnowledgeDocument(id, "other"), null);
    await assert.rejects(store.uploadKnowledgeDocument(collectionId, { name: "Foreign.pdf", mediaType: "application/pdf", contentBase64: bytes.toString("base64") }, "other"), /не найдена/);
    await assert.rejects(store.uploadKnowledgeDocument(collectionId, { name: "Policy.pdf", mediaType: "application/pdf", contentBase64: bytes.toString("base64") }), /уже существует/);
    const failed = await store.uploadKnowledgeDocument(collectionId, { name: "Corrupt.pdf", mediaType: "application/pdf", contentBase64: Buffer.from("broken").toString("base64") });
    assert.equal(failed.status, "failed");
    assert.match(String(failed.parseError), /Ошибка разбора/);
    assert.equal(failed.chunkCount, 0);
    assert.equal(store.leaseKnowledgeEmbedding(nodeId), null);
    assert.equal((await store.reindexKnowledgeDocument(String(failed.id)))!.status, "failed");
  } finally { store.close(); }
});

test("retrieval and reports preserve auditable page/fragment snapshots through reindex and deletion", async () => {
  const { store, collectionId, nodeId } = setup();
  try {
    const document = await store.uploadKnowledgeDocument(collectionId, { name: "Evidence.pdf", mediaType: "application/pdf", contentBase64: pdfFile().toString("base64") });
    const lease = store.leaseKnowledgeEmbedding(nodeId)!;
    store.completeKnowledgeEmbedding(nodeId, lease.leaseId, lease.chunks.map((chunk, i) => ({ chunkId: chunk.id, embedding: i === 1 ? [1, 0] : [0, 1] })));
    const run = store.createRun({ name: "Report", input: "Evidence", approvalRequired: false, agentIds: ["collector"], knowledgeCollectionIds: [collectionId] });
    const stage = store.leaseNext(nodeId)!;
    assert.ok(stage);
    await assert.rejects(store.reindexKnowledgeDocument(String(document.id)), /активный запуск/);
    const hit = store.searchKnowledge(nodeId, stage.leaseId, { queries: [{ embeddingModel: "embeddinggemma", collectionIds: [collectionId], vector: [1, 0], topK: 1 }] }).hits[0]!;
    assert.equal(hit.provenance.pageNumber, 2);
    assert.equal(hit.provenance.originalSha256, document.originalSha256);
    const secondHit = store.searchKnowledge(nodeId, stage.leaseId, { queries: [{ embeddingModel: "embeddinggemma", collectionIds: [collectionId], vector: [1, 0], topK: 1 }] }).hits[0]!;
    assert.equal(secondHit.marker, "K2", "new retrievals do not reuse citation markers within a run");
    const sources = store.getRunKnowledgeSources(run.id)!;
    assert.equal(sources[0]!.content, hit.content);
    assert.equal(sources[0]!.stageId, stage.stage.id);
    assert.equal(store.getRunKnowledgeSources(run.id, "other"), null);
    const report = appendKnowledgeSources("Facts [K1]", sources);
    assert.ok(report.includes(`documentId=${document.id}`));
    assert.ok(report.includes(`chunkId=${hit.provenance.chunkId}`));
    assert.match(report, /page=2/);
    assert.ok(report.includes(hit.content));
    store.cancelRun(run.id);
    await store.reindexKnowledgeDocument(String(document.id));
    assert.deepEqual(store.getRunKnowledgeSources(run.id), sources);
    store.deleteKnowledgeDocument(String(document.id));
    assert.deepEqual(store.getRunKnowledgeSources(run.id), sources);
  } finally { store.close(); }
});

test("process result artifacts contain sources and failed embedding jobs can be restarted", async () => {
  const directory = mkdtempSync(path.join(tmpdir(), "agat-knowledge-process-"));
  const store = new AgatStore(":memory:", { seedDemo: false, artifactsDir: directory });
  try {
    const collection = store.createKnowledgeCollection({ name: "Process sources", embeddingModel: "embeddinggemma" });
    const document = await store.uploadKnowledgeDocument(String(collection.id), { name: "Input.docx", mediaType: KNOWLEDGE_DOCX_MEDIA_TYPE, contentBase64: docxFile().toString("base64") });
    const worker = store.registerNode({ enrollmentToken: "unused", name: "local", platform: "test", models: ["test-model"], embeddingModels: ["embeddinggemma"], maxConcurrency: 1 });
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const lease = store.leaseKnowledgeEmbedding(worker.id)!;
      store.failKnowledgeEmbedding(worker.id, lease.leaseId, "Temporary model failure");
    }
    assert.equal(store.getKnowledgeDocument(String(document.id))!.status, "failed");
    const reset = await store.reindexKnowledgeDocument(String(document.id));
    assert.equal(reset!.status, "pending");
    assert.equal(reset!.error, null);
    const embeddingLease = store.leaseKnowledgeEmbedding(worker.id)!;
    store.completeKnowledgeEmbedding(worker.id, embeddingLease.leaseId, embeddingLease.chunks.map((chunk) => ({ chunkId: chunk.id, embedding: [1, 0] })));
    const nodes = [
      { id: "start", type: "start" as const, name: "Start", position: { x: 0, y: 0 }, config: {} },
      { id: "agent", type: "agent" as const, name: "Report", position: { x: 100, y: 0 }, config: { agentId: "collector", approvalRequired: false } },
      { id: "end", type: "end" as const, name: "End", position: { x: 200, y: 0 }, config: {} },
    ];
    const process = store.createProcess({ name: "Auditable report", graph: { nodes, edges: [
      { id: "a", source: "start", target: "agent", branch: "default" }, { id: "b", source: "agent", target: "end", branch: "default" },
    ] } });
    store.publishProcess(String(process.id));
    const instance = store.startProcess(String(process.id), { version: 1, startMode: "queue", input: "Report", knowledgeCollectionIds: [String(collection.id)], resultDestination: "artifacts" })!;
    const lease = store.leaseNext(worker.id)!;
    assert.ok(lease);
    const hit = store.searchKnowledge(worker.id, lease.leaseId, { queries: [{ embeddingModel: "embeddinggemma", collectionIds: [String(collection.id)], vector: [1, 0] }] }).hits[0]!;
    assert.equal(hit.provenance.pageNumber, null);
    store.completeLease(worker.id, lease.leaseId, "Report [K1]");
    assert.equal((store.getRunTrace(String(instance.runId))!.run as { status: string }).status, "completed");
    const artifact = store.db.prepare("SELECT id FROM artifacts WHERE run_id = ? AND kind = 'result'").get(String(instance.runId))!;
    const result = readFileSync(store.getArtifactDownload(String(artifact.id))!.filePath, "utf8");
    assert.ok(result.includes(`documentId=${document.id}`));
    assert.ok(result.includes(`chunkId=${hit.provenance.chunkId}`));
    assert.ok(result.includes(hit.provenance.chunkSha256));
    assert.match(result, /Проверенный факт/);
  } finally { store.close(); rmSync(directory, { recursive: true, force: true }); }
});

test("existing text-only SQLite databases migrate without losing document content", async () => {
  const directory = mkdtempSync(path.join(tmpdir(), "agat-knowledge-migration-"));
  const filename = path.join(directory, "state.db");
  try {
    const store = new AgatStore(filename, { seedDemo: false });
    const collection = store.createKnowledgeCollection({ name: "Legacy", embeddingModel: "embed" });
    const document = store.ingestKnowledgeDocument(String(collection.id), { name: "Legacy text", content: "Keep this content." });
    store.close();
    const db = new DatabaseSync(filename);
    for (const column of ["original_base64", "original_sha256", "pages_json", "parse_error"]) db.exec(`ALTER TABLE knowledge_documents DROP COLUMN ${column}`);
    db.exec("ALTER TABLE knowledge_chunks DROP COLUMN page_number");
    db.close();
    const reopened = new AgatStore(filename, { seedDemo: false });
    try {
      assert.equal(reopened.getKnowledgeDocument(String(document.id))!.content, "Keep this content.");
      assert.equal(reopened.getKnowledgeDocument(String(document.id))!.hasOriginal, false);
      assert.equal((await reopened.reindexKnowledgeDocument(String(document.id)))!.status, "pending");
    } finally { reopened.close(); }
  } finally { rmSync(directory, { recursive: true, force: true }); }
});
