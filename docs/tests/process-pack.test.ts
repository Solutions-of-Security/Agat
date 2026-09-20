import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { AgatStore } from "../../apps/coordinator/src/database.js";
import { loadConfig } from "../../apps/coordinator/src/config.js";
import { createCoordinatorServer } from "../../apps/coordinator/src/server.js";
import { getInternalReportPack, type ProcessPackPreview } from "../../apps/coordinator/src/process-packs.js";
import type { ProcessGraph } from "../../apps/coordinator/src/types.js";
import { normalizeCatalogTools } from "../../apps/coordinator/src/mcp.js";

const pack = getInternalReportPack();
const input = { version: pack.version, manifestSha256: pack.manifestSha256, model: pack.defaults.model };

test("clean project → preview → atomic/idempotent install → recovery → run → approval → downloadable report → evidence", async () => {
  const directory = mkdtempSync(path.join(tmpdir(), "agat-pack-"));
  const store = new AgatStore(":memory:", { seedDemo: false, artifactsDir: directory });
  store.updateModelRouterPolicy({ enabled: false });
  store.createProject({ id: "reports", name: "Reports" });
  const config = { ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false, adminToken: "pack-e2e-only", oidcEnabled: false,
    mcpEnabled: true, sandboxEnabled: false, a2aEnabled: false, localWorkerLauncherEnabled: false };
  const server = createCoordinatorServer(config, store);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address(); assert.ok(address && typeof address === "object");
  const base = `http://127.0.0.1:${address.port}/api/v1`;
  const headers = { "content-type": "application/json", "x-agat-admin-token": config.adminToken, "x-agat-project-id": "reports" };
  const post = (route: string, body: unknown, customHeaders = headers) => fetch(base + route, { method: "POST", headers: customHeaders, body: JSON.stringify(body) });
  const preview = async () => { const response = await post(`/process-packs/${pack.id}/preflight`, input); assert.equal(response.status, 200); return response.json() as Promise<ProcessPackPreview>; };
  try {
    const catalog = await fetch(base + "/process-packs", { headers });
    assert.equal((await catalog.json() as { packs: unknown[] }).packs.length, 1);
    const before = store.listEvents(0, 100, "reports").length;
    const empty = await preview();
    assert.equal(empty.preflight.saved, false);
    assert.ok(empty.preflight.blockers.some((blocker) => blocker.code === "pack_installation"));
    assert.ok(empty.preflight.blockers.some((blocker) => blocker.category === "runtime"));
    assert.equal(store.listEvents(0, 100, "reports").length, before);
    assert.equal(store.listProcesses("reports").length, 0);
    const denied = await post(`/process-packs/${pack.id}/install`, input, { ...headers, "x-agat-admin-token": "wrong" });
    assert.equal(denied.status, 401);
    const stale = await post(`/process-packs/${pack.id}/install`, { ...input, manifestSha256: "stale" });
    assert.equal(stale.status, 400);
    assert.equal(store.listProcesses("reports").length, 0);
    const installedResponse = await post(`/process-packs/${pack.id}/install`, input);
    assert.equal(installedResponse.status, 201);
    const installed = await installedResponse.json() as ProcessPackPreview;
    const installation = installed.installation!;
    assert.ok(installation);
    assert.equal(installed.preflight.queueable, true);
    assert.equal(installed.preflight.runnableNow, false);
    assert.equal(installed.preflight.scenarioVerified, false);
    assert.equal(Object.keys(installation.agentIds).length, 3);
    assert.ok(installed.preflight.blockers.some((blocker) => blocker.category === "embeddings" && blocker.recovery.href.includes(installation.knowledgeCollectionIds[0]!)));
    const repeats = await Promise.all([post(`/process-packs/${pack.id}/install`, input), post(`/process-packs/${pack.id}/install`, input)]);
    for (const repeat of repeats) { assert.equal(repeat.status, 200); assert.deepEqual((await repeat.json() as ProcessPackPreview).installation, installation); }
    assert.equal(store.listProcesses("reports").length, 1);
    assert.equal(store.getProcessPackInstallation(pack.id), null, "installation is project scoped");
    const other = await post(`/process-packs/${pack.id}/install`, input, { ...headers, "x-agat-project-id": "default" });
    assert.equal(other.status, 201, "the same pack also installs in another clean project");
    assert.notEqual((await other.json() as ProcessPackPreview).installation!.processId, installation.processId);
    const foreign = await post(`/processes/${installation.processId}/preflight`, { version: 1 }, { ...headers, "x-agat-project-id": "default" });
    assert.equal(foreign.status, 404);
    const deniedNow = await post(`/processes/${installation.processId}/start`, { input: pack.sample.input, version: 1, startMode: "now" });
    assert.equal(deniedNow.status, 409);
    const omitted = await post(`/processes/${installation.processId}/start`, { input: pack.sample.input, version: 1, startMode: "queue", knowledgeCollectionIds: [] });
    assert.equal(omitted.status, 409, "required knowledge cannot be removed to bypass readiness");
    assert.ok((await omitted.json() as { preflight: ProcessPackPreview["preflight"] }).preflight.blockers.some((item) => item.code.startsWith("knowledge_required:")));
    const node = store.registerNode({ enrollmentToken: "test", name: "report-worker", platform: "test", models: ["wrong-model"], embeddingModels: [pack.defaults.embeddingModel] }).id;
    const missingModel = (await preview()).preflight.blockers.find((blocker) => blocker.category === "model")!;
    assert.ok(missingModel.recovery.href.includes(`model=${encodeURIComponent(input.model)}`));
    assert.ok(missingModel.recovery.href.includes(`nodeId=${node}`));
    store.heartbeatNode(node, {}, { models: [input.model], embeddingModels: [pack.defaults.embeddingModel] });
    let jobs = 0;
    for (let lease = store.leaseKnowledgeEmbedding(node); lease; lease = store.leaseKnowledgeEmbedding(node)) {
      store.completeKnowledgeEmbedding(node, lease.leaseId, lease.chunks.map((chunk) => ({ chunkId: chunk.id, embedding: [1, 0, 0] }))); jobs++;
    }
    assert.equal(jobs, 4, "both project collections are indexed by the shared worker pool");
    const ready = await preview(); assert.equal(ready.preflight.runnableNow, true);
    assert.equal(ready.preflight.scenarioVerified, false);
    const skippedApproval = await post(`/processes/${installation.processId}/start`, { input: "Unreviewed report", version: 1, startMode: "now", startNodeId: "artifact" });
    assert.equal(skippedApproval.status, 400, "the installed pack cannot bypass review via a partial start");
    // Existing project tools must not leak into this pack, even if policy allows them.
    const external = store.createMcpServer({ name: "External", namespace: "external", endpoint: "https://example.invalid/mcp", enabled: true, trustAnnotations: true, defaultPolicy: "auto" }, "reports");
    store.saveMcpCatalog(String(external.id), normalizeCatalogTools("external", [{ name: "send", inputSchema: { type: "object" }, annotations: { readOnlyHint: true } }]), 60_000, "test");
    const startResponse = await post(`/processes/${installation.processId}/start`, { input: pack.sample.input, version: 1, startMode: "now", resultDestination: "history" });
    assert.equal(startResponse.status, 201);
    const instance = await startResponse.json() as { id: string; runId: string; processVersion: number };
    assert.equal(instance.processVersion, 1);
    for (const role of pack.roles) {
      const lease = store.leaseNext(node); assert.ok(lease);
      assert.equal(lease.agent.id, installation.agentIds[role.id]);
      assert.deepEqual(lease.mcpTools, []);
      const sources = store.searchKnowledge(node, lease.leaseId, { queries: [{ embeddingModel: pack.defaults.embeddingModel, collectionIds: installation.knowledgeCollectionIds, vector: [1, 0, 0] }] });
      assert.ok(sources.hits.some((hit) => hit.provenance.sourceUri === pack.knowledge.documents[0]!.sourceUri));
      assert.ok(sources.hits.every((hit) => installation.knowledgeCollectionIds.includes(hit.provenance.collectionId)));
      assert.equal(store.resolveMcpLeaseTool(node, lease.leaseId, "external__send"), null, "gateway rejects a forged tool request too");
      store.completeLease(node, lease.leaseId, `${role.name}\n${pack.sample.expected}\nИсточники: SUPPORT-DEMO v1; REPORT-DEMO v1.`);
    }
    assert.equal(store.getProcessInstance(instance.id, "reports")!.status, "waiting_approval");
    assert.equal((await preview()).preflight.scenarioVerified, false);
    assert.equal(store.listRunArtifacts(instance.runId).length, 0);
    const stages = store.getRun(instance.runId, "reports")!.stages as Array<{ id: string; status: string }>;
    const approval = stages.find((stage) => stage.status === "waiting_approval")!;
    const decision = await post(`/approvals/${approval.id}`, { decision: "approve" });
    assert.equal(decision.status, 204);
    assert.equal(store.getProcessInstance(instance.id, "reports")!.status, "completed");
    const artifact = store.listRunArtifacts(instance.runId).find((item) => item.name === "internal-report.md")!;
    assert.ok(artifact);
    const download = await fetch(`${base}/artifacts/${artifact.id}/download`, { headers });
    assert.equal(download.status, 200);
    assert.match(await download.text(), /SUPPORT-DEMO v1/);
    const verified = await preview(); assert.equal(verified.preflight.scenarioVerified, true);
    assert.equal(verified.preflight.verification?.runId, instance.runId);
    store.ingestKnowledgeDocument(installation.knowledgeCollectionIds[0]!, { name: "Новые данные", content: "Обновление источников требует повторной проверки отчёта." }, "reports");
    assert.equal((await preview()).preflight.scenarioVerified, false);
  } finally {
    server.closeAllConnections(); await new Promise<void>((resolve) => server.close(() => resolve()));
    store.close(); rmSync(directory, { recursive: true, force: true });
  }
});

