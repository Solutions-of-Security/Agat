import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { AgatStore } from "../src/database.js";
import { loadConfig } from "../src/config.js";
import { createCoordinatorServer } from "../src/server.js";
import { getInternalReportPack, getProcessPackReleases, validateProcessPackInput, type ProcessPackInput, type ProcessPackPreview } from "../src/process-packs.js";
import type { ProcessGraph } from "../src/types.js";

const legacy = getInternalReportPack();
const pack = getInternalReportPack(2);
const legacyInput = { version: 1, manifestSha256: legacy.manifestSha256, model: legacy.defaults.model };
const input = (ids: string[]): ProcessPackInput => ({ version: 2, manifestSha256: pack.manifestSha256, model: pack.defaults.model, knowledgeCollectionIds: ids });
function sources(store: AgatStore, project = "default") {
  const collection = store.createKnowledgeCollection({ name: "My documents", embeddingModel: pack.defaults.embeddingModel }, project);
  store.ingestKnowledgeDocument(String(collection.id), { name: "Counts.csv", content: "July: 100 requests. August: 120 requests. These synthetic test counts do not explain causes." }, project);
  return String(collection.id);
}

test("registry preserves exact v1 digest and pins v2 schemas, policies, graph and unqualified baseline", () => {
  assert.equal(legacy.manifestSha256, "65cd83ca1606b39d7ab9206520304fd3ec416b00e0c0d3653dddf066847213a7");
  for (const release of getProcessPackReleases()) {
    const { manifestSha256, ...content } = release;
    assert.equal(manifestSha256, createHash("sha256").update(JSON.stringify(content)).digest("hex"));
  }
  assert.equal(pack.knowledge.mode, "project_collections"); assert.deepEqual(pack.knowledge.documents, []);
  assert.equal(pack.schemas!.input.properties.schemaVersion.const, "report-request.v1");
  assert.equal(pack.schemas!.output.properties.schemaVersion.const, "business-report.v1");
  assert.equal(pack.profile!.qualification, "unqualified"); assert.equal(pack.evaluation!.humanAcceptance, "required");
  assert.equal(pack.process.graph.nodes[0]!.config.inputContract, "report-request.v1");
  assert.equal(pack.process.graph.nodes.find(node => node.id === "reviewer")!.config.outputContract, "business-report.v1");
  assert.equal(pack.process.graph.nodes.find(node => node.id === "artifact")!.config.reportFormat, "business-report-markdown.v1");
  assert.deepEqual(pack.process.graph.mcpToolAllowlist, []); assert.equal(pack.process.graph.allowPartialStart, false);
  const mutated = getInternalReportPack(2); mutated.roles[0]!.systemPrompt = "changed";
  assert.notEqual(getInternalReportPack(2).roles[0]!.systemPrompt, "changed", "registry reads return isolated objects");
  assert.throws(() => getInternalReportPack(3), /не найдена/);
  for (const ids of [[], ["same", "same"], [""], Array.from({ length: 21 }, (_, i) => String(i))]) assert.throws(() => validateProcessPackInput(pack.id, input(ids)), /коллекций/);
  assert.throws(() => validateProcessPackInput(pack.id, { ...input(["ok"]), manifestSha256: "stale" }), /изменилась/);
  assert.throws(() => validateProcessPackInput(pack.id, { ...legacyInput, knowledgeCollectionIds: ["own"] }), /v2/);
  assert.deepEqual(validateProcessPackInput(pack.id, input(["b", "a"])).knowledgeCollectionIds, ["a", "b"]);
});

