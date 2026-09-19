import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import { AgatStore } from "../src/database.js";
import { normalizeCatalogTools } from "../src/mcp.js";
import { ScenarioPreflightError, type ScenarioPreflightContext } from "../src/scenario-preflight.js";
import type { ProcessGraph, WorkerRegistration } from "../src/types.js";

const stores: AgatStore[] = [];
afterEach(() => { mock.timers.reset(); for (const store of stores.splice(0)) store.close(); });
function fixture() {
  const store = new AgatStore(":memory:", { seedDemo: false, credentialsKey: "scenario-test-only" });
  store.updateModelRouterPolicy({ enabled: false });
  stores.push(store);
  return store;
}
function graph(agentId = "collector"): ProcessGraph {
  return {
    nodes: [
      { id: "start", name: "Start", type: "start", position: { x: 0, y: 0 }, config: {} },
      { id: "work", name: "Research", type: "agent", position: { x: 200, y: 0 }, config: { agentId } },
      { id: "end", name: "End", type: "end", position: { x: 400, y: 0 }, config: {} },
    ],
    edges: [{ id: "first", source: "start", target: "work", branch: "default" }, { id: "last", source: "work", target: "end", branch: "default" }],
  };
}
function published(store: AgatStore, value = graph()) {
  const process = store.createProcess({ name: "Scenario", graph: value });
  store.publishProcess(String(process.id));
  return String(process.id);
}
function worker(store: AgatStore, patch: Partial<WorkerRegistration> = {}) {
  return store.registerNode({ enrollmentToken: "unused", name: "scenario-worker", platform: "test", models: ["test-model"], ...patch }).id;
}
function check(store: AgatStore, id: string, patch: Parameters<AgatStore["preflightProcess"]>[1] = {}) {
  return store.preflightProcess(id, { version: 1, ...patch })!;
}
const context: ScenarioPreflightContext = { runtime: { mode: "database", connected: true }, mcpEnabled: true };

test("template preview, saved draft, queueable, runnable and verified are distinct; blockers have exact recovery targets", () => {
  const store = fixture();
  const preview = store.preflightTemplate("research-to-report", { catalogTemplateVersion: 1 });
  assert.equal(preview.saved, false);
  assert.equal(preview.queueable, false);
  assert.ok(preview.blockers.some((blocker) => blocker.category === "team" && blocker.recovery.href.includes("nodeId=")));
  assert.equal(store.listProcesses().length, 0);
  assert.ok(store.preflightTemplate("research-to-report", { catalogTemplateVersion: 999 }).blockers.some((item) => item.code === "catalog_version"));
  const draft = store.createProcess({ name: "Scenario", graph: graph() });
  const id = String(draft.id);
  assert.equal(check(store, id).saved, true);
  assert.equal(check(store, id).queueable, false);
  store.publishProcess(id);
  const queued = check(store, id);
  assert.equal(queued.queueable, true);
  assert.equal(queued.runnableNow, false);
  assert.equal(queued.scenarioVerified, false);
  assert.ok(queued.blockers.every((blocker) => blocker.message && blocker.recovery.label && blocker.recovery.href.startsWith("#")));
  assert.throws(() => store.startProcess(id, { input: "test", version: 1, startMode: "now" }), ScenarioPreflightError);
  const instance = store.startProcess(id, { input: "test", version: 1, startMode: "queue", resultDestination: "history" })!;
  const node = worker(store);
  assert.equal(check(store, id).runnableNow, true);
  assert.equal(check(store, id).scenarioVerified, false);
  const lease = store.leaseNext(node)!;
  store.completeLease(node, lease.leaseId, "Scenario output");
  const verified = check(store, id);
  assert.equal(verified.scenarioVerified, true);
  assert.equal(verified.verification?.runId, instance.runId);
  assert.equal(verified.verification?.href, `#runs/${instance.runId}`);
});

