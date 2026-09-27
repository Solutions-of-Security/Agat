import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { AgatStore } from "../src/database.js";
import type { ProcessGraph, WorkerRegistration } from "../src/types.js";
import type { DecisionShadowConfig } from "../src/local-decisions.js";
import { decisionProfileJson } from "../../../tests/fixtures/decision-shadow-profile.mjs";

const stores: AgatStore[] = [];
afterEach(() => { for (const store of stores.splice(0)) store.close(); });
function fixture(enabled = true) {
  const store = new AgatStore(":memory:", { seedDemo: false, decisionShadowEnabled: enabled });
  stores.push(store); store.updateModelRouterPolicy({ enabled: false });
  const agent = store.createAgent({ name: "Primary", role: "Research", systemPrompt: "Preserve primary", model: "primary-model" });
  return { store, agentId: String(agent.id) };
}
function shadow(kind: DecisionShadowConfig["kind"] = "boolean"): DecisionShadowConfig {
  return { mode: "shadow", profileJson: decisionProfileJson, timeoutMs: 1000, question: "Private question is not a readiness notice",
    kind, options: [false, true].map((value, i) => ({ id: `option-${i}`, description: `Description ${i}`, abstain: false,
      ...(kind === "boolean" ? { value } : kind === "score" ? { value: i * 1.5 } : {}) })) };
}
function graph(agentId: string, decisionShadow?: DecisionShadowConfig): ProcessGraph {
  return { nodes: [
    { id: "start", name: "Start", type: "start", position: { x: 0, y: 0 }, config: {} },
    { id: "work", name: "Исследование", type: "agent", position: { x: 200, y: 0 }, config: { agentId, decisionShadow } },
    { id: "end", name: "End", type: "end", position: { x: 400, y: 0 }, config: {} },
  ], edges: [{ id: "a", source: "start", target: "work", branch: "default" }, { id: "b", source: "work", target: "end", branch: "default" }] };
}
function published(store: AgatStore, value: ProcessGraph, name = "Shadow readiness") {
  const id = String(store.createProcess({ name, graph: value }).id); store.publishProcess(id); return id;
}
function worker(store: AgatStore, capability?: string, patch: Partial<WorkerRegistration> = {}) {
  return store.registerNode({ enrollmentToken: "unused", name: "worker", platform: "test", models: ["primary-model"],
    labels: capability ? { decisionShadow: capability } : {}, ...patch }).id;
}
function check(store: AgatStore, id: string, version = 1) { return store.preflightProcess(id, { version })!; }
function notices(store: AgatStore, id: string, version = 1) { return check(store, id, version).notices.join("\n"); }

test("disabled shadow is a read-only notice and cannot block or qualify the primary scenario", () => {
  const { store, agentId } = fixture(false);
  const id = published(store, graph(agentId, shadow()));
  const node = worker(store);
  const count = () => store.db.prepare("SELECT COUNT(*) AS count FROM events").get()!.count;
  const before = count();
  const result = check(store, id);
  assert.equal(result.queueable, true); assert.equal(result.runnableNow, true); assert.deepEqual(result.blockers, []);
  assert.match(result.notices.join("\n"), /локальная проверка выключена на сервере/);
  assert.equal(count(), before);
  assert.ok(!result.notices.join().includes("Private question") && !result.notices.join().includes("artifactSha256"));
  const instance = store.startProcess(id, { input: "Source", version: 1, startMode: "now" })!;
  const lease = store.leaseNext(node)!; assert.equal(lease.decisionShadow, undefined);
  store.completeLease(node, lease.leaseId, "PRIMARY OUTPUT");
  assert.equal(store.getRun(String(instance.runId))!.status, "completed");
  assert.equal((store.getRunTrace(String(instance.runId))!.decisionObservations as any[])[0].observation.reason, "disabled");
  assert.equal(check(store, id).scenarioVerified, true);
  assert.match(notices(store, id), /Успешный основной сценарий не подтверждает качество локальных проверок/);
});

test("readiness uses the same Choice/Boolean/Score capability contract as actual leases", () => {
  for (const kind of ["choice", "boolean", "score"] as const) {
    for (const [capability, expected] of [[undefined, false], ["local_decision_shadow_v1", kind !== "score"], ["local_decision_shadow_v2", true], ["unknown", false]] as const) {
      const { store, agentId } = fixture();
      const id = published(store, graph(agentId, shadow(kind)));
      const node = worker(store, capability);
      const result = check(store, id);
      assert.equal(result.runnableNow, true);
      assert.deepEqual(result.blockers, []);
      assert.match(result.notices.join("\n"), expected ? /Совместимый профиль локальной проверки заявлен у 1 из 1/ : /Ни один из 1 подходящих workers не заявил/);
      store.startProcess(id, { input: "Source", version: 1, startMode: "now" });
      assert.equal(Boolean(store.leaseNext(node)!.decisionShadow), expected, `${kind}/${capability}`);
    }
  }
});

