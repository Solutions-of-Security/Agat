import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import MarkdownIt from "markdown-it";
import { AgatStore } from "../src/database.js";
import { BUSINESS_REPORT_CONTRACT, type BusinessReport } from "../src/business-contracts.js";
import { BUSINESS_REPORT_MARKDOWN_FORMAT, renderValidatedBusinessReport } from "../src/business-report-markdown.js";
import { normalizeProcessGraph } from "../src/process-engine.js";
import { exportProcessBpmn, importProcessBpmn } from "../src/process-bpmn.js";
import { loadConfig } from "../src/config.js";
import { createCoordinatorServer } from "../src/server.js";
import type { KnowledgeSource, ProcessGraph } from "../src/types.js";

const report = (): BusinessReport => ({ schemaVersion: BUSINESS_REPORT_CONTRACT, status: "ready", summary: "Объём заявок вырос",
  claims: [{ id: "volume", text: "100 → 120 заявок", sourceRefs: ["K1"] }],
  calculations: [{ id: "growth", operation: "percent_change", operands: [100, 120], result: 20, sourceRefs: ["K1"] }],
  artifactRefs: [], limitations: ["Причины изменений неизвестны"] });
const source: KnowledgeSource = { marker: "K1", retrievalId: "retrieval", stageId: "stage", createdAt: "2026-10-25", excerpt: "100 then 120", content: "100 then 120",
  provenance: { projectId: "default", collectionId: "collection", collectionName: "Facts", documentId: "document", documentName: "Source.txt",
    documentSha256: "a".repeat(64), chunkId: "chunk", chunkOrdinal: 0, charStart: 0, charEnd: 12,
    chunkSha256: "b".repeat(64), originalSha256: null, pageNumber: null, sourceUri: null } };
const context = { sourceMarkers: new Set(["K1"]), artifactSha256: () => null };

test("renderer is deterministic, links exact sources, includes formulas/hashes and escapes active Markdown/HTML", () => {
  const value = report();
  value.summary = '<script>alert(1)</script> [open](javascript:alert(1))';
  value.claims[0]!.text += '\n# Heading ![image](https://invalid.example/pixel)';
  const raw = JSON.stringify(value);
  const result = renderValidatedBusinessReport(raw, [source], context);
  assert.deepEqual(result, renderValidatedBusinessReport(raw, [source], context));
  assert.ok(result.content.includes("(120 − 100) / abs(100) × 100"));
  assert.ok(result.content.includes("SHA-256 исходного JSON"));
  assert.ok(result.content.includes("проверки человеком"));
  const html = new MarkdownIt({ html: true, linkify: false }).render(result.content);
  assert.ok(!html.includes("<script")); assert.ok(!html.includes("<img")); assert.ok(!html.includes('href="javascript:'));
  assert.ok(html.includes("documentId=document")); assert.ok(html.includes("chunkId=chunk"));
  assert.equal(result.receipt.contentSha256, createHash("sha256").update(result.content).digest("hex"));
  assert.equal(result.receipt.inputSha256, createHash("sha256").update(raw).digest("hex"));
  assert.ok(!JSON.stringify(result.receipt).includes("100 →"));
});

test("invalid/insufficient reports, missing display sources and excessive source expansion cannot export", () => {
  assert.throws(() => renderValidatedBusinessReport("plain text", [source], context), /invalid_json/);
  assert.throws(() => renderValidatedBusinessReport(JSON.stringify({ ...report(), status: "insufficient" }), [source], context), /insufficient/);
  assert.throws(() => renderValidatedBusinessReport(JSON.stringify(report()), [], context), /недоступны/);
  assert.throws(() => renderValidatedBusinessReport(JSON.stringify(report()), [{ ...source, content: "x".repeat(800_001) }], context), /800000/);
});

function graph(): ProcessGraph {
  return { nodes: [
    { id: "start", name: "Start", type: "start", position: { x: 0, y: 0 }, config: {} },
    { id: "agent", name: "Report", type: "agent", position: { x: 200, y: 0 }, config: { agentId: "collector", outputContract: BUSINESS_REPORT_CONTRACT } },
    { id: "approval", name: "Human review", type: "approval", position: { x: 400, y: 0 }, config: {} },
    { id: "artifact", name: "Markdown", type: "artifact", position: { x: 600, y: 0 }, config: { artifactName: "report.md", reportFormat: BUSINESS_REPORT_MARKDOWN_FORMAT } },
    { id: "end", name: "End", type: "end", position: { x: 800, y: 0 }, config: {} },
  ], edges: ["start", "agent", "approval", "artifact"].map((source, i) => ({ id: `e${i}`, source, target: ["agent", "approval", "artifact", "end"][i]!, branch: "default" })) };
}

test("artifact report format is versioned through BPMN and refuses conflicting media/placement", () => {
  const normalized = normalizeProcessGraph(graph(), new Set(["collector"]));
  assert.equal(normalized.nodes[3]!.config.artifactContent, "{{ lastOutput }}");
  assert.equal(normalized.nodes[3]!.config.artifactMediaType, "text/markdown; charset=utf-8");
  const imported = importProcessBpmn(exportProcessBpmn({ processId: "report", name: "Report", version: 1, graph: normalized }));
  assert.equal(normalizeProcessGraph(imported.graph, new Set(["collector"])).nodes[3]!.config.reportFormat, BUSINESS_REPORT_MARKDOWN_FORMAT);
  const wrong = graph(); wrong.nodes[1]!.config.reportFormat = BUSINESS_REPORT_MARKDOWN_FORMAT;
  assert.throws(() => normalizeProcessGraph(wrong, new Set(["collector"])), /только на шаге артефакта/);
  const media = graph(); media.nodes[3]!.config.artifactMediaType = "text/html";
  assert.throws(() => normalizeProcessGraph(media, new Set(["collector"])), /text\/markdown/);
});