test("start uses the reviewed version even after another publication and rejects missing or invalid pins", () => {
  const store = fixture();
  const id = published(store);
  for (const version of [undefined, 0, -1, 1.5, 999, "1" as unknown as number]) {
    assert.equal(store.preflightProcess(id, { version })!.queueable, false);
  }
  store.updateProcess(id, { name: "Scenario", graph: graph("editor") });
  store.publishProcess(id);
  const instance = store.startProcess(id, { input: "test", version: 1 })!;
  assert.equal(instance.processVersion, 1);
  const node = worker(store);
  assert.equal(store.leaseNext(node)?.agent.id, "collector");
  assert.throws(() => store.startProcess(id, { input: "test", version: 999 }), ScenarioPreflightError);
});

test("runtime profile and all specialist models must be available on the same worker", () => {
  const store = fixture();
  const specialists = ["research", "review"].map((name) => store.createAgent({ name, role: name, systemPrompt: name, model: name }));
  const team = store.createAgent({ name: "Team", role: "Team", systemPrompt: "Supervise", model: "supervisor", runtime: "langgraph",
    runtimeConfig: { profile: "specialist_team_v1", maxIterations: 3, maxHandoffs: 3, stateSchema: "specialist_team_state_v1", specialistAgentIds: specialists.map((item) => String(item.id)) } });
  const id = published(store, graph(String(team.id)));
  const node = worker(store, { models: ["supervisor", "research"] });
  assert.ok(check(store, id).blockers.some((item) => item.category === "runtime"));
  store.heartbeatNode(node, {}, { models: ["supervisor", "research"], agentRuntimes: ["langgraph"], agentRuntimeProfiles: ["specialist_team_v1"] });
  const modelBlocker = check(store, id).blockers.find((item) => item.category === "model")!;
  assert.ok(modelBlocker);
  assert.equal(new URLSearchParams(modelBlocker.recovery.href.split("?")[1]).get("model"), "review");
  assert.equal(new URLSearchParams(modelBlocker.recovery.href.split("?")[1]).get("nodeId"), node);
  worker(store, { name: "separate-review-worker", models: ["review"], agentRuntimes: ["langgraph"], agentRuntimeProfiles: ["specialist_team_v1"] });
  assert.equal(check(store, id).runnableNow, false);
  store.heartbeatNode(node, {}, { models: ["supervisor", "research", "review"], agentRuntimes: ["langgraph"], agentRuntimeProfiles: ["specialist_team_v1"] });
  assert.equal(check(store, id).runnableNow, true);
  store.createProject({ id: "other", name: "Other" });
  assert.equal(store.preflightProcess(id, { version: 1 }, "other"), null);
});

test("selected knowledge waits for complete embeddings and retrieval support on the agent worker", () => {
  const store = fixture();
  const id = published(store);
  const node = worker(store, { models: ["agent-model"] });
  const collection = store.createKnowledgeCollection({ name: "Evidence", embeddingModel: "embeddinggemma" });
  const ids = [String(collection.id)];
  assert.equal(check(store, id).runnableNow, true, "unselected empty collections do not block");
  assert.ok(check(store, id, { knowledgeCollectionIds: ids }).blockers.some((item) => item.code.startsWith("knowledge_empty")));
  store.ingestKnowledgeDocument(ids[0]!, { name: "Facts", content: "Verified source facts for a scenario." });
  assert.ok(check(store, id, { knowledgeCollectionIds: ids }).blockers.some((item) => item.code.startsWith("knowledge_embeddings")));
  store.startProcess(id, { input: "test", knowledgeCollectionIds: ids, startMode: "queue" });
  assert.equal(store.leaseNext(node), null, "queue does not run with an incomplete source");
  const embedder = worker(store, { name: "embedder", models: ["embed-only"], agentRuntimes: ["langgraph"], embeddingModels: ["embeddinggemma"] });
  const embedding = store.leaseKnowledgeEmbedding(embedder)!;
  store.completeKnowledgeEmbedding(embedder, embedding.leaseId, embedding.chunks.map((chunk) => ({ chunkId: chunk.id, embedding: [1, 0] })));
  assert.ok(check(store, id, { knowledgeCollectionIds: ids }).blockers.some((item) => item.code.startsWith("embedding_worker")));
  store.heartbeatNode(node, {}, { models: ["agent-model"], embeddingModels: ["embeddinggemma"] });
  assert.equal(check(store, id, { knowledgeCollectionIds: ids }).runnableNow, true);
  assert.ok(store.leaseNext(node));
  assert.equal(check(store, id, { knowledgeCollectionIds: ["absent"] }).queueable, false);
  store.createProject({ id: "other", name: "Other" });
  const foreign = store.createKnowledgeCollection({ name: "Private", embeddingModel: "embeddinggemma" }, "other");
  assert.equal(check(store, id, { knowledgeCollectionIds: [String(foreign.id)] }).queueable, false);
});

