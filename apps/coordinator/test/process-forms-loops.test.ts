import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, test } from "node:test";
import { AgatStore } from "../src/database.js";
import { evaluateProcessCondition, normalizeProcessDraftGraph, normalizeProcessGraph } from "../src/process-engine.js";
import { normalizeProcessApprovalForm, processFormOutput, validateProcessFormData } from "../src/process-forms.js";
import { exportProcessBpmn, importProcessBpmn } from "../src/process-bpmn.js";
import type { ProcessApprovalForm, ProcessGraph, ProcessGraphNode } from "../src/types.js";

const stores: AgatStore[] = [];
const directories: string[] = [];
afterEach(() => { stores.splice(0).forEach((store) => store.close()); directories.splice(0).forEach((dir) => fs.rmSync(dir, { recursive: true, force: true })); });
function createStore(file = ":memory:") { const store = new AgatStore(file, { seedDemo: false }); stores.push(store); return store; }
const form: ProcessApprovalForm = { title: "Проверка материала", description: "Укажите результат проверки", fields: [
  { id: "decision", label: "Решение", type: "select", required: true, options: ["Доработать", "Готово"] },
  { id: "details", label: "Пояснение", type: "textarea", required: false },
] };
function node(id: string, type: ProcessGraphNode["type"], config: ProcessGraphNode["config"] = {}): ProcessGraphNode {
  return { id, type, name: id, position: { x: 0, y: 0 }, config };
}
function edge(source: string, target: string, branch: ProcessGraph["edges"][number]["branch"] = "default") { return { id: `${source}-${branch}-${target}`, source, target, branch }; }
function graph(loop = false): ProcessGraph {
  return {
    nodes: [node("start", "start"), node("review", "approval", { approvalMode: "input", approvalForm: structuredClone(form) }),
      ...(loop ? [node("loop", "loop", { condition: { source: "json", path: "form.decision", operator: "equals", value: "Доработать", caseSensitive: false }, maxIterations: 2 })] : []),
      node("result", "transform", { template: "Решение: {{ json.form.decision }}; {{ json.form.details }}" }), node("end", "end")],
    edges: [edge("start", "review"), ...(loop ? [edge("review", "loop"), edge("loop", "review", "repeat"), edge("loop", "result", "exit")] : [edge("review", "result")]), edge("result", "end")],
  };
}
function start(store: AgatStore, definition = graph()) {
  const process = store.createProcess({ name: `Форма и цикл ${store.listProcesses().length}`, graph: definition });
  store.publishProcess(String(process.id));
  return { process, instance: store.startProcess(String(process.id), { input: "Исходный материал" })! };
}
function pending(store: AgatStore) { return (store.getOverview().approvals as Array<{ stageId: string; form: ProcessApprovalForm; mode: string }>)[0]!; }

test("form schema survives draft, publication and BPMN round trip; malformed published schemas are rejected", () => {
  const normalized = normalizeProcessGraph(graph(), new Set());
  assert.deepEqual(normalized.nodes[1]!.config.approvalForm?.fields.map((field) => field.id), ["decision", "details"]);
  const xml = exportProcessBpmn({ processId: "forms", name: "Forms", version: 1, graph: normalized });
  const imported = importProcessBpmn(xml);
  assert.deepEqual(imported.graph.nodes[1]!.config.approvalForm, normalized.nodes[1]!.config.approvalForm);
  assert.throws(() => normalizeProcessApprovalForm({ ...form, fields: [...form.fields, form.fields[0]] }), /уникальны/);
  assert.throws(() => normalizeProcessApprovalForm({ ...form, fields: [{ ...form.fields[0], id: "constructor" }] }), /ключ/);
  assert.throws(() => normalizeProcessApprovalForm({ ...form, fields: [{ ...form.fields[0], options: [] }] }), /вариантов/);
  assert.throws(() => normalizeProcessApprovalForm({ ...form, fields: [{ ...form.fields[0], type: "script" }] }), /тип/);
  const draft = graph();
  draft.nodes[1]!.config.approvalForm!.fields = [];
  assert.doesNotThrow(() => normalizeProcessDraftGraph(draft, new Set()));
  assert.throws(() => normalizeProcessGraph(draft, new Set()), /20 полей/);
});

