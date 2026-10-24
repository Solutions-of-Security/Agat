import assert from "node:assert/strict";
import { test } from "node:test";
import { AgatStore } from "../src/database.js";
import { BUSINESS_REPORT_INPUT_CONTRACT, BUSINESS_REPORT_CONTRACT, businessOutputContractDescriptor,
  validateBusinessReportInput } from "../src/business-contracts.js";
import { normalizeProcessGraph } from "../src/process-engine.js";
import { exportProcessBpmn, importProcessBpmn } from "../src/process-bpmn.js";
import { createCoordinatorServer } from "../src/server.js";
import { loadConfig } from "../src/config.js";
import type { ProcessGraph } from "../src/types.js";

const input = () => JSON.stringify({ schemaVersion: BUSINESS_REPORT_INPUT_CONTRACT,
  question: "Сравните показатели", period: "Июль–август 2026", requirements: ["Не делать выводов о причине"] });
function graph(): ProcessGraph {
  return { nodes: [
    { id: "start", name: "Start", type: "start", position: { x: 0, y: 0 }, config: { inputContract: BUSINESS_REPORT_INPUT_CONTRACT } },
    { id: "agent", name: "Agent", type: "agent", position: { x: 200, y: 0 }, config: { agentId: "collector", outputContract: BUSINESS_REPORT_CONTRACT } },
    { id: "end", name: "End", type: "end", position: { x: 400, y: 0 }, config: {} },
  ], edges: [{ id: "e1", source: "start", target: "agent", branch: "default" }, { id: "e2", source: "agent", target: "end", branch: "default" }] };
}
function setup() {
  const store = new AgatStore(":memory:", { seedDemo: false });
  store.updateModelRouterPolicy({ enabled: false });
  const process = store.createProcess({ name: "Contract report", graph: graph() });
  store.publishProcess(String(process.id));
  const node = store.registerNode({ name: "contract-worker", platform: "test", enrollmentToken: "unused", models: ["test-model"] }).id;
  return { store, processId: String(process.id), node };
}

test("input schema requires explicit fields, bounds UTF-8 bytes and exposes redacted errors", () => {
  assert.equal(validateBusinessReportInput(input()).status, "validated");
  for (const value of ["not JSON", "{}", JSON.stringify({ ...JSON.parse(input()), question: " " }),
    JSON.stringify({ ...JSON.parse(input()), period: 2026 }), JSON.stringify({ ...JSON.parse(input()), authority: "admin" }),
    JSON.stringify({ ...JSON.parse(input()), requirements: ["same", "same"] })]) {
    assert.throws(() => validateBusinessReportInput(value), /Контракт входных данных: (invalid_json|schema_invalid)/);
  }
  assert.throws(() => validateBusinessReportInput(JSON.stringify({ ...JSON.parse(input()), question: "я".repeat(60_000) })), /input_too_large/);
});

test("start contract survives BPMN and rejects incompatible templates or placement", () => {
  const normalized = normalizeProcessGraph(graph(), new Set(["collector"]));
  const imported = importProcessBpmn(exportProcessBpmn({ processId: "report", name: "Report", version: 1, graph: normalized }));
  assert.equal(normalizeProcessGraph(imported.graph, new Set(["collector"])).nodes[0]!.config.inputContract, BUSINESS_REPORT_INPUT_CONTRACT);
  const misplaced = graph(); misplaced.nodes[1]!.config.inputContract = BUSINESS_REPORT_INPUT_CONTRACT;
  assert.throws(() => normalizeProcessGraph(misplaced, new Set(["collector"])), /стартовом/);
  const templated = graph(); templated.nodes[0]!.config.inputTemplate = "{{ input }}";
  assert.throws(() => normalizeProcessGraph(templated, new Set(["collector"])), /несовместим/);
});

test("invalid inputs and partial starts leave no runs, instances or success events", () => {
  const { store, processId } = setup();
  try {
    const before = store.listEvents(0, 1_000).length;
    for (const value of ["not JSON", "{}", JSON.stringify({ ...JSON.parse(input()), question: false })]) {
      assert.throws(() => store.startProcess(processId, { input: value }), /Контракт входных данных/);
    }
    assert.throws(() => store.startProcess(processId, { input: input(), startNodeId: "agent" }), /partial_start_not_allowed/);
    assert.equal(store.listProcessInstances().length, 0);
    assert.equal(store.getOverview().counts && (store.getOverview().counts as { totalRuns: number }).totalRuns, 0);
    assert.equal(store.listEvents(0, 1_000).length, before);
  } finally { store.close(); }
});

test("effective schema instructions and descriptor are pinned in the lease and manifest", () => {
  const { store, processId, node } = setup();
  try {
    const originalPrompt = store.db.prepare("SELECT system_prompt FROM agents WHERE id = 'collector'").get()!.system_prompt;
    const instance = store.startProcess(processId, { input: input() })!;
    const lease = store.leaseNext(node)!;
    assert.ok(lease.agent.systemPrompt.startsWith(String(originalPrompt)));
    assert.ok(lease.agent.systemPrompt.includes('"schemaVersion":{"const":"business-report.v1"}'));
    assert.ok(lease.agent.systemPrompt.includes("status=insufficient"));
    assert.equal(store.db.prepare("SELECT system_prompt FROM agents WHERE id = 'collector'").get()!.system_prompt, originalPrompt);
    const trace = store.getRunTrace(String(instance.runId))!;
    const manifest = trace.manifest as { stages: Array<{ agent: { systemPrompt: string }; businessOutputContract: unknown }> };
    assert.equal(manifest.stages[0]!.agent.systemPrompt, lease.agent.systemPrompt);
    assert.deepEqual(manifest.stages[0]!.businessOutputContract, businessOutputContractDescriptor(BUSINESS_REPORT_CONTRACT));
    const event = (trace.events as Array<{ type: string; data: unknown }>).find((event) => event.type === "business.input.validated")!;
    assert.ok(event);
    assert.ok(!JSON.stringify(event.data).includes("Сравните"));
    assert.equal(lease.run.input, input());
  } finally { store.close(); }
});