test("install/update/rollback preserve active v1 instances, exact history, user edits and idempotency after restart", () => {
  const root = mkdtempSync(path.join(tmpdir(), "agat-pack-history-")); const database = path.join(root, "state.sqlite");
  let store = new AgatStore(database, { seedDemo: false, artifactsDir: root });
  try {
    store.updateModelRouterPolicy({ enabled: false });
    const agentsBefore = store.listAgents().length;
    const old = store.installProcessPack(pack.id, legacyInput).installation!;
    store.db.prepare("DELETE FROM process_pack_history").run(); // Exact pre-v31 active-pointer-only state.
    const beforeRead = store.listEvents(0, 100).length;
    assert.deepEqual(store.getProcessPackHistory(pack.id), [{ manifest: legacy, installation: old }]);
    assert.equal(store.listEvents(0, 100).length, beforeRead);
    assert.equal(store.db.prepare("SELECT COUNT(*) AS count FROM process_pack_history").get()!.count, 0);
    const oldProcess = store.getProcess(old.processId)!;
    store.updateProcess(old.processId, { name: "My v1 draft", graph: oldProcess.draftGraph as ProcessGraph });
    const oldInstance = store.startProcess(old.processId, { input: legacy.sample.input, version: 1, startMode: "queue" })!;
    const runBefore = store.getRun(String(oldInstance.runId))!;
    const collection = sources(store);
    const options = input([collection]);
    const preview = store.preflightProcessPack(pack.id, options);
    assert.equal(preview.preflight.queueable, false); assert.equal(preview.preflight.runnableNow, false);
    assert.ok(preview.changes.some(change => change.section === "schemas"));
    const next = store.installProcessPack(pack.id, options).installation!;
    assert.deepEqual(store.getProcessPackInstallation(pack.id), old, "installing a release does not replace active pointer");
    assert.equal((store.knowledgeOverview().collections as unknown[]).length, 2, "v2 reuses the user's collection");
    assert.deepEqual(store.installProcessPack(pack.id, options).installation, next);
    assert.equal(store.listAgents().length, agentsBefore + 6); assert.equal(store.listProcesses().length, 2);
    assert.throws(() => store.activateProcessPack(pack.id, { version: 2, manifestSha256: next.manifestSha256, expectedActiveManifestSha256: null }), /изменилась/);
    const activate = { version: 2, manifestSha256: next.manifestSha256, expectedActiveManifestSha256: old.manifestSha256 };
    assert.deepEqual(store.activateProcessPack(pack.id, activate).activeInstallation, next);
    const events = store.listEvents(0, 200).filter(event => event.type === "process.pack.activated").length;
    assert.deepEqual(store.activateProcessPack(pack.id, activate).activeInstallation, next, "lost acknowledgement may retry safely");
    assert.equal(store.listEvents(0, 200).filter(event => event.type === "process.pack.activated").length, events);
    assert.deepEqual(store.getRun(String(oldInstance.runId)), runBefore, "active instances are byte-for-byte untouched");
    assert.deepEqual(store.getProcessPackHistory(pack.id).map(entry => entry.manifest.version), [2, 1]);
    assert.equal(store.getProcess(old.processId)!.name, "My v1 draft");
    store.close(); store = new AgatStore(database, { seedDemo: false, artifactsDir: root });
    assert.deepEqual(store.installProcessPack(pack.id, options).installation, next);
    assert.equal(store.getProcessPackHistory(pack.id)[1]!.manifest.manifestSha256, legacy.manifestSha256);
    assert.deepEqual(store.activateProcessPack(pack.id, { version: 1, manifestSha256: old.manifestSha256, expectedActiveManifestSha256: next.manifestSha256 }).activeInstallation, old);
    assert.deepEqual(store.getRun(String(oldInstance.runId)), runBefore);
    assert.equal(store.listProcesses().length, 2); assert.equal(store.getProcess(old.processId)!.name, "My v1 draft");
    const rollbackPreview = store.preflightProcessPack(pack.id, options);
    assert.deepEqual(rollbackPreview.changes.find(change => change.section === "knowledgeCollectionIds")!.after, [collection]);
    assert.throws(() => store.installProcessPack(pack.id, { ...options, model: "other" }), /другой конфигурацией/);
    const other = store.createKnowledgeCollection({ name: "Other sources", embeddingModel: pack.defaults.embeddingModel });
    assert.throws(() => store.installProcessPack(pack.id, input([String(other.id)])), /конфигурацией/);
  } finally { store.close(); rmSync(root, { recursive: true, force: true }); }
});

test("v2 partial installation failure rolls back agents/history; foreign sources cannot install and deleted sources cannot activate", () => {
  const store = new AgatStore(":memory:", { seedDemo: false });
  try {
    const agentsBefore = store.listAgents().length;
    const collection = sources(store); const options = input([collection]);
    store.createProject({ id: "foreign", name: "Foreign" });
    assert.throws(() => store.installProcessPack(pack.id, options, "foreign"), /недоступна/);
    const conflict = store.createProcess({ name: `${pack.name} · default`, graph: { nodes: [
      { id: "start", type: "start", name: "Start", position: { x: 0, y: 0 }, config: {} },
      { id: "end", type: "end", name: "End", position: { x: 1, y: 0 }, config: {} },
    ], edges: [{ id: "edge", source: "start", target: "end", branch: "default" }] } });
    assert.throws(() => store.installProcessPack(pack.id, options), /существует|UNIQUE/);
    assert.equal(store.listAgents().length, agentsBefore); assert.deepEqual(store.getProcessPackHistory(pack.id), []);
    assert.equal(store.getProcessPackInstallation(pack.id), null);
    assert.equal((store.knowledgeOverview().collections as unknown[]).length, 1);
    store.updateProcess(String(conflict.id), { name: "Unrelated process", graph: store.getProcess(String(conflict.id))!.draftGraph as ProcessGraph });
    const old = store.installProcessPack(pack.id, legacyInput).installation!;
    const next = store.installProcessPack(pack.id, options).installation!;
    store.deleteKnowledgeCollection(collection);
    assert.throws(() => store.activateProcessPack(pack.id, { version: 2, manifestSha256: next.manifestSha256, expectedActiveManifestSha256: old.manifestSha256 }), /коллекция|Коллекция/);
    assert.deepEqual(store.getProcessPackInstallation(pack.id), old);
    assert.equal(store.getProcessPackInstallation(pack.id, "foreign", 2), null);
  } finally { store.close(); }
});

