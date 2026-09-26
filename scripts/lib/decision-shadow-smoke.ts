import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { AgatStore } from "../../apps/coordinator/src/database.js";
import { loadConfig } from "../../apps/coordinator/src/config.js";
import { createCoordinatorServer } from "../../apps/coordinator/src/server.js";
import { normalizeDecisionShadowConfig } from "../../apps/coordinator/src/local-decisions.js";
import type { ProcessGraph } from "../../apps/coordinator/src/types.js";

const root = fileURLToPath(new URL("../../", import.meta.url));
async function listen(server: http.Server): Promise<string> {
  await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  const address = server.address(); assert.ok(address && typeof address === "object");
  return `http://127.0.0.1:${address.port}`;
}
async function close(server: http.Server): Promise<void> {
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
}

/** Integration evidence only: the primary model is deliberately a fixture. */
export async function runDecisionShadowSmoke(decisionUrl: string, request: Record<string, unknown>, expected: boolean | "worker_timeout" = false) {
  const expectInferenceTimeout = expected === true;
  const expectWorkerTimeout = expected === "worker_timeout";
  const expectUnavailableBackend = expectInferenceTimeout || expectWorkerTimeout;
  const url = new URL(decisionUrl);
  assert.equal(url.hostname, "127.0.0.1"); assert.equal(url.protocol, "http:");
  assert.ok(url.port && !url.username && !url.password && url.pathname === "/" && !url.search && !url.hash);
  const healthResponse = await fetch(`${url.origin}/health`, { signal: AbortSignal.timeout(5000), redirect: "error" });
  assert.equal(healthResponse.status, 200);
  const health = await healthResponse.json() as { profileJson: string; profileSha256: string };
  const config = normalizeDecisionShadowConfig({ mode: "shadow", profileJson: health.profileJson, timeoutMs: expectWorkerTimeout ? 100 : 10_000,
    kind: request.kind, question: request.question, options: request.options });
  assert.equal(typeof request.state, "string");
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "agat-shadow-smoke-"));
  const store = new AgatStore(path.join(temporary, "state.sqlite"), { seedDemo: false, decisionShadowEnabled: true,
    artifactsDir: path.join(temporary, "artifacts") });
  let primaryCalls = 0;
  const primary = http.createServer((req, res) => {
    if (req.method !== "POST" || req.url !== "/v1/chat/completions") { res.writeHead(404).end(); return; }
    req.resume();
    req.on("end", () => {
      primaryCalls++;
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ choices: [{ message: { role: "assistant", content: "PRIMARY_OUTPUT" }, finish_reason: "stop" }],
        usage: { prompt_tokens: 1, completion_tokens: 1 } }));
    });
  });
  const coordinator = createCoordinatorServer({ ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false,
    adminToken: "shadow-smoke-admin", enrollmentToken: "shadow-smoke-enrollment", oidcEnabled: false,
    mcpEnabled: false, sandboxEnabled: false, a2aEnabled: false, localWorkerLauncherEnabled: false }, store);
  let worker: ReturnType<typeof spawn> | undefined;
  try {
    const primaryUrl = await listen(primary);
    const coordinatorUrl = await listen(coordinator);
    const graph: ProcessGraph = { nodes: [
      { id: "start", name: "Start", type: "start", position: { x: 0, y: 0 }, config: {} },
      { id: "agent", name: "Primary with shadow", type: "agent", position: { x: 200, y: 0 },
        config: { agentId: "collector", decisionShadow: config } },
      { id: "condition", name: "Primary route", type: "condition", position: { x: 400, y: 0 },
        config: { condition: { source: "last_output", operator: "equals", value: "PRIMARY_OUTPUT", caseSensitive: true } } },
      { id: "primary", name: "Primary branch", type: "transform", position: { x: 600, y: 0 }, config: { template: "PRIMARY_BRANCH" } },
      { id: "wrong", name: "Wrong branch", type: "transform", position: { x: 600, y: 200 }, config: { template: "WRONG_BRANCH" } },
      { id: "end", name: "End", type: "end", position: { x: 800, y: 0 }, config: {} },
    ], edges: [
      { id: "a", source: "start", target: "agent", branch: "default" },
      { id: "b", source: "agent", target: "condition", branch: "default" },
      { id: "c", source: "condition", target: "primary", branch: "true" },
      { id: "d", source: "condition", target: "wrong", branch: "false" },
      { id: "e", source: "primary", target: "end", branch: "default" },
      { id: "f", source: "wrong", target: "end", branch: "default" },
    ] };
    const process = store.createProcess({ name: "Decision shadow integration smoke", graph });
    store.publishProcess(String(process.id));
    const instance = store.startProcess(String(process.id), { input: String(request.state) })!;
    worker = spawn("python3", ["workers/agat_worker.py", "--coordinator", coordinatorUrl,
      "--enrollment-token", "shadow-smoke-enrollment", "--credentials", path.join(temporary, "worker.json"),
      "--name", "shadow-smoke-worker", "--models", "fixture-primary", "--model-url", `${primaryUrl}/v1`,
      "--model-discovery", "off", "--no-web", "--once", "--decision-url", url.origin],
    { cwd: root, stdio: ["ignore", "pipe", "pipe"], env: { ...processEnv(), OTEL_SDK_DISABLED: "true", NO_PROXY: "127.0.0.1,localhost" } });
    let workerLog = "";
    worker.stdout?.on("data", (chunk) => { workerLog = (workerLog + chunk).slice(-8000); });
    worker.stderr?.on("data", (chunk) => { workerLog = (workerLog + chunk).slice(-8000); });
    const exitCode = await new Promise<number | null>((resolve, reject) => {
      const timer = setTimeout(() => { worker?.kill("SIGTERM"); reject(new Error("Shadow smoke worker timed out")); }, 45_000);
      worker!.once("exit", (code) => { clearTimeout(timer); resolve(code); });
      worker!.once("error", (error) => { clearTimeout(timer); reject(error); });
    });
    assert.equal(exitCode, 0, workerLog);
    assert.equal(primaryCalls, 1, "Primary inference should run exactly once");
    const runId = String(instance.runId);
    const trace = store.getRunTrace(runId)!;
    const observations = trace.decisionObservations as Array<Record<string, any>>;
    assert.equal(observations.length, 1, workerLog);
    if (expectWorkerTimeout) {
      assert.equal(observations[0]!.observation.status, "unavailable");
      assert.equal(observations[0]!.observation.reason, "timeout");
      assert.equal(observations[0]!.observation.fallback, "primary");
    } else if (expectInferenceTimeout) {
      assert.equal(observations[0]!.observation.status, "error");
      assert.equal(observations[0]!.observation.reason, "inference_timeout");
    } else {
      assert.ok(["ok", "abstain"].includes(observations[0]!.observation.status), JSON.stringify(observations[0]));
    }
    const stages = store.getRun(runId)!.stages as Array<Record<string, unknown>>;
    assert.ok(stages.some((stage) => stage.processNodeId === "primary" && stage.output === "PRIMARY_BRANCH"));
    assert.ok(!stages.some((stage) => stage.processNodeId === "wrong"));
    assert.equal(store.getRun(runId)!.status, "completed");
    const events = (trace.events as Array<Record<string, any>>).filter((event) => event.type === "decision.shadow");
    assert.equal(events.length, 1);
    assert.ok(!JSON.stringify(events).includes(String(request.state)));
    const healthDeadline = Date.now() + (expectWorkerTimeout ? 1500 : 0);
    let finalHealthResponse: Response;
    let finalHealth: { status: string; profileSha256: string };
    do {
      finalHealthResponse = await fetch(`${url.origin}/health`, { signal: AbortSignal.timeout(5000), redirect: "error" });
      finalHealth = await finalHealthResponse.json() as typeof finalHealth;
      assert.equal(finalHealth.profileSha256, health.profileSha256);
      if (!expectWorkerTimeout || finalHealthResponse.status !== 200 || Date.now() >= healthDeadline) break;
      await new Promise<void>((resolve) => setTimeout(resolve, 20));
    } while (true);
    assert.equal(finalHealthResponse.status, expectUnavailableBackend ? 503 : 200);
    assert.equal(finalHealth.status, expectUnavailableBackend ? "unavailable" : "ready");
    assert.equal(finalHealth.profileSha256, health.profileSha256);
    return { schemaVersion: "agat.decision-shadow-smoke.v1", createdAt: new Date().toISOString(),
      status: "integration_pass", qualification: "not_assessed", primary: "fixture-chat-completions",
      integrationImplementation: implementationIdentity(),
      profileSha256: health.profileSha256, primaryCalls, primaryRoute: "PRIMARY_BRANCH",
      observation: observations[0]!.observation,
      checks: { workerHttpRoundtrip: true, coordinatorValidated: true, primaryRoutePreserved: true,
        singleObservation: true, shadowTraceContainsNoState: true, finalProfileUnchanged: true,
        ...(expectInferenceTimeout ? { timeoutPersisted: true, backendUnavailableAfterTimeout: true } : {}),
        ...(expectWorkerTimeout ? { workerTimeoutPersisted: true, backendUnavailableAfterWorkerDisconnect: true } : {}) } };
  } finally {
    if (worker && worker.exitCode === null && worker.signalCode === null) worker.kill("SIGKILL");
    await close(coordinator); await close(primary); store.close();
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}

function implementationIdentity() {
  const files = ["apps/coordinator/src/local-decisions.ts", "apps/coordinator/src/database.ts",
    "apps/coordinator/src/server.ts", "apps/coordinator/src/config.ts", "apps/coordinator/src/process-engine.ts",
    "apps/coordinator/src/types.ts", "workers/agat_worker.py", "workers/local_decisions.py",
    "scripts/lib/decision-shadow-smoke.ts", "scripts/qualify-decision-shadow.ts"].sort();
  const hashes = Object.fromEntries(files.map((file) => [file, createHash("sha256").update(fs.readFileSync(path.join(root, file))).digest("hex")]));
  return { files: hashes, sha256: createHash("sha256").update(JSON.stringify(hashes)).digest("hex") };
}

function processEnv(): NodeJS.ProcessEnv {
  // The isolated worker must not inherit enrollment, release or telemetry secrets.
  return Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("AGAT_") && !key.startsWith("OTEL_")));
}
