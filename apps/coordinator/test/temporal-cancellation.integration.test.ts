import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { Client, Connection } from "@temporalio/client";
import { historyToJSON } from "@temporalio/common/lib/proto-utils.js";
import { Worker } from "@temporalio/worker";
import { AgatStore } from "../src/database.js";
import type { ProcessGraph } from "../src/types.js";
import { root, cleanEnv, processChild, within, eventually, listen, close } from "./helpers/temporal.js";

const address = process.env.AGAT_TEST_TEMPORAL_ADDRESS;

for (const withCompensation of [false, true]) {
test(`Temporal parent cancellation settles application state${withCompensation ? " after compensation and worker restart" : ""}`,
  { skip: !address, timeout: 90_000 }, async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-temporal-cancel-"));
  const dbPath = path.join(directory, "state.sqlite"), artifacts = path.join(directory, "artifacts");
  const store = new AgatStore(dbPath, { seedDemo: false, temporalProcesses: true, artifactsDir: artifacts });
  const children: ReturnType<typeof processChild>[] = [];
  const taskQueue = `cancel-${randomUUID()}`;
  let connection: Connection | undefined, coordinatorUrl = "", instanceId = "", cancelCalls = 0;
  let heldCancel: http.ServerResponse | undefined;
  const proxyErrors: unknown[] = [];
  const proxy = http.createServer(async (req, res) => {
    try {
      assert.equal(req.method, "POST"); assert.equal(req.headers["x-agat-temporal-token"], "cancel-internal");
      assert.match(req.url!, /^\/api\/v1\/internal\/processes\/[^/]+\/(tick|scheduled-start|cancel)$/);
      let body = ""; for await (const chunk of req) body += chunk;
      const key = req.headers["idempotency-key"];
      const result = await fetch(`${coordinatorUrl}${req.url}`, { method: "POST", body,
        headers: { "content-type": "application/json", "x-agat-temporal-token": "cancel-internal",
          ...(typeof key === "string" ? { "idempotency-key": key } : {}) }, signal: AbortSignal.timeout(5_000) });
      const text = await result.text(); assert.ok(result.ok, text);
      if (req.url!.endsWith("/scheduled-start")) instanceId = JSON.parse(text).instanceId;
      if (req.url!.endsWith("/cancel") && ++cancelCalls === 1) {
        if (withCompensation) heldCancel = res; else res.destroy();
        return;
      }
      res.writeHead(result.status, { "content-type": "application/json" }).end(text);
    } catch (error) { proxyErrors.push(error); res.writeHead(502).end(); }
  });
  try {
    const graph: ProcessGraph = {
      nodes: [
        { id: "start", type: "start", name: "Start", position: { x: 0, y: 0 }, config: {} },
        { id: "signal", type: "signal", name: "Hold", position: { x: 100, y: 0 }, config: { signalName: "resume", signalTimeoutSeconds: 3600 } },
        { id: "end", type: "end", name: "End", position: { x: 200, y: 0 }, config: {} },
      ], edges: [{ id: "a", source: "start", target: "signal", branch: "default" }, { id: "b", source: "signal", target: "end", branch: "default" }],
    };
    const workerId = store.registerNode({ enrollmentToken: "unused", name: "Synthetic compensation worker", platform: "test",
      models: [], maxConcurrency: 1, agentRuntimes: ["single"] }).id;
    if (withCompensation) {
      graph.nodes.splice(1, 0, { id: "effect", type: "http", name: "Synthetic side effect", position: { x: 50, y: 0 }, config: {
        method: "POST", url: "https://example.test/effect", body: "{}", headers: {}, timeoutSeconds: 10,
        compensation: { method: "POST", url: "https://example.test/undo", headers: {}, body: "{}", timeoutSeconds: 10 },
      } });
      graph.edges = [{ id: "create", source: "start", target: "effect", branch: "default" },
        { id: "hold", source: "effect", target: "signal", branch: "default" }, graph.edges[1]!];
    }
    const process = store.createProcess({ name: "Cancellation fixture", graph }); store.publishProcess(String(process.id));
    const env = { ...cleanEnv(), AGAT_HOST: "127.0.0.1", AGAT_PORT: "0", AGAT_DB_PATH: dbPath,
      AGAT_ARTIFACTS_DIR: artifacts, AGAT_SEED_DEMO: "false", AGAT_SERVE_WEB: "false", AGAT_MCP_ENABLED: "false",
      AGAT_A2A_ENABLED: "false", AGAT_SANDBOX_ENABLED: "false", AGAT_LOCAL_WORKER_LAUNCHER: "false",
      AGAT_REQUIRE_SIGNED_WORKER_RELEASES: "false", AGAT_REQUIRE_WORKER_PROVENANCE: "false",
      AGAT_REQUIRE_WORKER_RUNTIME_ATTESTATION: "false", AGAT_ADMIN_TOKEN: "cancel-admin", AGAT_TEMPORAL_ENABLED: "true",
      AGAT_TEMPORAL_ADDRESS: address!, AGAT_TEMPORAL_NAMESPACE: "default", AGAT_TEMPORAL_TASK_QUEUE: taskQueue,
      AGAT_TEMPORAL_INTERNAL_TOKEN: "cancel-internal" };
    const coordinator = processChild(globalThis.process.execPath, ["apps/coordinator/dist/server.js"], env); children.push(coordinator);
    coordinatorUrl = `http://127.0.0.1:${(await coordinator.ready(/АГАТ слушает http:\/\/127\.0\.0\.1:(\d+)/))[1]}`;
    const proxyUrl = await listen(proxy);
    const startWorker = async () => {
      const worker = processChild(globalThis.process.execPath, ["apps/temporal-worker/dist/worker.js"], {
        ...env, AGAT_COORDINATOR_INTERNAL_URL: proxyUrl, AGAT_TEMPORAL_METRICS_ADDRESS: "127.0.0.1:0",
        AGAT_TEMPORAL_WORKER_ID: "cancel-fixture-worker" }); children.push(worker); await worker.ready(/Temporal worker слушает/);
      return worker;
    };
    let worker = await startWorker();
    connection = await Connection.connect({ address, connectTimeout: 5_000 });
    const client = new Client({ connection, namespace: "default", identity: "cancel-fixture-client" });
    const workflowId = `scheduled-cancel-${randomUUID()}`;
    const parent = await client.workflow.start("agatScheduledProcessWorkflow", { workflowId, taskQueue,
      args: [{ processId: String(process.id), projectId: "default", input: "Synthetic", priority: 50, knowledgeCollectionIds: [] }] });
    const outcome = parent.result().then(value => ({ value }), error => ({ error: String(error), cause: String(error.cause) }));
    await eventually(() => Boolean(instanceId), "Scheduled instance was not created");
    const child = client.workflow.getHandle(`agat-process-${instanceId}`);
    if (withCompensation) {
      const lease = store.leaseNext(workerId)!; assert.equal(lease.activity?.kind, "http");
      store.completeLease(workerId, lease.leaseId, "Synthetic effect completed");
    }
    await eventually(async () => {
      try {
        await child.executeUpdate("processChangedV1", { args: ["fixture.effect-completed"] });
        return (await child.query<{ status: string }>("processState"))?.status === "waiting_external";
      }
      catch { return false; }
    }, "Child did not enter its durable wait");
    assert.equal(store.getProcessInstance(instanceId)!.status, "waiting_external");
    await parent.cancel();
    if (withCompensation) {
      await eventually(() => Boolean(heldCancel), "Cancellation did not reach the coordinator");
      assert.equal(store.getProcessInstance(instanceId)!.status, "compensating");
      assert.deepEqual(await worker.stop("SIGKILL"), { code: null, signal: "SIGKILL" });
      heldCancel!.destroy(); heldCancel = undefined;
      worker = await startWorker();
      await eventually(async () => (await child.query<{ status: string }>("processState"))?.status === "compensating",
        "Restarted worker did not recover the pending cancellation Activity");
      assert.equal((await child.describe()).status.name, "RUNNING");
      assert.equal((await parent.describe()).status.name, "RUNNING", "Parent must await child cleanup");
      const undo = store.leaseNext(workerId)!; assert.equal(undo.activity?.kind, "http");
      assert.equal(undo.activity?.request.url, "https://example.test/undo");
      store.completeLease(workerId, undo.leaseId, "Synthetic compensation completed");
      await child.executeUpdate("processChangedV1", { args: ["fixture.compensation-completed"] });
    }
    const parentOutcome = await within(outcome, "Parent did not finish cancellation");
    await within(child.result().catch(() => undefined), "Child did not finish cancellation");
    const histories = await Promise.all([parent, child].map(async handle => ({ workflowId: handle.workflowId,
      history: JSON.parse(historyToJSON(await handle.fetchHistory()), (key, value) => key === "identity" ? "cancel-fixture" : value) })));
    const observed = { instanceId, cancelCalls, withCompensation, compensations: store.getProcessInstance(instanceId)!.compensations, parentOutcome, parentStatus: (await parent.describe()).status.name,
      childStatus: (await child.describe()).status.name, applicationStatus: store.getProcessInstance(instanceId)!.status,
      waitingSignals: store.db.prepare("SELECT COUNT(*) AS count FROM process_signal_waits WHERE instance_id = ? AND status = 'waiting'").get(instanceId)!.count,
      histories };
    const evidence = globalThis.process.env.AGAT_TEMPORAL_CANCEL_EVIDENCE_DIR;
    if (evidence) {
      const target = path.resolve(evidence); assert.ok(target.startsWith(path.join(root, "docs") + path.sep)); fs.mkdirSync(target, { recursive: true });
      fs.writeFileSync(path.join(target, withCompensation ? "compensation.json" : "cancellation.json"), JSON.stringify(observed, null, 2) + "\n", { flag: "wx" });
    }
    assert.equal(observed.childStatus, "CANCELLED"); assert.equal(observed.parentStatus, "CANCELLED");
    assert.equal(observed.applicationStatus, "cancelled", "Temporal cancellation must settle application state");
    if (withCompensation) assert.deepEqual(observed.compensations, [{ processNodeId: "effect", sequence: 0, status: "completed", error: null }]);
    assert.equal(observed.waitingSignals, 0); assert.equal(cancelCalls, 2); assert.deepEqual(proxyErrors, []);
    for (const saved of histories) await Worker.runReplayHistory({ workflowBundle: {
      codePath: path.join(root, "apps/temporal-worker/dist/workflow-bundle.js") } }, saved.history, saved.workflowId);
    assert.deepEqual(await worker.stop(), { code: 0, signal: null }, worker.log());
    assert.deepEqual(await coordinator.stop(), { code: 0, signal: null }, coordinator.log());
  } finally {
    heldCancel?.destroy();
    for (const child of children.reverse()) await child.stop();
    await close(proxy); await connection?.close(); store.close(); fs.rmSync(directory, { recursive: true, force: true });
  }
});
}