test("required tool catalog, gateway, policies and credential scopes are checked without exposing secrets", () => {
  const store = fixture();
  const id = published(store, { ...graph(), requiredTools: ["crm__read"] });
  const node = worker(store);
  assert.ok(check(store, id).blockers.some((item) => item.code === "tool_missing:crm__read"));
  const credential = store.createCredential({ name: "CRM", type: "api_key", data: { apiKey: "never-return-this-secret" }, scope: { kind: "mcp", serverNamespaces: ["crm"], toolPatterns: ["different"], risks: ["read"], allowCatalog: true, expiresAt: null } });
  const server = store.createMcpServer({ name: "CRM", namespace: "crm", endpoint: "https://crm.invalid/mcp", enabled: true, trustAnnotations: true, defaultPolicy: "auto", credentialId: String(credential.id) });
  store.saveMcpCatalog(String(server.id), normalizeCatalogTools("crm", [{ name: "read", inputSchema: { type: "object" }, annotations: { readOnlyHint: true } }]), 60_000, "private");
  const scope = check(store, id);
  assert.ok(scope.blockers.some((item) => item.category === "scopes" && item.recovery.href.includes(`credentials=${credential.id}`)));
  assert.equal(JSON.stringify(scope).includes("never-return-this-secret"), false);
  store.updateCredential(String(credential.id), { name: "CRM", type: "api_key", data: { apiKey: "never-return-this-secret" }, scope: { kind: "mcp", serverNamespaces: ["crm"], toolPatterns: ["read"], risks: ["read"], allowCatalog: true, expiresAt: null } });
  assert.equal(check(store, id).runnableNow, true);
  assert.equal(store.preflightProcess(id, { version: 1 }, "default", { ...context, mcpEnabled: false })!.queueable, false);
  store.startProcess(id, { input: "test", version: 1, startMode: "now" });
  store.setMcpEmergencyDeny(true, "Test emergency stop", { subject: "test-admin", display: "Test" });
  assert.ok(check(store, id).blockers.some((item) => item.code === "tool_denied:crm__read"));
  assert.equal(store.leaseNext(node), null, "policy is rechecked after preflight and before leasing");
});

test("availability is rechecked on start; busy workers remain queueable", () => {
  const store = fixture();
  const id = published(store);
  const node = worker(store);
  assert.equal(check(store, id).runnableNow, true);
  store.updateScheduler("auto");
  store.heartbeatNode(node, { cpuPercent: 99 });
  assert.throws(() => store.startProcess(id, { input: "test", startMode: "now", version: 1 }), ScenarioPreflightError);
  assert.equal(check(store, id).queueable, true);
  store.heartbeatNode(node, { cpuPercent: 0 });
  store.startProcess(id, { input: "test", startMode: "now", version: 1 });
  assert.ok(store.leaseNext(node));
  assert.equal(check(store, id).runnableNow, false);
  assert.equal(check(store, id).queueable, true);
});