test("server validates required fields, scalar types, choices, real dates, size and unexpected keys", () => {
  const schema = normalizeProcessApprovalForm({ ...form, fields: [
    ...form.fields,
    { id: "count", label: "Число", type: "number", required: true },
    { id: "date", label: "Дата", type: "date", required: true },
    { id: "confirmed", label: "Проверено", type: "checkbox", required: true },
  ] });
  const valid = { decision: "Готово", count: 0, date: "2028-02-29", confirmed: true };
  assert.deepEqual(validateProcessFormData(schema, valid), valid);
  for (const invalid of [{}, { ...valid, decision: "Иное" }, { ...valid, count: "0" }, { ...valid, count: Number.NaN }, { ...valid, date: "2026-02-29" }, { ...valid, confirmed: false }, { ...valid, confirmed: "true" }, { ...valid, extra: true }, { ...valid, details: " ".repeat(10_001) }, { ...valid, details: {} }]) {
    assert.throws(() => validateProcessFormData(schema, invalid));
  }
  assert.throws(() => validateProcessFormData(undefined, { injected: true }), /Неизвестное/);
  assert.deepEqual(validateProcessFormData({ ...form, fields: [{ id: "flag", label: "Флаг", type: "checkbox", required: false }] }, { flag: false }), { flag: false });
});

test("answers resume a pinned form after a restart, preserve input, reach templates and cannot be submitted twice", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-human-form-")); directories.push(directory);
  const file = path.join(directory, "state.db");
  let store = createStore(file);
  const { process, instance } = start(store);
  const approval = pending(store);
  assert.equal(approval.mode, "input");
  const changed = graph(); changed.nodes[1]!.config.approvalForm!.fields[0]!.options = ["Другой ответ"];
  store.updateProcess(String(process.id), { name: "Новая форма", graph: changed });
  store.publishProcess(String(process.id));
  stores.pop()!.close(); store = createStore(file);
  assert.deepEqual(pending(store).form.fields[0]!.options, ["Доработать", "Готово"]);
  assert.throws(() => store.decideApproval(approval.stageId, true), /Заполните/);
  assert.equal(store.getProcessInstance(String(instance.id))?.status, "waiting_approval");
  store.decideApproval(approval.stageId, true, "default", { decision: "Готово", details: "Проверено" });
  const run = store.getRun(String(instance.runId))!;
  assert.equal(run.status, "completed");
  const stages = run.stages as Array<{ output: string }>;
  assert.deepEqual(JSON.parse(stages[0]!.output).form, { decision: "Готово", details: "Проверено" });
  assert.equal(JSON.parse(stages[0]!.output).input, "Исходный материал");
  assert.equal(stages[1]!.output, "Решение: Готово; Проверено");
  assert.throws(() => store.decideApproval(approval.stageId, true, "default", { decision: "Готово" }), /не найден/);
});

test("each loop visit gets a new human task and a JSON answer exits the cycle early", () => {
  const store = createStore(); const { instance } = start(store, graph(true));
  const first = pending(store);
  store.decideApproval(first.stageId, true, "default", { decision: "Доработать", details: "Нужны факты" });
  const second = pending(store);
  assert.notEqual(second.stageId, first.stageId);
  assert.equal(store.getProcessInstance(String(instance.id))?.status, "waiting_approval");
  assert.throws(() => store.decideApproval(first.stageId, true, "default", { decision: "Готово" }), /не найден/);
  store.decideApproval(second.stageId, true, "default", { decision: "Готово" });
  assert.equal(store.getRun(String(instance.runId))?.status, "completed");
  assert.deepEqual(store.getProcessInstance(String(instance.id))?.loopCounts, { loop: 1 });
});

test("loop limit exits even when the answer keeps requesting rework, rejection needs no form", () => {
  const store = createStore(); const { instance } = start(store, graph(true));
  for (let index = 0; index < 3; index++) store.decideApproval(pending(store).stageId, true, "default", { decision: "Доработать" });
  assert.equal(store.getRun(String(instance.runId))?.status, "completed");
  assert.deepEqual(store.getProcessInstance(String(instance.id))?.loopCounts, { loop: 2 });
  const rejected = start(store);
  store.decideApproval(pending(store).stageId, false);
  assert.equal(store.getRun(String(rejected.instance.runId))?.status, "cancelled");
});

