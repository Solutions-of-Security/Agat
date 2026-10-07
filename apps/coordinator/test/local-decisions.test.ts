import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { InMemorySpanExporter } from "@opentelemetry/sdk-trace-node";

import { AgatStore } from "../src/database.js";
import { createDecisionShadowLease, decisionSha256, normalizeDecisionShadowConfig, validateDecisionShadowResult,
  SCORE_FINGERPRINT_VERSION,
  DECISION_CALLER_TIMING_VERSION,
  type DecisionShadowConfig, type DecisionShadowLease } from "../src/local-decisions.js";
import { normalizeProcessGraph } from "../src/process-engine.js";
import { CoordinatorTelemetry } from "../src/telemetry.js";
import type { ProcessGraph } from "../src/types.js";

const root = fileURLToPath(new URL("../../../", import.meta.url));
function pythonResult(request?: unknown, logits = [8, 0]) {
  return JSON.parse(execFileSync("python3", ["-c", `
import json, sys
from decision_runtime.engine import DecisionEngine, Scores
from decision_runtime.contracts import canonical_json
data = json.load(sys.stdin)
class Backend:
    identity = {"repository": "test-only", "revision": "fixture", "artifactSha256": "1"*64,
                "tokenizerSha256": "2"*64, "implementationSha256": "3"*64,
                "promptVersion": "test-v1", "backend": "fixture", "quantization": "none"}
    def score(self, request): return Scores(data["logits"], 20)
engine = DecisionEngine(Backend())
print(canonical_json({"profileJson": canonical_json(engine.profile()),
                      "result": engine.decide(data["request"]) if data.get("request") else None}))
`], { cwd: root, input: JSON.stringify({ request, logits }), encoding: "utf8" }));
}
const profileJson = pythonResult().profileJson as string;
const callerTiming = { schemaVersion: DECISION_CALLER_TIMING_VERSION, clock: "monotonic", boundary: "local_http_call", durationMs: 42.125 };
function config(): DecisionShadowConfig {
  return normalizeDecisionShadowConfig({ mode: "shadow", profileJson, timeoutMs: 1000,
    question: "Подтверждено?", kind: "boolean", options: [
      { id: "no", description: "Нет", value: false }, { id: "yes", description: "Да", value: true },
    ] });
}
function scoreConfig(values = [0, 10]): DecisionShadowConfig {
  return normalizeDecisionShadowConfig({ ...config(), kind: "score", question: "Оцените полноту по заданным уровням",
    options: values.map((value, i) => ({ id: `level-${i}`, description: `Уровень ${i}`, value })) });
}
function graph(shadow = config(), approval = false): ProcessGraph {
  return { nodes: [
    { id: "start", name: "Start", type: "start", position: { x: 0, y: 0 }, config: {} },
    { id: "agent", name: "Agent", type: "agent", position: { x: 200, y: 0 },
      config: { agentId: "collector", approvalRequired: approval, decisionShadow: shadow } },
    { id: "end", name: "End", type: "end", position: { x: 400, y: 0 }, config: {} },
  ], edges: [{ id: "a", source: "start", target: "agent", branch: "default" },
    { id: "b", source: "agent", target: "end", branch: "default" }] };
}
let nodeNumber = 0;
function node(store: AgatStore, capable: boolean | "v2" = true) {
  return store.registerNode({ enrollmentToken: "test", name: `Shadow worker ${++nodeNumber}`, platform: "test", models: ["test-model"],
    maxConcurrency: 1, labels: capable ? { decisionShadow: capable === "v2" ? "local_decision_shadow_v2" : "local_decision_shadow_v1" } : {} }).id;
}
function start(store: AgatStore, approval = false) {
  const process = store.createProcess({ name: "Shadow test", graph: graph(config(), approval) });
  store.publishProcess(String(process.id));
  return store.startProcess(String(process.id), { input: "Источник: результата нет. 🧪\nПриватный текст" })!;
}
function observation(store: AgatStore, runId: string) {
  return (store.getRunTrace(runId)!.decisionObservations as any[])[0]?.observation;
}