test("pack requirements survive BPMN, drafts and version copies; missing sources and human rejection cannot produce verified reports", () => {
  const directory = mkdtempSync(path.join(tmpdir(), "agat-pack-policy-"));
  const store = new AgatStore(":memory:", { seedDemo: false, artifactsDir: directory });
  try {
    store.updateModelRouterPolicy({ enabled: false });
    const installation = store.installProcessPack(pack.id, input).installation!;
    const process = store.getProcess(installation.processId)!;
    const imported = store.importProcessBpmn(store.exportProcessBpmn(installation.processId, 1)!, { name: "Imported report" });
    assert.deepEqual((imported.draftGraph as ProcessGraph).mcpToolAllowlist, []);
    assert.equal((imported.draftGraph as ProcessGraph).allowPartialStart, false);
    assert.deepEqual((imported.draftGraph as ProcessGraph).requiredKnowledgeCollectionIds, installation.knowledgeCollectionIds);
    const graph = process.draftGraph as ProcessGraph;
    const { mcpToolAllowlist: _allowlist, ...withoutPolicy } = graph;
    store.updateProcess(installation.processId, { name: String(process.name), graph: withoutPolicy });
    assert.ok(store.diffProcessVersions(installation.processId, 1, "draft")!.entries.some((entry) => entry.kind === "metadata_changed"));
    assert.deepEqual((store.getProcessVersion(installation.processId, 1)!.graph as ProcessGraph).mcpToolAllowlist, [], "draft policy edits cannot change installed version");
    const node = store.registerNode({ name: "review-worker", enrollmentToken: "test", platform: "test", models: [input.model], embeddingModels: [pack.defaults.embeddingModel] }).id;
    for (let job = store.leaseKnowledgeEmbedding(node); job; job = store.leaseKnowledgeEmbedding(node)) store.completeKnowledgeEmbedding(node, job.leaseId, job.chunks.map((chunk) => ({ chunkId: chunk.id, embedding: [1, 0] })));
    assert.equal(store.preflightProcessPack(pack.id, input).preflight.runnableNow, true);
    const instance = store.startProcess(installation.processId, { input: pack.sample.input, version: 1, startMode: "now", resultDestination: "history" })!;
    for (const _role of pack.roles) { const lease = store.leaseNext(node)!; store.completeLease(node, lease.leaseId, "Требуется ручная проверка: отсутствуют основания вывода."); }
    const approval = (store.getRun(String(instance.runId))!.stages as Array<{ id: string; status: string }>).find((stage) => stage.status === "waiting_approval")!;
    store.decideApproval(approval.id, false);
    assert.equal(store.preflightProcessPack(pack.id, input).preflight.scenarioVerified, false);
    assert.equal(store.listRunArtifacts(String(instance.runId)).length, 0);
    store.deleteKnowledgeCollection(installation.knowledgeCollectionIds[0]!);
    const missing = store.preflightProcessPack(pack.id, input).preflight;
    assert.equal(missing.queueable, false);
    assert.ok(missing.blockers.some((blocker) => blocker.code.startsWith("knowledge_missing:") && blocker.recovery.href.includes("field=requiredKnowledge")));
  } finally { store.close(); rmSync(directory, { recursive: true, force: true }); }
});

