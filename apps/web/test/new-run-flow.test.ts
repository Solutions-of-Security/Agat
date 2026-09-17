import assert from "node:assert/strict";
import test from "node:test";

import {
  RUN_PRIORITY_VALUES,
  buildCreateRunRequest,
  initialRunAgentIds,
  moveRunAgent,
  normalizeRunPriority,
  priorityPresetForValue,
} from "../src/newRunFlow";

test("new run selects one built-in agent instead of every built-in agent", () => {
  const agents = [
    { id: "custom", isBuiltIn: false },
    { id: "collector", isBuiltIn: true },
    { id: "editor", isBuiltIn: true },
  ];

  assert.deepEqual(initialRunAgentIds(agents), ["collector"]);
});

test("new run preserves valid requested agent order and removes duplicates", () => {
  const agents = [
    { id: "collector", isBuiltIn: true },
    { id: "editor", isBuiltIn: true },
  ];

  assert.deepEqual(initialRunAgentIds(agents, ["editor", "missing", "collector", "editor"]), ["editor", "collector"]);
  assert.deepEqual(initialRunAgentIds(agents, ["missing"]), ["collector"]);
});

test("agent reorder is immutable and ignores invalid moves", () => {
  const source = ["collector", "analyst", "editor"];
  assert.deepEqual(moveRunAgent(source, "analyst", -1), ["analyst", "collector", "editor"]);
  assert.deepEqual(moveRunAgent(source, "editor", 1), source);
  assert.deepEqual(source, ["collector", "analyst", "editor"]);
});

test("priority presets and exact advanced value stay within the API range", () => {
  assert.equal(priorityPresetForValue(50), "normal");
  assert.equal(priorityPresetForValue(60), "normal");
  assert.equal(priorityPresetForValue(61), "high");
  assert.equal(priorityPresetForValue(75), "high");
  assert.equal(priorityPresetForValue(85), "high");
  assert.equal(priorityPresetForValue(86), "urgent");
  assert.equal(priorityPresetForValue(100), "urgent");
  assert.equal(normalizeRunPriority(-3), 0);
  assert.equal(normalizeRunPriority(108), 100);
  assert.equal(normalizeRunPriority(Number.NaN), RUN_PRIORITY_VALUES.normal);
});

test("request builder trims copy and omits artifact path for journal-only result", () => {
  assert.deepEqual(buildCreateRunRequest({
    name: "  Проверка отчёта  ",
    input: "  Сверить показатели.  ",
    executionMode: "sequential",
    priority: 74.6,
    approvalRequired: true,
    agentIds: ["collector", "editor"],
    resultDestination: "history",
    artifactPath: " reports/q3 ",
    knowledgeCollectionIds: ["finance"],
  }), {
    name: "Проверка отчёта",
    input: "Сверить показатели.",
    executionMode: "sequential",
    priority: 75,
    approvalRequired: true,
    agentIds: ["collector", "editor"],
    resultDestination: "history",
    artifactPath: "",
    knowledgeCollectionIds: ["finance"],
  });
});