test("Python and TypeScript agree on Unicode request identity and Boolean false", () => {
  const lease = createDecisionShadowLease(config(), "stage-1", "строка 🧪\n\u2028\t\"test\"");
  const { result } = pythonResult(lease.request);
  assert.equal(result.inputSha256, lease.inputSha256);
  assert.equal(lease.profileSha256, decisionSha256(profileJson));
  const checked = validateDecisionShadowResult({ result }, lease, profileJson);
  assert.equal(checked.status, "ok");
  assert.equal(checked.result?.value, false);
  assert.equal(checked.fallback, "primary");
});

test("negotiated caller timing survives computed, unavailable and invalid-result observations", () => {
  const lease = createDecisionShadowLease(config(), "timing", "private state");
  const result = pythonResult(lease.request).result;
  assert.equal(lease.callerTimingVersion, DECISION_CALLER_TIMING_VERSION);
  assert.deepEqual(validateDecisionShadowResult({ result, callerTiming }, lease, profileJson).callerTiming, callerTiming);
  assert.deepEqual(validateDecisionShadowResult({ status: "unavailable", reason: "timeout", callerTiming }, lease, profileJson),
    { mode: "shadow", fallback: "primary", status: "unavailable", reason: "timeout", callerTiming });
  const invalid = validateDecisionShadowResult({ result: { ...result, inputSha256: "0".repeat(64) }, callerTiming }, lease, profileJson);
  assert.equal(invalid.reason, "invalid_response"); assert.deepEqual(invalid.callerTiming, callerTiming);
  const legacy = { ...lease }; delete legacy.callerTimingVersion;
  assert.equal(validateDecisionShadowResult({ result }, legacy, profileJson).status, "ok");
  assert.equal(validateDecisionShadowResult({ result, callerTiming }, legacy, profileJson).reason, "invalid_response");
});

test("malformed caller timing cannot enter the stored observation", () => {
  const lease = createDecisionShadowLease(config(), "timing", "private state");
  const result = pythonResult(lease.request).result;
  const mutations = [
    ...[false, "0", -1, NaN, Infinity, 86_400_001].map((durationMs) => ({ ...callerTiming, durationMs })),
    { ...callerTiming, clock: "wall" }, { ...callerTiming, boundary: "inference" },
    { ...callerTiming, schemaVersion: "unknown" }, { ...callerTiming, privateText: "secret" }, null,
  ];
  for (const timing of mutations) {
    assert.deepEqual(validateDecisionShadowResult({ result, callerTiming: timing }, lease, profileJson),
      { mode: "shadow", fallback: "primary", status: "unavailable", reason: "invalid_response" });
  }
  assert.equal(validateDecisionShadowResult({ result, callerTiming: { ...callerTiming, durationMs: 0 } }, lease, profileJson).callerTiming?.durationMs, 0);
});

test("coordinator recomputes abstention and rejects identity, probability, candidate and policy tampering", () => {
  const lease = createDecisionShadowLease(config(), "stage-1", "private state");
  const valid = pythonResult(lease.request).result;
  const changes = [
    (r: any) => { r.id = "other-stage"; }, (r: any) => { r.inputSha256 = "0".repeat(64); },
    (r: any) => { r.model.tokenizerSha256 = "0".repeat(64); }, (r: any) => { r.policy.minProbability = 0; },
    (r: any) => { r.distribution[0].probability = 1; }, (r: any) => { r.distribution[0].logit = -10; },
    (r: any) => { r.distribution[1].id = "no"; }, (r: any) => { r.value = "false"; },
    (r: any) => { r.extra = "secret"; }, (r: any) => { r.status = "abstain"; },
    (r: any) => { r.generatedTokens = 1; }, (r: any) => { r.distribution[0].logit = Infinity; },
  ];
  for (const change of changes) {
    const result = structuredClone(valid); change(result);
    assert.deepEqual(validateDecisionShadowResult({ result }, lease, profileJson),
      { mode: "shadow", fallback: "primary", status: "unavailable", reason: "invalid_response" });
  }
  const abstain = validateDecisionShadowResult({ result: pythonResult(lease.request, [0, 0]).result }, lease, profileJson);
  assert.equal(abstain.status, "abstain"); assert.equal(abstain.result?.value, null);
  const choice = normalizeDecisionShadowConfig({ ...config(), kind: "choice", options: [
    { id: "unknown", description: "Недостаточно данных", abstain: true }, { id: "yes", description: "Да" },
  ] });
  const choiceLease = createDecisionShadowLease(choice, "choice", "Source");
  assert.equal(validateDecisionShadowResult({ result: pythonResult(choiceLease.request).result }, choiceLease, profileJson).reason, "abstain_option");
});

