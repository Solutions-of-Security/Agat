import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import test from "node:test";
import { Client, Connection } from "@temporalio/client";
import { historyToJSON } from "@temporalio/common/lib/proto-utils.js";
import { Worker } from "@temporalio/worker";
import { AgatStore } from "../src/database.js";
import { root, cleanEnv, processChild, within, eventually, listen, close } from "./helpers/temporal.js";

const address = process.env.AGAT_TEST_TEMPORAL_ADDRESS;

for (const mode of ["recover", "cancel"] as const) {
  test(`Temporal process tick survives a long outage with ${mode}`, { skip: !address, timeout: 210_000 }, async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-process-retry-"));
    const dbPath = path.join(directory, "state.sqlite"), artifacts = path.join(directory, "artifacts");
    const store = new AgatStore(dbPath, { seedDemo: false, temporalProcesses: true, artifactsDir: artifacts });
    const children: ReturnType<typeof processChild>[] = [];
    const taskQueue = `process-retry-${randomUUID()}`;
    let connection: Connection | undefined, coordinatorUrl = "", instanceId = "", firstTickAt = 0, recovered = false;
    let restarts = 0, cancels = 0;
    const ticks: Array<{ elapsedMs: number; status: number }> = [], proxyErrors: unknown[] = [];
    const proxy = http.createServer(async (req, res) => {
      try {
        assert.equal(req.method, "POST"); assert.equal(req.headers["x-agat-temporal-token"], "tick-retry-internal");
        assert.match(req.url!, /^\/api\/v1\/internal\/processes\/[^/]+\/(scheduled-start|tick|cancel)$/);
        let body = ""; for await (const chunk of req) body += chunk;
        const ticking = req.url!.endsWith("/tick"), cancelling = req.url!.endsWith("/cancel");
        if (ticking) {
          assert.ok(req.url!.includes(instanceId));
          if (firstTickAt === 0) firstTickAt = performance.now();
          const elapsedMs = performance.now() - firstTickAt;
          if (ticks.length && elapsedMs < 125_000) {
            ticks.push({ elapsedMs, status: 503 });
            res.writeHead(503, { "content-type": "application/json" }).end(JSON.stringify({ error: "Synthetic tick outage" })); return;
          }
        }
        const key = req.headers["idempotency-key"];
        const result = await fetch(`${coordinatorUrl}${req.url}`, { method: "POST", body,
          headers: { "content-type": "application/json", "x-agat-temporal-token": "tick-retry-internal",
            ...(typeof key === "string" ? { "idempotency-key": key } : {}) }, signal: AbortSignal.timeout(5_000) });
        const text = await result.text(); assert.ok(result.ok, text);
        if (req.url!.endsWith("/scheduled-start")) instanceId = JSON.parse(text).instanceId;
        if (ticking) {
          const first = ticks.length === 0;
          ticks.push({ elapsedMs: performance.now() - firstTickAt, status: first ? 503 : result.status });
          if (first) {
            res.writeHead(503, { "content-type": "application/json" }).end(JSON.stringify({ error: "Synthetic lost tick response" })); return;
          }
          recovered = true;
        }
        if (cancelling && ++cancels === 1) {
          res.writeHead(503, { "content-type": "application/json" }).end(JSON.stringify({ error: "Synthetic lost cancellation response" })); return;
        }
        res.writeHead(result.status, { "content-type": "application/json" }).end(text);
      } catch (error) { proxyErrors.push(error); res.writeHead(502).end(); }
    });
    try {
      const process = store.createProcess({ name: "Process tick retry", graph: {
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
        AGAT_REQUIRE_WORKER_RUNTIME_ATTESTATION: "false", AGAT_ADMIN_TOKEN: "tick-retry-admin", AGAT_TEMPORAL_ENABLED: "true",
        AGAT_TEMPORAL_ADDRESS: address!, AGAT_TEMPORAL_NAMESPACE: "default", AGAT_TEMPORAL_TASK_QUEUE: taskQueue,
        AGAT_TEMPORAL_INTERNAL_TOKEN: "tick-retry-internal" };
      const coordinator = processChild(globalThis.process.execPath, ["apps/coordinator/dist/server.js"], env); children.push(coordinator);
      coordinatorUrl = `http://127.0.0.1:${(await coordinator.ready(/АГАТ слушает http:\/\/127\.0\.0\.1:(\d+)/))[1]}`;
      const workerEnv = { ...env, AGAT_COORDINATOR_INTERNAL_URL: await listen(proxy), AGAT_TEMPORAL_METRICS_ADDRESS: "127.0.0.1:0",
        AGAT_TEMPORAL_WORKER_ID: "tick-retry-fixture-worker" };
      let worker = processChild(globalThis.process.execPath, ["apps/temporal-worker/dist/worker.js"], workerEnv);
      children.push(worker); await worker.ready(/Temporal worker слушает/);
      connection = await Connection.connect({ address, connectTimeout: 5_000 });
      const client = new Client({ connection, namespace: "default", identity: "tick-retry-fixture-client" });
      const workflowId = `process-retry-${randomUUID()}`;
      const parent = await client.workflow.start("agatScheduledProcessWorkflow", { workflowId, taskQueue,
        args: [{ processId: String(process.id), projectId: "default", input: "Synthetic", priority: 50, knowledgeCollectionIds: [] }] });
      let finished = false;
      const outcome = parent.result().then(value => ({ value }), error => ({ error: String(error), cause: String(error.cause) }))
        .finally(() => { finished = true; });
      await eventually(() => ticks.length >= 3, "The tick Activity did not retry");
      const child = client.workflow.getHandle(`agat-process-${instanceId}`);
      const childRunId = (await child.describe()).runId;
      // Updates remain acknowledged while the Activity is retrying. This wake
      // must neither create another instance nor replace the pending Activity.
      const wake = await child.executeUpdate<{ acceptedRevision: number }, [string]>("processChangedV1", { args: ["outage-observed"] });
      assert.ok(wake.acceptedRevision > 0);
      if (mode === "cancel") await parent.cancel();
      else {
        await within((async () => {
          while (!finished && !recovered) {
            if (!restarts && ticks.length >= 9) {
              assert.equal((await parent.describe()).status.name, "RUNNING");
              assert.equal((await child.describe()).status.name, "RUNNING");
              assert.equal(store.getProcessInstance(instanceId)!.status, "waiting_external");
              assert.deepEqual(await worker.stop("SIGKILL"), { code: null, signal: "SIGKILL" });
              worker = processChild(globalThis.process.execPath, ["apps/temporal-worker/dist/worker.js"], workerEnv);
              children.push(worker); await worker.ready(/Temporal worker слушает/); restarts += 1;
            }
            await delay(200);
          }
        })(), "Neither tick recovery nor parent failure occurred", 170_000);
        if (recovered) {
          const response = await fetch(`${coordinatorUrl}/api/v1/processes/${process.id}/signals/resume`, { method: "POST",
            headers: { "content-type": "application/json", "x-agat-admin-token": "tick-retry-admin" },
            body: JSON.stringify({ instanceId, payload: { resumed: true } }), signal: AbortSignal.timeout(5_000) });
          assert.equal(response.status, 202, await response.text());
        }
      }
      const parentOutcome = await within(outcome, "Parent did not settle after tick recovery/cancellation");
      const normalize = (value: unknown) => JSON.parse(historyToJSON(value as Parameters<typeof historyToJSON>[0]), (key, data) => key === "identity" ? "tick-retry-fixture" : data);
      const parentHistory = normalize(await parent.fetchHistory()), childHistory = normalize(await child.fetchHistory());
      const observed = { workflowId, instanceId, ticks, restarts, cancels, parentOutcome, parentStatus: (await parent.describe()).status.name,
        childStatus: (await child.describe()).status.name, childRunId, finalChildRunId: (await child.describe()).runId,
        applicationStatus: store.getProcessInstance(instanceId)!.status, instances: store.listProcessInstances().length, parentHistory, childHistory };
      const evidence = globalThis.process.env.AGAT_TEMPORAL_TICK_RETRY_EVIDENCE_DIR;
      if (evidence) {
        const target = path.resolve(evidence); assert.ok(target.startsWith(path.join(root, "docs") + path.sep)); fs.mkdirSync(target, { recursive: true });
        fs.writeFileSync(path.join(target, `${mode}.json`), JSON.stringify(observed, null, 2) + "\n", { flag: "wx" });
      }
      assert.equal(observed.parentStatus, mode === "recover" ? "COMPLETED" : "CANCELLED", "Transient tick failures must not abandon application state");
      assert.equal(observed.childStatus, observed.parentStatus);
      assert.equal(observed.applicationStatus, mode === "recover" ? "completed" : "cancelled");
      assert.equal(observed.instances, 1); assert.equal(observed.childRunId, observed.finalChildRunId);
      if (mode === "recover") { assert.equal(restarts, 1); assert.ok(ticks.length > 8); assert.ok(ticks.find(row => row.status === 200)!.elapsedMs > 120_000); }
      else { assert.equal(cancels, 2); assert.equal(recovered, false); }
      assert.deepEqual(proxyErrors, []);
      for (const [id, history] of [[workflowId, parentHistory], [`agat-process-${instanceId}`, childHistory]] as const) {
        await Worker.runReplayHistory({ workflowBundle: { codePath: path.join(root, "apps/temporal-worker/dist/workflow-bundle.js") } }, history, id);
      }
      assert.deepEqual(await worker.stop(), { code: 0, signal: null }, worker.log());
      assert.deepEqual(await coordinator.stop(), { code: 0, signal: null }, coordinator.log());
    } finally {
      for (const child of children.reverse()) await child.stop();
      await close(proxy); await connection?.close(); store.close(); fs.rmSync(directory, { recursive: true, force: true });
    }
  });
}
