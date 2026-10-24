import assert from "node:assert/strict";
import { test } from "node:test";
import { zipSync, strToU8 } from "fflate";
import { AgatStore } from "../src/database.js";
import { parseKnowledgeFile, normalizeKnowledgeUpload } from "../src/knowledge-files.js";
import { chunkTabularKnowledgeText, KNOWLEDGE_XLSX_MEDIA_TYPE as XLSX, KNOWLEDGE_CSV_MEDIA_TYPE as CSV } from "../src/knowledge-tables.js";

const main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main";
export function xlsxFixture(cells = '<c r="A1" t="s"><v>0</v></c><c r="B1"><v>120.50</v></c><c r="C1" t="b"><v>1</v></c>', extra: Record<string, string> = {}) {
  return Buffer.from(zipSync(Object.fromEntries(Object.entries({
    "xl/workbook.xml": `<workbook xmlns="${main}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Данные" sheetId="1" r:id="rId1"/></sheets></workbook>`,
    "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
    "xl/sharedStrings.xml": `<sst xmlns="${main}"><si><r><t>Заявки &amp; </t></r><r><t>часы</t></r></si></sst>`,
    "xl/worksheets/sheet1.xml": `<worksheet xmlns="${main}"><sheetData><row r="1">${cells}</row></sheetData></worksheet>`,
    ...extra,
  }).map(([key, value]) => [key, strToU8(value)]))));
}

test("CSV keeps logical cell addresses, Unicode, BOM, quoted delimiters/newlines and empty rows", async () => {
  const csv = '\ufeffname,value\r\n"two, words","line one\nline two"\r\n\r\n"quote ""yes""",00120\r\n';
  const parsed = await parseKnowledgeFile(Buffer.from(csv), CSV);
  assert.match(parsed.content, /"CSV"!A1 \[text\] = "name"/);
  assert.ok(parsed.content.includes('"CSV"!B2 [text] = "line one\\nline two"'));
  assert.ok(parsed.content.includes('"CSV"!A4 [text] = "quote \\"yes\\""'));
  assert.ok(parsed.content.includes('"CSV"!B4 [text] = "00120"'));
  assert.deepEqual(parsed.pages, []);
  for (const delimiter of [";", "\t"]) {
    const result = await parseKnowledgeFile(Buffer.from(`Имя${delimiter}Число\nТест${delimiter}1,25`), CSV);
    assert.ok(result.content.includes('"CSV"!B2 [text] = "1,25"'));
  }
});

test("CSV rejects broken quoting, inconsistent width, non-UTF8 and excess rows/columns", async () => {
  for (const csv of ['a,b\n"broken,b', "a,b\n1,2,3", Array(257).fill("cell").join(","), "a\n".repeat(10_001)]) {
    await assert.rejects(parseKnowledgeFile(Buffer.from(csv), CSV));
  }
  await assert.rejects(parseKnowledgeFile(new Uint8Array([0xff, 0xfe, 0x41]), CSV), /UTF-8/);
  await assert.rejects(parseKnowledgeFile(Buffer.from("\n\n"), CSV), /не содержит/);
});

test("XLSX preserves shared/rich/inline strings, exact numbers, boolean and cell/sheet addresses", async () => {
  const parsed = await parseKnowledgeFile(xlsxFixture(), XLSX);
  assert.ok(parsed.content.includes('"Данные"!A1 [s] = "Заявки & часы"'));
  assert.ok(parsed.content.includes('"Данные"!B1 [n] = "120.50"'));
  assert.ok(parsed.content.includes('"Данные"!C1 [b] = "true"'));
  assert.deepEqual(parsed.pages, []);
  const inline = await parseKnowledgeFile(xlsxFixture('<c r="B7" t="inlineStr"><is><t>Точный адрес</t></is></c><c r="C7" s="2"><v>45292</v></c>'), XLSX);
  assert.ok(inline.content.includes('"Данные"!B7 [inlineStr] = "Точный адрес"'));
  assert.ok(inline.content.includes('"Данные"!C7 [n; style=2] = "45292"'));
  assert.match(inline.content, /даты не интерпретируются/);
});