test("isolated inference timeout and unavailable errors stay bound to the lease and preserve primary fallback", () => {
  const lease = createDecisionShadowLease(config(), "isolated-stage", "private state");
  const valid = pythonResult(lease.request).result;
  for (const key of ["selectedProbability", "margin", "inputTokens", "generatedTokens"]) delete valid[key];
  for (const reason of ["inference_timeout", "inference_cancelled", "backend_unavailable"]) {
    const result = { ...valid, status: "error", reason, selectedOptionId: null, value: null, distribution: [] };
    const checked = validateDecisionShadowResult({ result }, lease, profileJson);
    assert.equal(checked.status, "error");
    assert.equal(checked.reason, reason);
    assert.equal(checked.fallback, "primary");
    for (const change of [{ value: true }, { inputSha256: "0".repeat(64) }, { inputTokens: 20 }]) {
      assert.equal(validateDecisionShadowResult({ result: { ...result, ...change } }, lease, profileJson).reason, "invalid_response");
    }
  }
});

test("process publication rejects invalid contracts and routing mode", () => {
  for (const change of [{ mode: "route" }, { kind: "score" }, { timeoutMs: 10001 }, { options: [] },
    { profileJson: "{}" }, { question: "" }, { endpoint: "https://other.example" }]) {
    assert.throws(() => normalizeDecisionShadowConfig({ ...config(), ...change }));
  }
  const invalid = graph(); invalid.nodes[0]!.config.decisionShadow = config();
  assert.throws(() => normalizeProcessGraph(invalid, new Set(["collector"])));
  assert.throws(() => createDecisionShadowLease(config(), "stage", "x".repeat(24_001)));
});

test("Score uses the same binary64 fingerprint across Python and JavaScript numeric encodings", () => {
  const vectors = [
    [0, 1], [-0, 1], [1e-7, 0.000001, 0.1, 0.30000000000000004],
    [Number.MIN_VALUE, -Number.MIN_VALUE, Number.MIN_VALUE * 2, -1e-300],
    [-1_000_000, 999999.9999999999, 1_000_000], [1.0000000000000002, 1.0000000000000004],
  ];
  for (const values of vectors) {
    const lease = createDecisionShadowLease(scoreConfig(values), "score-stage", "Источник 🧪\u2028\nquote: \"");
    assert.equal(lease.profile, "local_decision_shadow_v2");
    assert.equal(lease.request.inputFingerprintVersion, SCORE_FINGERPRINT_VERSION);
    const { result } = pythonResult(lease.request, values.map((_, i) => i === 0 ? 8 : 0));
    assert.equal(result.inputSha256, lease.inputSha256);
    assert.equal(validateDecisionShadowResult({ result }, lease, profileJson).status, "ok");
  }
  const zero = createDecisionShadowLease(scoreConfig([0, 1]), "stage", "State");
  const minusZero = createDecisionShadowLease(scoreConfig([-0, 1]), "other-id", "State");
  assert.equal(zero.inputSha256, minusZero.inputSha256);
  assert.equal(Object.is(minusZero.request.options[0]!.value, -0), false);
  const changed = createDecisionShadowLease(scoreConfig([Number.MIN_VALUE, 1]), "stage", "State");
  assert.notEqual(zero.inputSha256, changed.inputSha256);
});

