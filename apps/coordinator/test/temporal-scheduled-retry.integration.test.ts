import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import test from "node:test";
import { Client, Connection, WorkflowNotFoundError } from "@temporalio/client";
import { historyToJSON } from "@temporalio/common/lib/proto-utils.js";
import { Worker } from "@temporalio/worker";
import { AgatStore } from "../src/database.js";
import { root, cleanEnv, processChild, within, eventually, listen, close } from "./helpers/temporal.js";

const address = process.env.AGAT_TEST_TEMPORAL_ADDRESS;

test("Temporal scheduled-start retains a committed instance through an outage beyond its former retry budget",
  { skip: !address, timeout: 210_000 }, async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-scheduled-retry-"));
  const dbPath = path.join(directory, "state.sqlite"), artifacts = path.join(directory, "artifacts");
  const store = new AgatStore(dbPath, { seedDemo: false, temporalProcesses: true, artifactsDir: artifacts });
  const children: ReturnType<typeof processChild>[] = [];
  const taskQueue = `scheduled-retry-${randomUUID()}`;
  let connection: Connection | undefined, coordinatorUrl = "", instanceId = "", firstRequestAt = 0;
  const starts: Array<{ key: string; elapsedMs: number; status: number }> = [], proxyErrors: unknown[] = [];
  const proxy = http.createServer(async (req, res) => {
    try {
      assert.equal(req.method, "POST"); assert.equal(req.headers["x-agat-temporal-token"], "retry-internal");
      assert.match(req.url!, /^\/api\/v1\/internal\/processes\/[^/]+\/(scheduled-start|tick)$/);
      let body = ""; for await (const chunk of req) body += chunk;
      const starting = req.url!.endsWith("/scheduled-start"), key = req.headers["idempotency-key"];
      if (starting) {
        assert.equal(typeof key, "string");
        if (firstRequestAt === 0) firstRequestAt = performance.now();
        // Commit the first create and lose its positive response, then model a
        // coordinator outage. The live retry policy and real clock are unchanged.
        const elapsedMs = performance.now() - firstRequestAt;
        if (instanceId && elapsedMs < 125_000) {
          starts.push({ key: key as string, elapsedMs, status: 503 });
          res.writeHead(503, { "content-type": "application/json" }).end(JSON.stringify({ error: "Synthetic coordinator outage" })); return;
        }
      }
      const result = await fetch(`${coordinatorUrl}${req.url}`, { method: "POST", body,
        headers: { "content-type": "application/json", "x-agat-temporal-token": "retry-internal",
          ...(typeof key === "string" ? { "idempotency-key": key } : {}) }, signal: AbortSignal.timeout(5_000) });
      const text = await result.text(); assert.ok(result.ok, text);
      if (starting) {
        const id = JSON.parse(text).instanceId;
        if (!instanceId) {
          instanceId = id; starts.push({ key: key as string, elapsedMs: performance.now() - firstRequestAt, status: 503 });
          res.writeHead(503, { "content-type": "application/json" }).end(JSON.stringify({ error: "Synthetic lost commit acknowledgement" })); return;
        }
        assert.equal(id, instanceId); starts.push({ key: key as string, elapsedMs: performance.now() - firstRequestAt, status: result.status });
      }
      res.writeHead(result.status, { "content-type": "application/json" }).end(text);
    } catch (error) { proxyErrors.push(error); res.writeHead(502).end(); }
  });
  try {
    const process = store.createProcess({ name: "Scheduled retry budget", graph: {
      nodes: [
        { id: "start", type: "start", name: "Start", position: { x: 0, y: 0 }, config: {} },
        { id: "hold", type: "signal", name: "Hold", position: { x: 100, y: 0 }, config: { signalName: "resume", signalTimeoutSeconds: 3600 } },
        { id: "end", type: "end", name: "End", position: { x: 200, y: 0 }, config: {} },
      ], edges: [{ id: "a", source: "start", target: "hold", branch: "default" }, { id: "b", source: "hold", target: "end", branch: "default" }],
    } }); store.publishProcess(String(process.id));
    const env = { ...cleanEnv(), AGAT_HOST: "127.0.0.1", AGAT_PORT: "0", AGAT_DB_PATH: dbPath,
      AGAT_ARTIFACTS_DIR: artifacts, AGAT_SEED_DEMO: "false", AGAT_SERVE_WEB: "false", AGAT_MCP_ENABLED: "false",
      AGAT_A2A_ENABLED: "false", AGAT_SANDBOX_ENABLED: "false", AGAT_LOCAL_WORKER_LAUNCHER: "false",
      AGAT_REQUIRE_SIGNED_WORKER_RELEASES: "false", AGAT_REQUIRE_WORKER_PROVENANCE: "false",
      AGAT_REQUIRE_WORKER_RUNTIME_ATTESTATION: "false", AGAT_ADMIN_TOKEN: "retry-admin", AGAT_TEMPORAL_ENABLED: "true",
      AGAT_TEMPORAL_ADDRESS: address!, AGAT_TEMPORAL_NAMESPACE: "default", AGAT_TEMPORAL_TASK_QUEUE: taskQueue,
      AGAT_TEMPORAL_INTERNAL_TOKEN: "retry-internal" };
    const coordinator = processChild(globalThis.process.execPath, ["apps/coordinator/dist/server.js"], env); children.push(coordinator);
    coordinatorUrl = `http://127.0.0.1:${(await coordinator.ready(/АГАТ слушает http:\/\/127\.0\.0\.1:(\d+)/))[1]}`;
    const worker = processChild(globalThis.process.execPath, ["apps/temporal-worker/dist/worker.js"], {
      ...env, AGAT_COORDINATOR_INTERNAL_URL: await listen(proxy), AGAT_TEMPORAL_METRICS_ADDRESS: "127.0.0.1:0",
      AGAT_TEMPORAL_WORKER_ID: "retry-fixture-worker" }); children.push(worker); await worker.ready(/Temporal worker слушает/);
    connection = await Connection.connect({ address, connectTimeout: 5_000 });
    const client = new Client({ connection, namespace: "default", identity: "retry-fixture-client" });
    const workflowId = `scheduled-retry-${randomUUID()}`;
    const parent = await client.workflow.start("agatScheduledProcessWorkflow", { workflowId, taskQueue,
      args: [{ processId: String(process.id), projectId: "default", input: "Synthetic", priority: 50, knowledgeCollectionIds: [] }] });
    let finished = false;
    const outcome = parent.result().then(value => ({ value }), error => ({ error: String(error), cause: String(error.cause) }))
      .finally(() => { finished = true; });
    await eventually(() => Boolean(instanceId), "Scheduled instance was not committed");
    const child = client.workflow.getHandle(`agat-process-${instanceId}`);
    let childExists = false;
    await within((async () => {
      while (!finished) {
        try { await child.describe(); childExists = true; break; }
        catch (error) { if (!(error instanceof WorkflowNotFoundError)) throw error; }
        await delay(200);
      }
    })(), "Neither child creation nor parent failure occurred", 170_000);
    if (childExists) {
      const response = await fetch(`${coordinatorUrl}/api/v1/processes/${process.id}/signals/resume`, { method: "POST",
        headers: { "content-type": "application/json", "x-agat-admin-token": "retry-admin" },
        body: JSON.stringify({ instanceId, payload: { resumed: true } }), signal: AbortSignal.timeout(5_000) });
      assert.equal(response.status, 202, await response.text());
    }
    const parentOutcome = await within(outcome, "Parent did not settle after recovery");
    const history = JSON.parse(historyToJSON(await parent.fetchHistory()), (key, value) => key === "identity" ? "retry-fixture" : value);
    const observed = { workflowId, instanceId, starts, childExists, parentOutcome, parentStatus: (await parent.describe()).status.name,
      applicationStatus: store.getProcessInstance(instanceId)!.status, instances: store.listProcessInstances().length, history };
    const evidence = globalThis.process.env.AGAT_TEMPORAL_RETRY_EVIDENCE_DIR;
    if (evidence) {
      const target = path.resolve(evidence); assert.ok(target.startsWith(path.join(root, "docs") + path.sep)); fs.mkdirSync(target, { recursive: true });
      fs.writeFileSync(path.join(target, "retry.json"), JSON.stringify(observed, null, 2) + "\n", { flag: "wx" });
    }
    assert.equal(observed.parentStatus, "COMPLETED", "Transient outage must not abandon a committed instance");
    assert.equal(observed.applicationStatus, "completed"); assert.equal(observed.instances, 1); assert.equal(childExists, true);
    assert.ok(starts.length > 8); assert.ok(starts.at(-1)!.elapsedMs > 120_000); assert.equal(new Set(starts.map(row => row.key)).size, 1);
    assert.deepEqual(proxyErrors, []);
    await Worker.runReplayHistory({ workflowBundle: { codePath: path.join(root, "apps/temporal-worker/dist/workflow-bundle.js") } }, history, workflowId);
    assert.deepEqual(await worker.stop(), { code: 0, signal: null }, worker.log());
    assert.deepEqual(await coordinator.stop(), { code: 0, signal: null }, coordinator.log());
  } finally {
    for (const child of children.reverse()) await child.stop();
    await close(proxy); await connection?.close(); store.close(); fs.rmSync(directory, { recursive: true, force: true });
  }
});
