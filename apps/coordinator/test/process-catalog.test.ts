import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, test } from "node:test";

import { AgatStore } from "../src/database.js";
import { getProcessTemplateCatalog, instantiateCatalogTemplate } from "../src/process-catalog.js";
import { normalizeProcessGraph } from "../src/process-engine.js";
import { renderProcessTemplate } from "../src/process-expressions.js";
import type { ProcessGraph } from "../src/types.js";

const stores: AgatStore[] = [];
const directories: string[] = [];
afterEach(() => {
  for (const store of stores.splice(0)) store.close();
  for (const directory of directories.splice(0)) fs.rmSync(directory, { recursive: true, force: true });
});
function fixture() {
  const artifactsDir = fs.mkdtempSync(path.join(os.tmpdir(), "agat-catalog-test-"));
  directories.push(artifactsDir);
  const store = new AgatStore(":memory:", { seedDemo: false, artifactsDir });
  stores.push(store);
  return { store, artifactsDir };
}

test("every catalog graph can be published after explicit project agent binding", () => {
  const { store } = fixture();
  const catalog = getProcessTemplateCatalog();
  assert.equal(catalog.categories.length, 7);
  assert.equal(catalog.templates.length, 13);
  assert.equal(new Set(catalog.templates.map((template) => template.id)).size, catalog.templates.length);
  assert.equal(new Set(catalog.templates.map((template) => template.roadmapId)).size, catalog.templates.length);
  const categoryIds = new Set(catalog.categories.map((category) => category.id));
  for (const template of catalog.templates) {
    assert.ok(categoryIds.has(template.categoryId));
    const templateBindings = Object.fromEntries(template.roles.map((role) => [role.id, role.id === "reviewer" ? "editor" : "collector"]));
    const process = store.createProcess({ name: template.name, catalogTemplateId: template.id, catalogTemplateVersion: template.version, templateBindings });
    assert.equal(process.status, "draft");
    assert.equal(process.publishedVersion, 0);
    const graph = process.draftGraph as ProcessGraph;
    assert.doesNotThrow(() => normalizeProcessGraph(graph, new Set(["collector", "editor"])));
    for (const stage of template.stages) {
      assert.equal(graph.nodes.find((node) => node.id === stage.id)?.config.agentId, templateBindings[stage.roleId]);
      const task = graph.nodes.find((node) => node.id === `${stage.id}-task`)!;
      const rendered = renderProcessTemplate(task.config.template!, { input: "ORIGINAL-INPUT", lastOutput: "PREVIOUS-EVIDENCE" });
      assert.ok(rendered.includes("ORIGINAL-INPUT") && rendered.includes("PREVIOUS-EVIDENCE"));
    }
    assert.equal(graph.nodes.some((node) => ["http", "subprocess", "signal"].includes(node.type)), false);
    assert.doesNotThrow(() => store.publishProcess(String(process.id)));
  }
});

test("unassigned roles stay draft; project template copies preserve independence and cannot run unpublished", () => {
  const { store } = fixture();
  const template = getProcessTemplateCatalog().templates[0]!;
  const original = store.createProcess({ name: "Reusable draft", catalogTemplateId: template.id, catalogTemplateVersion: 1, isTemplate: true });
  assert.throws(() => store.publishProcess(String(original.id)), /агент/);
  assert.throws(() => store.startProcess(String(original.id), { input: "test" }), /[Оо]публик/);
  const copy = store.createProcess({ name: "Independent draft", templateId: String(original.id) });
  const graph = copy.draftGraph as ProcessGraph;
  graph.nodes.find((node) => node.type === "agent")!.config.agentId = "collector";
  store.updateProcess(String(copy.id), { name: String(copy.name), graph });
  const originalGraph = store.getProcess(String(original.id))!.draftGraph as ProcessGraph;
  assert.equal(originalGraph.nodes.find((node) => node.type === "agent")!.config.agentId, undefined);
  assert.throws(() => store.publishProcess(String(copy.id)), /агент/);
  assert.equal(getProcessTemplateCatalog().templates[0]!.graph.nodes.find((node) => node.type === "agent")!.config.agentId, undefined);
});