test("Score validates the weighted mean, numeric zero, abstention and fingerprint version", () => {
  const lease = createDecisionShadowLease(scoreConfig(), "score", "State");
  const valid = pythonResult(lease.request).result;
  assert.equal(validateDecisionShadowResult({ result: valid }, lease, profileJson).status, "ok");
  assert.ok(valid.value > 0 && valid.value < 1);
  for (const value of [0, false, "0", 10, null, valid.value + 1e-5, NaN, Infinity]) {
    assert.equal(validateDecisionShadowResult({ result: { ...valid, value } }, lease, profileJson).reason, "invalid_response");
  }
  for (const version of [undefined, null, "python-json-v1", "binary64-v2"]) {
    assert.equal(validateDecisionShadowResult({ result: { ...valid, inputFingerprintVersion: version } }, lease, profileJson).reason, "invalid_response");
  }
  const zero = pythonResult(lease.request, [1000, -1000]).result;
  assert.equal(zero.value, 0);
  assert.equal(validateDecisionShadowResult({ result: zero }, lease, profileJson).status, "ok");
  const abstain = pythonResult(lease.request, [0, 0]).result;
  assert.equal(validateDecisionShadowResult({ result: abstain }, lease, profileJson).status, "abstain");
  assert.equal(abstain.value, null);
  const tinyLease = createDecisionShadowLease(scoreConfig([0, 1e-300]), "tiny", "State");
  const tiny = pythonResult(tinyLease.request).result;
  assert.equal(validateDecisionShadowResult({ result: tiny }, tinyLease, profileJson).status, "ok");
  assert.equal(validateDecisionShadowResult({ result: { ...tiny, value: 1e-17 } }, tinyLease, profileJson).reason, "invalid_response");
});

test("legacy runtime profiles and Choice/Boolean input fingerprints remain accepted", () => {
  const profile = JSON.parse(profileJson); delete profile.inputFingerprintVersions;
  const legacyJson = JSON.stringify(profile);
  const legacy = normalizeDecisionShadowConfig({ ...config(), profileJson: legacyJson });
  const lease = createDecisionShadowLease(legacy, "legacy", "State");
  assert.equal(lease.profile, "local_decision_shadow_v1");
  assert.ok(!Object.hasOwn(lease.request, "inputFingerprintVersion"));
  const result = pythonResult(lease.request).result;
  delete result.inputFingerprintVersions;
  assert.equal(validateDecisionShadowResult({ result }, lease, legacyJson).status, "ok");
  result.inputFingerprintVersions = ["python-json-v1", "binary64-v1"];
  assert.equal(validateDecisionShadowResult({ result }, lease, legacyJson).reason, "invalid_response");
});

test("Score requires pinned runtime support, finite unique numeric levels and v2 worker capability", () => {
  for (const values of [[true, 1], [0, -0], [1, 1], [0, 1_000_001], [0, NaN], [0, Infinity], ["0", 1]]) {
    assert.throws(() => scoreConfig(values as number[]));
  }
  const legacy = JSON.parse(profileJson); delete legacy.inputFingerprintVersions;
  assert.throws(() => normalizeDecisionShadowConfig({ ...scoreConfig(), profileJson: JSON.stringify(legacy) }), /binary64/);
  const abstain = scoreConfig(); abstain.options[0]!.abstain = true;
  assert.throws(() => normalizeDecisionShadowConfig(abstain));
  for (const capable of [true, "v2"] as const) {
    const store = new AgatStore(":memory:", { seedDemo: false, decisionShadowEnabled: true });
    try {
      const worker = node(store, capable);
      const process = store.createProcess({ name: "Score shadow", graph: graph(scoreConfig()) });
      store.publishProcess(String(process.id));
      const instance = store.startProcess(String(process.id), { input: "State" })!;
      const lease = store.leaseNext(worker)!;
      if (capable === "v2") {
        assert.equal(lease.decisionShadow!.profile, "local_decision_shadow_v2");
        const result = pythonResult(lease.decisionShadow!.request).result;
        assert.equal(store.recordDecisionShadow(worker, lease.leaseId, { result }).status, "ok");
      } else {
        assert.equal(lease.decisionShadow, undefined);
        assert.equal(observation(store, String(instance.runId)).reason, "unsupported_worker");
      }
      store.completeLease(worker, lease.leaseId, "PRIMARY OUTPUT");
      assert.equal(store.getRun(String(instance.runId))!.status, "completed");
    } finally { store.close(); }
  }
});

