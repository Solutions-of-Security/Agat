import assert from "node:assert/strict";
import test from "node:test";

import {
  AGENT_MARKETPLACE_TEMPLATES,
  createAgentRequestFromTemplate,
  normalizeAgentTemplateName,
} from "../src/agentMarketplace.ts";

test("marketplace содержит согласованный набор P0–P2 из отчёта", () => {
  assert.equal(AGENT_MARKETPLACE_TEMPLATES.length, 13);
  assert.deepEqual(
    Object.fromEntries(["P0", "P1", "P2"].map((priority) => [
      priority,
      AGENT_MARKETPLACE_TEMPLATES.filter((template) => template.priority === priority).length,
    ])),
    { P0: 6, P1: 5, P2: 2 },
  );
  assert.deepEqual(
    AGENT_MARKETPLACE_TEMPLATES.filter((template) => template.priority === "P0").map((template) => template.id),
    [
      "corporate-researcher",
      "document-operator",
      "itsm-operations-specialist",
      "data-reporting-analyst",
      "quality-guardian",
      "specialist-supervisor",
    ],
  );
});

test("template ids и имена уникальны, а payload проходит ограничения формы", () => {
  const ids = AGENT_MARKETPLACE_TEMPLATES.map((template) => template.id);
  const names = AGENT_MARKETPLACE_TEMPLATES.map((template) => normalizeAgentTemplateName(template.name));
  assert.equal(new Set(ids).size, ids.length);
  assert.equal(new Set(names).size, names.length);

  for (const template of AGENT_MARKETPLACE_TEMPLATES) {
    assert.match(template.id, /^[a-z0-9]+(?:-[a-z0-9]+)*$/);
    assert.ok(template.version >= 1);
    assert.ok(template.request.name.length > 0 && template.request.name.length <= 80);
    assert.ok(template.request.role.length > 0 && template.request.role.length <= 280);
    assert.ok(template.request.systemPrompt.length > 300 && template.request.systemPrompt.length <= 20_000);
    assert.ok(template.request.systemPrompt.includes("Общие правила контролируемого исполнения"));
    assert.ok(template.request.systemPrompt.includes("Не выполняй"));
    assert.ok(template.capabilities.length >= 3);
    assert.ok(template.requirements.length >= 1);
    assert.ok(template.outcome.length > 20);
    assert.ok(template.recommendedProcess.length > 10);
  }
});

test("supervisor требует явный состав specialist team, остальные шаблоны не создают вложенные команды", () => {
  const supervisor = AGENT_MARKETPLACE_TEMPLATES.find((template) => template.id === "specialist-supervisor");
  assert.ok(supervisor);
  assert.equal(supervisor.request.runtime, "langgraph");
  assert.equal(supervisor.request.runtimeConfig.profile, "specialist_team_v1");
  if (supervisor.request.runtimeConfig.profile !== "specialist_team_v1") return;
  assert.deepEqual(supervisor.request.runtimeConfig.specialistAgentIds, []);
  assert.equal(supervisor.request.runtimeConfig.stateSchema, "specialist_team_state_v1");

  for (const template of AGENT_MARKETPLACE_TEMPLATES.filter((item) => item.id !== supervisor.id)) {
    assert.equal(template.request.runtimeConfig.profile, "tool_loop_v1", template.id);
  }
});

test("установка создаёт независимую копию mutable runtime config", () => {
  const supervisor = AGENT_MARKETPLACE_TEMPLATES.find((template) => template.id === "specialist-supervisor");
  assert.ok(supervisor);
  const first = createAgentRequestFromTemplate(supervisor);
  const second = createAgentRequestFromTemplate(supervisor);
  assert.notEqual(first, supervisor.request);
  assert.notEqual(first.runtimeConfig, supervisor.request.runtimeConfig);
  assert.notEqual(first.runtimeConfig, second.runtimeConfig);

  if (first.runtimeConfig.profile !== "specialist_team_v1"
    || second.runtimeConfig.profile !== "specialist_team_v1"
    || supervisor.request.runtimeConfig.profile !== "specialist_team_v1") return;
  first.runtimeConfig.specialistAgentIds.push("agent-1");
  assert.deepEqual(second.runtimeConfig.specialistAgentIds, []);
  assert.deepEqual(supervisor.request.runtimeConfig.specialistAgentIds, []);
});