test("XLSX fails explicitly for formulas, errors, macros, stale caches and unknown shared strings", async () => {
  for (const cells of ['<c r="A1"><f>WEBSERVICE("https://invalid.example/")</f><v>99</v></c>',
    '<c r="A1" t="e"><v>#DIV/0!</v></c>', '<c r="A1" t="s"><v>999</v></c>',
    '<c r="IW1"><v>1</v></c>', '<c r="A10001"><v>1</v></c>',
    '<c r="A1"><v>1</v></c><c r="A1"><v>2</v></c>']) {
    await assert.rejects(parseKnowledgeFile(xlsxFixture(cells), XLSX));
  }
  await assert.rejects(parseKnowledgeFile(xlsxFixture(undefined, { "xl/vbaProject.bin": "never execute" }), XLSX), /макрос/);
  await assert.rejects(parseKnowledgeFile(xlsxFixture(undefined, { "xl/worksheets/sheet1.xml": `<worksheet xmlns="${main}"><!DOCTYPE x></worksheet>` }), XLSX));
  await assert.rejects(parseKnowledgeFile(xlsxFixture(undefined, { "xl/_rels/workbook.xml.rels": '<Relationships><Relationship Id="rId1" Type="x/worksheet" Target="https://invalid.example/data.xml" TargetMode="External"/></Relationships>' }), XLSX), /внешний/);
  await assert.rejects(parseKnowledgeFile(Buffer.from("not ZIP"), XLSX), /сигнатура/);
  await assert.rejects(parseKnowledgeFile(xlsxFixture(undefined, { "xl/sharedStrings.xml": "x".repeat(8 * 1024 * 1024 + 1) }), XLSX), /размер/);
  await assert.rejects(parseKnowledgeFile(Buffer.from(`column\n${"x".repeat(32_001)}`), CSV), /32 000/);
});

test("XLSX reads every declared sheet in workbook order and identifies hidden sheets", async () => {
  const parsed = await parseKnowledgeFile(xlsxFixture(undefined, {
    "xl/workbook.xml": `<workbook xmlns="${main}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="First" sheetId="1" r:id="rId1"/><sheet name="Hidden" sheetId="2" r:id="rId2" state="hidden"/></sheets></workbook>`,
    "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId2" Type="x/worksheet" Target="/xl/worksheets/second.xml"/><Relationship Id="rId1" Type="x/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
    "xl/worksheets/second.xml": `<worksheet xmlns="${main}"><sheetData><row r="3"><c r="D3" t="inlineStr"><is><t>Hidden evidence</t></is></c></row></sheetData></worksheet>`,
  }), XLSX);
  assert.ok(parsed.content.indexOf('"First"!A1') < parsed.content.indexOf('"Hidden"!D3'));
  assert.match(parsed.content, /состояние: hidden/);
  assert.ok(parsed.content.includes('"Hidden"!D3 [inlineStr] = "Hidden evidence"'));
});

test("long cell parts retain their address in every chunk, exact offsets and a stable reindex", async () => {
  const value = 'Long quoted \\" value and emoji 🌍 '.repeat(80);
  const exact = await parseKnowledgeFile(Buffer.from(`description\n"${value.replaceAll('"', '""')}"`), CSV);
  assert.ok(exact.content.includes("часть"));
  for (const overlap of [0, 160, 399]) {
    const chunks = chunkTabularKnowledgeText(exact.content, 400, overlap);
    const recovered = new Map();
    for (const chunk of chunks) {
      assert.equal(exact.content.slice(chunk.charStart, chunk.charEnd), chunk.content);
      assert.ok(chunk.content.length <= 400);
      for (const line of chunk.content.split("\n").filter(line => line.includes("часть"))) {
        const match = /^"CSV"!A2 \[text\] \(часть (\d+)\/(\d+)\) = (.*)$/.exec(line);
        assert.ok(match, "no fragment may separate a cell part from its address");
        recovered.set(Number(match[1]), JSON.parse(match[3]));
      }
    }
    assert.equal([...recovered.entries()].sort((a, b) => a[0] - b[0]).map(([, part]) => part).join(""), value);
  }
  const store = new AgatStore(":memory:", { seedDemo: false });
  try {
    const collection = store.createKnowledgeCollection({ name: "Long cells", embeddingModel: "embed", chunkSize: 400, chunkOverlap: 160 });
    const document = await store.uploadKnowledgeDocument(String(collection.id), { name: "Long.csv", mediaType: CSV,
      contentBase64: Buffer.from(`description\n"${value.replaceAll('"', '""')}"`).toString("base64") });
    const before = store.getKnowledgeDocument(String(document.id))!.chunks;
    await store.reindexKnowledgeDocument(String(document.id));
    assert.deepEqual(store.getKnowledgeDocument(String(document.id))!.chunks, before);
  } finally { store.close(); }
});

