import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";
import { test } from "node:test";
import { unzipSync } from "fflate";
import { officeDocument, officeText, BusinessReportOfficeError } from "../src/business-report-office-document.js";
import { renderBusinessReportOffice } from "../src/business-report-office.js";
import { parseKnowledgeFile } from "../src/knowledge-files.js";
import { officeFixture } from "./business-report-office-fixtures.js";

test("both formats use one canonical document and keep literal data, tables, source links and hashes", async () => {
  const directory = process.env.AGAT_OFFICE_QA_DIR ?? mkdtempSync(path.join(tmpdir(), "agat-office-qa-"));
  mkdirSync(directory, { recursive: true });
  for (const long of [false, true]) {
    const input = officeFixture(long), tree = officeDocument(input);
    assert.equal(officeDocument({ ...input, format: "docx" }).documentSha256, tree.documentSha256);
    const texts = tree.blocks.flatMap(block => block.type === "table" ? block.rows.flat().map(officeText) : [officeText(block.runs)]).join("\n");
    assert.match(texts, /<script>alert\(1\)<\/script>/); assert.match(texts, /\(120 − 100\) \/ abs\(100\) × 100/);
    assert.ok(texts.includes(input.canonicalSha256)); assert.match(texts, /Визуальная полнота не подтверждена/);
    for (const format of ["docx", "pdf"] as const) {
      const rendered = await renderBusinessReportOffice({ ...input, format });
      assert.equal(rendered.documentSha256, tree.documentSha256);
      const file = path.join(directory, `${long ? "long" : "short"}.${format}`); writeFileSync(file, rendered.bytes);
      if (format === "docx") {
        const files = unzipSync(rendered.bytes);
        const xml = new TextDecoder().decode(files["word/document.xml"]);
        assert.match(xml, /&lt;script&gt;alert/); assert.ok(!xml.includes("<script>"));
        assert.match(xml, /w:tblHeader/); assert.ok(xml.includes(input.canonicalSha256));
        const rels = new TextDecoder().decode(files["word/_rels/document.xml.rels"]);
        const external = [...rels.matchAll(/<Relationship\b[^>]*TargetMode="External"[^>]*\/>/g)].map(match => match[0]);
        assert.equal(external.length, 1); assert.match(external[0]!, /Target="https:\/\/agat.example\/"/);
        assert.match(xml, /w:anchor="knowledge\?projectId=default&amp;documentId=fixture-document&amp;chunkId=fixture-chunk/);
        assert.ok(Object.keys(files).some(name => name.endsWith(".odttf")), "embedded Cyrillic font must travel with DOCX");
      } else {
        assert.equal(readFileSync(file).subarray(0, 5).toString(), "%PDF-");
        const parsed = await parseKnowledgeFile(rendered.bytes, "application/pdf");
        assert.ok(parsed.content.includes("Технический пример экспорта"));
        assert.ok(parsed.content.includes(input.canonicalSha256));
        assert.ok(parsed.content.includes("<script>alert(1)</script>"));
        for (const page of parsed.pages) {
          const text = parsed.content.slice(page.charStart, page.charEnd);
          assert.ok(text.replace(/Страница \d+ из \d+/, "").trim().length > 40, "footer must not create a blank page");
          assert.ok(text.includes(`Страница ${page.pageNumber} из ${parsed.pages.length}`), "footer counts every physical page");
        }
        if (!long) assert.equal(parsed.pages.length, 2);
        if (long) {
          assert.ok(parsed.pages.length >= 3);
          const compact = parsed.content.replace(/\s+/g, "");
          for (const block of tree.blocks) if (block.type === "table") for (const row of block.rows.slice(1)) {
            assert.ok(compact.includes(officeText(row[0]!).replace(/\s+/g, "")), "long table identifiers must not be clipped");
          }
          for (let row = 1; row <= 50; row++) assert.ok(parsed.content.includes(`Строка ${row}:`));
        }
      }
    }
  }
  console.log(`Office visual QA files: ${directory}`);
});

test("hash mismatch, unsafe links, unsupported syntax and missing glyphs fail instead of dropping content", async () => {
  const input = officeFixture();
  assert.throws(() => officeDocument({ ...input, canonicalSha256: "0".repeat(64) }), BusinessReportOfficeError);
  for (const suffix of ["\n\n![image](https://invalid.example/pixel)", "\n\n[link](javascript:alert(1))", "\n\n[link](https://invalid.example/)"]) {
    const markdown = input.markdown + suffix;
    // javascript: links are rejected by MarkdownIt itself and become literal text.
    if (suffix.includes("javascript:")) continue;
    assert.throws(() => officeDocument({ ...input, markdown, canonicalSha256: createHash("sha256").update(markdown).digest("hex") }), BusinessReportOfficeError);
  }
  const markdown = `${input.markdown}\n\nНеподдерживаемый символ 🐈`;
  await assert.rejects(renderBusinessReportOffice({ ...input, markdown, canonicalSha256: createHash("sha256").update(markdown).digest("hex") }),
    (error: unknown) => error instanceof BusinessReportOfficeError && error.code === "unsupported_glyph");
});

test("renderer refuses excess concurrent work with a retryable response and releases capacity", async () => {
  const input = officeFixture(true);
  const pending = [renderBusinessReportOffice(input), renderBusinessReportOffice({ ...input, format: "docx" })];
  await assert.rejects(renderBusinessReportOffice(input), (error: unknown) => error instanceof BusinessReportOfficeError && error.code === "renderer_busy" && error.status === 503);
  await Promise.all(pending);
  assert.ok((await renderBusinessReportOffice(officeFixture())).bytes.length > 0);
});