test("failed installation rolls back all resources; persisted repeat preserves local edits and rejects reconfiguration", () => {
  const directory = mkdtempSync(path.join(tmpdir(), "agat-pack-persist-"));
  const db = path.join(directory, "state.sqlite");
  let store = new AgatStore(db, { seedDemo: false });
  try {
    const conflict = store.createKnowledgeCollection({ name: pack.knowledge.name, embeddingModel: pack.defaults.embeddingModel });
    const agentsBefore = store.listAgents().length;
    assert.throws(() => store.installProcessPack(pack.id, input), /существует/);
    assert.equal(store.listAgents().length, agentsBefore);
    assert.equal(store.listProcesses().length, 0);
    assert.equal(store.getProcessPackInstallation(pack.id), null);
    store.deleteKnowledgeCollection(String(conflict.id));
    const installed = store.installProcessPack(pack.id, input).installation!;
    const process = store.getProcess(installed.processId)!;
    store.updateProcess(installed.processId, { name: "Мой внутренний отчёт", graph: process.draftGraph as ProcessGraph });
    store.close(); store = new AgatStore(db, { seedDemo: false });
    assert.deepEqual(store.installProcessPack(pack.id, input).installation, installed);
    assert.equal(store.getProcess(installed.processId)!.name, "Мой внутренний отчёт");
    assert.throws(() => store.installProcessPack(pack.id, { ...input, model: "other" }), /другой конфигурацией/);
    assert.equal(store.listProcesses().length, 1);
  } finally { store.close(); rmSync(directory, { recursive: true, force: true }); }
});
