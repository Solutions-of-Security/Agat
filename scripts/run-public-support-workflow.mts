import assert from "node:assert/strict";
import { randomUUID, createHash } from "node:crypto";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { parseArgs } from "node:util";
import { AgatStore } from "../apps/coordinator/src/database.ts";
import { loadConfig } from "../apps/coordinator/src/config.ts";
import { createCoordinatorServer } from "../apps/coordinator/src/server.ts";
import type { ProcessGraph } from "../apps/coordinator/src/types.ts";

const { values } = parseArgs({ options: { "decision-url": { type: "string" }, "evidence-dir": { type: "string" } } });
assert.ok(values["decision-url"] && values["evidence-dir"]);
const directory = path.resolve(values["evidence-dir"]);
assert.ok(directory.startsWith(path.resolve("docs/private")+path.sep));
const input = JSON.parse(fs.readFileSync(path.join(directory, "plan.json"), "utf8"));
const url = new URL(values["decision-url"]);
assert.equal(url.protocol, "http:"); assert.equal(url.hostname, "127.0.0.1");
assert.ok(url.port && url.port !== "8766" && !url.username && !url.password && url.pathname === "/" && !url.search && !url.hash);
const save = (name: string, data: unknown) => fs.writeFileSync(path.join(directory, name), JSON.stringify(data, null, 2)+"\n", { flag: "wx", mode: 0o600 });
const healthResponse = await fetch(url.origin+"/health", { signal: AbortSignal.timeout(5000), redirect: "error" });
assert.equal(healthResponse.status, 200);
const health = await healthResponse.json() as any;
assert.equal(health.profileSha256, input.context.profileSha256);
const loss = input.runtimeLoss;
const recovery = input.runtimeRecovery;
const timeout = input.callerTimeout;
const cancellation = input.coordinatorCancellation;
assert.ok([loss, recovery, timeout, cancellation].filter(Boolean).length <= 1);
if (loss) {
  assert.equal(input.schemaVersion, "agat.decision.public-workflow-launch-plan.v2");
  assert.ok(Number.isInteger(loss.beforeIndex) && loss.beforeIndex > 0 && loss.beforeIndex < input.context.inputs.length);
  assert.equal(loss.kind, "stop_owned_runtime"); assert.equal(loss.restart, false);
  assert.equal(loss.boundary, "after_completed_previous_instance_before_next_creation");
  assert.equal(loss.endpoint, "reserve_original_loopback_port_with_tcp_reset_guard");
} else if (recovery) {
  assert.equal(input.schemaVersion, "agat.decision.public-workflow-launch-plan.v3");
  assert.ok(Number.isInteger(recovery.targetIndex) && recovery.targetIndex > 0 && recovery.targetIndex < input.context.inputs.length-1);
  const target = input.context.inputs[recovery.targetIndex];
  assert.equal(target.contextEligible, true); assert.equal(recovery.targetCaseId, target.id); assert.equal(recovery.targetInputSha256, target.inputSha256);
  assert.equal(recovery.kind, "crash_owned_runtime_during_active_http_handler"); assert.equal(recovery.signal, "SIGKILL");
  assert.equal(recovery.trigger, "exactly_one_active_http_handler"); assert.equal(recovery.restart, true);
  assert.equal(recovery.endpoint, "same_loopback_port_same_frozen_profile"); assert.equal(recovery.retryCount, 0);
  assert.equal(recovery.warmupPerRuntime, 2); assert.equal(recovery.armDeadlineMs, 30000); assert.equal(recovery.recoveryDeadlineMs, 90000);
  assert.equal(recovery.workflowDeadlineMs, 240000);
} else if (timeout || cancellation) {
  const spec = cancellation ?? timeout;
  assert.equal(input.schemaVersion, cancellation ? "agat.decision.public-workflow-launch-plan.v5" : "agat.decision.public-workflow-launch-plan.v4");
  assert.ok(Number.isInteger(spec.targetIndex) && spec.targetIndex > 0 && spec.targetIndex < input.context.inputs.length-1);
  const target = input.context.inputs[spec.targetIndex];
  assert.equal(target.contextEligible, true); assert.equal(spec.targetCaseId, target.id); assert.equal(spec.targetInputSha256, target.inputSha256);
  assert.equal(spec.kind, cancellation ? "cancel_actual_run_while_owned_proxy_withholds_completed_response" : "withhold_owned_proxy_response");
  assert.equal(spec.boundary, "after_upstream_completion_before_response_headers");
  assert.equal(spec.endpoint, "temporary_loopback_proxy_to_owned_runtime"); assert.equal(spec.callerTimeoutMs, 10000);
  assert.equal(spec.upstreamTimeoutMs, 8000); assert.equal(spec.disconnectDeadlineMs, 15000);
  assert.equal(spec.retryCount, 0); assert.equal(spec.restart, false); assert.equal(spec.warmupCount, 2);
  if (cancellation) {
    assert.equal(spec.cancelEndpoint, "authenticated_coordinator_run_cancel"); assert.equal(spec.cancelRequestDeadlineMs, 5000);
    assert.equal(spec.cancelToEofDeadlineMs, 2500); assert.equal(spec.cancelledDurableReturn, "unknown");
  }
} else assert.equal(input.schemaVersion, "agat.decision.public-workflow-launch-plan.v1");
let runtimeLossApplied: any;
let crashArmed: any; let crashApplied: any; let recoveryApplied: any;
let callerTimeoutDrained: any;
let coordinatorCancellationReady: any; let coordinatorCancellationApplied: any; let coordinatorCancellationDrained: any;
const cancellationHttp: any[] = [];
function publish(name: string, data: unknown) {
  const pending = name.replace(/\.json$/, ".pending.json"); save(pending, data);
  try { fs.linkSync(path.join(directory, pending), path.join(directory, name)); }
  finally { fs.unlinkSync(path.join(directory, pending)); }
}
function digest(name: string) { return createHash("sha256").update(fs.readFileSync(path.join(directory, name))).digest("hex"); }
const config = { mode: "shadow", profileJson: health.profileJson, timeoutMs: 10000, ...input.config };
const store = new AgatStore(":memory:", { seedDemo: false, decisionShadowEnabled: true });
store.updateModelRouterPolicy({ enabled: false });
const agent = store.createAgent({ name: "Public inventory fixture primary", role: "Diagnostic only", systemPrompt: "Return the fixture primary output", model: "fixture-primary" });
let primaryCalls = 0;
const primary = http.createServer((req, res) => {
  if (req.method !== "POST" || req.url !== "/v1/chat/completions") { res.writeHead(404).end(); return; }
  req.resume(); req.on("end", () => { primaryCalls++;
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ choices: [{ message: { role: "assistant", content: "PRIMARY_OUTPUT" }, finish_reason: "stop" }],
      usage: { prompt_tokens: 1, completion_tokens: 1 } }));
  });
});
const adminToken = randomUUID(); const enrollmentToken = randomUUID();
const coordinator = createCoordinatorServer({ ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false,
  adminToken, enrollmentToken, oidcEnabled: false, mcpEnabled: false, sandboxEnabled: false,
  a2aEnabled: false, localWorkerLauncherEnabled: false }, store);
