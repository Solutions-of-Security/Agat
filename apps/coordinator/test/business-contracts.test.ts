import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { test } from "node:test";
import { AgatStore } from "../src/database.js";
import { BUSINESS_REPORT_CONTRACT, validateBusinessReportOutput, type BusinessReport } from "../src/business-contracts.js";
import { normalizeProcessGraph } from "../src/process-engine.js";
import { exportProcessBpmn, importProcessBpmn } from "../src/process-bpmn.js";
import type { ProcessGraph } from "../src/types.js";

function report(): BusinessReport {
  return { schemaVersion: BUSINESS_REPORT_CONTRACT, status: "ready", summary: "Проверяемый отчёт",
    claims: [{ id: "volume", text: "Объём вырос на 20%", sourceRefs: ["K1"] }],
    calculations: [{ id: "growth", operation: "percent_change", operands: [100, 120], result: 20, sourceRefs: ["K1"] }],
    artifactRefs: [], limitations: ["Причины изменений не установлены"] };
}
const context = { sourceMarkers: new Set(["K1"]), artifactSha256: () => null };
const check = (value: unknown) => validateBusinessReportOutput(JSON.stringify(value), context);

test("fixed schema validates without coercion or mutation, exposes the boundary of factual checking", () => {
  const value = report(); const before = structuredClone(value);
  const receipt = check(value);
  assert.equal(receipt.status, "validated");
  assert.equal(receipt.factualClaims, "human_review_required");
  assert.match(receipt.schemaSha256, /^[a-f0-9]{64}$/);
  assert.match(receipt.outputSha256, /^[a-f0-9]{64}$/);
  assert.deepEqual(value, before);
  for (const invalid of [{ ...value, extra: true }, { ...value, claims: [] },
    { ...value, status: "unknown" }, { ...value, summary: " " },
    { ...value, calculations: [{ ...value.calculations[0], result: "20" }] },
    { ...value, claims: [{ ...value.claims[0], sourceRefs: ["K1", "K1"] }] },
    { ...value, calculations: [{ ...value.calculations[0], operands: [1, 1e309] }] },
  ]) assert.equal(check(invalid).status, "invalid");
  assert.equal(validateBusinessReportOutput("```json\n{}\n```", context).codes[0], "invalid_json");
  assert.equal(check({ ...value, summary: "я".repeat(100_001) }).codes[0], "output_too_large");
});

test("missing evidence, duplicate IDs and false arithmetic cannot produce a validated result", () => {
  const value = report();
  assert.deepEqual(check({ ...value, claims: [{ ...value.claims[0], sourceRefs: ["K2"] }] }).codes, ["source_out_of_scope"]);
  assert.deepEqual(check({ ...value, claims: [value.claims[0], value.claims[0]] }).codes, ["duplicate_item_id"]);
  assert.deepEqual(check({ ...value, calculations: [{ ...value.calculations[0], result: 21 }] }).codes, ["calculation_mismatch"]);
  for (const [operation, operands, result] of [
    ["sum", [1, 2], 3], ["difference", [4, 1], 3], ["ratio", [0.3, 0.1], 3],
    ["percentage_points", [0.08, 0.05], -3], ["percent_change", [-100, -80], 20],
  ] as const) assert.equal(check({ ...value, calculations: [{ ...value.calculations[0], operation, operands, result }] }).status, "validated");
  for (const operation of ["ratio", "percent_change"]) {
    assert.equal(check({ ...value, calculations: [{ ...value.calculations[0], operation, operands: [0, 0] }] }).codes[0], "undefined_calculation");
  }
  assert.equal(check({ ...value, status: "insufficient", claims: [], calculations: [] }).status, "review_required");
  assert.equal(check({ ...value, status: "insufficient", limitations: [] }).codes[0], "missing_limitations");
});

test("artifact references need exact authoritative hashes and an available resolver", () => {
  const value = { ...report(), artifactRefs: [{ id: "artifact-1", sha256: "a".repeat(64) }] };
  assert.equal(check(value).codes[0], "artifact_unavailable_or_mismatched");
  assert.equal(validateBusinessReportOutput(JSON.stringify(value), { ...context, artifactSha256: () => "a".repeat(64) }).status, "validated");
  assert.equal(validateBusinessReportOutput(JSON.stringify(value), { ...context, artifactSha256: () => { throw new Error("secret upstream payload"); } }).codes[0], "artifact_unavailable_or_mismatched");
});