test("required isolated tools wait for sandbox availability and container network enforcement", () => {
  for (const transport of ["wasi", "container"] as const) {
    const store = fixture();
    worker(store);
    const namespace = transport;
    const tool = { name: "read", inputSchema: { type: "object" }, annotations: { readOnlyHint: true } };
    const server = store.createMcpServer({ name: transport, namespace, transport, enabled: true, trustAnnotations: true, defaultPolicy: "auto",
      sandbox: transport === "wasi" ? { tool, moduleBase64: Buffer.from([0, 97, 115, 109, 1, 0, 0, 0]).toString("base64") }
        : { tool, image: `example/tool@sha256:${"a".repeat(64)}`, command: ["/tool"] } });
    const id = published(store, { ...graph(), requiredTools: [`${namespace}__read`] });
    const unavailable = store.preflightProcess(id, { version: 1 }, "default", { ...context, sandbox: null })!;
    assert.equal(unavailable.queueable, true);
    assert.equal(unavailable.runnableNow, false);
    assert.ok(unavailable.blockers.some((item) => item.code === `tool_sandbox:${namespace}__read` && item.recovery.href.includes(`serverId=${server.id}`)));
    const withoutEnforcement = store.preflightProcess(id, { version: 1 }, "default", { ...context, sandbox: { available: true, networkPolicyEnforced: false } })!;
    assert.equal(withoutEnforcement.runnableNow, transport === "wasi");
    assert.equal(store.preflightProcess(id, { version: 1 }, "default", { ...context, sandbox: { available: true, networkPolicyEnforced: true } })!.runnableNow, true);
  }
});

test("trigger readiness distinguishes manual, disabled runtime, missing/paused schedule and project-owned start webhook", () => {
  const store = fixture();
  const id = published(store);
  worker(store);
  assert.equal(check(store, id).runnableNow, true);
  const disconnected = store.preflightProcess(id, { version: 1 }, "default", { ...context, runtime: { mode: "temporal", connected: false } })!;
  assert.equal(disconnected.queueable, false);
  assert.equal(disconnected.blockers.find((item) => item.code === "runtime_disconnected")?.recovery.href, "#fleet?section=runtime");
  assert.ok(check(store, id, { trigger: { kind: "schedule" } }).blockers.some((item) => item.code === "trigger_runtime"));
  for (const schedule of [null, { paused: true, nextActionTimes: ["2030-01-01"] }, { paused: false, nextActionTimes: [] }]) {
    assert.equal(store.preflightProcess(id, { version: 1, trigger: { kind: "schedule" } }, "default", { ...context, runtime: { mode: "temporal", connected: true }, schedule })!.queueable, false);
  }
  assert.equal(store.preflightProcess(id, { version: 1, trigger: { kind: "schedule" } }, "default", { ...context, runtime: { mode: "temporal", connected: true }, schedule: { paused: false, nextActionTimes: ["2030-01-01"] } })!.runnableNow, true);
  assert.equal(check(store, id, { trigger: { kind: "webhook", webhookId: "missing" } }).queueable, false);
  const webhook = store.createProcessWebhook(id, { name: "Start", kind: "start" });
  assert.equal(check(store, id, { trigger: { kind: "webhook", webhookId: String(webhook.id) } }).runnableNow, true);
  assert.throws(() => check(store, id, { trigger: { kind: "invalid" } as never }), /Trigger|trigger|manual/);
});

test("verification requires a full completed scenario, survives a worker outage and is invalidated by agent or version changes", () => {
  const store = fixture();
  const agent = store.createAgent({ name: "Researcher", role: "Research", systemPrompt: "Research", model: null });
  const id = published(store, graph(String(agent.id)));
  const node = worker(store);
  store.startProcess(id, { input: "partial test", version: 1, startNodeId: "work", resultDestination: "history" });
  let lease = store.leaseNext(node)!;
  store.completeLease(node, lease.leaseId, "partial");
  assert.equal(check(store, id).scenarioVerified, false);
  store.startProcess(id, { input: "full test", version: 1, resultDestination: "history" });
  lease = store.leaseNext(node)!;
  store.completeLease(node, lease.leaseId, "full");
  assert.equal(check(store, id).scenarioVerified, true);
  store.updateScheduler("auto");
  store.heartbeatNode(node, { cpuPercent: 99 });
  assert.equal(check(store, id).scenarioVerified, true);
  assert.equal(check(store, id).runnableNow, false);
  store.updateAgent(String(agent.id), { name: "Researcher", role: "Changed role", systemPrompt: "Research", model: null });
  assert.equal(check(store, id).scenarioVerified, false);
  store.updateProcess(id, { name: "Scenario", graph: graph("editor") });
  store.publishProcess(id);
  assert.equal(check(store, id, { version: 2 }).scenarioVerified, false);
});

