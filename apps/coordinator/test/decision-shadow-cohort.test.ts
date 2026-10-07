import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { afterEach, test } from "node:test";
import { AgatStore } from "../src/database.js";
import { loadConfig } from "../src/config.js";
import { createCoordinatorServer } from "../src/server.js";
import { COHORT_LIMITS, cohortScope } from "../src/decision-shadow-cohort.js";
import type { ProcessGraph } from "../src/types.js";
import { decisionProfileJson } from "../../../tests/fixtures/decision-shadow-profile.mjs";

const scope = { processVersion: 1, startAt: "2026-09-29T00:00:00.000Z", endAt: "2026-09-30T00:00:00.000Z" };
const stores: AgatStore[] = [];
afterEach(() => { for (const store of stores.splice(0)) store.close(); });
function fixture() {
  const store = new AgatStore(":memory:", { seedDemo: false, decisionShadowEnabled: true }); stores.push(store);
  store.updateModelRouterPolicy({ enabled: false });
  const agent = store.createAgent({ name: "Cohort fixture", role: "Test", systemPrompt: "PRIVATE_PROMPT", model: "cohort-primary" });
  const graph: ProcessGraph = { nodes: [
    { id: "start", name: "Start", type: "start", position: { x: 0, y: 0 }, config: {} },
    { id: "work", name: "Agent", type: "agent", position: { x: 200, y: 0 }, config: { agentId: String(agent.id),
      decisionShadow: { mode: "shadow", profileJson: decisionProfileJson, timeoutMs: 1000, kind: "boolean", question: "PRIVATE_QUESTION",
        options: [{ id: "no", description: "PRIVATE_OPTION_FALSE", value: false }, { id: "yes", description: "PRIVATE_OPTION_TRUE", value: true }] } } },
    { id: "end", name: "End", type: "end", position: { x: 400, y: 0 }, config: {} },
  ], edges: [{ id: "a", source: "start", target: "work", branch: "default" }, { id: "b", source: "work", target: "end", branch: "default" }] };
  const processId = String(store.createProcess({ name: "Cohort", graph }).id); store.publishProcess(processId);
  function instance(createdAt = scope.startAt, status = "queued", version = 1, id = processId) {
    const row = store.startProcess(id, { version, input: "PRIVATE_INPUT" })!;
    store.db.prepare("UPDATE process_instances SET created_at = ?, status = ? WHERE id = ?").run(createdAt, status, String(row.id));
    return row;
  }
  return { store, processId, graph, instance };
}

test("cohort includes failed, cancelled, pending and replay within the exact process/version half-open window", () => {
  const f = fixture();
  const included = [f.instance(scope.startAt, "failed"), f.instance("2026-09-29T00:00:00.001Z", "cancelled"),
    f.instance("2026-09-29T23:59:59.999Z", "running")];
  f.store.db.prepare("UPDATE process_instances SET replay_of_instance_id = ?, replay_mode = 'safe' WHERE id = ?")
    .run(String(included[0]!.id), String(included[1]!.id));
  f.instance("2026-09-28T23:59:59.999Z"); f.instance(scope.endAt);
  f.store.updateProcess(f.processId, { name: "Cohort v2", description: "Second fixture version", graph: f.graph });
  f.store.publishProcess(f.processId); f.instance(scope.startAt, "queued", 2);
  const other = String(f.store.createProcess({ name: "Other", graph: f.graph }).id); f.store.publishProcess(other);
  f.instance(scope.startAt, "queued", 1, other);
  const events = f.store.db.prepare("SELECT COUNT(*) AS count FROM events").get()!.count;
  const result = f.store.getDecisionShadowCohort(f.processId, scope)!;
  assert.equal(result.counts.instances, 3); assert.equal(result.counts.runs, 3);
  assert.deepEqual(result.instances.map(i => i.instanceId), included.map(i => String(i.id)));
  assert.deepEqual(result.instances.map(i => i.status), ["failed", "cancelled", "running"]);
  assert.equal(result.instances[1]!.replayOfInstanceId, included[0]!.id);
  assert.equal(result.runIdsSha256, createHash("sha256").update(JSON.stringify(included.map(i => i.runId))).digest("hex"));
  assert.equal(result.snapshot.storedCohortComplete, true); assert.equal(result.snapshot.truncated, false);
  assert.equal(result.populationCoverageVerified, false); assert.equal(result.sloAccepted, false);
  assert.equal(result.routingEnabled, false); assert.equal(result.qualification, "not_assessed");
  assert.equal(f.store.db.prepare("SELECT COUNT(*) AS count FROM events").get()!.count, events);
  f.store.createProject({ id: "cohort-other", name: "Other" });
  assert.equal(f.store.getDecisionShadowCohort(f.processId, scope, "cohort-other"), null);
  assert.equal(f.store.getDecisionShadowCohort(f.processId, { ...scope, processVersion: 999 }), null);
});