function graph(): ProcessGraph {
  return { nodes: [
    { id: "start", name: "Начало", type: "start", position: { x: 0, y: 0 }, config: {} },
    { id: "report", name: "Отчёт", type: "agent", position: { x: 200, y: 0 }, config: { agentId: "collector", outputContract: BUSINESS_REPORT_CONTRACT } },
    { id: "artifact", name: "Сохранение", type: "artifact", position: { x: 400, y: 0 }, config: { artifactName: "report.json", artifactContent: "{{ lastOutput }}" } },
    { id: "end", name: "Конец", type: "end", position: { x: 600, y: 0 }, config: {} },
  ], edges: ["start", "report", "artifact"].map((source, i) => ({ id: `e${i}`, source, target: ["report", "artifact", "end"][i]!, branch: "default" })) };
}

test("contract survives publication and BPMN; unknown or misplaced contracts are rejected", () => {
  const normalized = normalizeProcessGraph(graph(), new Set(["collector"]));
  const imported = importProcessBpmn(exportProcessBpmn({ processId: "report", name: "Report", version: 1, graph: normalized }));
  assert.equal(normalizeProcessGraph(imported.graph, new Set(["collector"])).nodes[1]!.config.outputContract, BUSINESS_REPORT_CONTRACT);
  const unknown = graph(); (unknown.nodes[1]!.config as Record<string, unknown>).outputContract = "unknown.v1";
  assert.throws(() => normalizeProcessGraph(unknown, new Set(["collector"])), /Неизвестный контракт/);
  const wrong = graph(); wrong.nodes[2]!.config.outputContract = BUSINESS_REPORT_CONTRACT;
  assert.throws(() => normalizeProcessGraph(wrong, new Set(["collector"])), /только на агентном/);
});

function setup(store: AgatStore) {
  store.updateModelRouterPolicy({ enabled: false });
  const node = store.registerNode({ name: "contract-worker", enrollmentToken: "test", platform: "test", models: ["test-model"], embeddingModels: ["embeddinggemma"] }).id;
  const collection = store.createKnowledgeCollection({ name: "Report sources", embeddingModel: "embeddinggemma" }) as { id: string };
  store.ingestKnowledgeDocument(collection.id, { name: "Counts", content: "100 requests then 120 requests." });
  for (let job = store.leaseKnowledgeEmbedding(node); job; job = store.leaseKnowledgeEmbedding(node)) {
    store.completeKnowledgeEmbedding(node, job.leaseId, job.chunks.map((chunk) => ({ chunkId: chunk.id, embedding: [1, 0] })));
  }
  const process = store.createProcess({ name: "Structured report", graph: { ...graph(), requiredKnowledgeCollectionIds: [collection.id] } });
  store.publishProcess(String(process.id));
  return { node, collection, process };
}

for (const kind of ["valid", "invalid", "insufficient", "no_retrieval", "stale_source"] as const) {
  test(`real lease completion enforces ${kind} before artifact writes or downstream execution`, () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-business-"));
    const file = path.join(directory, "state.sqlite");
    const store = new AgatStore(file, { seedDemo: false, artifactsDir: directory });
    try {
      const { node, collection, process } = setup(store);
      const instance = store.startProcess(String(process.id), { input: "Compare request counts" })!;
      const lease = store.leaseNext(node)!;
      if (kind !== "no_retrieval") store.searchKnowledge(node, lease.leaseId, { queries: [{ embeddingModel: "embeddinggemma", collectionIds: [collection.id], topK: 3, vector: [1, 0] }] });
      if (kind === "stale_source") {
        // Simulate storage drift; normal document mutation is already blocked for active runs.
        const database = new DatabaseSync(file);
        database.prepare("UPDATE knowledge_chunks SET content_sha256 = ? WHERE collection_id = ?")
          .run("0".repeat(64), collection.id);
        database.close();
      }
      const value = report();
      if (kind === "invalid") value.calculations[0]!.result = 99;
      if (kind === "insufficient") value.status = "insufficient";
      const result = store.completeLease(node, lease.leaseId, JSON.stringify(value), [{ name: "forged.txt", content: "should never persist on failure" }]);
      const valid = kind === "valid";
      assert.equal(result.runCompleted, valid);
      assert.equal(store.getProcessInstance(String(instance.id))!.status, valid ? "completed" : "failed");
      assert.equal(store.listRunArtifacts(String(instance.runId)).length, valid ? 1 : 0);
      const stages = store.getRun(String(instance.runId))!.stages as Array<{ status: string; processNodeId: string }>;
      assert.equal(stages[0]!.status, valid ? "completed" : "failed");
      if (!valid) assert.equal(stages.length, 1, "downstream stage must not be created");
      const events = store.getRunTrace(String(instance.runId))!.events as Array<{ type: string; data: unknown }>;
      const receipt = events.find((event) => event.type === "business.output.validated")!;
      assert.ok(receipt);
      assert.ok(!JSON.stringify(receipt.data).includes(value.summary));
      assert.throws(() => store.completeLease(node, lease.leaseId, JSON.stringify(value)), /аренд/);
    } finally { store.close(); fs.rmSync(directory, { recursive: true, force: true }); }
  });
}