for (const mode of ["insufficient", "invalid_calculation"] as const) {
  test(`v2 ${mode} stops before human approval and final artifact`, () => {
    const store = new AgatStore(":memory:", { seedDemo: false });
    try {
      store.updateModelRouterPolicy({ enabled: false });
      const collection = sources(store);
      const installed = store.installProcessPack(pack.id, input([collection])).installation!;
      const node = store.registerNode({ enrollmentToken: "test", name: "report-worker", platform: "test", models: [pack.defaults.model], embeddingModels: [pack.defaults.embeddingModel] }).id;
      for (let job = store.leaseKnowledgeEmbedding(node); job; job = store.leaseKnowledgeEmbedding(node)) store.completeKnowledgeEmbedding(node, job.leaseId, job.chunks.map(chunk => ({ chunkId: chunk.id, embedding: [1, 0] })));
      const instance = store.startProcess(installed.processId, { input: pack.sample.input, version: installed.processVersion, startMode: "now", resultDestination: "history" })!;
      for (const role of pack.roles) {
        const lease = store.leaseNext(node)!; assert.ok(lease);
        const retrieved = store.searchKnowledge(node, lease.leaseId, { queries: [{ embeddingModel: pack.defaults.embeddingModel, collectionIds: [collection], vector: [1, 0] }] });
        const marker = retrieved.hits[0]!.marker;
        store.completeLease(node, lease.leaseId, role.id !== "reviewer" ? "Draft" : JSON.stringify({ schemaVersion: "business-report.v1", status: mode === "insufficient" ? "insufficient" : "ready", summary: "Review required",
          claims: [{ id: "counts", text: "Counts", sourceRefs: [marker] }], calculations: [{ id: "sum", operation: "sum", operands: [100, 120], result: mode === "insufficient" ? 220 : 999, sourceRefs: [marker] }], artifactRefs: [], limitations: ["Insufficient evidence"] }));
      }
      assert.equal(store.getProcessInstance(String(instance.id))!.status, "failed");
      assert.equal(store.listRunArtifacts(String(instance.runId)).length, 0);
      assert.equal((store.getRun(String(instance.runId))!.stages as Array<{ status: string }>).some(stage => stage.status === "waiting_approval"), false);
      assert.ok(store.listEvents(0, 200).some(event => event.type === "business.output.validated"));
    } finally { store.close(); }
  });
}

test("history verifies stored manifest bytes instead of trusting its declared digest", () => {
  const store = new AgatStore(":memory:", { seedDemo: false });
  try {
    store.installProcessPack(pack.id, legacyInput);
    const manifest = store.getProcessPackHistory(pack.id)[0]!.manifest;
    store.db.prepare("UPDATE process_pack_history SET manifest_json = ?").run(JSON.stringify({ ...manifest, name: "Changed after installation" }));
    assert.throws(() => store.getProcessPackHistory(pack.id), /повреждена/);
  } finally { store.close(); }
});

