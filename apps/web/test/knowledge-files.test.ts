import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readKnowledgeFile, MAX_KNOWLEDGE_FILE_BYTES, knowledgeReport, knowledgeSourceHref } from "../src/knowledge.ts";
import { KnowledgePreviewContent } from "../src/components/KnowledgeDocumentPreview.tsx";
import { KnowledgeLinkedOutput, KnowledgeSources } from "../src/components/KnowledgeSources.tsx";
import type { KnowledgeDocumentPreview, KnowledgeSource } from "../src/types.ts";

const source: KnowledgeSource = { marker: "K1", stageId: "stage-a", retrievalId: "retrieval-a", createdAt: "2026-09-19", excerpt: "Page two", content: "Page two evidence",
  provenance: { projectId: "default", collectionId: "collection", collectionName: "Docs", documentId: "document", documentName: "Policy.pdf",
    documentSha256: "a".repeat(64), chunkId: "chunk-2", chunkOrdinal: 1, charStart: 11, charEnd: 28,
    chunkSha256: "b".repeat(64), originalSha256: "c".repeat(64), pageNumber: 2, sourceUri: null } };
const document: KnowledgeDocumentPreview = {
  id: "document", collectionId: "collection", collectionName: "Docs", embeddingModel: "embed", name: "Policy.pdf", sourceUri: null,
  mediaType: "application/pdf", contentSha256: "a".repeat(64), status: "ready", error: null, parseError: null, chunkCount: 2, embeddedCount: 2,
  createdAt: "2026-09-19", updatedAt: "2026-09-19", content: "First page\nPage two evidence",
  pages: [{ pageNumber: 1, charStart: 0, charEnd: 10 }, { pageNumber: 2, charStart: 11, charEnd: 28 }],
  chunks: [{ id: "chunk-1", ordinal: 0, content: "First page", charStart: 0, charEnd: 10, pageNumber: 1, contentSha256: "first" },
    { id: "chunk-2", ordinal: 1, content: "Page two evidence", charStart: 11, charEnd: 28, pageNumber: 2, contentSha256: "second" }],
};

test("file selection sends PDF/DOCX bytes losslessly and keeps plain text supported", async () => {
  const bytes = new Uint8Array([37, 80, 68, 70, 0, 255, 128]);
  const binary = await readKnowledgeFile(new File([bytes], "Local.PDF", { type: "application/octet-stream" }));
  assert.ok("contentBase64" in binary);
  assert.equal(binary.mediaType, "application/pdf");
  assert.deepEqual(new Uint8Array(Buffer.from(binary.contentBase64, "base64")), bytes);
  const docx = await readKnowledgeFile(new File([bytes], "local.docx"));
  assert.ok("contentBase64" in docx);
  assert.match(docx.mediaType, /wordprocessingml/);
  assert.deepEqual(await readKnowledgeFile(new File(["Текст"], "notes.md")), { name: "notes.md", mediaType: "text/plain", content: "Текст" });
  await assert.rejects(readKnowledgeFile(new File([new Uint8Array(MAX_KNOWLEDGE_FILE_BYTES + 1)], "large.pdf")), /5 МиБ/);
  await assert.rejects(readKnowledgeFile(new File([], "empty.docx")), /пуст/);
  await assert.rejects(readKnowledgeFile(new File([bytes], "app.exe")), /PDF, DOCX/);
});

test("preview targets PDF page and exact fragment, exposes parsing and stale-source errors safely", () => {
  const html = renderToStaticMarkup(createElement(KnowledgePreviewContent, { document, chunkId: "chunk-2", expectedSha256: document.contentSha256 }));
  assert.match(html, /value="2" selected=""/);
  assert.match(html, /<mark>Page two evidence<\/mark>/);
  const stale = renderToStaticMarkup(createElement(KnowledgePreviewContent, { document, chunkId: "missing", expectedSha256: "old" }));
  assert.match(stale, /Текст документа изменился/);
  assert.match(stale, /Фрагмент из отчёта отсутствует/);
  assert.ok(!stale.includes("<mark>"));
  const failed = renderToStaticMarkup(createElement(KnowledgePreviewContent, { document: { ...document, chunks: [], status: "failed", parseError: "Ошибка разбора <script>alert(1)</script>" } }));
  assert.match(failed, /role="alert"/);
  assert.ok(failed.includes("&lt;script&gt;"));
  assert.ok(!failed.includes("<script>"));
  const docx = renderToStaticMarkup(createElement(KnowledgePreviewContent, { document: { ...document, pages: [], chunks: document.chunks.map((chunk) => ({ ...chunk, pageNumber: null })) }, chunkId: "chunk-2", pageNumber: 1 }));
  assert.match(docx, /Фрагмент документа/);
  assert.match(docx, /<mark>Page two evidence<\/mark>/);
});

test("report source links target the precise document/page/chunk and preserve citations in downloaded Markdown", () => {
  const href = knowledgeSourceHref(source);
  assert.match(href, /^#knowledge\?/);
  assert.match(href, /documentId=document/);
  assert.match(href, /chunkId=chunk-2/);
  assert.match(href, /page=2/);
  const html = renderToStaticMarkup(createElement(KnowledgeSources, { sources: [source], stages: [] }));
  assert.match(html, /Policy.pdf · стр. 2 · фрагмент 2/);
  assert.match(html, /href="#knowledge\?/);
  assert.match(html, /Page two evidence/);
  const report = knowledgeReport("Report [K1]", [source], "https://agat.example/#runs/run");
  assert.match(report, /https:\/\/agat.example\/#knowledge\?/);
  assert.ok(report.includes(source.provenance.chunkSha256));
  assert.ok(report.includes(source.content!));
  assert.ok(report.includes("stage-a"));
  assert.equal(knowledgeReport("No sources", [], "https://agat.example"), "No sources");
  const linked = renderToStaticMarkup(createElement(KnowledgeLinkedOutput, { text: "Fact [K1]. Unknown [K9].", sources: [source] }));
  assert.match(linked, /<a[^>]+>\[K1\]<\/a>/);
  assert.ok(!linked.includes(">[K9]</a>"));
  const ambiguous = renderToStaticMarkup(createElement(KnowledgeLinkedOutput, { text: "Fact [K1].", sources: [source, { ...source, retrievalId: "other" }] }));
  assert.ok(!ambiguous.includes("<a"));
});
