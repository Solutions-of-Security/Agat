import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { test } from "node:test";
import { ReportMarkdown } from "../src/components/ReportMarkdown.tsx";
import { knowledgeSourceHref } from "../src/knowledge.ts";
import type { KnowledgeSource } from "../src/types.ts";

const source: KnowledgeSource = { marker: "K1", retrievalId: "r1", stageId: "s1", createdAt: "2026-10-25", excerpt: "100 requests",
  provenance: { projectId: "project", collectionId: "collection", collectionName: "Facts", documentId: "document", documentName: "Counts.csv",
    documentSha256: "a".repeat(64), chunkId: "chunk", chunkOrdinal: 0, charStart: 0, charEnd: 12,
    chunkSha256: "b".repeat(64), originalSha256: null, pageNumber: null, sourceUri: null } };
const render = (text: string, sources = [source]) => renderToStaticMarkup(createElement(ReportMarkdown, { text, sources }));

test("Markdown formats headings/tables while known citations open exact sources", () => {
  const html = render("# Отчёт\n\n**Период**: июль [K1]\n\n| Метрика | Значение |\n| --- | --- |\n| Заявки | 100 [K1] |\n\n> Цитата\n\n- Проверка");
  assert.match(html, /<h1>Отчёт<\/h1>/);
  assert.match(html, /<strong>Период<\/strong>/);
  assert.match(html, /<table>/); assert.match(html, /<th>Метрика<\/th>/); assert.match(html, /<blockquote>/);
  assert.match(html, /aria-label="Таблица отчёта"/);
  assert.match(html, /documentId=document/); assert.match(html, /chunkId=chunk/);
  assert.equal((html.match(/>\[K1\]<\/a>/g) ?? []).length, 2);
});

test("active HTML, remote images and model-supplied links stay inert", () => {
  const html = render('<script>alert(1)</script>\n\n<img src="https://invalid.example/track">\n\n![Image](https://invalid.example/image)\n\n[Bad](javascript:alert(1)) [Remote](https://invalid.example/) [File](file:///etc/passwd)');
  assert.ok(!html.includes("<script")); assert.ok(!html.includes("<img")); assert.ok(!html.includes("<iframe"));
  assert.ok(!html.includes("href=")); assert.ok(!html.includes("src="));
  assert.ok(html.includes("Image")); assert.ok(html.includes("Remote"));
});

test("verified appendix links are allowed; invented, ambiguous and malformed citations do not link", () => {
  const second = { ...source, marker: "K2", provenance: { ...source.provenance, documentId: "other", chunkId: "other-chunk" } };
  const html = render(`Known [K1, K2/K9]. [Appendix](/${knowledgeSourceHref(source)}). [Invented](#knowledge?documentId=unknown). [K1-K2].`, [source, second]);
  assert.equal((html.match(/<a /g) ?? []).length, 3);
  assert.ok(html.includes("[K1-K2]")); assert.ok(html.includes("K9"));
  const ambiguous = render("[K1] [K2]", [source, { ...source, retrievalId: "duplicate" }, second]);
  assert.equal((ambiguous.match(/<a /g) ?? []).length, 1);
  assert.ok(ambiguous.includes("[K1]"));
});

test("code blocks retain literal citations; large results explicitly keep full source text", () => {
  const code = render("`[K1]`\n\n```text\n[K1]\n```\n\nReal [K1]");
  assert.equal((code.match(/<a /g) ?? []).length, 1);
  assert.match(code, /<code>\[K1\]<\/code>/);
  const long = "x".repeat(200_001);
  const html = render(long);
  assert.ok(html.includes("слишком велик")); assert.ok(html.includes(long));
  assert.ok(!html.includes('class="report-markdown"'));
});