test("HTTP v2 uses project documents, validates before approval, and produces exact downloadable Markdown after human decision", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "agat-pack-v2-http-"));
  const store = new AgatStore(":memory:", { seedDemo: false, artifactsDir: root });
  store.updateModelRouterPolicy({ enabled: false });
  const config = { ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false, adminToken: "pack-v2-test-only", oidcEnabled: false,
    mcpEnabled: false, sandboxEnabled: false, a2aEnabled: false, localWorkerLauncherEnabled: false };
  const server = createCoordinatorServer(config, store);
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address(); assert.ok(address && typeof address === "object");
  const base = `http://127.0.0.1:${address.port}/api/v1`;
  const headers = { "content-type": "application/json", "x-agat-admin-token": config.adminToken };
  const post = (route: string, body: unknown, customHeaders = headers) => fetch(base + route, { method: "POST", headers: customHeaders, body: JSON.stringify(body) });
  try {
    const agentsBefore = store.listAgents().length;
    const catalog = await (await fetch(base + "/process-packs", { headers })).json() as { packs: Array<{ version: number }>; releases: Array<{ version: number }> };
    assert.deepEqual(catalog.packs.map(item => item.version), [1], "old qualification clients retain exact catalog");
    assert.deepEqual(catalog.releases.map(item => item.version), [2, 1]);
    const collection = sources(store); const options = input([collection]);
    const installs = await Promise.all([post(`/process-packs/${pack.id}/install`, options), post(`/process-packs/${pack.id}/install`, options)]);
    assert.deepEqual(installs.map(response => response.status).sort(), [200, 201]);
    const previews = await Promise.all(installs.map(response => response.json() as Promise<ProcessPackPreview>));
    const installed = previews[0]!.installation!; assert.deepEqual(installed, previews[1]!.installation);
    assert.deepEqual(installed.knowledgeCollectionIds, [collection]);
    assert.equal(store.listProcesses().length, 1); assert.equal(store.listAgents().length, agentsBefore + 3);
    const graph = store.getProcessVersion(installed.processId, installed.processVersion)!.graph as ProcessGraph;
    assert.deepEqual(graph.requiredKnowledgeCollectionIds, [collection]);
    const denied = await post(`/process-packs/${pack.id}/activate`, { version: 2, manifestSha256: pack.manifestSha256, expectedActiveManifestSha256: null }, { ...headers, "x-agat-admin-token": "wrong" });
    assert.equal(denied.status, 401);
    const invalidStart = await post(`/processes/${installed.processId}/start`, { version: 1, input: "plain text", startMode: "queue" });
    assert.equal(invalidStart.status, 400); assert.equal(store.db.prepare("SELECT COUNT(*) AS count FROM runs").get()!.count, 0);
    const node = store.registerNode({ enrollmentToken: "test", name: "report-worker", platform: "test", models: [options.model], embeddingModels: [pack.defaults.embeddingModel] }).id;
    for (let job = store.leaseKnowledgeEmbedding(node); job; job = store.leaseKnowledgeEmbedding(node)) store.completeKnowledgeEmbedding(node, job.leaseId, job.chunks.map(chunk => ({ chunkId: chunk.id, embedding: [1, 0] })));
    const started = await post(`/processes/${installed.processId}/start`, { version: 1, input: JSON.stringify({ schemaVersion: "report-request.v1", question: "Compare counts", period: "July and August", requirements: [] }), startMode: "now", resultDestination: "history" });
    assert.equal(started.status, 201); const instance = await started.json() as { id: string; runId: string };
    let marker = "";
    for (const role of pack.roles) {
      const lease = store.leaseNext(node)!; assert.ok(lease); assert.equal(lease.agent.id, installed.agentIds[role.id]);
      assert.deepEqual(lease.mcpTools, []);
      const retrieved = store.searchKnowledge(node, lease.leaseId, { queries: [{ embeddingModel: pack.defaults.embeddingModel, collectionIds: [collection], vector: [1, 0] }] });
      marker = retrieved.hits[0]!.marker;
      store.completeLease(node, lease.leaseId, role.id === "reviewer" ? JSON.stringify({ schemaVersion: "business-report.v1", status: "ready", summary: "Requests increased",
        claims: [{ id: "counts", text: "100 then 120 requests", sourceRefs: [marker] }], calculations: [{ id: "growth", operation: "percent_change", operands: [100, 120], result: 20, sourceRefs: [marker] }], artifactRefs: [], limitations: ["Causes unknown"] }) : `Facts: 100 then 120 [${marker}].`);
    }
    assert.equal(store.getProcessInstance(instance.id)!.status, "waiting_approval");
    assert.equal(store.listRunArtifacts(instance.runId).length, 0);
    const approval = (store.getRun(instance.runId)!.stages as Array<{ id: string; status: string }>).find(stage => stage.status === "waiting_approval")!;
    assert.equal((await post(`/approvals/${approval.id}`, { decision: "approve" })).status, 204);
    assert.equal(store.getProcessInstance(instance.id)!.status, "completed");
    const artifact = store.listRunArtifacts(instance.runId).find(item => item.name === "internal-report.md")!; assert.ok(artifact);
    const downloaded = await fetch(`${base}/artifacts/${artifact.id}/download`, { headers }); assert.equal(downloaded.status, 200);
    const bytes = Buffer.from(await downloaded.arrayBuffer());
    assert.equal(createHash("sha256").update(bytes).digest("hex"), artifact.sha256);
    assert.match(bytes.toString("utf8"), /20/);
    const sourceId = String(store.db.prepare("SELECT id FROM knowledge_documents WHERE collection_id = ?").get(collection)!.id);
    assert.ok(bytes.toString("utf8").includes(`documentId=${sourceId}`));
    const history = await fetch(base + `/process-packs/${pack.id}/history`, { headers }); assert.equal(history.status, 200);
    assert.deepEqual((await history.json() as { history: Array<{ manifest: { version: number } }> }).history.map(entry => entry.manifest.version), [2]);
  } finally { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); store.close(); rmSync(root, { recursive: true, force: true }); }
});