test("trigger preflight checks the knowledge actually configured for scheduled and webhook starts", () => {
  const store = fixture();
  const id = published(store);
  worker(store);
  const collection = store.createKnowledgeCollection({ name: "Scheduled evidence", embeddingModel: "embeddinggemma" });
  const ids = [String(collection.id)];
  const scheduleContext: ScenarioPreflightContext = { ...context, runtime: { mode: "temporal", connected: true },
    schedule: { paused: false, nextActionTimes: ["2030-01-01"], knowledgeCollectionIds: ids } };
  const mismatched = store.preflightProcess(id, { version: 1, trigger: { kind: "schedule" } }, "default", scheduleContext)!;
  assert.equal(mismatched.queueable, false);
  assert.ok(mismatched.blockers.some((item) => item.code === "trigger_knowledge"));
  assert.ok(mismatched.blockers.some((item) => item.code === `knowledge_empty:${collection.id}`), "actual schedule source is checked even when omitted from the UI selection");
  const matched = store.preflightProcess(id, { version: 1, trigger: { kind: "schedule" }, knowledgeCollectionIds: ids }, "default", scheduleContext)!;
  assert.equal(matched.queueable, true);
  assert.equal(matched.runnableNow, false);
  const webhook = store.createProcessWebhook(id, { name: "Start", kind: "start" });
  assert.ok(check(store, id, { trigger: { kind: "webhook", webhookId: String(webhook.id) }, knowledgeCollectionIds: ids }).blockers.some((item) => item.code === "trigger_knowledge"));
});

test("required tools survive save, publication, project-template copy and schema validation", () => {
  const store = fixture();
  const process = store.createProcess({ name: "Template", graph: { ...graph(), requiredTools: ["crm__read", "crm__read"] }, isTemplate: true });
  store.publishProcess(String(process.id));
  const copy = store.createProcess({ name: "Copy", templateId: String(process.id) });
  assert.deepEqual((copy.draftGraph as ProcessGraph).requiredTools, ["crm__read"]);
  store.updateProcess(String(process.id), { name: "Template", graph: { ...graph(), requiredTools: ["crm__write"] } });
  assert.equal(store.diffProcessVersions(String(process.id), 1, "draft")!.summary.metadataChanged, true);
  const exported = store.exportProcessBpmn(String(process.id), 1)!;
  const imported = store.importProcessBpmn(exported, { name: "Imported" });
  assert.deepEqual((imported.draftGraph as ProcessGraph).requiredTools, ["crm__read"]);
  assert.throws(() => store.updateProcess(String(copy.id), { name: "Copy", graph: { ...graph(), requiredTools: ["not a public name"] } }), /requiredTools/);
});

