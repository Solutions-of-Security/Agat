import assert from "node:assert/strict";
import test from "node:test";

import { AgatStore } from "../src/database.js";
import { normalizeProcessDraftGraph, normalizeProcessGraph } from "../src/process-engine.js";
import type { ProcessGraph } from "../src/types.js";

function graph(inputTemplate?: unknown): ProcessGraph {
  return {
    nodes: [
      { id: "start", type: "start", name: "Вход", position: { x: 0, y: 0 }, config: { inputTemplate } },
      { id: "echo", type: "transform", name: "Эхо", position: { x: 200, y: 0 }, config: { template: "{{ input }}" } },
      { id: "end", type: "end", name: "Выход", position: { x: 400, y: 0 }, config: {} },
    ],
    edges: [
      { id: "a", source: "start", target: "echo", branch: "default" },
      { id: "b", source: "echo", target: "end", branch: "default" },
    ],
  } as ProcessGraph;
}

test("input templates are optional, literal text and limited in both drafts and published graphs", () => {
  for (const normalize of [normalizeProcessDraftGraph, normalizeProcessGraph]) {
    assert.deepEqual(normalize(graph(), new Set()).nodes[0]!.config, {});
    assert.deepEqual(normalize(graph("  "), new Set()).nodes[0]!.config, {});
    assert.equal(normalize(graph("  Owner: <заполнить>\nLiteral {{ input }}  "), new Set()).nodes[0]!.config.inputTemplate,
      "Owner: <заполнить>\nLiteral {{ input }}");
    assert.throws(() => normalize(graph(42), new Set()), /строкой/);
    assert.throws(() => normalize(graph("a".repeat(100_001)), new Set()), /100000/);
  }
});

test("saved templates survive versions, cloning and BPMN without changing actual run input", () => {
  const store = new AgatStore(":memory:", { seedDemo: false });
  try {
    const template = 'Owner: <заполнить>\n{ "namespace": "keycloak" }';
    const process = store.createProcess({ name: "Input template", isTemplate: true, graph: graph(template) });
    const id = String(process.id);
    store.publishProcess(id);
    store.updateProcess(id, { name: "Input template", graph: graph("Draft only") });
    const published = store.getProcessVersion(id, 1)!.graph as ProcessGraph;
    const draft = store.getProcessVersion(id, "draft")!.graph as ProcessGraph;
    assert.equal(published.nodes[0]!.config.inputTemplate, template);
    assert.equal(draft.nodes[0]!.config.inputTemplate, "Draft only");
    assert.equal(store.diffProcessVersions(id, 1, "draft")!.summary.changedNodes, 1);
    const cloned = store.createProcess({ name: "Copy with template", templateId: id });
    assert.equal((cloned.draftGraph as ProcessGraph).nodes[0]!.config.inputTemplate, template);
    const imported = store.importProcessBpmn(store.exportProcessBpmn(id, 1)!, { name: "BPMN template" });
    assert.equal((imported.draftGraph as ProcessGraph).nodes[0]!.config.inputTemplate, template);
    const instance = store.startProcess(id, { input: "User supplied values", version: 1 })!;
    assert.equal(instance.input, "User supplied values");
    assert.equal(instance.status, "completed");
  } finally {
    store.close();
  }
});
