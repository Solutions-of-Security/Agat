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
import { root, cleanEnv, processChild, within, eventually, listen, close } from "./helpers/temporal.js";

const address = process.env.AGAT_TEST_TEMPORAL_ADDRESS;

test("Temporal scheduled child keeps its parent ownership across coordinator restart before and after child start",
  { skip: !address, timeout: 90_000 }, async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-scheduled-owner-"));
  const dbPath = path.join(directory, "state.sqlite"), artifacts = path.join(directory, "artifacts");
  const store = new AgatStore(dbPath, { seedDemo: false, temporalProcesses: true, artifactsDir: artifacts });
  const children: ReturnType<typeof processChild>[] = [];
  const taskQueue = `scheduled-owner-${randomUUID()}`;
  let coordinatorUrl = "", connection: Connection | undefined;
  let held: http.ServerResponse | undefined, instanceId = "", starts = 0;
  let allowTick = Promise.resolve(), releaseTicks: (() => void) | undefined;
  let activeTicks = 0;
  const proxyErrors: unknown[] = [];
  const proxy = http.createServer(async (req, res) => {
    let activeTick = false;
    try {
      assert.equal(req.method, "POST"); assert.equal(req.headers["x-agat-temporal-token"], "owner-internal");
      assert.match(req.url!, /^\/api\/v1\/internal\/processes\/[^/]+\/(tick|scheduled-start)$/);
      if (req.url!.endsWith("/tick")) {
        await allowTick;
        activeTicks++; activeTick = true;
      }
      let body = ""; for await (const chunk of req) body += chunk;
      const key = req.headers["idempotency-key"];
      const result = await fetch(`${coordinatorUrl}${req.url}`, { method: "POST", body,
        headers: { "content-type": "application/json", "x-agat-temporal-token": "owner-internal",
          ...(typeof key === "string" ? { "idempotency-key": key } : {}) }, signal: AbortSignal.timeout(5_000) });
      const text = await result.text(); assert.ok(result.ok, text);
      if (req.url!.endsWith("/scheduled-start")) {
        starts++;
        if (starts === 1) {
          instanceId = JSON.parse(text).instanceId;
          allowTick = new Promise<void>(resolve => { releaseTicks = resolve; });
          held = res; return;
        }
        assert.equal(JSON.parse(text).instanceId, instanceId);
      }
      res.writeHead(result.status, { "content-type": "application/json" }).end(text);
    } catch (error) { proxyErrors.push(error); res.writeHead(502).end(); }
    finally { if (activeTick) activeTicks--; }
  });
  try {
    const process = store.createProcess({ name: "Scheduled ownership recovery", graph: {
      nodes: [
        { id: "start", type: "start", name: "Start", position: { x: 0, y: 0 }, config: {} },
        { id: "signal", type: "signal", name: "Resume", position: { x: 100, y: 0 }, config: { signalName: "resume", signalTimeoutSeconds: 3600 } },
        { id: "end", type: "end", name: "End", position: { x: 200, y: 0 }, config: {} },
      ], edges: [{ id: "a", source: "start", target: "signal", branch: "default" }, { id: "b", source: "signal", target: "end", branch: "default" }],
    } }); store.publishProcess(String(process.id));
    const env = { ...cleanEnv(), AGAT_HOST: "127.0.0.1", AGAT_PORT: "0", AGAT_DB_PATH: dbPath,
      AGAT_ARTIFACTS_DIR: artifacts, AGAT_SEED_DEMO: "false", AGAT_SERVE_WEB: "false", AGAT_MCP_ENABLED: "false",
      AGAT_A2A_ENABLED: "false", AGAT_SANDBOX_ENABLED: "false", AGAT_LOCAL_WORKER_LAUNCHER: "false",
      AGAT_REQUIRE_SIGNED_WORKER_RELEASES: "false", AGAT_REQUIRE_WORKER_PROVENANCE: "false",
      AGAT_REQUIRE_WORKER_RUNTIME_ATTESTATION: "false", AGAT_ADMIN_TOKEN: "owner-admin", AGAT_TEMPORAL_ENABLED: "true",
      AGAT_TEMPORAL_ADDRESS: address!, AGAT_TEMPORAL_NAMESPACE: "default", AGAT_TEMPORAL_TASK_QUEUE: taskQueue,
      AGAT_TEMPORAL_INTERNAL_TOKEN: "owner-internal" };
    const startCoordinator = async () => {
      const child = processChild(globalThis.process.execPath, ["apps/coordinator/dist/server.js"], env); children.push(child);
      coordinatorUrl = `http://127.0.0.1:${(await child.ready(/АГАТ слушает http:\/\/127\.0\.0\.1:(\d+)/))[1]}`;
      return child;
    };
    const first = await startCoordinator();
    const proxyUrl = await listen(proxy);
    const worker = processChild(globalThis.process.execPath, ["apps/temporal-worker/dist/worker.js"], {
      ...env, AGAT_COORDINATOR_INTERNAL_URL: proxyUrl, AGAT_TEMPORAL_METRICS_ADDRESS: "127.0.0.1:0",
      AGAT_TEMPORAL_WORKER_ID: "scheduled-owner-worker" }); children.push(worker); await worker.ready(/Temporal worker слушает/);
    connection = await Connection.connect({ address, connectTimeout: 5_000 });
    const client = new Client({ connection, namespace: "default", identity: "owner-fixture-client" });
    const workflowId = `scheduled-owner-${randomUUID()}`;
    const parent = await client.workflow.start("agatScheduledProcessWorkflow", { workflowId, taskQueue,
      args: [{ processId: String(process.id), projectId: "default", input: "Synthetic", priority: 50, knowledgeCollectionIds: [] }] });
    // Attach a rejection handler immediately: the baseline parent fails before
    // we finish observing the orphan that reconciliation created.
    const outcome = parent.result().then(value => ({ value }), error => ({ error: String(error), cause: String(error.cause) }));
    await eventually(() => Boolean(held), "Scheduled-start did not commit its instance");
    const child = client.workflow.getHandle(`agat-process-${instanceId}`);
    await assert.rejects(child.describe(), WorkflowNotFoundError);
    const manualInstance = store.startProcess(String(process.id), { input: "Manual recovery fixture" })!;
    const manual = client.workflow.getHandle(`agat-process-${manualInstance.id}`);
    await assert.rejects(manual.describe(), WorkflowNotFoundError);
    assert.deepEqual(await first.stop("SIGKILL"), { code: null, signal: "SIGKILL" });
    const second = await startCoordinator(); releaseTicks!(); releaseTicks = undefined;
    const manualStarted = await manual.describe();
    assert.equal(manualStarted.parentExecution, undefined, "Manual recovery remains the coordinator's responsibility");
    const beforeRetry = await child.describe().then(value => ({ exists: true, runId: value.runId, parent: value.parentExecution ?? null }),
      error => { if (error instanceof WorkflowNotFoundError) return { exists: false }; throw error; });
    held!.destroy(); held = undefined;
    await eventually(async () => {
      const history = await parent.fetchHistory();
      return Boolean(history.events?.some(event => event.childWorkflowExecutionStartedEventAttributes || event.workflowExecutionFailedEventAttributes));
    }, "Parent neither started its child nor reported a conflict");
    const startedChild = await child.describe();
    // A second restart exercises the already-started child boundary as well.
    allowTick = new Promise<void>(resolve => { releaseTicks = resolve; });
    // A tick may already have passed the gate when the child-start event arrives.
    // Drain it before SIGKILL so the fixture isolates startup ownership rather
    // than accidentally injecting an additional lost tick response.
    await eventually(() => activeTicks === 0, "In-flight ticks did not drain before restart");
    assert.deepEqual(await second.stop("SIGKILL"), { code: null, signal: "SIGKILL" });
    const third = await startCoordinator(); releaseTicks!(); releaseTicks = undefined;
    assert.equal((await child.describe()).runId, startedChild.runId);
    assert.equal((await manual.describe()).runId, manualStarted.runId);
    for (const targetInstance of [instanceId, String(manualInstance.id)]) {
      const delivered = await fetch(`${coordinatorUrl}/api/v1/processes/${process.id}/signals/resume`, {
        method: "POST", headers: { "content-type": "application/json", "x-agat-admin-token": "owner-admin" },
        body: JSON.stringify({ instanceId: targetInstance, payload: { resumed: true } }), signal: AbortSignal.timeout(5_000) });
      assert.equal(delivered.status, 202, await delivered.clone().text());
    }
    assert.equal((await within(child.result(), "Child did not finish") as { status: string }).status, "completed");
    assert.equal((await within(manual.result(), "Manual workflow did not finish") as { status: string }).status, "completed");
    const parentOutcome = await within(outcome, "Parent did not finish");
    const history = JSON.parse(historyToJSON(await parent.fetchHistory()), (key, value) => key === "identity" ? "owner-fixture" : value);
    const evidence = globalThis.process.env.AGAT_TEMPORAL_OWNER_EVIDENCE_DIR;
    if (evidence) {
      const target = path.resolve(evidence); assert.ok(target.startsWith(path.join(root, "docs") + path.sep)); fs.mkdirSync(target, { recursive: true });
      fs.writeFileSync(path.join(target, "recovery.json"), JSON.stringify({ workflowId, instanceId, starts, beforeRetry,
        childRunId: startedChild.runId, childParent: startedChild.parentExecution ?? null, parentOutcome, history,
        manual: { instanceId: manualInstance.id, runId: manualStarted.runId, parent: manualStarted.parentExecution ?? null } }, null, 2) + "\n", { flag: "wx" });
    }
    assert.equal(beforeRetry.exists, false, "Coordinator reconciliation must not start the parent's scheduled child");
    assert.deepEqual(startedChild.parentExecution, { workflowId, runId: parent.firstExecutionRunId });
    assert.equal((parentOutcome as { value: { status: string } }).value?.status, "completed");
    assert.equal(starts, 2); assert.equal(store.listProcessInstances().length, 2); assert.deepEqual(proxyErrors, []);
    await Worker.runReplayHistory({ workflowBundle: { codePath: path.join(root, "apps/temporal-worker/dist/workflow-bundle.js") } }, history, workflowId);
    assert.deepEqual(await worker.stop(), { code: 0, signal: null }, worker.log());
    assert.deepEqual(await third.stop(), { code: 0, signal: null }, third.log());
  } finally {
    held?.destroy(); releaseTicks?.();
    for (const child of children.reverse()) await child.stop();
    await close(proxy); await connection?.close(); store.close(); fs.rmSync(directory, { recursive: true, force: true });
  }
});