function setup(directory: string) {
  const store = new AgatStore(":memory:", { seedDemo: false, artifactsDir: directory });
  store.updateModelRouterPolicy({ enabled: false });
  const collection = store.createKnowledgeCollection({ name: "Report facts", embeddingModel: "embeddinggemma" });
  store.ingestKnowledgeDocument(String(collection.id), { name: "Counts", content: "100 requests then 120 requests." });
  const node = store.registerNode({ name: "report-worker", enrollmentToken: "test", platform: "test", models: ["test-model"], embeddingModels: ["embeddinggemma"] }).id;
  for (let job = store.leaseKnowledgeEmbedding(node); job; job = store.leaseKnowledgeEmbedding(node)) store.completeKnowledgeEmbedding(node, job.leaseId, job.chunks.map((chunk) => ({ chunkId: chunk.id, embedding: [1, 0] })));
  const process = store.createProcess({ name: "Verified Markdown", graph: { ...graph(), requiredKnowledgeCollectionIds: [String(collection.id)] } });
  store.publishProcess(String(process.id));
  const instance = store.startProcess(String(process.id), { input: "Compare counts" })!;
  const lease = store.leaseNext(node)!;
  store.searchKnowledge(node, lease.leaseId, { queries: [{ embeddingModel: "embeddinggemma", collectionIds: [String(collection.id)], vector: [1, 0], topK: 3 }] });
  store.completeLease(node, lease.leaseId, JSON.stringify(report()));
  const approval = (store.getRun(String(instance.runId))!.stages as Array<{ id: string; status: string }>).find((stage) => stage.status === "waiting_approval")!;
  return { store, instance, approval };
}

for (const mode of ["approve", "reject", "source_changed", "ambiguous_source"] as const) {
  test(`human review -> ${mode} -> final artifact keeps validation boundaries`, () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-report-md-"));
    const { store, instance, approval } = setup(directory);
    try {
      assert.equal(store.listRunArtifacts(String(instance.runId)).length, 0);
      if (mode === "source_changed") store.db.prepare("UPDATE knowledge_chunks SET content_sha256 = ?").run("0".repeat(64));
      if (mode === "ambiguous_source") {
        const row = store.db.prepare("SELECT id, hits_json FROM knowledge_retrievals WHERE run_id = ?").get(String(instance.runId))!;
        const hits = JSON.parse(String(row.hits_json));
        store.db.prepare("UPDATE knowledge_retrievals SET hits_json = ? WHERE id = ?").run(JSON.stringify([...hits, ...hits]), row.id);
      }
      store.decideApproval(approval.id, mode !== "reject");
      assert.equal(store.getProcessInstance(String(instance.id))!.status, mode === "approve" ? "completed" : mode === "reject" ? "cancelled" : "failed");
      const artifacts = store.listRunArtifacts(String(instance.runId));
      assert.equal(artifacts.length, mode === "approve" ? 1 : 0);
      if (mode === "approve") {
        const downloaded = store.getArtifactDownload(String(artifacts[0]!.id))!;
        const bytes = fs.readFileSync(downloaded.filePath);
        assert.equal(artifacts[0]!.sha256, createHash("sha256").update(bytes).digest("hex"));
        assert.ok(bytes.toString().startsWith("# Внутренний отчёт"));
        assert.ok(bytes.toString().includes("Источники Knowledge"));
        assert.ok(bytes.toString().includes("documentId="));
        const stages = store.getRun(String(instance.runId))!.stages as Array<{ output: string }>;
        assert.equal(stages.at(-1)!.output, bytes.toString());
      }
    } finally { store.close(); fs.rmSync(directory, { recursive: true, force: true }); }
  });
}

test("HTTP download returns the same validated Markdown bytes and content type", async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-report-download-"));
  const { store, instance, approval } = setup(directory);
  store.decideApproval(approval.id, true);
  const artifact = store.listRunArtifacts(String(instance.runId))[0]!;
  const expected = fs.readFileSync(store.getArtifactDownload(String(artifact.id))!.filePath);
  const server = createCoordinatorServer({ ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false,
    adminToken: "report-test", oidcEnabled: false, mcpEnabled: false, sandboxEnabled: false, a2aEnabled: false, localWorkerLauncherEnabled: false }, store);
  try {
    await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
    const address = server.address(); assert.ok(address && typeof address === "object");
    const response = await fetch(`http://127.0.0.1:${address.port}/api/v1/artifacts/${artifact.id}/download`, { headers: { "x-agat-admin-token": "report-test" } });
    assert.equal(response.status, 200);
    assert.equal(artifact.mediaType, "text/markdown; charset=utf-8");
    assert.equal(response.headers.get("content-type"), "application/octet-stream");
    assert.match(response.headers.get("content-disposition")!, /attachment/);
    assert.deepEqual(Buffer.from(await response.arrayBuffer()), expected);
  } finally { server.closeAllConnections(); await new Promise<void>((resolve) => server.close(() => resolve())); store.close(); fs.rmSync(directory, { recursive: true, force: true }); }
});