test("table ingestion preserves originals, indexes cell references, records provenance and survives retry", async () => {
  const store = new AgatStore(":memory:", { seedDemo: false });
  try {
    store.updateModelRouterPolicy({ enabled: false });
    const collection = store.createKnowledgeCollection({ name: "Tables", embeddingModel: "embeddinggemma" });
    const node = store.registerNode({ name: "table-worker", enrollmentToken: "test", platform: "test", models: ["test-model"], embeddingModels: ["embeddinggemma"] }).id;
    const bytes = xlsxFixture();
    const document = await store.uploadKnowledgeDocument(String(collection.id), { name: "Table.xlsx", mediaType: XLSX, contentBase64: bytes.toString("base64") });
    assert.equal(document.status, "pending");
    assert.deepEqual(store.getKnowledgeDocumentFile(String(document.id))!.bytes, bytes);
    assert.ok(String(store.getKnowledgeDocument(String(document.id))!.content).includes('"Данные"!B1'));
    const first = store.leaseKnowledgeEmbedding(node)!;
    store.failKnowledgeEmbedding(node, first.leaseId, "Temporary failure");
    const lease = store.leaseKnowledgeEmbedding(node)!;
    store.completeKnowledgeEmbedding(node, lease.leaseId, lease.chunks.map((chunk) => ({ chunkId: chunk.id, embedding: [1, 0] })));
    assert.equal(store.getKnowledgeDocument(String(document.id))!.status, "ready");
    const run = store.createRun({ name: "Table question", input: "What value is in B1?", agentIds: ["collector"], approvalRequired: false, knowledgeCollectionIds: [String(collection.id)] });
    const stage = store.leaseNext(node)!;
    const hit = store.searchKnowledge(node, stage.leaseId, { queries: [{ embeddingModel: "embeddinggemma", collectionIds: [String(collection.id)], vector: [1, 0], topK: 3 }] }).hits[0]!;
    assert.ok(hit.content.includes('"Данные"!B1'));
    assert.equal(hit.provenance.pageNumber, null);
    assert.equal(hit.provenance.originalSha256, document.originalSha256);
    store.completeLease(node, stage.leaseId, "Stored value 120.50 [K1]");
    assert.equal(store.getRunKnowledgeSources(run.id)![0]!.provenance.chunkId, hit.provenance.chunkId);
    await assert.rejects(store.uploadKnowledgeDocument(String(collection.id), { name: "Table.xlsx", mediaType: XLSX, contentBase64: bytes.toString("base64") }), /уже существует/);
    store.createProject({ id: "other", name: "Other" });
    assert.equal(store.getKnowledgeDocument(String(document.id), "other"), null);
    const bad = await store.uploadKnowledgeDocument(String(collection.id), { name: "Formula.xlsx", mediaType: XLSX,
      contentBase64: xlsxFixture('<c r="A1"><f>1+1</f><v>2</v></c>').toString("base64") });
    assert.equal(bad.status, "failed"); assert.equal(bad.chunkCount, 0); assert.match(String(bad.parseError), /формул/);
    assert.equal((await store.reindexKnowledgeDocument(String(bad.id)))!.status, "failed");
    for (const mediaType of [CSV, XLSX]) assert.equal(normalizeKnowledgeUpload({ name: "test", mediaType, contentBase64: bytes.toString("base64") }).mediaType, mediaType);
  } finally { store.close(); }
});