test("partial capability is explicit; shadow does not steer dispatch away from the primary worker", () => {
  const { store, agentId } = fixture();
  const id = published(store, graph(agentId, shadow()));
  const plain = worker(store, undefined, { name: "A primary" });
  worker(store, "local_decision_shadow_v2", { name: "B capable" });
  assert.match(notices(store, id), /заявлен у 1 из 2/);
  assert.match(notices(store, id), /Шаг может попасть на worker без этой поддержки/);
  const run = store.startProcess(id, { input: "Source", version: 1, startMode: "now" })!;
  const lease = store.leaseNext(plain)!; assert.equal(lease.decisionShadow, undefined);
  store.completeLease(plain, lease.leaseId, "PRIMARY OUTPUT");
  assert.equal((store.getRunTrace(String(run.runId))!.decisionObservations as any[])[0].observation.reason, "unsupported_worker");
});

test("ineligible and stale capable workers cannot make shadow readiness look available", () => {
  const cases: Array<{ patch?: Partial<WorkerRegistration>; mutate?: (store: AgatStore, node: string) => void }> = [
    { patch: { models: ["other-model"] } },
    { patch: { agentRuntimes: ["langgraph"] } },
    { mutate: (store, node) => { store.db.prepare("UPDATE nodes SET credential_state = 'revoked' WHERE id = ?").run(node); } },
    { mutate: (store, node) => { store.db.prepare("UPDATE nodes SET last_seen = ? WHERE id = ?").run(new Date(Date.now() - 100_000).toISOString(), node); } },
    { mutate: (store, node) => { store.updateScheduler("auto"); store.heartbeatNode(node, { cpuPercent: 99 }); } },
  ];
  for (const item of cases) {
    const { store, agentId } = fixture();
    const id = published(store, graph(agentId, shadow()));
    worker(store, undefined, { name: "primary" });
    const capable = worker(store, "local_decision_shadow_v2", { name: "excluded", ...item.patch });
    item.mutate?.(store, capable);
    assert.equal(check(store, id).runnableNow, true);
    assert.match(notices(store, id), /Ни один из 1 подходящих workers не заявил/);
  }
});

test("model-router selection controls which capability is relevant without changing the primary route", () => {
  const { store, agentId } = fixture();
  const id = published(store, graph(agentId, shadow()));
  const plain = worker(store, undefined, { name: "A selected primary" });
  const capable = worker(store, "local_decision_shadow_v2", { name: "B other worker" });
  store.updateModelRouterPolicy({ enabled: true, allowUnknownProfiles: true });
  assert.equal(check(store, id).runnableNow, true);
  assert.match(notices(store, id), /Ни один из 1 подходящих workers не заявил/);
  store.startProcess(id, { input: "Source", version: 1, startMode: "now" });
  assert.equal(store.leaseNext(capable), null);
  const lease = store.leaseNext(plain)!; assert.equal(lease.decisionShadow, undefined);
});

test("no free worker remains queueable; shadow adds advice, never a new blocker", () => {
  const { store, agentId } = fixture();
  const id = published(store, graph(agentId, shadow()));
  const node = worker(store, "local_decision_shadow_v2");
  store.startProcess(id, { input: "Source", version: 1, startMode: "now" });
  const lease = store.leaseNext(node)!;
  const result = check(store, id);
  assert.equal(result.queueable, true); assert.equal(result.runnableNow, false);
  assert.match(result.notices.join("\n"), /Сейчас нет подходящих свободных workers/);
  assert.ok(result.blockers.every(blocker => !blocker.code.startsWith("shadow")));
  store.completeLease(node, lease.leaseId, "PRIMARY OUTPUT");
  assert.match(notices(store, id), /заявлен у 1 из 1/);
  assert.match(notices(store, id), /Доступность модели и точное совпадение профиля проверяются при вызове/);
});

test("notices follow the published pin, include nested steps and do not leak across projects", () => {
  const { store, agentId } = fixture();
  const id = published(store, graph(agentId));
  worker(store, "local_decision_shadow_v1");
  assert.ok(!notices(store, id).includes("локальн"));
  store.updateProcess(id, { name: "Shadow readiness", graph: graph(agentId, shadow("score")) });
  store.publishProcess(id);
  assert.ok(!notices(store, id, 1).includes("локальн"));
  assert.match(notices(store, id, 2), /Ни один из 1 подходящих workers не заявил/);
  store.updateProcess(id, { name: "Shadow readiness", graph: graph(agentId, shadow()) });
  assert.match(notices(store, id, 2), /Ни один из 1 подходящих workers не заявил/);
  store.publishProcess(id);
  assert.match(notices(store, id, 3), /заявлен у 1 из 1/);
  const parent = graph(agentId);
  parent.nodes[1] = { ...parent.nodes[1]!, type: "subprocess", config: { subprocessProcessId: id, subprocessVersion: 2 } };
  const parentId = published(store, parent, "Parent");
  assert.match(notices(store, parentId), /Ни один из 1 подходящих workers не заявил/);
  store.createProject({ id: "other", name: "Other" });
  assert.equal(store.preflightProcess(id, { version: 3 }, "other"), null);
});