test("opt-in shadow stores once, survives retry and preserves primary output, ACL and process replay guard", () => {
  const store = new AgatStore(":memory:", { seedDemo: false, decisionShadowEnabled: true });
  try {
    store.createProject({ id: "isolated", name: "Isolated" });
    const worker = node(store); const other = node(store);
    const instance = start(store); const runId = String(instance.runId);
    const lease = store.leaseNext(worker)!; assert.ok(lease.decisionShadow);
    assert.equal(lease.decisionShadow.request.state, lease.run.input);
    const result = pythonResult(lease.decisionShadow.request).result;
    assert.throws(() => store.recordDecisionShadow(other, lease.leaseId, { result }), /аренда/);
    const saved = store.recordDecisionShadow(worker, lease.leaseId, { result });
    assert.equal(saved.status, "ok");
    assert.deepEqual(store.recordDecisionShadow(worker, lease.leaseId, { result: {} }), saved);
    store.failLease(worker, lease.leaseId, "Primary completion response lost");
    const retry = store.leaseNext(other)!; assert.ok(retry); assert.equal(retry.decisionShadow, undefined);
    assert.throws(() => store.recordDecisionShadow(worker, lease.leaseId, { result }), /аренда/);
    store.completeLease(other, retry.leaseId, "PRIMARY OUTPUT");
    assert.equal(store.getRun(runId)!.status, "completed");
    assert.equal((store.getRun(runId)!.stages as any[])[0].output, "PRIMARY OUTPUT");
    assert.deepEqual(observation(store, runId), saved);
    const traceObservation = (store.getRunTrace(runId)!.decisionObservations as any[])[0];
    assert.equal(traceObservation.inputSha256, lease.decisionShadow!.inputSha256);
    assert.equal(traceObservation.callerTimeoutMs, lease.decisionShadow!.timeoutMs);
    const context = traceObservation.context;
    assert.deepEqual(context, { kind: config().kind, question: config().question, options: config().options });
    assert.ok(!("state" in context) && !("profileJson" in context));
    assert.equal(store.getRunTrace(runId, "isolated"), null);
    assert.throws(() => store.replayRun(runId, {}), /Replay процесса отключён/);
    const events = (store.getRunTrace(runId)!.events as any[]).filter((e) => e.type === "decision.shadow");
    assert.equal(events.length, 1);
    assert.ok(!JSON.stringify(events).includes("Приватный текст"));
    assert.ok(!JSON.stringify(events).includes("profileJson"));
  } finally { store.close(); }
});

test("stage inventory includes queued, assigned pending and cancelled stages without inventing observations", () => {
  const store = new AgatStore(":memory:", { seedDemo: false, decisionShadowEnabled: true });
  try {
    const worker = node(store); const instance = start(store); const runId = String(instance.runId);
    const before = store.getRunTrace(runId)!;
    const queued = (before.decisionStageInventory as any).stages;
    assert.equal(queued.length, 1); assert.equal(queued[0].assigned, false);
    assert.equal(queued[0].observationRecorded, false); assert.equal(queued[0].profileSha256, null);
    assert.deepEqual(before.decisionObservations, []);
    const lease = store.leaseNext(worker)!;
    const pending = store.getRunTrace(runId)!;
    const assigned = (pending.decisionStageInventory as any).stages[0];
    assert.deepEqual(pending.decisionStageInventory, { schemaVersion: "agat.decision.shadow-stage-inventory.v1", scope: "stored_shadow_stages", stages: [{
      stageId: lease.stage.id, stageStatus: "running", assigned: true, observationRecorded: false,
      profileSha256: lease.decisionShadow!.profileSha256, inputSha256: lease.decisionShadow!.inputSha256, callerTimeoutMs: lease.decisionShadow!.timeoutMs,
    }] });
    assert.deepEqual(pending.decisionObservations, []);
    store.cancelRun(runId);
    const cancelled = store.getRunTrace(runId)!;
    assert.deepEqual(cancelled.decisionStageInventory, { schemaVersion: "agat.decision.shadow-stage-inventory.v1", scope: "stored_shadow_stages",
      stages: [{ ...assigned, stageStatus: "cancelled" }] });
    assert.deepEqual(cancelled.decisionObservations, []);
    assert.equal(store.getRunTrace(runId, "isolated"), null);
  } finally { store.close(); }
});