test("schema revision drift fails closed and node tests enforce their output contract", () => {
  const { store, processId, node } = setup();
  try {
    const instance = store.startProcess(processId, { input: input() })!;
    const lease = store.leaseNext(node)!;
    store.db.prepare("UPDATE stages SET activity_json = ? WHERE id = ?")
      .run(JSON.stringify({ businessOutputContract: { id: BUSINESS_REPORT_CONTRACT, schemaSha256: "0".repeat(64) } }), lease.stage.id);
    store.completeLease(node, lease.leaseId, JSON.stringify({ schemaVersion: BUSINESS_REPORT_CONTRACT,
      status: "insufficient", summary: "Нужны данные", claims: [], calculations: [], artifactRefs: [], limitations: ["Нет источников"] }));
    assert.equal(store.getProcessInstance(String(instance.id))!.status, "failed");
    const trace = store.getRunTrace(String(instance.runId))!;
    const event = (trace.events as Array<{ type: string; data: { codes?: string[] } }>).find((e) => e.type === "business.output.validated")!;
    assert.deepEqual(event.data.codes, ["schema_revision_mismatch"]);
    const tested = store.testProcessNode({ input: input(), node: graph().nodes[1]! });
    const testLease = store.leaseNext(node)!;
    assert.ok(testLease.agent.systemPrompt.includes(BUSINESS_REPORT_CONTRACT));
    store.completeLease(node, testLease.leaseId, "plain text cannot pass");
    assert.equal(store.getRun(tested.runId!)!.status, "failed");
  } finally { store.close(); }
});

test("subprocess input validation stops its parent without creating an invalid child", () => {
  const { store, processId } = setup();
  try {
    const parentGraph = graph();
    delete parentGraph.nodes[0]!.config.inputContract;
    parentGraph.nodes[1] = { id: "child", type: "subprocess", name: "Child", position: { x: 200, y: 0 },
      config: { subprocessProcessId: processId, subprocessVersion: 1, subprocessInputTemplate: "bad JSON" } };
    parentGraph.edges[0]!.target = "child"; parentGraph.edges[1]!.source = "child";
    const parent = store.createProcess({ name: "Parent", graph: parentGraph });
    store.publishProcess(String(parent.id));
    const instance = store.startProcess(String(parent.id), { input: "Ordinary parent input" })!;
    assert.equal(instance.status, "failed");
    assert.equal(store.listProcessInstances().length, 1);
    assert.match(String(instance.error), /invalid_json/);
  } finally { store.close(); }
});

test("replay uses the original version's input boundary after a newer publication", () => {
  const { store, processId } = setup();
  try {
    const instance = store.startProcess(processId, { input: input() })!;
    store.cancelRun(String(instance.runId));
    const next = graph(); delete next.nodes[0]!.config.inputContract;
    store.updateProcess(processId, { name: "Legacy input v2", graph: next });
    store.publishProcess(processId);
    const replay = store.replayProcessInstance(String(instance.id), { mode: "safe" })!;
    assert.equal(replay.processVersion, 1);
    assert.ok((store.getRunTrace(String(replay.runId))!.events as Array<{ type: string }>).some((e) => e.type === "business.input.validated"));
    store.db.prepare("UPDATE runs SET input = 'corrupted JSON' WHERE id = ?").run(String(instance.runId));
    assert.throws(() => store.replayProcessInstance(String(instance.id), { mode: "safe" }), /invalid_json/);
    assert.equal(store.listProcessInstances().length, 2);
  } finally { store.close(); }
});

test("HTTP start enforces schema and project authentication before queueing a run", async () => {
  const { store, processId } = setup();
  const server = createCoordinatorServer({ ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false,
    adminToken: "contract-test", oidcEnabled: false, mcpEnabled: false, sandboxEnabled: false, a2aEnabled: false, localWorkerLauncherEnabled: false }, store);
  try {
    await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
    const address = server.address(); assert.ok(address && typeof address === "object");
    const url = `http://127.0.0.1:${address.port}/api/v1/processes/${processId}/start`;
    const post = (data: string, token = "contract-test") => fetch(url, { method: "POST",
      headers: { "content-type": "application/json", "x-agat-admin-token": token }, body: JSON.stringify({ input: data, version: 1 }) });
    assert.equal((await post(input(), "wrong")).status, 401);
    const invalid = await post("not JSON"); assert.equal(invalid.status, 400);
    assert.equal(store.listProcessInstances().length, 0);
    assert.equal((await post(input())).status, 201);
    assert.equal(store.listProcessInstances().length, 1);
  } finally { server.closeAllConnections(); await new Promise<void>((resolve) => server.close(() => resolve())); store.close(); }
});