test("cohort preserves the run trace ledger and omits task/primary text while keeping pending assignments", () => {
  const f = fixture(); const instance = f.instance();
  const worker = f.store.registerNode({ enrollmentToken: "fixture", name: "Cohort worker", platform: "test", models: ["cohort-primary"],
    labels: { decisionShadow: "local_decision_shadow_v1" } });
  const lease = f.store.leaseNext(worker.id)!; assert.ok(lease.decisionShadow);
  const before = f.store.getRunTrace(String(instance.runId))!;
  const cohort = f.store.getDecisionShadowCohort(f.processId, scope)!; const trace = cohort.traces[0]!;
  for (const key of ["decisionCallerAccounting", "decisionAssignmentHistory", "decisionStageInventory"] as const) assert.deepEqual(trace[key], before[key]);
  assert.equal(trace.decisionAssignmentHistory.stages[0]!.assignments[0]!.outcome, "pending");
  const serialize = JSON.stringify(cohort);
  for (const privateValue of ["PRIVATE_INPUT", "PRIVATE_PROMPT", "PRIVATE_QUESTION", "PRIVATE_OPTION_FALSE", "PRIVATE_OPTION_TRUE"]) assert.ok(!serialize.includes(privateValue));
  f.store.completeLease(worker.id, lease.leaseId, "PRIVATE_PRIMARY_OUTPUT");
  const finished = f.store.getDecisionShadowCohort(f.processId, scope)!;
  assert.ok(!JSON.stringify(finished).includes("PRIVATE_PRIMARY_OUTPUT"));
  assert.equal(finished.traces[0]!.decisionAssignmentHistory.stages[0]!.assignments[0]!.outcome, "recorded");
  assert.equal((finished.traces[0]!.decisionObservations[0]!.observation as { reason: string }).reason, "missing_result");
});

test("invalid stored activity aborts the whole export and releases the read transaction", () => {
  const f = fixture(); const instance = f.instance();
  const stage = f.store.db.prepare("SELECT id, activity_json FROM stages WHERE run_id = ?").get(String(instance.runId))!;
  f.store.db.prepare("UPDATE stages SET activity_json = '{broken' WHERE id = ?").run(String(stage.id));
  assert.throws(() => f.store.getDecisionShadowCohort(f.processId, scope), SyntaxError);
  f.store.db.prepare("UPDATE stages SET activity_json = ? WHERE id = ?").run(String(stage.activity_json), String(stage.id));
  assert.equal(f.store.getDecisionShadowCohort(f.processId, scope)!.counts.instances, 1);
});