test("recorded and missing-result inventory markers match the saved observations", () => {
  for (const record of [true, false]) {
    const store = new AgatStore(":memory:", { seedDemo: false, decisionShadowEnabled: true });
    try {
      const worker = node(store); const instance = start(store); const runId = String(instance.runId); const lease = store.leaseNext(worker)!;
      if (record) store.recordDecisionShadow(worker, lease.leaseId, { result: pythonResult(lease.decisionShadow!.request).result, callerTiming });
      store.completeLease(worker, lease.leaseId, "PRIMARY");
      const trace = store.getRunTrace(runId)!;
      assert.equal((trace.decisionStageInventory as any).stages[0].observationRecorded, true);
      assert.equal((trace.decisionStageInventory as any).stages[0].stageStatus, "completed");
      assert.equal((trace.decisionObservations as any[])[0].observation.status, record ? "ok" : "unavailable");
      assert.equal((trace.decisionStageInventory as any).stages[0].inputSha256, (trace.decisionObservations as any[])[0].inputSha256);
    } finally { store.close(); }
  }
});

test("process root trace exists before dispatch and remains stable after SQLite reopen", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-caller-trace-"));
  const database = path.join(directory, "trace.sqlite");
  let store = new AgatStore(database, { seedDemo: false, decisionShadowEnabled: true });
  try {
    const worker = node(store); const instance = start(store); const runId = String(instance.runId);
    const initial = store.getRunTrace(runId)!;
    assert.match(String((initial.run as any).traceId), /^[a-f0-9]{32}$/);
    assert.notEqual((initial.run as any).traceId, "0".repeat(32));
    const lease = store.leaseNext(worker)!;
    assert.equal(lease.traceContext.traceId, (initial.run as any).traceId);
    store.recordDecisionShadow(worker, lease.leaseId, { result: pythonResult(lease.decisionShadow!.request).result, callerTiming });
    store.completeLease(worker, lease.leaseId, "PRIMARY");
    const before = store.getRunTrace(runId)!;
    store.close(); store = new AgatStore(database, { seedDemo: false, decisionShadowEnabled: true });
    assert.deepEqual(store.getRunTrace(runId), before);
  } finally { store.close(); fs.rmSync(directory, { recursive: true, force: true }); }
});

test("process root span ends when the enclosing start transaction rolls back", async () => {
  const exporter = new InMemorySpanExporter();
  const telemetry = new CoordinatorTelemetry({ enabled: true, serviceName: "agat-caller-rollback", exporterEndpoint: "", exporter });
  const store = new AgatStore(":memory:", { seedDemo: false, decisionShadowEnabled: true, telemetry });
  const prepare = store.db.prepare.bind(store.db);
  try {
    node(store);
    store.db.prepare = sql => {
      const statement = prepare(sql);
      if (/INSERT INTO events/.test(sql)) {
        const get = statement.get.bind(statement);
        statement.get = (...args) => {
          if (args.includes("process.scenario.started")) throw new Error("Controlled post-start event fault");
          return get(...args);
        };
      }
      return statement;
    };
    assert.throws(() => start(store), /Controlled post-start event fault/);
    store.db.prepare = prepare;
    assert.equal((store.db.prepare("SELECT COUNT(*) AS count FROM runs").get() as any).count, 0);
    await telemetry.forceFlush();
    const spans = exporter.getFinishedSpans().filter(span => span.name === "invoke_workflow agat");
    assert.equal(spans.length, 1);
    assert.equal(spans[0]!.attributes["agat.run.status"], "failed");
  } finally { store.db.prepare = prepare; store.close(); await telemetry.shutdown(); }
});

test("feature flag, old workers, malformed responses and missing observations always retain the primary path", () => {
  for (const variant of ["disabled", "unsupported_worker", "invalid_response", "missing_result", "timeout"]) {
    const store = new AgatStore(":memory:", { seedDemo: false, decisionShadowEnabled: variant !== "disabled" });
    try {
      const worker = node(store, variant !== "unsupported_worker"); const instance = start(store);
      const lease = store.leaseNext(worker)!;
      if (["disabled", "unsupported_worker"].includes(variant)) assert.equal(lease.decisionShadow, undefined);
      if (variant === "invalid_response") store.recordDecisionShadow(worker, lease.leaseId, { result: { error: "private" } });
      if (variant === "timeout") store.recordDecisionShadow(worker, lease.leaseId, { status: "unavailable", reason: "timeout" });
      store.completeLease(worker, lease.leaseId, "PRIMARY");
      assert.equal(store.getRun(String(instance.runId))!.status, "completed");
      assert.equal(observation(store, String(instance.runId)).reason, variant);
    } finally { store.close(); }
  }
});