if (cancellation) coordinator.prependListener("request", (req: http.IncomingMessage, res: http.ServerResponse) => {
  if (req.method !== "POST" || !/^\/api\/v1\/leases\/[^/]+\/(renew|decision-shadow(?:\/intent)?|complete|fail)$/.test(req.url ?? "")) return;
  const row: any = { method: req.method, path: req.url, startedAt: new Date().toISOString(), startedMs: performance.now(),
    requestBody: "", requestBodySha256: createHash("sha256").update("").digest("hex"), requestBodyComplete: false };
  const chunks: Buffer[] = [];
  req.on("data", (body: Buffer) => { chunks.push(body); const raw = Buffer.concat(chunks); assert.ok(raw.length <= 128*1024);
    row.requestBody = raw.toString("utf8"); row.requestBodySha256 = createHash("sha256").update(raw).digest("hex"); });
  req.on("end", () => { row.requestBodyComplete = true; });
  res.once("finish", () => { row.httpStatus = res.statusCode; row.finishedAt = new Date().toISOString();
    row.finishedMs = performance.now(); cancellationHttp.push(row); });
});
async function listen(server: http.Server) {
  await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  const address = server.address(); assert.ok(address && typeof address === "object"); return `http://127.0.0.1:${address.port}`;
}
async function close(server: http.Server) {
  if (server.listening) { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); }
}
const graph: ProcessGraph = { nodes: [
  { id: "start", name: "Start", type: "start", position: { x: 0, y: 0 }, config: {} },
  { id: "agent", name: "Fixture primary with real shadow", type: "agent", position: { x: 200, y: 0 }, config: { agentId: String(agent.id), decisionShadow: config } },
  { id: "condition", name: "Primary route", type: "condition", position: { x: 400, y: 0 }, config: { condition: { source: "last_output", operator: "equals", value: "PRIMARY_OUTPUT", caseSensitive: true } } },
  { id: "primary", name: "Primary branch", type: "transform", position: { x: 600, y: 0 }, config: { template: "PRIMARY_BRANCH" } },
  { id: "wrong", name: "Wrong branch", type: "transform", position: { x: 600, y: 200 }, config: { template: "WRONG_BRANCH" } },
  { id: "end", name: "End", type: "end", position: { x: 800, y: 0 }, config: {} }
], edges: [
  { id: "a", source: "start", target: "agent", branch: "default" }, { id: "b", source: "agent", target: "condition", branch: "default" },
  { id: "c", source: "condition", target: "primary", branch: "true" }, { id: "d", source: "condition", target: "wrong", branch: "false" },
  { id: "e", source: "primary", target: "end", branch: "default" }, { id: "f", source: "wrong", target: "end", branch: "default" }
] };
let worker: ReturnType<typeof spawn> | undefined; const routes: any[] = [];
const credentialPath = path.join(directory, "worker-credentials.json");
const workerLog = fs.openSync(path.join(directory, "worker.log"), "wx", 0o600);
async function stopWorker() {
  if (!worker) return;
  if (worker.exitCode === null && worker.signalCode === null) {
    const exit = new Promise<void>(resolve => worker!.once("exit", () => resolve())); worker.kill("SIGTERM");
    const timer = setTimeout(() => { if (worker?.exitCode === null && worker.signalCode === null) worker.kill("SIGKILL"); }, 5000);
    try { await exit; } finally { clearTimeout(timer); }
  }
}
try {
  const primaryUrl = await listen(primary); const coordinatorUrl = await listen(coordinator);
  const processId = String(store.createProcess({ name: "Whole public inventory integration; fixture primary", graph }).id);
  store.publishProcess(processId);
  const startAt = new Date(Date.now()+1000).toISOString();
  const recipe = { schemaVersion: cancellation ? "agat.decision.public-workflow-plan.v5" : timeout ? "agat.decision.public-workflow-plan.v4" : recovery ? "agat.decision.public-workflow-plan.v3" : loss ? "agat.decision.public-workflow-plan.v2" : "agat.decision.public-workflow-plan.v1", ...(loss ? { runtimeLoss: loss } : {}), ...(recovery ? { runtimeRecovery: recovery } : {}), ...(timeout ? { callerTimeout: timeout } : {}), ...(cancellation ? { coordinatorCancellation: cancellation } : {}), mode: "serial_closed_model_integration",
    primary: "fixture_chat_completions", processId, processVersion: 1, projectId: "default", startAt,
    scopeEndRule: "after_full_input_inventory_and_worker_drain", config: input.config, profileSha256: health.profileSha256,
    inputs: input.context.inputs.map((row: any) => ({ caseId: row.id, inputSha256: row.inputSha256 })),
    ownersAppointed: false, routingEnabled: false, qualification: "not_assessed", graphSha256: createHash("sha256").update(JSON.stringify(graph)).digest("hex") };
  save("workflow-plan.json", recipe);
  const environment = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("AGAT_") && !key.startsWith("OTEL_")));
  worker = spawn("python3", ["workers/agat_worker.py", "--coordinator", coordinatorUrl, "--credentials", credentialPath,
    "--name", "public-inventory-diagnostic", "--models", "fixture-primary", "--model-url", primaryUrl+"/v1",
    "--model-discovery", "off", "--no-web", "--poll-interval", "0.2", "--concurrency", "1", "--decision-url", url.origin],
    { cwd: process.cwd(), stdio: ["ignore", workerLog, workerLog], env: { ...environment, AGAT_ENROLLMENT_TOKEN: enrollmentToken,
      OTEL_SDK_DISABLED: "true", NO_PROXY: "127.0.0.1,localhost" } });
  await new Promise(resolve => setTimeout(resolve, Math.max(0, Date.parse(startAt)-Date.now())));
  const globalDeadline = Date.now()+(recovery?.workflowDeadlineMs ?? 240000);
  async function waitReceipt(name: string, budget: number) {
    const deadline = Math.min(Date.now()+budget, globalDeadline);
    while (!fs.existsSync(path.join(directory, name))) {
      assert.ok(Date.now()<deadline, `Owned runtime barrier ${name} was not acknowledged`);
      assert.equal(worker!.exitCode, null); assert.equal(worker!.signalCode, null);
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    return JSON.parse(fs.readFileSync(path.join(directory, name), "utf8"));
  }
  for (const [index, item] of input.context.inputs.entries()) {
    assert.equal(worker.exitCode, null, "Owned worker exited early"); assert.equal(worker.signalCode, null);
    if (loss && index === loss.beforeIndex) {
      const request = { schemaVersion: "agat.decision.public-workflow-loss-request.v1", beforeIndex: index,
        afterCaseId: routes.at(-1).caseId, afterRunId: routes.at(-1).runId, createdAt: new Date().toISOString() };
      save("runtime-loss-request.pending.json", request);
      fs.linkSync(path.join(directory, "runtime-loss-request.pending.json"), path.join(directory, "runtime-loss-request.json"));
      fs.unlinkSync(path.join(directory, "runtime-loss-request.pending.json"));
      const requestSha = createHash("sha256").update(fs.readFileSync(path.join(directory, "runtime-loss-request.json"))).digest("hex");
      const deadline = Math.min(Date.now()+30000, globalDeadline);
      while (!fs.existsSync(path.join(directory, "runtime-loss-applied.json"))) {
        assert.ok(Date.now()<deadline, "Owned runtime loss was not acknowledged");
        assert.equal(worker.exitCode, null); assert.equal(worker.signalCode, null);
        await new Promise(resolve => setTimeout(resolve, 20));
      }
      runtimeLossApplied = JSON.parse(fs.readFileSync(path.join(directory, "runtime-loss-applied.json"), "utf8"));
      assert.equal(runtimeLossApplied.schemaVersion, "agat.decision.public-workflow-loss-applied.v1");
      assert.equal(runtimeLossApplied.beforeIndex, index); assert.equal(runtimeLossApplied.requestFileSha256, requestSha);
      assert.equal(runtimeLossApplied.runtimeExited, true); assert.equal(runtimeLossApplied.endpointGuardedWithTcpReset, true);
      assert.ok(Number.isInteger(runtimeLossApplied.runtimeExitCode));
      assert.ok(Date.parse(runtimeLossApplied.appliedAt) >= Date.parse(request.createdAt));
    }
    if (recovery && index === recovery.targetIndex) {
      const request = { schemaVersion: "agat.decision.public-workflow-crash-request.v1", targetIndex: index,
        afterCaseId: routes.at(-1).caseId, afterRunId: routes.at(-1).runId, createdAt: new Date().toISOString() };
      publish("runtime-crash-request.json", request);
      crashArmed = await waitReceipt("runtime-crash-armed.json", recovery.armDeadlineMs);
      assert.equal(crashArmed.schemaVersion, "agat.decision.public-workflow-crash-armed.v1");
      assert.equal(crashArmed.targetIndex, index); assert.equal(crashArmed.requestFileSha256, digest("runtime-crash-request.json"));
      assert.equal(crashArmed.port, Number(url.port)); assert.equal(crashArmed.profileSha256, health.profileSha256);
      assert.ok(Number.isInteger(crashArmed.runtimePid) && crashArmed.runtimePid > 0);
      assert.ok(Date.parse(crashArmed.armedAt) >= Date.parse(request.createdAt));
    }
    const beforeCalls = primaryCalls; const instance = store.startProcess(processId, { input: item.request.state })!;
    if (recovery && index === recovery.targetIndex) publish("runtime-crash-started.json", {
      schemaVersion: "agat.decision.public-workflow-crash-started.v1", targetIndex: index, caseId: item.id,
      inputSha256: item.inputSha256, instanceId: String(instance.id), runId: String(instance.runId), createdAt: new Date().toISOString() });
    const runId = String(instance.runId); const deadline = Math.min(Date.now()+20000, globalDeadline);
    const cancelTarget = cancellation && index === cancellation.targetIndex;
    if (cancelTarget) {
      coordinatorCancellationReady = await waitReceipt("coordinator-cancellation-ready.json", cancellation.disconnectDeadlineMs);
      const ready = coordinatorCancellationReady;
      assert.equal(ready.schemaVersion, "agat.decision.public-workflow-cancellation-ready.v1");
      assert.equal(ready.targetIndex, index); assert.equal(ready.caseId, item.id); assert.equal(ready.inputSha256, item.inputSha256);
      assert.equal(ready.profileSha256, health.profileSha256); assert.equal(ready.upstreamCompletedNormally, true); assert.equal(ready.responseBytesWritten, 0);
      const beforeResponse = await fetch(coordinatorUrl+`/api/v1/runs/${runId}/trace`, {
        headers: { "x-agat-admin-token": adminToken }, signal: AbortSignal.timeout(5000), redirect: "error" });
      assert.equal(beforeResponse.status, 200); const beforeRaw = await beforeResponse.text(); assert.ok(Buffer.byteLength(beforeRaw) <= 16*1024*1024);
      const before = JSON.parse(beforeRaw); assert.equal(before.run.status, "running"); assert.equal(before.decisionObservations.length, 0);
      assert.equal(before.decisionCallerAccounting.stages[0].stageId, ready.stageId);
      assert.equal(before.decisionCallerAccounting.stages[0].assignments[0].intent, true);
      fs.writeFileSync(path.join(directory, "coordinator-cancellation-before.http.json"), beforeRaw, { flag: "wx", mode: 0o600 });
      const requestPath = `/api/v1/runs/${runId}/cancel`;
      const unauthorized = await fetch(coordinatorUrl+requestPath, { method: "POST", signal: AbortSignal.timeout(5000), redirect: "error" });
      assert.equal(unauthorized.status, 401);
      const requestStartedAt = new Date().toISOString(); const started = performance.now();
      const cancelResponse = await fetch(coordinatorUrl+requestPath, { method: "POST", headers: { "x-agat-admin-token": adminToken },
        signal: AbortSignal.timeout(cancellation.cancelRequestDeadlineMs), redirect: "error" });
      const responseBody = await cancelResponse.text();
      const cancelRequestElapsedMs = Math.round((performance.now()-started)*1000)/1000;
      const responseCompletedAt = new Date().toISOString();
      assert.equal(cancelResponse.status, 204); assert.equal(responseBody, ""); assert.equal(store.getRun(runId)!.status, "cancelled");
      coordinatorCancellationApplied = { schemaVersion: "agat.decision.public-workflow-cancellation-applied.v1", targetIndex: index,
        caseId: item.id, runId, instanceId: String(instance.id), stageId: ready.stageId, inputSha256: item.inputSha256, profileSha256: health.profileSha256,
        readyFileSha256: digest("coordinator-cancellation-ready.json"), beforeTraceFileSha256: digest("coordinator-cancellation-before.http.json"),
        requestMethod: "POST", requestPath, requestBody: "", requestBodySha256: createHash("sha256").update("").digest("hex"),
        unauthenticatedStatus: unauthorized.status, httpStatus: cancelResponse.status, responseBody,
        responseBodySha256: createHash("sha256").update(responseBody).digest("hex"), requestStartedAt, responseCompletedAt,
        cancelRequestElapsedMs };
      publish("coordinator-cancellation-applied.json", coordinatorCancellationApplied);
    }
    const expectedState = cancelTarget ? "cancelled" : "completed";
    while (store.getRun(runId)!.status !== expectedState) {
      assert.equal(worker.exitCode, null); assert.ok(Date.now()<deadline, "Workflow inventory deadline exceeded");
      assert.ok(!["failed", ...(cancelTarget ? [] : ["cancelled"])].includes(String(store.getRun(runId)!.status)), "Workflow failed");
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    const run = store.getRun(runId)!; const trace = store.getRunTrace(runId)!;
    const observations = trace.decisionObservations as any[]; assert.equal(observations.length, cancelTarget ? 0 : 1);
    const stages = run.stages as any[];
    const route = { index, caseId: item.id, inputSha256: item.inputSha256, instanceId: String(instance.id), runId,
      stageId: String(cancelTarget ? coordinatorCancellationReady.stageId : observations[0].stageId), primaryCalls: primaryCalls-beforeCalls,
      primaryBranch: stages.some(s => s.processNodeId === "primary" && s.output === "PRIMARY_BRANCH"),
      wrongBranch: stages.some(s => s.processNodeId === "wrong"), runStatus: run.status };
    routes.push(route);
    fs.appendFileSync(path.join(directory, "workflow-routes.jsonl"), JSON.stringify(route)+"\n", { mode: 0o600 });
    if (cancelTarget) {
      coordinatorCancellationDrained = await waitReceipt("coordinator-cancellation-drained.json", cancellation.disconnectDeadlineMs);
      assert.equal(coordinatorCancellationDrained.schemaVersion, "agat.decision.public-workflow-cancellation-drained.v1");
      assert.equal(coordinatorCancellationDrained.targetIndex, index); assert.equal(coordinatorCancellationDrained.caseId, item.id);
      assert.equal(coordinatorCancellationDrained.stageId, route.stageId); assert.equal(coordinatorCancellationDrained.inputSha256, item.inputSha256);
      assert.equal(coordinatorCancellationDrained.profileSha256, health.profileSha256);
    }
    if (timeout && index === timeout.targetIndex) {
      assert.equal(observations[0].observation.status, "unavailable"); assert.equal(observations[0].observation.reason, "timeout");
      assert.equal(Object.hasOwn(observations[0].observation, "result"), false);
      callerTimeoutDrained = await waitReceipt("caller-timeout-drained.json", timeout.disconnectDeadlineMs);
      assert.equal(callerTimeoutDrained.schemaVersion, "agat.decision.public-workflow-deadline-drained.v1");
      assert.equal(callerTimeoutDrained.targetIndex, index); assert.equal(callerTimeoutDrained.caseId, item.id);
      assert.equal(callerTimeoutDrained.stageId, route.stageId); assert.equal(callerTimeoutDrained.inputSha256, item.inputSha256);
      assert.equal(callerTimeoutDrained.profileSha256, health.profileSha256);
    }
    if (recovery && index === recovery.targetIndex) {
      assert.equal(observations[0].observation.status, "unavailable"); assert.equal(observations[0].observation.reason, "unreachable");
      assert.equal(Object.hasOwn(observations[0].observation, "result"), false);
      crashApplied = await waitReceipt("runtime-crash-applied.json", recovery.armDeadlineMs);
      assert.equal(crashApplied.schemaVersion, "agat.decision.public-workflow-crash-applied.v1");
      assert.equal(crashApplied.targetIndex, index); assert.equal(crashApplied.runtimePid, crashArmed.runtimePid);
      assert.equal(crashApplied.runtimeExitCode, -9); assert.equal(crashApplied.runtimeExited, true); assert.equal(crashApplied.signal, "SIGKILL");
      assert.equal(crashApplied.port, Number(url.port)); assert.equal(crashApplied.profileSha256, health.profileSha256);
      assert.equal(crashApplied.startedFileSha256, digest("runtime-crash-started.json"));
      const request = { schemaVersion: "agat.decision.public-workflow-recovery-request.v1", targetIndex: index,
        afterCaseId: item.id, afterRunId: runId, stageId: route.stageId, observation: observations[0].observation, createdAt: new Date().toISOString() };
      publish("runtime-recovery-request.json", request);
      recoveryApplied = await waitReceipt("runtime-recovery-applied.json", recovery.recoveryDeadlineMs);
      assert.equal(recoveryApplied.schemaVersion, "agat.decision.public-workflow-recovery-applied.v1");
      assert.equal(recoveryApplied.targetIndex, index); assert.equal(recoveryApplied.requestFileSha256, digest("runtime-recovery-request.json"));
      assert.equal(recoveryApplied.crashSealSha256, crashApplied.sha256); assert.equal(recoveryApplied.port, Number(url.port));
      assert.equal(recoveryApplied.profileSha256, health.profileSha256); assert.equal(recoveryApplied.warmupCount, 2);
      assert.ok(Number.isInteger(recoveryApplied.runtimePid) && recoveryApplied.runtimePid > 0 && recoveryApplied.runtimePid !== crashArmed.runtimePid);
      assert.ok(Date.parse(recoveryApplied.appliedAt) >= Date.parse(request.createdAt));
    }
  }
  await stopWorker(); assert.equal(worker.exitCode, 0, "Worker did not drain cleanly");
  await new Promise(resolve => setTimeout(resolve, 5));
  if (cancellation) save("coordinator-cancellation-http.json", cancellationHttp);
  const endAt = new Date().toISOString();
  const endpoint = `${coordinatorUrl}/api/v1/processes/${processId}/decision-shadow-cohort?`+new URLSearchParams({ processVersion: "1", startAt, endAt });
  assert.equal((await fetch(endpoint, { signal: AbortSignal.timeout(5000) })).status, 401);
  const response = await fetch(endpoint, { headers: { "x-agat-admin-token": adminToken }, signal: AbortSignal.timeout(10000), redirect: "error" });
  assert.equal(response.status, 200);
  const raw = await response.text(); assert.ok(Buffer.byteLength(raw)<=16*1024*1024);
  fs.writeFileSync(path.join(directory, "cohort.http.json"), raw, { flag: "wx", mode: 0o600 });
  save("workflow-driver.json", { status: "observed", primary: "fixture_chat_completions", primaryCalls, routes, nodeVersion: process.version,
    ...(loss ? { runtimeLossApplied } : {}), ownedPids: [process.pid, worker.pid], workerExitCode: worker.exitCode, actualWindow: { startAt, endAt },
    ...(recovery ? { runtimeRecovery: { armed: crashArmed, crashed: crashApplied, recovered: recoveryApplied } } : {}),
    ...(timeout ? { callerTimeoutDrained } : {}),
    ...(cancellation ? { coordinatorCancellationReady, coordinatorCancellationApplied, coordinatorCancellationDrained } : {}),
    unauthenticatedStatus: 401, authenticatedStatus: response.status, ownersAppointed: false, routingEnabled: false, qualification: "not_assessed" });
  console.log(JSON.stringify({ status: "observed", inputs: routes.length, primaryFixtureCalls: primaryCalls }));
} finally {
  await stopWorker(); fs.closeSync(workerLog);
  if (fs.existsSync(credentialPath)) fs.unlinkSync(credentialPath);
  await close(coordinator); await close(primary); store.close();
}
