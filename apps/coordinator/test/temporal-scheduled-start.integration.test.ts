import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { Client, Connection } from "@temporalio/client";
import { historyToJSON } from "@temporalio/common/lib/proto-utils.js";
import { Worker } from "@temporalio/worker";
import { AgatStore } from "../src/database.js";
import { root, cleanEnv, processChild, within, listen, close } from "./helpers/temporal.js";

const address = process.env.AGAT_TEST_TEMPORAL_ADDRESS;

test("Temporal scheduled-start retries a lost response with one durable instance and independent subsequent occurrences",
  { skip: !address, timeout: 90_000 }, async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-temporal-scheduled-"));
  const dbPath = path.join(directory, "state.sqlite"), artifacts = path.join(directory, "artifacts");
  const store = new AgatStore(dbPath, { seedDemo: false, temporalProcesses: true, artifactsDir: artifacts });
  const children: ReturnType<typeof processChild>[] = [];
  const taskQueue = `scheduled-${randomUUID()}`;
  let coordinatorUrl = "", connection: Connection | undefined, loseReply = true;
  const starts: Array<{ key: string; body: unknown; response: { instanceId: string; processId: string; projectId: string }; dropped: boolean }> = [];
  const errors: unknown[] = [];
  let activityRequests = 0;
  let transientConflicts = 0, permanentConflicts = false, rejectedRequests = 0;
  const proxy = http.createServer(async (req, res) => {
    try {
      assert.equal(req.method, "POST"); assert.equal(req.headers["x-agat-temporal-token"], "scheduled-internal");
      assert.match(req.url!, /^\/api\/v1\/internal\/processes\/[^/]+\/(tick|scheduled-start)$/);
      activityRequests++;
      let raw = ""; for await (const chunk of req) raw += chunk;
      const key = req.headers["idempotency-key"];
      if (req.url!.endsWith("/scheduled-start") && (transientConflicts > 0 || permanentConflicts)) {
        rejectedRequests++;
        if (transientConflicts > 0) transientConflicts--;
        res.writeHead(409, { "content-type": "application/json" }).end(JSON.stringify({ error: "Fixture conflict",
          ...(permanentConflicts ? { code: "SCHEDULED_START_IDEMPOTENCY_CONFLICT" } : { preflight: { queueable: false } }) }));
        return;
      }
      const result = await fetch(`${coordinatorUrl}${req.url}`, { method: "POST", body: raw,
        headers: { "content-type": "application/json", "x-agat-temporal-token": "scheduled-internal",
          ...(typeof key === "string" ? { "idempotency-key": key } : {}) }, signal: AbortSignal.timeout(5_000) });
      const text = await result.text(); assert.ok(result.ok, text);
      if (req.url!.endsWith("/scheduled-start")) {
        assert.equal(typeof key, "string"); assert.match(key as string, /^agat-scheduled-v1:[a-f0-9]{64}$/);
        starts.push({ key: key as string, body: JSON.parse(raw), response: JSON.parse(text), dropped: loseReply });
        // Discard the response after the coordinator committed both the run and receipt.
        if (loseReply) { loseReply = false; res.destroy(); return; }
      }
      res.writeHead(result.status, { "content-type": "application/json" }).end(text);
    } catch (error) { errors.push(error); res.writeHead(502).end(); }
  });
  try {
    const process = store.createProcess({ name: "Scheduled recovery", graph: {
      nodes: [
        { id: "start", type: "start", name: "Start", position: { x: 0, y: 0 }, config: {} },
        { id: "end", type: "end", name: "End", position: { x: 200, y: 0 }, config: {} },
      ], edges: [{ id: "e", source: "start", target: "end", branch: "default" }],
    } }); store.publishProcess(String(process.id));
    const env = { ...cleanEnv(), AGAT_HOST: "127.0.0.1", AGAT_PORT: "0", AGAT_DB_PATH: dbPath,
      AGAT_ARTIFACTS_DIR: artifacts, AGAT_SEED_DEMO: "false", AGAT_SERVE_WEB: "false", AGAT_MCP_ENABLED: "false",
      AGAT_A2A_ENABLED: "false", AGAT_SANDBOX_ENABLED: "false", AGAT_LOCAL_WORKER_LAUNCHER: "false",
      AGAT_REQUIRE_SIGNED_WORKER_RELEASES: "false", AGAT_REQUIRE_WORKER_PROVENANCE: "false",
      AGAT_REQUIRE_WORKER_RUNTIME_ATTESTATION: "false", AGAT_TEMPORAL_ENABLED: "true",
      AGAT_TEMPORAL_ADDRESS: address!, AGAT_TEMPORAL_NAMESPACE: "default", AGAT_TEMPORAL_TASK_QUEUE: taskQueue,
      AGAT_TEMPORAL_INTERNAL_TOKEN: "scheduled-internal" };
    const coordinator = processChild(globalThis.process.execPath, ["apps/coordinator/dist/server.js"], env); children.push(coordinator);
    coordinatorUrl = `http://127.0.0.1:${(await coordinator.ready(/АГАТ слушает http:\/\/127\.0\.0\.1:(\d+)/))[1]}`;
    const proxyUrl = await listen(proxy);
    const worker = processChild(globalThis.process.execPath, ["apps/temporal-worker/dist/worker.js"], {
      ...env, AGAT_COORDINATOR_INTERNAL_URL: proxyUrl, AGAT_TEMPORAL_METRICS_ADDRESS: "127.0.0.1:0",
      AGAT_TEMPORAL_WORKER_ID: "scheduled-retry-fixture" }); children.push(worker);
    await worker.ready(/Temporal worker слушает/);
    connection = await Connection.connect({ address, connectTimeout: 5_000 });
    const client = new Client({ connection, namespace: "default", identity: "scheduled-fixture-client" });
    const input = { processId: String(process.id), projectId: "default", input: "Synthetic scheduled input", priority: 50, knowledgeCollectionIds: [] };
    const workflowId = `scheduled-parent-${randomUUID()}`;
    const handle = await client.workflow.start("agatScheduledProcessWorkflow", { workflowId, taskQueue, args: [input] });
    assert.equal((await within(handle.result(), "Scheduled retry did not finish") as { status: string }).status, "completed");
    assert.equal(starts.length, 2); assert.deepEqual(starts[0]!.response, starts[1]!.response);
    assert.equal(starts[0]!.key, starts[1]!.key); assert.equal(starts[0]!.dropped, true);
    assert.deepEqual(starts[0]!.body, starts[1]!.body);
    assert.equal(store.listProcessInstances().length, 1);
    const history = await handle.fetchHistory();
    assert.ok(history.events?.some(event => Number(event.activityTaskStartedEventAttributes?.attempt) === 2));
    const scheduled = history.events?.find(event => event.activityTaskScheduledEventAttributes)?.activityTaskScheduledEventAttributes;
    assert.equal(scheduled?.activityType?.name, "startScheduledProcess");
    assert.equal(starts[0]!.key, "agat-scheduled-v1:" + createHash("sha256").update(JSON.stringify([
      "default", handle.firstExecutionRunId, scheduled!.activityId,
    ])).digest("hex"));
    const childId = `agat-process-${starts[0]!.response.instanceId}`;
    assert.equal(history.events?.filter(event => event.childWorkflowExecutionStartedEventAttributes).length, 1);
    assert.equal(history.events?.find(event => event.childWorkflowExecutionStartedEventAttributes)
      ?.childWorkflowExecutionStartedEventAttributes?.workflowExecution?.workflowId, childId);
    const json = JSON.parse(historyToJSON(history), (key, value) => key === "identity" ? "scheduled-fixture" : value);
    const checkpoint = { activityRequests, instances: JSON.stringify(store.listProcessInstances()), events: JSON.stringify(store.listEvents(0, 100)) };
    await Worker.runReplayHistory({ workflowBundle: { codePath: path.join(root, "apps/temporal-worker/dist/workflow-bundle.js") } }, json, workflowId);
    assert.deepEqual({ activityRequests, instances: JSON.stringify(store.listProcessInstances()), events: JSON.stringify(store.listEvents(0, 100)) }, checkpoint);
    const next = await client.workflow.start("agatScheduledProcessWorkflow", { workflowId: `${workflowId}-next`, taskQueue, args: [input] });
    assert.equal((await within(next.result(), "Next occurrence did not finish") as { status: string }).status, "completed");
    assert.equal(starts.length, 3); assert.notEqual(starts[2]!.key, starts[0]!.key);
    assert.notEqual(starts[2]!.response.instanceId, starts[0]!.response.instanceId);
    assert.equal(store.listProcessInstances().length, 2); assert.deepEqual(errors, []);
    transientConflicts = 1;
    const temporary = await client.workflow.start("agatScheduledProcessWorkflow", { workflowId: `${workflowId}-temporary`, taskQueue, args: [input] });
    assert.equal((await within(temporary.result(), "Transient preflight conflict was not retried") as { status: string }).status, "completed");
    const transientHistory = await temporary.fetchHistory();
    assert.ok(transientHistory.events?.some(event => Number(event.activityTaskStartedEventAttributes?.attempt) === 2));
    assert.equal(rejectedRequests, 1); assert.equal(store.listProcessInstances().length, 3);
    permanentConflicts = true;
    const conflict = await client.workflow.start("agatScheduledProcessWorkflow", { workflowId: `${workflowId}-conflict`, taskQueue, args: [input] });
    await assert.rejects(within(conflict.result(), "Permanent conflict was retried"), (error: any) =>
      error.cause?.cause?.type === "CoordinatorRequestError" && error.cause?.cause?.nonRetryable === true);
    assert.equal(rejectedRequests, 2, "A permanent conflict must finish after one Activity attempt");
    assert.equal(store.listProcessInstances().length, 3);
    assert.deepEqual(errors, []);
    assert.deepEqual(await worker.stop(), { code: 0, signal: null }, worker.log());
    assert.deepEqual(await coordinator.stop(), { code: 0, signal: null }, coordinator.log());
    const evidence = globalThis.process.env.AGAT_TEMPORAL_SCHEDULED_EVIDENCE_DIR;
    if (evidence) {
      const target = path.resolve(evidence); assert.ok(target.startsWith(path.join(root, "docs") + path.sep));
      fs.mkdirSync(target, { recursive: true });
      fs.writeFileSync(path.join(target, "recovery.json"), JSON.stringify({ workflowId, starts, history: json,
        transientHistory: JSON.parse(historyToJSON(transientHistory), (key, value) => key === "identity" ? "scheduled-fixture" : value),
        assertions: { realActivityRetry: true, singleInstance: true, singleChild: true, distinctOccurrence: true,
          nativeReplay: true, transientConflictRetried: true, permanentConflictNotRetried: true } }, null, 2) + "\n", { flag: "wx" });
    }
  } finally {
    for (const child of children.reverse()) await child.stop();
    await close(proxy); await connection?.close(); store.close(); fs.rmSync(directory, { recursive: true, force: true });
  }
});