test("a restarted instance keeps its contract even after the draft removes validation", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-business-restart-"));
  const file = path.join(directory, "state.db");
  let store = new AgatStore(file, { seedDemo: false, artifactsDir: directory });
  try {
    const { node, process } = setup(store);
    const instance = store.startProcess(String(process.id), { input: "Compare counts" })!;
    const lease = store.leaseNext(node)!;
    const draft = graph(); delete draft.nodes[1]!.config.outputContract;
    store.updateProcess(String(process.id), { name: "Report v2", graph: draft });
    store.publishProcess(String(process.id));
    store.close(); store = new AgatStore(file, { seedDemo: false, artifactsDir: directory });
    store.completeLease(node, lease.leaseId, "unstructured answer");
    assert.equal(store.getProcessInstance(String(instance.id))!.status, "failed");
    assert.equal(store.listRunArtifacts(String(instance.runId)).length, 0);
  } finally { store.close(); fs.rmSync(directory, { recursive: true, force: true }); }
});

test("a referenced artifact is verified from stored bytes and cannot cross run boundaries", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-business-artifact-"));
  const store = new AgatStore(":memory:", { seedDemo: false, artifactsDir: directory });
  try {
    const { node, collection, process } = setup(store);
    const before = graph();
    before.nodes.splice(1, 0, { id: "source-artifact", type: "artifact", name: "Evidence", position: { x: 1, y: 1 },
      config: { artifactName: "evidence.txt", artifactContent: "100 to 120" } });
    before.edges[0]!.target = "source-artifact";
    before.edges.push({ id: "evidence-report", source: "source-artifact", target: "report", branch: "default" });
    store.updateProcess(String(process.id), { name: "Report with evidence", graph: { ...before, requiredKnowledgeCollectionIds: [collection.id] } });
    store.publishProcess(String(process.id));
    let previousArtifact: { id: string; sha256: string } | null = null;
    for (const mode of ["valid", "other_run", "corrupt"] as const) {
      const instance = store.startProcess(String(process.id), { input: "Compare counts" })!;
      const artifact = store.listRunArtifacts(String(instance.runId))[0]!;
      const download = store.getArtifactDownload(String(artifact.id))!;
      if (mode === "corrupt") fs.writeFileSync(download.filePath, "tampered payload");
      const lease = store.leaseNext(node)!;
      store.searchKnowledge(node, lease.leaseId, { queries: [{ embeddingModel: "embeddinggemma", collectionIds: [collection.id], topK: 3, vector: [1, 0] }] });
      const ownArtifact = { id: String(artifact.id), sha256: String(artifact.sha256) };
      const value = { ...report(), artifactRefs: [mode === "other_run" ? previousArtifact! : ownArtifact] };
      store.completeLease(node, lease.leaseId, JSON.stringify(value));
      assert.equal(store.getProcessInstance(String(instance.id))!.status, mode === "valid" ? "completed" : "failed");
      previousArtifact = ownArtifact;
    }
  } finally { store.close(); fs.rmSync(directory, { recursive: true, force: true }); }
});
