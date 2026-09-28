import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { Client, Connection, WorkflowNotFoundError } from "@temporalio/client";
import { historyToJSON } from "@temporalio/common/lib/proto-utils.js";
import { Worker } from "@temporalio/worker";
import { AgatStore } from "../src/database.js";
import type { ProcessGraph } from "../src/types.js";
import { root, cleanEnv, processChild, within, eventually, listen, close } from "./helpers/temporal.js";

const address = process.env.AGAT_TEST_TEMPORAL_ADDRESS;

for (const boundary of ["before-commit", "after-commit", "after-commit-compensating"] as const) {
test(`Temporal scheduled cancellation fences creation ${boundary}`,
  { skip: !address, timeout: 90_000 }, async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-scheduled-cancel-"));
  const dbPath = path.join(directory, "state.sqlite"), artifacts = path.join(directory, "artifacts");
  const store = new AgatStore(dbPath, { seedDemo: false, temporalProcesses: true, artifactsDir: artifacts });
  const children: ReturnType<typeof processChild>[] = [];
  const taskQueue = `scheduled-cancel-${randomUUID()}`;
  let connection: Connection | undefined, coordinatorUrl = "", entered = false, cancelCalls = 0;
  let startReply: { status: number; body: Record<string, unknown> } | undefined;
  let releaseStart!: () => void, releaseReply!: () => void;
  const startGate = new Promise<void>(resolve => { releaseStart = resolve; });
  const replyGate = new Promise<void>(resolve => { releaseReply = resolve; });
  const proxyErrors: unknown[] = [];
  const proxy = http.createServer(async (req, res) => {
    try {
      assert.equal(req.method, "POST"); assert.equal(req.headers["x-agat-temporal-token"], "scheduled-cancel-internal");
      assert.match(req.url!, /^\/api\/v1\/internal\/processes\/[^/]+\/(scheduled-start|scheduled-cancel|tick|cancel)$/);
      let body = ""; for await (const chunk of req) body += chunk;
      const starting = req.url!.endsWith("/scheduled-start");
      if (starting && boundary === "before-commit") { entered = true; await startGate; }
      const key = req.headers["idempotency-key"];
      const result = await fetch(`${coordinatorUrl}${req.url}`, { method: "POST", body,
        headers: { "content-type": "application/json", "x-agat-temporal-token": "scheduled-cancel-internal",
          ...(typeof key === "string" ? { "idempotency-key": key } : {}) }, signal: AbortSignal.timeout(5_000) });
      const text = await result.text();
      if (starting) {
        startReply = { status: result.status, body: JSON.parse(text) };
        if (boundary !== "before-commit") { entered = true; await replyGate; }
      } else {
        assert.ok(result.ok, text);
        if (req.url!.endsWith("/scheduled-cancel") && ++cancelCalls === 1) { res.destroy(); return; }
      }
      res.writeHead(result.status, { "content-type": "application/json" }).end(text);
    } catch (error) { proxyErrors.push(error); res.writeHead(502).end(); }
  });
  try {
    const graph: ProcessGraph = {
      nodes: [
        { id: "start", type: "start", name: "Start", position: { x: 0, y: 0 }, config: {} },
        { id: "hold", type: "signal", name: "Hold", position: { x: 100, y: 0 }, config: { signalName: "resume", signalTimeoutSeconds: 3600 } },
        { id: "end", type: "end", name: "End", position: { x: 200, y: 0 }, config: {} },
      ], edges: [{ id: "a", source: "start", target: "hold", branch: "default" }, { id: "b", source: "hold", target: "end", branch: "default" }],
    };
    const workerId = store.registerNode({ enrollmentToken: "unused", name: "Synthetic compensation worker", platform: "test",
      models: [], maxConcurrency: 1, agentRuntimes: ["single"] }).id;
    if (boundary === "after-commit-compensating") {
      graph.nodes.splice(1, 0, { id: "effect", type: "http", name: "Synthetic effect", position: { x: 50, y: 0 }, config: {
        method: "POST", url: "https://example.test/effect", body: "{}", headers: {}, timeoutSeconds: 10,
        compensation: { method: "POST", url: "https://example.test/undo", headers: {}, body: "{}", timeoutSeconds: 10 },
      } });
      graph.edges = [{ id: "create", source: "start", target: "effect", branch: "default" },
        { id: "hold", source: "effect", target: "hold", branch: "default" }, graph.edges[1]!];
    }
    const process = store.createProcess({ name: "Scheduled creation cancellation", graph }); store.publishProcess(String(process.id));
    const env = { ...cleanEnv(), AGAT_HOST: "127.0.0.1", AGAT_PORT: "0", AGAT_DB_PATH: dbPath,
      AGAT_ARTIFACTS_DIR: artifacts, AGAT_SEED_DEMO: "false", AGAT_SERVE_WEB: "false", AGAT_MCP_ENABLED: "false",
      AGAT_A2A_ENABLED: "false", AGAT_SANDBOX_ENABLED: "false", AGAT_LOCAL_WORKER_LAUNCHER: "false",
      AGAT_REQUIRE_SIGNED_WORKER_RELEASES: "false", AGAT_REQUIRE_WORKER_PROVENANCE: "false",
      AGAT_REQUIRE_WORKER_RUNTIME_ATTESTATION: "false", AGAT_ADMIN_TOKEN: "scheduled-cancel-admin", AGAT_TEMPORAL_ENABLED: "true",
      AGAT_TEMPORAL_ADDRESS: address!, AGAT_TEMPORAL_NAMESPACE: "default", AGAT_TEMPORAL_TASK_QUEUE: taskQueue,
      AGAT_TEMPORAL_INTERNAL_TOKEN: "scheduled-cancel-internal" };
    const coordinator = processChild(globalThis.process.execPath, ["apps/coordinator/dist/server.js"], env); children.push(coordinator);
    coordinatorUrl = `http://127.0.0.1:${(await coordinator.ready(/АГАТ слушает http:\/\/127\.0\.0\.1:(\d+)/))[1]}`;
    const worker = processChild(globalThis.process.execPath, ["apps/temporal-worker/dist/worker.js"], {
      ...env, AGAT_COORDINATOR_INTERNAL_URL: await listen(proxy), AGAT_TEMPORAL_METRICS_ADDRESS: "127.0.0.1:0",
      AGAT_TEMPORAL_WORKER_ID: "scheduled-cancel-fixture-worker" }); children.push(worker); await worker.ready(/Temporal worker слушает/);
    connection = await Connection.connect({ address, connectTimeout: 5_000 });
    const client = new Client({ connection, namespace: "default", identity: "scheduled-cancel-fixture-client" });
    const workflowId = `scheduled-creation-cancel-${randomUUID()}`;
    const parent = await client.workflow.start("agatScheduledProcessWorkflow", { workflowId, taskQueue,
      args: [{ processId: String(process.id), projectId: "default", input: "Synthetic", priority: 50, knowledgeCollectionIds: [] }] });
    const outcome = parent.result().then(value => ({ value }), error => ({ error: String(error), cause: String(error.cause) }));
    await eventually(() => entered, "Start request did not reach the selected cancellation boundary");
    assert.equal(store.listProcessInstances().length, boundary === "before-commit" ? 0 : 1);
    if (boundary === "after-commit-compensating") {
      const effect = store.leaseNext(workerId)!; assert.equal(effect.activity?.kind, "http");
      store.completeLease(workerId, effect.leaseId, "Synthetic effect completed");
    }
    await parent.cancel();
    if (boundary === "after-commit-compensating") {
      await eventually(async () => (await parent.fetchHistory()).events?.some(event => Boolean(event.timerStartedEventAttributes)) ?? false,
        "Parent did not begin polling the compensation before a child exists");
      assert.equal(store.listProcessInstances()[0]!.status, "compensating");
      assert.equal((await parent.describe()).status.name, "RUNNING");
      const undo = store.leaseNext(workerId)!; assert.equal(undo.activity?.request.url, "https://example.test/undo");
      store.completeLease(workerId, undo.leaseId, "Synthetic compensation completed");
    }
    const parentOutcome = await within(outcome, "Parent cancellation did not settle", 75_000);
    releaseStart(); releaseReply();
    await eventually(() => Boolean(startReply), "Delayed start did not reach coordinator");
    const instances = store.listProcessInstances().map(row => ({ id: row.id, status: row.status }));
    for (const row of instances) await assert.rejects(client.workflow.getHandle(`agat-process-${row.id}`).describe(), WorkflowNotFoundError);
    if (boundary === "after-commit-compensating") {
      assert.deepEqual(store.getProcessInstance(String(instances[0]!.id))!.compensations,
        [{ processNodeId: "effect", sequence: 0, status: "completed", error: null }]);
    }
    const history = JSON.parse(historyToJSON(await parent.fetchHistory()), (key, value) => key === "identity" ? "scheduled-cancel-fixture" : value);
    const evidence = globalThis.process.env.AGAT_TEMPORAL_CREATION_CANCEL_EVIDENCE_DIR;
    if (evidence) {
      const target = path.resolve(evidence); assert.ok(target.startsWith(path.join(root, "docs") + path.sep)); fs.mkdirSync(target, { recursive: true });
      fs.writeFileSync(path.join(target, `${boundary}.json`), JSON.stringify({ boundary, workflowId, cancelCalls, startReply,
        instances, parentOutcome, parentStatus: (await parent.describe()).status.name, history }, null, 2) + "\n", { flag: "wx" });
    }
    assert.equal((await parent.describe()).status.name, "CANCELLED");
    assert.equal(instances.length, boundary === "before-commit" ? 0 : 1);
    if (boundary === "before-commit") {
      assert.equal(startReply!.status, 409); assert.equal(startReply!.body.code, "SCHEDULED_START_CANCELLED");
    } else assert.equal(instances[0]!.status, "cancelled");
    assert.equal(cancelCalls, 2); assert.deepEqual(proxyErrors, []);
    await Worker.runReplayHistory({ workflowBundle: { codePath: path.join(root, "apps/temporal-worker/dist/workflow-bundle.js") } }, history, workflowId);
    assert.deepEqual(await worker.stop(), { code: 0, signal: null }, worker.log());
    assert.deepEqual(await coordinator.stop(), { code: 0, signal: null }, coordinator.log());
  } finally {
    releaseStart(); releaseReply();
    for (const child of children.reverse()) await child.stop();
    await close(proxy); await connection?.close(); store.close(); fs.rmSync(directory, { recursive: true, force: true });
  }
});
}