test("cancelled and expired leases cannot append a shadow result or revive ownership", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-shadow-"));
  const database = path.join(directory, "test.sqlite");
  const store = new AgatStore(database, { seedDemo: false, decisionShadowEnabled: true });
  try {
    const worker = node(store); const instance = start(store); const lease = store.leaseNext(worker)!;
    const sql = new DatabaseSync(database);
    sql.prepare("UPDATE stages SET lease_expires_at = ? WHERE lease_id = ?").run("2000-01-01T00:00:00.000Z", lease.leaseId);
    sql.close();
    assert.equal(store.renewLease(worker, lease.leaseId), false);
    assert.throws(() => store.recordDecisionShadow(worker, lease.leaseId, { status: "unavailable", reason: "timeout" }), /аренда/);
    store.cancelRun(String(instance.runId));
    assert.throws(() => store.recordDecisionShadow(worker, lease.leaseId, {}), /аренда/);
  } finally { store.close(); fs.rmSync(directory, { recursive: true, force: true }); }
});

for (const boundary of ["stage read", "event insert", "idempotent retry"] as const) {
  test(`shadow rejects lease expiry during ${boundary} and preserves only previously committed observations`, context => {
    context.mock.timers.enable({ apis: ["Date"], now: Date.now() });
    const store = new AgatStore(":memory:", { seedDemo: false, decisionShadowEnabled: true });
    try {
      const worker = node(store); const instance = start(store); const runId = String(instance.runId);
      const lease = store.leaseNext(worker)!;
      const payload = { status: "unavailable", reason: "timeout" };
      const previous = boundary === "idempotent retry" ? store.recordDecisionShadow(worker, lease.leaseId, payload) : undefined;
      const prepare = store.db.prepare.bind(store.db);
      let reachedBoundary = false;
      store.db.prepare = sql => {
        const statement = prepare(sql);
        const matches = boundary === "event insert" ? /INSERT INTO events\s*\(/.test(sql)
          : /FROM stages s JOIN runs r/.test(sql) && /s\.activity_json/.test(sql);
        if (matches) {
          const get = statement.get.bind(statement);
          statement.get = (...params) => {
            const result = get(...params);
            reachedBoundary = true;
            context.mock.timers.tick(Date.parse(lease.expiresAt) - Date.now());
            return result;
          };
        }
        return statement;
      };
      let error: unknown;
      try { store.recordDecisionShadow(worker, lease.leaseId, payload); }
      catch (caught) { error = caught; }
      finally { store.db.prepare = prepare; }
      const events = () => (store.getRunTrace(runId)!.events as Array<{ type: string }>).filter(e => e.type === "decision.shadow");
      context.diagnostic(`After expiry at ${boundary}: observation=${Boolean(observation(store, runId))}, events=${events().length}`);
      assert.ok(reachedBoundary, "The actual SQL statement must finish before advancing the clock");
      assert.ok(error instanceof Error, "An expired owner must be rejected even on an idempotent retry");
      assert.match(error.message, /Активная аренда не найдена/);
      assert.deepEqual(observation(store, runId), previous);
      assert.equal(events().length, previous ? 1 : 0);
      assert.equal(store.renewLease(worker, lease.leaseId), false);
      context.mock.timers.tick(1); // Maintenance reclaims leases strictly older than now.
      store.maintenanceTick(); store.heartbeatNode(worker, {});
      const replacement = store.leaseNext(worker)!; assert.ok(replacement);
      assert.notEqual(replacement.leaseId, lease.leaseId);
      assert.throws(() => store.recordDecisionShadow(worker, lease.leaseId, payload), /аренда/);
      const saved = store.recordDecisionShadow(worker, replacement.leaseId, payload);
      assert.deepEqual(store.recordDecisionShadow(worker, replacement.leaseId, { result: {} }), saved);
      if (previous) assert.deepEqual(saved, previous);
      store.completeLease(worker, replacement.leaseId, "PRIMARY AFTER EXPIRY");
      assert.equal(store.getRun(runId)!.status, "completed");
      assert.equal((store.getRun(runId)!.stages as any[])[0].output, "PRIMARY AFTER EXPIRY");
      assert.equal(events().length, 1);
    } finally { store.close(); }
  });
}

test("shadow never bypasses stage approval", () => {
  const store = new AgatStore(":memory:", { seedDemo: false, decisionShadowEnabled: true });
  try { const worker = node(store); start(store, true); assert.equal(store.leaseNext(worker), null); }
  finally { store.close(); }
});

test("safe replay reuses the recorded shadow with provenance; live replay starts a new observation", () => {
  const store = new AgatStore(":memory:", { seedDemo: false, decisionShadowEnabled: true });
  try {
    const worker = node(store); const source = start(store); const lease = store.leaseNext(worker)!;
    const result = pythonResult(lease.decisionShadow!.request).result;
    store.recordDecisionShadow(worker, lease.leaseId, { result, callerTiming });
    store.completeLease(worker, lease.leaseId, "PRIMARY");
    const replay = store.replayProcessInstance(String(source.id), { mode: "safe" })!;
    const repeated = store.leaseNext(worker)!;
    assert.equal(repeated.decisionShadow, undefined);
    const reused = observation(store, String(replay.runId));
    assert.equal(reused.reusedFromStageId, lease.stage.id);
    assert.deepEqual(reused.result, result);
    assert.deepEqual(reused.callerTiming, callerTiming);
    store.completeLease(worker, repeated.leaseId, "PRIMARY AGAIN");
    const live = store.replayProcessInstance(String(source.id), { mode: "live" })!;
    assert.ok(store.leaseNext(worker)!.decisionShadow);
    assert.equal(observation(store, String(live.runId)), undefined);
  } finally { store.close(); }
});

test("safe replay does not substitute a recorded observation for a changed upstream output", () => {
  const store = new AgatStore(":memory:", { seedDemo: false, decisionShadowEnabled: true });
  try {
    const worker = node(store);
    const twoSteps = graph();
    twoSteps.nodes.splice(2, 0, { ...structuredClone(twoSteps.nodes[1]!), id: "second" });
    twoSteps.edges[1]!.target = "second";
    twoSteps.edges.push({ id: "c", source: "second", target: "end", branch: "default" });
    const process = store.createProcess({ name: "Two stages", graph: twoSteps }); store.publishProcess(String(process.id));
    const source = store.startProcess(String(process.id), { input: "original" })!;
    for (let i = 0; i < 2; i++) {
      const lease = store.leaseNext(worker)!;
      store.recordDecisionShadow(worker, lease.leaseId, { result: pythonResult(lease.decisionShadow!.request).result });
      store.completeLease(worker, lease.leaseId, "ORIGINAL OUTPUT");
    }
    const replay = store.replayProcessInstance(String(source.id), { mode: "safe" })!;
    const first = store.leaseNext(worker)!; assert.equal(first.decisionShadow, undefined);
    store.completeLease(worker, first.leaseId, "CHANGED OUTPUT");
    const second = store.leaseNext(worker)!; assert.equal(second.decisionShadow, undefined);
    const observations = store.getRunTrace(String(replay.runId))!.decisionObservations as any[];
    assert.equal(observations[1].observation.reason, "safe_replay_unavailable");
    assert.equal(observations[1].observation.result, undefined);
  } finally { store.close(); }
});

test("a recorded decision survives coordinator restart without recomputation", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-shadow-restart-"));
  const database = path.join(directory, "state.sqlite");
  let store = new AgatStore(database, { seedDemo: false, decisionShadowEnabled: true });
  try {
    const worker = node(store); const instance = start(store); const lease = store.leaseNext(worker)!;
    const saved = store.recordDecisionShadow(worker, lease.leaseId, { result: pythonResult(lease.decisionShadow!.request).result });
    store.close();
    store = new AgatStore(database, { seedDemo: false, decisionShadowEnabled: true });
    assert.deepEqual(store.recordDecisionShadow(worker, lease.leaseId, {}), saved);
    store.completeLease(worker, lease.leaseId, "PRIMARY AFTER RESTART");
    assert.deepEqual(observation(store, String(instance.runId)), saved);
  } finally { store.close(); fs.rmSync(directory, { recursive: true, force: true }); }
});