test("expired scopes are precise blockers, not exceptions; an unrelated expired credential does not block a scenario", () => {
  const now = Date.now();
  mock.timers.enable({ apis: ["Date"], now });
  const store = fixture();
  const id = published(store, { ...graph(), requiredTools: ["crm__read"] });
  worker(store);
  const credential = store.createCredential({ name: "Expiring", type: "api_key", data: { apiKey: "private-test-value" },
    scope: { kind: "mcp", serverNamespaces: ["crm"], toolPatterns: ["read"], risks: ["read"], allowCatalog: true, expiresAt: new Date(now + 1_000).toISOString() } });
  const server = store.createMcpServer({ name: "CRM", namespace: "crm", endpoint: "https://crm.invalid/mcp", enabled: true, trustAnnotations: true, defaultPolicy: "auto", credentialId: String(credential.id) });
  store.saveMcpCatalog(String(server.id), normalizeCatalogTools("crm", [{ name: "read", inputSchema: { type: "object" }, annotations: { readOnlyHint: true } }]), 60_000, "private");
  assert.equal(check(store, id).runnableNow, true);
  mock.timers.tick(1_001);
  const expired = check(store, id);
  assert.ok(expired.blockers.some((item) => item.category === "scopes" && item.message.includes("истёк")));
  assert.doesNotThrow(() => store.getOverview(), "expired credentials remain visible for recovery");
  store.updateProcess(id, { name: "Scenario", graph: graph() });
  store.publishProcess(id);
  assert.equal(check(store, id, { version: 2 }).runnableNow, true);
  store.updateCredential(String(credential.id), { name: "Expiring", type: "api_key", data: { apiKey: "renewed-test-value" },
    scope: { kind: "mcp", serverNamespaces: ["crm"], toolPatterns: ["read"], risks: ["read"], allowCatalog: true, expiresAt: new Date(now + 60_000).toISOString() } });
  assert.equal(check(store, id).runnableNow, true, "renewed credentials recover the pinned scenario");
});

test("human rejection and unfinished approval cannot verify a scenario; completed approval can", () => {
  const store = fixture();
  const value = graph();
  value.nodes.splice(2, 0, { id: "approval", type: "approval", name: "Review", position: { x: 300, y: 0 }, config: {} });
  value.edges[1]!.target = "approval";
  value.edges.push({ id: "approved", source: "approval", target: "end", branch: "default" });
  const id = published(store, value);
  const node = worker(store);
  for (const accepted of [false, true]) {
    store.startProcess(id, { input: "Scenario evidence", version: 1, resultDestination: "history" });
    const lease = store.leaseNext(node)!;
    store.completeLease(node, lease.leaseId, "Evidence");
    assert.equal(check(store, id).scenarioVerified, false);
    const approval = (store.getOverview().approvals as Array<{ stageId: string }>)[0]!;
    store.decideApproval(approval.stageId, accepted);
    assert.equal(check(store, id).scenarioVerified, accepted);
  }
});

test("verification is bound to the actual trigger; latest-version triggers reject older pins", () => {
  const store = fixture();
  const id = published(store);
  const node = worker(store);
  const webhook = store.createProcessWebhook(id, { name: "Scenario trigger", kind: "start" });
  store.invokeProcessWebhook(String(webhook.id), String(webhook.token), { input: "Triggered scenario" }, "once");
  let lease = store.leaseNext(node)!;
  store.completeLease(node, lease.leaseId, "Webhook result");
  assert.equal(check(store, id).scenarioVerified, false, "webhook evidence cannot masquerade as manual evidence");
  const trigger = { kind: "webhook" as const, webhookId: String(webhook.id) };
  assert.equal(check(store, id, { trigger }).scenarioVerified, true);
  const scheduledContext: ScenarioPreflightContext = { ...context, runtime: { mode: "temporal", connected: true },
    schedule: { paused: false, nextActionTimes: ["2030-01-01"] } };
  store.startProcess(id, { input: "Scheduled scenario", version: 1, resultDestination: "history" }, "default", { ...scheduledContext, executionTrigger: { kind: "schedule" } });
  lease = store.leaseNext(node)!;
  store.completeLease(node, lease.leaseId, "Scheduled result");
  assert.equal(check(store, id).scenarioVerified, false);
  assert.equal(store.preflightProcess(id, { version: 1, trigger: { kind: "schedule" } }, "default", scheduledContext)!.scenarioVerified, true);
  store.updateProcess(id, { name: "Scenario", graph: graph("editor") });
  store.publishProcess(id);
  assert.ok(check(store, id, { trigger }).blockers.some((item) => item.code === "trigger_version"));
});