test("overflow fails rather than quietly producing a partial cohort", () => {
  const f = fixture(); const source = f.instance();
  const run = f.store.db.prepare(`INSERT INTO runs(id, name, input, status, execution_mode, project_id, created_at, updated_at)
    SELECT ?, name, input, status, execution_mode, project_id, created_at, updated_at FROM runs WHERE id = ?`);
  const instance = f.store.db.prepare(`INSERT INTO process_instances(id, process_id, process_version, run_id, graph_json, created_at, updated_at)
    SELECT ?, process_id, process_version, ?, graph_json, created_at, updated_at FROM process_instances WHERE id = ?`);
  for (let i = 0; i < COHORT_LIMITS.instances; i++) {
    const runId = randomUUID(); run.run(runId, String(source.runId)); instance.run(randomUUID(), runId, String(source.id));
  }
  assert.throws(() => f.store.getDecisionShadowCohort(f.processId, scope), /instance limit.*no partial/);
  assert.equal(f.store.getDecisionShadowCohort(f.processId, { ...scope, startAt: "2026-09-30T00:00:00.000Z", endAt: "2026-10-01T00:00:00.000Z" })!.counts.instances, 0);
});

test("byte overflow and unsupported observation payloads abort without leaking partial data", () => {
  const f = fixture(); const instance = f.instance();
  const stage = f.store.db.prepare("SELECT id, activity_json FROM stages WHERE run_id = ?").get(String(instance.runId))!;
  for (const activity of [JSON.stringify({ padding: "x".repeat(COHORT_LIMITS.activityBytes) }),
    JSON.stringify({ decisionShadowObservation: { mode: "shadow", fallback: "primary", status: "unavailable", reason: "timeout", state: "PRIVATE_INPUT" } })]) {
    f.store.db.prepare("UPDATE stages SET activity_json = ? WHERE id = ?").run(activity, String(stage.id));
    assert.throws(() => f.store.getDecisionShadowCohort(f.processId, scope), /byte limit|observation fields/);
  }
  f.store.db.prepare("UPDATE stages SET activity_json = ? WHERE id = ?").run(String(stage.activity_json), String(stage.id));
  assert.equal(f.store.getDecisionShadowCohort(f.processId, scope)!.counts.instances, 1);
});

test("cohort scope rejects aliases, future windows, invalid UTC and extra status filters", () => {
  for (const patch of [{ processVersion: true }, { processVersion: "1" }, { processVersion: 0 }, { processVersion: 2**53 },
    { startAt: "2026-09-29T00:00:00Z" }, { startAt: "2026-02-30T00:00:00.000Z" },
    { endAt: scope.startAt }, { endAt: "2026-10-07T00:00:00.001Z" }, { status: "completed" }]) {
    assert.throws(() => cohortScope({ ...scope, ...patch }, "2026-10-08T00:00:00.000Z"));
  }
  assert.throws(() => cohortScope(scope, scope.startAt), /completed/);
});

test("cohort HTTP export requires authenticated trace access and rejects duplicate/additional query fields", async () => {
  const f = fixture(); f.instance();
  const server = createCoordinatorServer({ ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false,
    adminToken: "cohort-http-test-only", oidcEnabled: false, mcpEnabled: false, a2aEnabled: false, localWorkerLauncherEnabled: false }, f.store);
  try {
    await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
    const address = server.address(); assert.ok(address && typeof address === "object");
    const base = `http://127.0.0.1:${address.port}/api/v1/processes/${f.processId}/decision-shadow-cohort?`;
    const query = new URLSearchParams({ processVersion: "1", startAt: scope.startAt, endAt: scope.endAt }).toString();
    const headers = { "x-agat-admin-token": "cohort-http-test-only" };
    assert.equal((await fetch(base + query)).status, 401);
    for (const suffix of ["&status=completed", "&startAt=" + encodeURIComponent(scope.startAt), "&projectId=default"]) {
      assert.equal((await fetch(base + query + suffix, { headers })).status, 400);
    }
    const response = await fetch(base + query, { headers }); assert.equal(response.status, 200);
    const body = await response.json() as { counts: { instances: number }; routingEnabled: boolean };
    assert.equal(body.counts.instances, 1); assert.equal(body.routingEnabled, false);
    f.store.createProject({ id: "cohort-other", name: "Other" });
    assert.equal((await fetch(base + query, { headers: { ...headers, "x-agat-project-id": "cohort-other" } })).status, 404);
  } finally {
    server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve()));
  }
});
