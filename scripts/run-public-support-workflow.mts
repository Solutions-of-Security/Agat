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
  const recipe = { schemaVersion: "agat.decision.public-workflow-plan.v1", mode: "serial_closed_model_integration",
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
  const globalDeadline = Date.now()+240000;
  for (const [index, item] of input.context.inputs.entries()) {
    assert.equal(worker.exitCode, null, "Owned worker exited early"); assert.equal(worker.signalCode, null);
    const beforeCalls = primaryCalls; const instance = store.startProcess(processId, { input: item.request.state })!;
    const runId = String(instance.runId); const deadline = Math.min(Date.now()+20000, globalDeadline);
    while (store.getRun(runId)!.status !== "completed") {
      assert.equal(worker.exitCode, null); assert.ok(Date.now()<deadline, "Workflow inventory deadline exceeded");
      assert.ok(!["failed", "cancelled"].includes(String(store.getRun(runId)!.status)), "Workflow failed");
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    const run = store.getRun(runId)!; const trace = store.getRunTrace(runId)!;
    const observations = trace.decisionObservations as any[]; assert.equal(observations.length, 1);
    const stages = run.stages as any[];
    const route = { index, caseId: item.id, inputSha256: item.inputSha256, instanceId: String(instance.id), runId,
      stageId: String(observations[0].stageId), primaryCalls: primaryCalls-beforeCalls,
      primaryBranch: stages.some(s => s.processNodeId === "primary" && s.output === "PRIMARY_BRANCH"),
      wrongBranch: stages.some(s => s.processNodeId === "wrong"), runStatus: run.status };
    routes.push(route);
    fs.appendFileSync(path.join(directory, "workflow-routes.jsonl"), JSON.stringify(route)+"\n", { mode: 0o600 });
  }
  await stopWorker(); assert.equal(worker.exitCode, 0, "Worker did not drain cleanly");
  await new Promise(resolve => setTimeout(resolve, 5));
  const endAt = new Date().toISOString();
  const endpoint = `${coordinatorUrl}/api/v1/processes/${processId}/decision-shadow-cohort?`+new URLSearchParams({ processVersion: "1", startAt, endAt });
  assert.equal((await fetch(endpoint, { signal: AbortSignal.timeout(5000) })).status, 401);
  const response = await fetch(endpoint, { headers: { "x-agat-admin-token": adminToken }, signal: AbortSignal.timeout(10000), redirect: "error" });
  assert.equal(response.status, 200);
  const raw = await response.text(); assert.ok(Buffer.byteLength(raw)<=16*1024*1024);
  fs.writeFileSync(path.join(directory, "cohort.http.json"), raw, { flag: "wx", mode: 0o600 });
  save("workflow-driver.json", { status: "observed", primary: "fixture_chat_completions", primaryCalls, routes, nodeVersion: process.version,
    ownedPids: [process.pid, worker.pid], workerExitCode: worker.exitCode, actualWindow: { startAt, endAt },
    unauthenticatedStatus: 401, authenticatedStatus: response.status, ownersAppointed: false, routingEnabled: false, qualification: "not_assessed" });
  console.log(JSON.stringify({ status: "observed", inputs: routes.length, primaryFixtureCalls: primaryCalls }));
} finally {
  await stopWorker(); fs.closeSync(workerLog);
  if (fs.existsSync(credentialPath)) fs.unlinkSync(credentialPath);
  await close(coordinator); await close(primary); store.close();
}