test("catalog instantiation rejects stale versions, ambiguous input and invalid or foreign bindings before creating anything", () => {
  const { store } = fixture();
  store.createProject({ id: "isolated", name: "Isolated" });
  const foreign = store.createAgent({ name: "Foreign", role: "Test", systemPrompt: "Test only", model: null }, "isolated");
  const input = { name: "Rejected", catalogTemplateId: "research-to-report", catalogTemplateVersion: 1 };
  assert.throws(() => store.createProcess({ ...input, catalogTemplateVersion: 2 }), /[Вв]ерсия/);
  assert.throws(() => store.createProcess({ ...input, templateId: "other" }), /один источник/);
  assert.throws(() => store.createProcess({ ...input, graph: { nodes: [], edges: [] } }), /один источник/);
  assert.throws(() => store.createProcess({ ...input, templateBindings: { unknown: "collector" } }), /[Нн]еизвестная роль/);
  assert.throws(() => store.createProcess({ ...input, templateBindings: { researcher: String(foreign.id) } }), /проекте/);
  assert.throws(() => store.createProcess({ name: "Rejected", templateBindings: {} }), /требуют шаблон/);
  for (const invalid of [null, [], "collector", 42]) {
    assert.throws(() => instantiateCatalogTemplate(input.catalogTemplateId, 1, invalid, new Set()), /объектом/);
  }
  assert.throws(() => instantiateCatalogTemplate("absent", 1, {}, new Set()), /не найден/);
  assert.equal(store.listProcesses().length, 0);
});

test("catalog reads and instances do not mutate canonical definitions", () => {
  const first = getProcessTemplateCatalog();
  first.templates[0]!.graph.nodes.length = 0;
  first.categories[0]!.requirements.push("injected");
  const second = getProcessTemplateCatalog();
  assert.ok(second.templates[0]!.graph.nodes.length > 0);
  assert.equal(second.categories[0]!.requirements.includes("injected"), false);
  const copy = instantiateCatalogTemplate("research-to-report", 1, { researcher: "collector" }, new Set(["collector"]));
  assert.notEqual(copy.graph, copy.template.graph);
  assert.equal(copy.template.graph.nodes.find((node) => node.type === "agent")!.config.agentId, undefined);
});

for (const accepted of [true, false]) {
  test(`all 13 templates reach human review and ${accepted ? "persist their artifact on approval" : "stop without final artifact on rejection"} using simulated worker output`, () => {
    const { store, artifactsDir } = fixture();
    const worker = store.registerNode({ enrollmentToken: "unused", name: "catalog-test-worker", platform: "test", models: ["test-model"], agentRuntimes: ["single"] }).id;
    for (const template of getProcessTemplateCatalog().templates) {
      const templateBindings = Object.fromEntries(template.roles.map((role) => [role.id, "collector"]));
      const process = store.createProcess({ name: template.name, catalogTemplateId: template.id, catalogTemplateVersion: 1, templateBindings });
      store.publishProcess(String(process.id));
      const instance = store.startProcess(String(process.id), { input: "Synthetic task", resultDestination: "artifacts" })!;
      for (const stage of template.stages) {
        const lease = store.leaseNext(worker);
        assert.ok(lease, `${template.id}/${stage.id}`);
        assert.equal(lease.run.id, instance.runId);
        assert.equal(lease.stage.processNodeId, stage.id);
        store.completeLease(worker, lease.leaseId, `SIMULATED evidence ${template.id}/${stage.id}`);
      }
      assert.equal(store.getProcessInstance(String(instance.id))!.status, "waiting_approval");
      assert.equal(store.leaseNext(worker), null);
      assert.equal(store.getRunTrace(String(instance.runId))!.artifacts.some((item) => item.kind === "process_artifact"), false);
      const approval = (store.getOverview().approvals as Array<{ stageId: string }>)[0]!;
      store.decideApproval(approval.stageId, accepted);
      const result = store.getProcessInstance(String(instance.id))!;
      assert.equal(result.status, accepted ? "completed" : "cancelled");
      const artifact = store.getRunTrace(String(instance.runId))!.artifacts.find((item) => item.kind === "process_artifact");
      if (accepted) {
        assert.ok(artifact);
        const content = fs.readFileSync(path.join(artifactsDir, artifact.relativePath), "utf8");
        assert.ok(content.includes(`SIMULATED evidence ${template.id}/review`));
        assert.ok(content.includes(`${template.id} v1`));
      } else assert.equal(artifact, undefined);
    }
  });
}
