import assert from "node:assert/strict";
import test from "node:test";
import { getProcessTemplateCatalog } from "../../coordinator/src/process-catalog.ts";
import { catalogProcessRequest, filterProcessTemplates, suggestedProcessName } from "../src/processTemplates.ts";
import { processReadiness } from "../src/processReadiness.ts";
import type { ProcessTemplateCatalog } from "../src/types.ts";

const catalog: ProcessTemplateCatalog = getProcessTemplateCatalog();

test("category and case-insensitive multiword search compose, including empty results", () => {
  assert.deepEqual(filterProcessTemplates(catalog.templates, "documents", " ДОГОВОР   политика "), []);
  assert.deepEqual(filterProcessTemplates(catalog.templates, "documents", " ДОГОВОР ").map((item) => item.id), ["contract-review"]);
  assert.equal(filterProcessTemplates(catalog.templates, "analytics", "").length, 3);
  assert.equal(filterProcessTemplates(catalog.templates, "", "").length, 13);
  assert.equal(filterProcessTemplates(catalog.templates, "operations", "договор").length, 0);
});

test("creation pins catalog version, only sends selected roles, and never mutates the template", () => {
  const template = catalog.templates[0]!;
  const payload = catalogProcessRequest(template, "Report", "Description", { researcher: "agent-1", reviewer: "", obsolete: "agent-2" }, false);
  assert.deepEqual(payload.templateBindings, { researcher: "agent-1" });
  assert.equal(payload.catalogTemplateVersion, template.version);
  assert.equal(payload.graph, undefined);
  assert.equal(payload.templateId, undefined);
  assert.equal(template.graph.nodes.find((node) => node.type === "agent")!.config.agentId, undefined);
});

test("suggested copy names are unique case-insensitively and respect the API length limit", () => {
  assert.equal(suggestedProcessName("Отчёт", ["ОТЧЁТ", "Отчёт (2)"]), "Отчёт (3)");
  const longName = "x".repeat(100);
  assert.equal(suggestedProcessName(longName, [longName]).length, 100);
  assert.ok(suggestedProcessName(longName, [longName]).endsWith(" (2)"));
});

test("editor readiness blocks unbound catalog drafts and accepts graphs after all agent roles are assigned", () => {
  for (const template of catalog.templates) {
    assert.ok(processReadiness(template.graph, []).length > 0, template.id);
    const graph = structuredClone(template.graph);
    for (const node of graph.nodes) if (node.type === "agent") node.config.agentId = "test-agent";
    assert.deepEqual(processReadiness(graph, [{ id: "test-agent" }]), [], template.id);
  }
});