test("an agent approval passes validated answers to its worker input", () => {
  const store = createStore();
  const definition = graph(); definition.nodes[1] = node("review", "agent", { agentId: "editor", approvalRequired: true, approvalForm: form });
  const { instance } = start(store, definition);
  store.decideApproval(pending(store).stageId, true, "default", { decision: "Готово" });
  const worker = store.registerNode({ enrollmentToken: "unused", name: "test", platform: "test", models: [] }).id;
  const lease = store.leaseNext(worker)!;
  assert.equal(JSON.parse(lease.run.input).form.decision, "Готово");
  store.completeLease(worker, lease.leaseId, '{"form":{"decision":"Готово"}}');
  assert.equal(store.getRun(String(instance.runId))?.status, "completed");
});

test("a legacy approval without a form preserves the original input for the first agent", () => {
  const store = createStore();
  const definition: ProcessGraph = {
    nodes: [node("start", "start"), node("approval", "approval"), node("agent", "agent", { agentId: "editor" }), node("end", "end")],
    edges: [edge("start", "approval"), edge("approval", "agent"), edge("agent", "end")],
  };
  start(store, definition);
  const approval = pending(store);
  assert.equal(approval.form, undefined);
  store.decideApproval(approval.stageId, true);
  const worker = store.registerNode({ enrollmentToken: "unused", name: "legacy", platform: "test", models: [] }).id;
  assert.equal(store.leaseNext(worker)!.run.input, "Исходный материал");
});

test("fifty immediate repeats complete beyond the old 128-transition ceiling; original context stays bounded", () => {
  const store = createStore();
  const definition: ProcessGraph = {
    nodes: [node("start", "start"), node("loop", "loop", { condition: { source: "last_output", operator: "always", value: "", caseSensitive: false }, maxIterations: 50 }), node("a", "transform", { template: "{{ loop.loop }}" }), node("b", "transform", { template: "{{ lastOutput }}" }), node("end", "end")],
    edges: [edge("start", "loop"), edge("loop", "a", "repeat"), edge("a", "b"), edge("b", "loop"), edge("loop", "end", "exit")],
  };
  const { instance } = start(store, definition);
  assert.equal(store.getRun(String(instance.runId))?.status, "completed");
  assert.equal((store.getRun(String(instance.runId))?.stages as unknown[]).length, 100);
  assert.deepEqual(store.getProcessInstance(String(instance.id))?.loopCounts, { loop: 50 });
  let output = "Original";
  for (let i = 0; i < 50; i++) output = processFormOutput(output, { decision: "Доработать" });
  assert.ok(output.length < 5_000);
  assert.equal(JSON.parse(output).form.decision, "Доработать");
});

test("publication rejects cycles bypassing a bounded repeat and repeat branches that never return", () => {
  const definition = graph(true);
  definition.edges.find((item) => item.branch === "exit")!.target = "review";
  definition.edges.push(edge("review", "result", "default"));
  assert.throws(() => normalizeProcessGraph(definition, new Set()));
  const bypass: ProcessGraph = { nodes: [node("start", "start"), node("c", "condition", { condition: { source: "last_output", operator: "always", value: "", caseSensitive: false } }), node("work", "transform", { template: "x" }), node("end", "end")], edges: [edge("start", "c"), edge("c", "work", "true"), edge("work", "c"), edge("c", "end", "false")] };
  assert.throws(() => normalizeProcessGraph(bypass, new Set()), /Замкнутый путь/);
  const disconnected = graph(true); disconnected.edges.find((item) => item.branch === "repeat")!.target = "result";
  assert.throws(() => normalizeProcessGraph(disconnected, new Set()), /возвращаться/);
  const condition = { source: "json", path: "form.decision", operator: "not_equals", value: "Готово", caseSensitive: false } as const;
  assert.equal(evaluateProcessCondition(condition, "not-json"), false);
  assert.equal(evaluateProcessCondition(condition, "{}"), false);
  assert.equal(evaluateProcessCondition(condition, '{"form":{"decision":"готово"}}'), false);
});
