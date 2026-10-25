import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { test } from "node:test";
import { KnowledgeExtractionCoverage } from "../src/components/KnowledgeDocumentPreview.tsx";
import type { KnowledgeDocumentPreview } from "../src/types.ts";

const document: KnowledgeDocumentPreview = { id: "document", name: "Mixed.pdf", collectionId: "collection", collectionName: "Sources", embeddingModel: "embedding",
  sourceUri: null, mediaType: "application/pdf", contentSha256: "a".repeat(64), originalSha256: "b".repeat(64), hasOriginal: true, status: "review_required", error: null,
  chunkCount: 1, embeddedCount: 0, createdAt: "2026-10-25", updatedAt: "2026-10-25", content: "Extracted text", chunks: [], pages: [],
  extraction: { state: "review_required", revision: "revision", reportSha256: "c".repeat(64), review: null, report: {
    schemaVersion: "document-extraction.v1", profile: "pdf-text-layer.v1", textCharacters: 14, totalPages: 3, emptyPages: [2], omittedParts: ["<script>part</script>"], features: [], reviewRequired: true, fidelity: "not_assessed" } } };

test("coverage states gaps and limitations; review starts unchecked and requires an explicit reason", () => {
  const html = renderToStaticMarkup(createElement(KnowledgeExtractionCoverage, { document, canReview: true }));
  assert.match(html, /Страницы без извлечённого текста: 2/); assert.match(html, /До подтверждения документ не индексируется/);
  assert.ok(!html.includes("checked=")); assert.match(html, /disabled=""/);
  assert.ok(!html.includes("<script>")); assert.ok(html.includes("&lt;script&gt;"));
});

test("read roles cannot acknowledge and legacy coverage is unknown", () => {
  const html = renderToStaticMarkup(createElement(KnowledgeExtractionCoverage, { document }));
  assert.ok(!html.includes("<button")); assert.ok(html.includes("администратор или проектировщик"));
  const legacy = { ...document, extraction: { ...document.extraction!, state: "legacy_unknown", report: null } };
  assert.match(renderToStaticMarkup(createElement(KnowledgeExtractionCoverage, { document: legacy })), /История извлечения неизвестна/);
});
