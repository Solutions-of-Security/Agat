import assert from "node:assert/strict";
import test from "node:test";
import { processReadiness } from "../src/processReadiness.ts";
import { autoLayout, hasOverlappingSteps } from "../src/processLayout.ts";
import type { ProcessGraph, ProcessGraphNode } from "../src/types.ts";

const node = (id: string, type: ProcessGraphNode["type"], config: ProcessGraphNode["config"] = {}): ProcessGraphNode => ({
  id, type, name: id, config, position: { x: 0, y: 0 },
});
const edge = (source: string, target: string, branch: "default" | "true" | "false" | "repeat" | "exit" = "default") => ({ id: `${source}-${target}-${branch}`, source, target, branch });

test("готовая цепочка с назначенным агентом не требует исправлений", () => {
  const graph: ProcessGraph = { nodes: [node("start", "start"), node("work", "agent", { agentId: "agent-1" }), node("end", "end")], edges: [edge("start", "work"), edge("work", "end")] };
  assert.deepEqual(processReadiness(graph, [{ id: "agent-1" }]), []);
  assert.deepEqual(processReadiness(graph, []).map((issue) => issue.id), ["work:agent"]);
});

test("проверка указывает конкретный шаг и недостающую ветку условия", () => {
  const graph: ProcessGraph = { nodes: [node("start", "start"), node("if", "condition"), node("end", "end")], edges: [edge("start", "if"), edge("if", "end", "true")] };
  const issues = processReadiness(graph, []);
  assert.deepEqual(issues.map((issue) => [issue.id, issue.nodeId]), [["if:branches", "if"]]);
  graph.edges.push(edge("if", "end", "false"));
  assert.deepEqual(processReadiness(graph, []), []);
});

test("изолированный цикл и повреждённые связи не зависают и видны в проверке", () => {
  const graph: ProcessGraph = { nodes: [node("start", "start"), node("end", "end"), node("a", "wait"), node("b", "wait")], edges: [edge("start", "end"), edge("a", "b"), edge("b", "a"), edge("missing", "end")] };
  assert.deepEqual(processReadiness(graph, []).map((issue) => issue.id), ["missing-end-default", "a:unreachable", "b:unreachable"]);
});

test("пустой процесс и незаполненный HTTP-шаг объясняют необходимые действия", () => {
  assert.deepEqual(processReadiness({ nodes: [], edges: [] }, []).map((issue) => issue.id), ["start", "end"]);
  const graph: ProcessGraph = { nodes: [node("start", "start"), node("http", "http"), node("end", "end")], edges: [edge("start", "http"), edge("http", "end")] };
  assert.deepEqual(processReadiness(graph, []).map((issue) => issue.id), ["http:url"]);
});

test("автораскладка устраняет перекрытия и сохраняет конфигурацию, ветки и цикл", () => {
  const graph: ProcessGraph = { nodes: [node("start", "start"), node("if", "condition"), node("work", "agent", { agentId: "agent-1" }), node("loop", "loop", { maxIterations: 3 }), node("end", "end"), node("isolated", "wait")], edges: [edge("start", "if"), edge("if", "work", "true"), edge("if", "end", "false"), edge("work", "loop"), edge("loop", "work", "repeat"), edge("loop", "end", "exit")] };
  const original = structuredClone(graph);
  assert.equal(hasOverlappingSteps(graph), true);
  const arranged = autoLayout(graph);
  assert.equal(hasOverlappingSteps(arranged), false);
  assert.deepEqual(arranged.edges, graph.edges);
  assert.deepEqual(arranged.nodes.map(({ position: _, ...step }) => step), graph.nodes.map(({ position: _, ...step }) => step));
  assert.deepEqual(graph, original);
  assert.deepEqual(autoLayout(arranged), arranged);
});
