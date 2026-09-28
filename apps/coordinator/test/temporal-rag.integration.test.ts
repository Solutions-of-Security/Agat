import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { Client, Connection } from "@temporalio/client";
import { historyToJSON } from "@temporalio/common/lib/proto-utils.js";
import { Worker } from "@temporalio/worker";
import pg from "pg";
import { root, cleanEnv, processChild, within, eventually, listen, close } from "./helpers/temporal.js";
import { AgatStore } from "../src/database.js";
import { normalizeDecisionShadowConfig } from "../src/local-decisions.js";
import type { ProcessGraph } from "../src/types.js";
import { decisionProfile, digest, type Fixture } from "../../../scripts/lib/decision-primary-workflow.js";
import { verifyIngestion, verifyRetrieval } from "../../../scripts/lib/decision-rag.js";

const address = process.env.AGAT_TEST_TEMPORAL_ADDRESS;
const stateStoreDriver = process.env.AGAT_TEST_TEMPORAL_STATE_STORE ?? "sqlite";
assert.ok(stateStoreDriver === "sqlite" || stateStoreDriver === "postgresql");
const postgres = stateStoreDriver === "postgresql";
const region = postgres ? "eu-test-1" : "local", residencyDomain = postgres ? "eu-test" : "local";
const fixture = JSON.parse(fs.readFileSync(path.join(root,
  "docs/qualification/local-decisions/performance/rag-workflow.fixture.json"), "utf8")) as Fixture;
const workflowBundle = { codePath: path.join(root, "apps/temporal-worker/dist/workflow-bundle.js") };

function processInventory() {
  // Numeric process metadata only; do not collect unrelated process arguments.
  return execFileSync("ps", ["-axo", "pid=,ppid=,stat="], { encoding: "utf8", timeout: 2_000 }).trim().split("\n").map(line => {
    const parts = /^\s*(\d+)\s+(\d+)\s+(\S+)\s*$/.exec(line); assert.ok(parts);
    return { pid: Number(parts[1]), parent: Number(parts[2]), state: parts[3]! };
  });
}

const completions = ["recover", "cancel", "interrupt", "query-interrupt", "search-interrupt", "worker-crash", "completion-crash", "coordinator-crash"] as const;
for (const transport of ["isolated", "session"] as const) for (const completion of completions) {
  const leaseRecovery = completion === "worker-crash";
  const coordinatorCrash = completion === "coordinator-crash";
  const completionCrash = completion === "completion-crash" || coordinatorCrash;
  const workerCrash = leaseRecovery || completion === "completion-crash";
  const recovering = workerCrash || coordinatorCrash;
  const cancelling = completion !== "recover" && !recovering;
  const queryInterrupted = completion === "query-interrupt";
  const searchInterrupted = completion === "search-interrupt";
  const beforePrimary = queryInterrupted || searchInterrupted;
  const interrupted = completion === "interrupt" || beforePrimary;
  const expectedPrimaryCalls = cancelling ? (beforePrimary ? 1 : 2) : leaseRecovery ? 4 : 3;
  const expectedRetrievals = searchInterrupted ? 2 : expectedPrimaryCalls;
  const requestName = queryInterrupted ? "Query embedding" : searchInterrupted ? "Coordinator search" : "Primary";
  const cancelledDiagnostic = `${queryInterrupted ? "Embedding" : searchInterrupted ? "Knowledge" : "Model"} request cancelled`;
  test(`Temporal RAG ${stateStoreDriver}/${transport}/${completion}: ${coordinatorCrash ? "preserves committed completion through coordinator SIGKILL, tick retry and restart" : completionCrash ? "preserves committed output after Python SIGKILL before completion acknowledgement" : leaseRecovery ? "exits owned HTTP helpers after Python SIGKILL and recovers on real lease expiry" : interrupted ? `interrupts blocked ${requestName.toLowerCase()} HTTP after lease rejection and reuses the worker slot` : cancelling
    ? "cancels an active primary call and rejects its late output"
    : "retries a lost tick reply, restores a killed worker and replays without model calls"}`,
    { skip: !address || (recovering && globalThis.process.platform === "win32"), timeout: 120_000 }, async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-temporal-rag-"));
    const dbPath = path.join(directory, "state.sqlite"), artifacts = path.join(directory, "artifacts");
    const store = new AgatStore(dbPath, { seedDemo: false, temporalProcesses: true, decisionShadowEnabled: true, artifactsDir: artifacts,
      stateStoreDriver, region, residencyDomain,
      ...(postgres ? { postgresSchemaMode: "runtime", coordinatorInstanceId: `rag-observer-${randomUUID()}`,
        postgres: { systemUrl: process.env.AGAT_POSTGRES_URL!, tenantUrl: process.env.AGAT_POSTGRES_TENANT_URL!,
          roleMode: "runtime", applicationName: "temporal-rag-observer", poolMax: 1, connectTimeoutMs: 5_000,
          idleTimeoutMs: 30_000, statementTimeoutMs: 30_000, sslMode: "disable" } } : {}),
    });
    const children: ReturnType<typeof processChild>[] = [];
    const taskQueue = `rag-${randomUUID()}`, model = `temporal-rag-primary-${randomUUID()}`;
    let databaseEvidence: Record<string, unknown> = { driver: stateStoreDriver };
    let connection: Connection | undefined, coordinatorUrl = "";
    let primaryCalls = 0, embeddedItems = 0, held: (() => void) | undefined;
    const modelInputs: string[] = [], primaryOutputs: string[] = [], serverErrors: unknown[] = [];
    const ticks: Array<{ path: string; response: unknown; dropped: boolean }> = [];
    let dropNextTick = true, dropNextCancel = true;
    let applicationCancelledBeforeLateResponse = false, latePrimaryRejected = false;
    let latePrimaryFailure: string | undefined;
    let heldConnectionClosed = false;
    let interruption: Record<string, unknown> | undefined;
    let recovery: Record<string, unknown> | undefined;
    const acceptedStagesBeforeCrash = new Map<string, string>(), acceptedShadowsBeforeCrash = new Map<string, string>();
    let crashedLeaseId = "", workflowRunId = "";
    let heldCompletionLeaseId = "";
    let coordinatorRestarting = false;
    const coordinatorOutage = { ticks: 0, workerRequests: 0 };
    const expectedConnectionLoss = (error: unknown, target: string) => coordinatorCrash
      && (coordinatorRestarting || target !== coordinatorUrl) && error instanceof TypeError && error.message === "fetch failed";
    const modelServer = http.createServer(async (req, res) => {
      try {
        assert.equal(req.method, "POST");
        let raw = ""; for await (const chunk of req) raw += chunk;
        const body = JSON.parse(raw); res.setHeader("content-type", "application/json");
        if (req.url === "/v1/embeddings") {
          assert.equal(body.model, fixture.rag!.embeddingModel);
          embeddedItems += body.input.length;
          const finish = () => res.end(JSON.stringify({ model: body.model, data: body.input.map((text: string, index: number) =>
            ({ index, embedding: [1, text.length % 7 + 1, 1] })) }));
          if (queryInterrupted && embeddedItems === 4) {
            held = finish; res.once("close", () => { heldConnectionClosed = true; });
          } else finish();
          return;
        }
        assert.equal(req.url, "/v1/chat/completions"); assert.equal(body.model, model);
        primaryCalls++;
        const prompt = body.messages.map((message: { content: string }) => message.content).join("\n");
        for (const source of fixture.rag!.sources) assert.ok(prompt.includes(source.content));
        modelInputs.push(digest(JSON.stringify(body.messages)));
        const markers = [...new Set([...prompt.matchAll(/\[(K[0-9]+)\]/g)].map(match => match[0]))];
        assert.ok(markers.length >= 2);
        const content = `Учебные данные июля и августа ${markers.join(" ")}; причины неизвестны.`;
        primaryOutputs.push(content);
        const finish = () => res.end(JSON.stringify({ choices: [{ message: { role: "assistant", content }, finish_reason: "stop" }],
          usage: { prompt_tokens: 100, completion_tokens: 20 } }));
        if (primaryCalls === 2 && !beforePrimary && !completionCrash) {
          held = finish; res.once("close", () => { heldConnectionClosed = true; });
        } else finish();
      } catch (error) { serverErrors.push(error); res.writeHead(502).end(); }
    });
    let searchCalls = 0, completionCalls = 0;
    const workerProxy = http.createServer(async (req, res) => {
      const target = coordinatorUrl;
      try {
        const headers = new Headers();
        for (const [name, value] of Object.entries(req.headers)) {
          if (value !== undefined && !["host", "connection", "content-length", "transfer-encoding"].includes(name))
            headers.set(name, Array.isArray(value) ? value.join(", ") : value);
        }
        let body = ""; for await (const chunk of req) body += chunk;
        const response = await fetch(`${target}${req.url}`, { method: req.method!, headers,
          ...(body ? { body } : {}), signal: AbortSignal.timeout(5_000) });
        const output = Buffer.from(await response.arrayBuffer());
        const finish = () => res.writeHead(response.status, { "content-type": response.headers.get("content-type") ?? "application/json" }).end(output);
        if (req.url!.endsWith("/knowledge/search")) {
          searchCalls++;
          assert.equal(response.status, 200);
          if (searchInterrupted && searchCalls === 2) {
            held = finish; res.once("close", () => { heldConnectionClosed = true; });
            return;
          }
        }
        const completedLease = /^\/api\/v1\/leases\/([^/]+)\/complete$/.exec(req.url!);
        if (completedLease) {
          completionCalls++; assert.equal(response.status, 200);
          if (completionCrash && completionCalls === 2) {
            heldCompletionLeaseId = completedLease[1]!;
            held = finish; res.once("close", () => { heldConnectionClosed = true; });
            return;
          }
        }
        finish();
      } catch (error) {
        if (expectedConnectionLoss(error, target)) { coordinatorOutage.workerRequests++; res.writeHead(503).end(); }
        else { serverErrors.push(error); res.writeHead(502).end(); }
      }
    });
    const proxy = http.createServer(async (req, res) => {
      const target = coordinatorUrl;
      try {
        assert.equal(req.method, "POST"); assert.match(req.url!, /^\/api\/v1\/internal\/processes\/[^/]+\/(tick|cancel)$/);
        assert.equal(req.headers["x-agat-temporal-token"], "temporal-rag-internal");
        let body = ""; for await (const chunk of req) body += chunk;
        assert.deepEqual(JSON.parse(body), { projectId: "default" });
        const result = await fetch(`${target}${req.url}`, { method: "POST", body,
          headers: { "content-type": "application/json", "x-agat-temporal-token": "temporal-rag-internal" },
          signal: AbortSignal.timeout(5_000) });
        assert.equal(result.status, 200); const text = await result.text();
        const cancelling = req.url!.endsWith("/cancel");
        const dropped = cancelling ? dropNextCancel : dropNextTick;
        if (cancelling) dropNextCancel = false; else dropNextTick = false;
        ticks.push({ path: req.url!, response: JSON.parse(text), dropped });
        // Lose acknowledgement only after the real coordinator committed the tick.
        if (dropped) res.destroy(); else res.writeHead(200, { "content-type": "application/json" }).end(text);
      } catch (error) {
        if (expectedConnectionLoss(error, target)) { coordinatorOutage.ticks++; res.writeHead(503).end(); }
        else { serverErrors.push(error); res.writeHead(502).end(); }
      }
    });
    try {
      const decision = processChild("python3", ["-u", "-c", `
from decision_runtime.engine import DecisionEngine, Scores
from decision_runtime.server import make_server
class Backend:
    identity = {"repository":"fixture", "revision":"fixture", "artifactSha256":"1"*64,
                "tokenizerSha256":"2"*64,"implementationSha256":"3"*64,
                "promptVersion":"fixture","backend":"fixture","quantization":"none"}
    def score(self, request): return Scores([8.0,0.0],20)
with make_server(DecisionEngine(Backend()),0) as server:
    print("DECISION_PORT="+str(server.server_port),flush=True)
    server.serve_forever()
`], cleanEnv());
      children.push(decision);
      const decisionUrl = `http://127.0.0.1:${(await decision.ready(/DECISION_PORT=(\d+)/))[1]}`;
      const { profileJson } = await decisionProfile(decisionUrl);
      const collection = store.createKnowledgeCollection({ name: `${taskQueue} sources`, embeddingModel: fixture.rag!.embeddingModel,
        chunkSize: 4000, chunkOverlap: 0, topK: 2 });
      for (const source of fixture.rag!.sources) store.ingestKnowledgeDocument(String(collection.id),
        { name: source.name, sourceUri: source.sourceUri, mediaType: "text/markdown", content: source.content });
      const agents = fixture.roles.map(role => store.createAgent({ name: `${taskQueue} ${role.name}`, role: role.id, systemPrompt: role.instruction,
        model, runtime: "single", runtimeConfig: { profile: "tool_loop_v1", maxIterations: 1 } }));
      const shadow = normalizeDecisionShadowConfig({ mode: "shadow", profileJson, timeoutMs: 5_000, ...fixture.shadow });
      // Exercise the timer before any lease-completed Updates can race its due time.
      const ids = ["start", "recovery-wait", ...fixture.roles.map(role => role.id), "end"];
      const graph: ProcessGraph = { nodes: [
        { id: "start", name: "Start", type: "start", position: { x: 0, y: 0 }, config: {} },
        ...fixture.roles.map((role, index) => ({ id: role.id, name: role.name, type: "agent" as const,
          position: { x: 200 * (index + 1), y: 0 }, config: { agentId: String(agents[index]!.id), decisionShadow: shadow } })),
        { id: "recovery-wait", name: "Durable timer", type: "wait", position: { x: 500, y: 100 }, config: { waitSeconds: 5 } },
        { id: "end", name: "End", type: "end", position: { x: 800, y: 0 }, config: {} },
      ], edges: ids.slice(1).map((id, index) => ({ id: `edge-${index}`, source: ids[index]!, target: id, branch: "default" })),
      requiredKnowledgeCollectionIds: [String(collection.id)] };
      const process = store.createProcess({ name: `${taskQueue} recovery`, graph }); store.publishProcess(String(process.id));
      const env = { ...cleanEnv(), AGAT_HOST: "127.0.0.1", AGAT_PORT: "0", AGAT_DB_PATH: dbPath,
        AGAT_ARTIFACTS_DIR: artifacts, AGAT_SEED_DEMO: "false", AGAT_SERVE_WEB: "false", AGAT_MCP_ENABLED: "false",
        AGAT_A2A_ENABLED: "false", AGAT_SANDBOX_ENABLED: "false", AGAT_LOCAL_WORKER_LAUNCHER: "false",
        AGAT_ADMIN_TOKEN: "temporal-rag-admin", AGAT_ENROLLMENT_TOKEN: "temporal-rag-enroll",
        AGAT_REQUIRE_SIGNED_WORKER_RELEASES: "false", AGAT_REQUIRE_WORKER_PROVENANCE: "false",
        AGAT_REQUIRE_WORKER_RUNTIME_ATTESTATION: "false", AGAT_DECISION_SHADOW_ENABLED: "true",
        AGAT_TEMPORAL_ENABLED: "true", AGAT_TEMPORAL_ADDRESS: address!, AGAT_TEMPORAL_NAMESPACE: "default",
        AGAT_TEMPORAL_TASK_QUEUE: taskQueue, AGAT_TEMPORAL_INTERNAL_TOKEN: "temporal-rag-internal",
        ...(leaseRecovery ? { AGAT_LEASE_TTL_SECONDS: "30" } : {}),
        ...(postgres ? { AGAT_STATE_STORE_DRIVER: "postgresql", AGAT_ARTIFACT_STORE_DRIVER: "postgresql",
          AGAT_POSTGRES_URL: globalThis.process.env.AGAT_POSTGRES_URL, AGAT_POSTGRES_TENANT_URL: globalThis.process.env.AGAT_POSTGRES_TENANT_URL,
          AGAT_POSTGRES_SSL_MODE: "disable", AGAT_POSTGRES_POOL_MAX: "2", AGAT_REGION: region, AGAT_RESIDENCY_DOMAIN: residencyDomain,
          AGAT_COORDINATOR_INSTANCE_ID: `${taskQueue}-coordinator`, AGAT_KNOWLEDGE_SEARCH_EXECUTION: "isolated",
          AGAT_KNOWLEDGE_SEARCH_MAX_PENDING: "4", AGAT_KNOWLEDGE_SEARCH_TIMEOUT_MS: "1000" } : {}),
      };
      const startCoordinator = async () => {
        const child = processChild(globalThis.process.execPath, ["apps/coordinator/dist/server.js"], env);
        children.push(child);
        coordinatorUrl = `http://127.0.0.1:${(await child.ready(/АГАТ слушает http:\/\/127\.0\.0\.1:(\d+)/))[1]}`;
        return child;
      };
      let coordinator = await startCoordinator();
      const proxyUrl = await listen(proxy), modelUrl = await listen(modelServer);
      const workerCoordinatorUrl = searchInterrupted || completionCrash ? await listen(workerProxy) : coordinatorUrl;
      const startTemporal = async (identity: string) => {
        const child = processChild(globalThis.process.execPath, ["apps/temporal-worker/dist/worker.js"], {
          ...env, AGAT_COORDINATOR_INTERNAL_URL: proxyUrl, AGAT_TEMPORAL_METRICS_ADDRESS: "127.0.0.1:0",
          AGAT_TEMPORAL_WORKER_ID: identity });
        children.push(child); await child.ready(/Temporal worker слушает/); return child;
      };
      const firstIdentity = `${taskQueue}-before`, secondIdentity = `${taskQueue}-after`;
      const first = await startTemporal(firstIdentity);
      connection = await Connection.connect({ address, connectTimeout: 5_000 });
      const client = new Client({ connection, namespace: "default" });
      const startWorker = () => {
        const child = processChild("python3", ["workers/agat_worker.py", "--coordinator", workerCoordinatorUrl,
          "--enrollment-token", "temporal-rag-enroll", "--credentials", path.join(directory, "worker.json"),
          "--name", `${taskQueue}-worker`, "--models", model, "--embedding-models", fixture.rag!.embeddingModel,
          "--region", region, "--residency-domain", residencyDomain,
          "--model-url", `${modelUrl}/v1`, "--model-api-key", "test-local", "--model-discovery", "off", "--no-web",
          "--poll-interval", "0.2", "--concurrency", "1", "--decision-url", decisionUrl], {
          ...cleanEnv(), AGAT_EMBEDDING_TRANSPORT: transport, OTEL_SDK_DISABLED: "true", NO_PROXY: "127.0.0.1,localhost" });
        children.push(child); return child;
      };
      let worker = startWorker();
      await eventually(() => {
        assert.equal(worker.child.exitCode, null, worker.log());
        const docs = (store.exportKnowledge() as any).collections.find((row: any) => row.id === collection.id)?.documents;
        return docs?.length === 2 && docs.every((doc: any) => doc.status === "ready");
      }, "Worker did not index the source documents");
      const ingestion = verifyIngestion(store.exportKnowledge(), String(collection.id), fixture.rag!);
      const readiness = await fetch(`${coordinatorUrl}/api/v1/processes/${process.id}/preflight`, { method: "POST",
        headers: { "x-agat-admin-token": "temporal-rag-admin", "content-type": "application/json" },
        body: JSON.stringify({ version: 1 }), signal: AbortSignal.timeout(5_000) });
      assert.equal(readiness.status, 200, await readiness.clone().text());
      assert.equal((await readiness.json() as { runnableNow: boolean }).runnableNow, true);
      const started = await fetch(`${coordinatorUrl}/api/v1/processes/${process.id}/start`, { method: "POST",
        headers: { "x-agat-admin-token": "temporal-rag-admin", "content-type": "application/json" },
        body: JSON.stringify({ input: fixture.input }), signal: AbortSignal.timeout(5_000) });
      assert.equal(started.status, 201, await started.clone().text());
      const instance = await started.json() as { id: string; runId: string };
      assert.equal(store.getProcessInstance(instance.id)!.runtime, "temporal");
      const workflowId = `agat-process-${instance.id}`, handle = client.workflow.getHandle(workflowId);
      if (recovering) workflowRunId = (await handle.describe()).runId;
      await eventually(() => Boolean(held), "Second stage did not reach the held HTTP response");
      await within(handle.query("processState"), "Workflow did not become queryable");
      await eventually(async () => {
        const history = await handle.fetchHistory();
        return Boolean(history.events?.some(event => Number(event.activityTaskStartedEventAttributes?.attempt) === 2));
      }, "The lost response must cause a real Temporal Activity retry");
      assert.equal(ticks.filter(row => row.dropped).length, 1);
      assert.equal(primaryCalls, beforePrimary ? 1 : 2);
      assert.deepEqual(await first.stop("SIGKILL"), { code: null, signal: "SIGKILL" });
      const second = await startTemporal(secondIdentity);
      // Query forces the replacement worker to replay the existing history before
      // releasing the still-running primary request on the independent Python worker.
      await within(handle.query("processState"), "Replacement worker did not restore workflow state");
      assert.equal(primaryCalls, beforePrimary ? 1 : 2, "Worker restart must not re-execute the completed primary stage");
      const captureAccepted = (before: any, expected: number) => {
        const accepted = before.run.stages.filter((stage: any) => stage.status === "completed"
          && fixture.roles.some(role => role.id === stage.processNodeId));
        assert.equal(accepted.length, expected);
        for (const [index, stage] of accepted.entries()) {
          assert.equal(stage.output, primaryOutputs[index]);
          acceptedStagesBeforeCrash.set(stage.id, JSON.stringify(stage));
        }
        assert.equal(before.decisionObservations.length, accepted.length);
        for (const shadow of before.decisionObservations) acceptedShadowsBeforeCrash.set(shadow.stageId, JSON.stringify(shadow));
      };
      if (workerCrash) {
        captureAccepted(store.getRunTrace(instance.runId)!, completionCrash ? 2 : 1);
        const active = completionCrash
          ? store.db.prepare("SELECT id, lease_id, lease_expires_at, attempt, status FROM stages WHERE run_id = ? AND lease_id = ?").get(instance.runId, heldCompletionLeaseId)!
          : store.db.prepare("SELECT id, lease_id, lease_expires_at, attempt, status FROM stages WHERE run_id = ? AND status = 'running'").get(instance.runId)!;
        assert.equal(active.status, completionCrash ? "completed" : "running");
        crashedLeaseId = String(active.lease_id);
        const expiry = Date.parse(String(active.lease_expires_at));
        if (leaseRecovery) assert.ok(expiry > Date.now(), "Worker must die before its active lease expires");
        const helperPids = processInventory().filter(row => row.parent === worker.child.pid).map(row => row.pid);
        if (leaseRecovery) assert.ok(helperPids.length >= 1, "No owned helper observed during primary HTTP");
        const killedAt = performance.now();
        assert.deepEqual(await worker.stop("SIGKILL"), { code: null, signal: "SIGKILL" });
        await eventually(() => heldConnectionClosed, "Held HTTP connection remained open after Python worker SIGKILL", 5_000);
        await eventually(() => processInventory().every(row => !helperPids.includes(row.pid) || row.state.startsWith("Z")),
          "Owned helper remained executing after Python worker SIGKILL", 5_000);
        const helpersExitedAfterMs = performance.now() - killedAt;
        assert.ok(held, "The HTTP response must remain held throughout recovery");
        worker = startWorker();
        if (leaseRecovery) {
          await eventually(() => {
            const trace = store.getRunTrace(instance.runId)! as any;
            return trace.events.some((event: any) => event.type === "lease.expired" && event.stageId === active.id);
          }, "The real lease deadline did not requeue the interrupted stage", 40_000);
          assert.ok(Date.now() >= expiry);
        }
        await eventually(() => primaryCalls >= 3, "Replacement worker did not continue the RAG process");
        const credentials = JSON.parse(fs.readFileSync(path.join(directory, "worker.json"), "utf8"));
        const stale = await fetch(`${coordinatorUrl}/api/v1/leases/${encodeURIComponent(crashedLeaseId)}/complete`, { method: "POST",
          headers: { authorization: `Bearer ${credentials.token}`, "content-type": "application/json" },
          body: JSON.stringify({ output: "stale abandoned primary" }), signal: AbortSignal.timeout(5_000) });
        assert.equal(stale.status, 400); assert.match(await stale.text(), /Активная аренда не найдена/);
        recovery = { signal: "SIGKILL", leaseTtlSeconds: leaseRecovery ? 30 : 180, leaseId: crashedLeaseId,
          helpersObserved: helperPids.length, helpersExitedAfterMs, helperProcessesExited: true,
          responseStillHeld: true, staleCompletionRejected: true, originalAttempt: active.attempt,
          ...(leaseRecovery ? { primaryConnectionClosed: heldConnectionClosed, expiredStageId: active.id }
            : { completionConnectionClosed: heldConnectionClosed, committedStageId: active.id, committedBeforeReplyLoss: true }) };
      }
      if (coordinatorCrash) {
        captureAccepted(store.getRunTrace(instance.runId)!, 2);
        crashedLeaseId = heldCompletionLeaseId;
        const sourcesBefore = JSON.stringify(store.getRunKnowledgeSources(instance.runId));
        const pythonPid = worker.child.pid, coordinatorPid = coordinator.child.pid;
        coordinatorRestarting = true;
        assert.deepEqual(await coordinator.stop("SIGKILL"), { code: null, signal: "SIGKILL" });
        const wake = await within(handle.executeUpdate<{ acceptedRevision: number }, [string]>("processChangedV1",
          { args: ["fixture.coordinator-restart"] }), "Workflow did not accept an Update during coordinator outage");
        assert.ok(wake.acceptedRevision > 0);
        await eventually(() => coordinatorOutage.ticks > 0, "The tick did not observe the real coordinator outage");
        coordinator = await startCoordinator(); coordinatorRestarting = false;
        assert.notEqual(coordinator.child.pid, coordinatorPid);
        assert.equal(worker.child.pid, pythonPid); assert.equal(worker.child.exitCode, null, worker.log());
        assert.equal(primaryCalls, 2, "Worker must still wait for the held completion acknowledgement");
        assert.ok(held && !heldConnectionClosed);
        const restored = await fetch(`${coordinatorUrl}/api/v1/runs/${encodeURIComponent(instance.runId)}/trace`, {
          headers: { "x-agat-admin-token": "temporal-rag-admin" }, signal: AbortSignal.timeout(5_000) });
        assert.equal(restored.status, 200, await restored.clone().text());
        const restoredTrace = await restored.json() as any;
        for (const [id, snapshot] of acceptedStagesBeforeCrash) assert.equal(JSON.stringify(restoredTrace.run.stages.find((stage: any) => stage.id === id)), snapshot);
        for (const [id, snapshot] of acceptedShadowsBeforeCrash) assert.equal(JSON.stringify(restoredTrace.decisionObservations.find((row: any) => row.stageId === id)), snapshot);
        assert.equal(JSON.stringify(store.getRunKnowledgeSources(instance.runId)), sourcesBefore);
        const credentials = JSON.parse(fs.readFileSync(path.join(directory, "worker.json"), "utf8"));
        const stale = await fetch(`${coordinatorUrl}/api/v1/leases/${encodeURIComponent(crashedLeaseId)}/complete`, { method: "POST",
          headers: { authorization: `Bearer ${credentials.token}`, "content-type": "application/json" },
          body: JSON.stringify({ output: "stale completion after coordinator restart" }), signal: AbortSignal.timeout(5_000) });
        assert.equal(stale.status, 400); assert.match(await stale.text(), /Активная аренда не найдена/);
        held(); held = undefined;
        recovery = { signal: "SIGKILL", target: "coordinator", leaseTtlSeconds: 180, leaseId: crashedLeaseId,
          coordinatorRestarted: true, samePythonWorker: true, acceptedStateReadFromReplacement: true,
          sourcesPreservedBeforeAcknowledgement: true, completionAcknowledgedAfterRestart: true,
          staleCompletionRejected: true, outage: coordinatorOutage, acceptedUpdateRevision: wake.acceptedRevision };
      }
      let cancelledLeaseId = "";
      if (cancelling) {
        cancelledLeaseId = String(store.db.prepare("SELECT lease_id FROM stages WHERE run_id = ? AND status = 'running'").get(instance.runId)!.lease_id);
        await handle.cancel();
        await assert.rejects(within(handle.result(), "RAG workflow did not cancel"));
        assert.equal((await handle.describe()).status.name, "CANCELLED");
        assert.equal(store.getProcessInstance(instance.id)!.status, "cancelled");
        assert.ok(held, "The held HTTP response must remain pending until after application cancellation");
        applicationCancelledBeforeLateResponse = true;
        assert.equal(ticks.filter(row => row.path.endsWith("/cancel")).length, 2);
      }
      if (interrupted) {
        const cancelledAt = performance.now();
        await eventually(() => worker.log().includes(`Lease renewal failed for ${cancelledLeaseId}:`),
          "Real 45-second lease renewal did not reject the cancelled lease", 60_000);
        const rejectedAt = performance.now();
        await eventually(() => heldConnectionClosed, `${requestName} HTTP remained open after lease renewal rejection`, 5_000);
        await eventually(() => worker.log().includes(`[${cancelledLeaseId.slice(0, 8)}] failed: RuntimeError: ${cancelledDiagnostic}`),
          "Worker did not stop the blocked model request", 5_000);
        assert.ok(held, "Model fixture must not release the response to free the worker");
        interruption = { renewalRejectedAfterMs: rejectedAt - cancelledAt,
          clientDisconnectedAfterRejectionMs: performance.now() - rejectedAt, responseStillHeld: true };
      } else if (!recovering) {
        held!(); held = undefined;
      }
      if (cancelling) {
        await eventually(() => worker.log().includes(`[${cancelledLeaseId.slice(0, 8)}] failed:`)
          && worker.log().includes("Could not report failure:"), "The real worker did not observe rejection of the late primary result");
        latePrimaryFailure = worker.log().split("\n").find(line => line.includes(`[${cancelledLeaseId.slice(0, 8)}] failed:`));
        assert.ok(latePrimaryFailure?.endsWith(interrupted
          ? `failed: RuntimeError: ${cancelledDiagnostic}` : "failed: ApiError: Активная аренда не найдена"),
          latePrimaryFailure ?? "Missing primary failure");
        latePrimaryRejected = completion === "cancel";
      } else {
        const result = await within(handle.result(), "Recovered workflow did not finish") as { status: string };
        assert.equal(result.status, "completed");
      }
      const trace = store.getRunTrace(instance.runId)! as any;
      assert.equal(trace.truncated, false); assert.equal(trace.run.status, cancelling ? "cancelled" : "completed");
      assert.equal(primaryCalls, expectedPrimaryCalls); assert.equal(embeddedItems, cancelling ? 4 : leaseRecovery ? 6 : 5);
      const stages = trace.run.stages.filter((stage: any) => fixture.roles.some(role => role.id === stage.processNodeId));
      assert.equal(stages.length, cancelling ? 2 : 3);
      for (const [index, stage] of stages.entries()) {
        if (cancelling && index === 1) {
          assert.equal(stage.status, "cancelled"); assert.equal(stage.output, null);
          continue;
        }
        const outputIndex = leaseRecovery && index > 0 ? index + 1 : index;
        assert.equal(stage.status, "completed"); assert.equal(stage.output, primaryOutputs[outputIndex]);
        assert.equal(stage.metrics.modelCalls, 1);
        const retrieval = verifyRetrieval(trace, stage, ingestion, leaseRecovery && index === 1 ? 2 : 1);
        assert.ok(retrieval.bothSourcesCited); assert.deepEqual(retrieval.unknownMarkers, []);
      }
      if (recovering) {
        for (const [id, snapshot] of acceptedStagesBeforeCrash) assert.equal(JSON.stringify(stages.find((stage: any) => stage.id === id)), snapshot);
        for (const [id, snapshot] of acceptedShadowsBeforeCrash) assert.equal(JSON.stringify(trace.decisionObservations.find((row: any) => row.stageId === id)), snapshot);
        assert.equal(stages[1].attempt, leaseRecovery ? 2 : 1); assert.equal(stages[2].attempt, 1);
        const replacementLeaseId = store.db.prepare("SELECT lease_id FROM stages WHERE id = ?").get(stages[1].id)!.lease_id;
        assert.equal(typeof replacementLeaseId, "string");
        if (leaseRecovery) {
          assert.notEqual(replacementLeaseId, crashedLeaseId);
          assert.notEqual(stages[1].output, primaryOutputs[1], "The abandoned primary output must not be accepted");
        } else assert.equal(replacementLeaseId, crashedLeaseId, "An accepted stage must retain its completed lease");
        assert.equal(trace.events.filter((event: any) => event.type === "lease.expired").length, leaseRecovery ? 1 : 0);
        assert.equal((await handle.describe()).runId, workflowRunId);
        if (workerCrash) assert.ok(held && heldConnectionClosed);
        recovery = { ...recovery, sameWorkflowRun: true, replacementAttempt: stages[1].attempt,
          firstAcceptedStageUnchanged: true, acceptedStagesPreserved: acceptedStagesBeforeCrash.size,
          acceptedShadowsPreserved: acceptedShadowsBeforeCrash.size,
          ...(leaseRecovery ? { replacementLeaseId } : { completedStageNotRepeated: true, completedLeaseId: replacementLeaseId }) };
      }
      assert.equal(trace.decisionObservations.length, cancelling ? 1 : 3);
      assert.ok(trace.decisionObservations.every((row: any) => row.observation.status === "ok"
        && row.observation.fallback === "primary"));
      assert.equal(store.getRunKnowledgeSources(instance.runId)!.length, expectedRetrievals * 2);
      if (postgres) {
        const runtimeRole = store.db.prepare("SELECT current_user AS role").get()!.role;
        assert.equal(runtimeRole, "agat_system");
        const foreign = `rag-foreign-${randomUUID()}`; store.createProject({ id: foreign, name: foreign });
        const tenant = new pg.Client({ connectionString: globalThis.process.env.AGAT_POSTGRES_TENANT_URL, ssl: false });
        try {
          await tenant.connect();
          const role = (await tenant.query("SELECT current_user AS role, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).rows[0];
          assert.deepEqual(role, { role: "agat_tenant", rolsuper: false, rolbypassrls: false });
          await assert.rejects(tenant.query("SELECT id FROM worker_releases"), { code: "42501" });
          const visible: Record<string, number[]> = {};
          for (const project of ["default", foreign]) {
            await tenant.query("BEGIN"); await tenant.query("SELECT set_config('agat.current_project_id', $1, true)", [project]);
            visible[project === "default" ? "own" : "foreign"] = [
              (await tenant.query("SELECT id FROM runs WHERE id = $1", [instance.runId])).rowCount!,
              (await tenant.query("SELECT id FROM process_instances WHERE id = $1", [instance.id])).rowCount!,
              (await tenant.query("SELECT id FROM knowledge_retrievals WHERE run_id = $1", [instance.runId])).rowCount!,
              (await tenant.query("SELECT id FROM knowledge_chunks WHERE collection_id = $1", [collection.id])).rowCount!,
            ];
            await tenant.query("ROLLBACK");
          }
          assert.deepEqual(visible, { own: [1, 1, expectedRetrievals, 2], foreign: [0, 0, 0, 0] });
          databaseEvidence = { driver: stateStoreDriver, runtimeRole, tenantRole: role, visible, releaseRegistryDenied: true,
            visibilityColumns: ["runs", "process_instances", "knowledge_retrievals", "knowledge_chunks"], retrievalExecution: "isolated" };
        } finally { await tenant.end(); }
      }
      assert.deepEqual(serverErrors, []);
      const history = await handle.fetchHistory();
      assert.ok(history.events?.some(event => event.timerFiredEventAttributes),
        `The durable wait must fire through Temporal: ${JSON.stringify(ticks)}`);
      const identities = new Set(history.events?.map(event => event.workflowTaskStartedEventAttributes?.identity).filter(Boolean));
      assert.ok(identities.has(firstIdentity) && identities.has(secondIdentity));
      const checkpoint = { primaryCalls, embeddedItems, ticks: ticks.length, trace: JSON.stringify(trace) };
      const serializedHistory = JSON.parse(historyToJSON(history), (key, value) =>
        key === "identity" && typeof value === "string" && value !== firstIdentity && value !== secondIdentity
          ? "fixture-client" : value);
      await Worker.runReplayHistory({ workflowBundle }, serializedHistory, workflowId);
      assert.deepEqual({ primaryCalls, embeddedItems, ticks: ticks.length, trace: JSON.stringify(store.getRunTrace(instance.runId)) }, checkpoint,
        "History replay must perform no HTTP activities, model calls or database writes");
      if (interrupted) {
        const resumed = await fetch(`${coordinatorUrl}/api/v1/processes/${process.id}/start`, { method: "POST",
          headers: { "x-agat-admin-token": "temporal-rag-admin", "content-type": "application/json" },
          body: JSON.stringify({ input: fixture.input }), signal: AbortSignal.timeout(5_000) });
        assert.equal(resumed.status, 201, await resumed.clone().text());
        const next = await resumed.json() as { id: string; runId: string };
        const result = await within(client.workflow.getHandle(`agat-process-${next.id}`).result(),
          "The same single-slot worker did not complete another RAG process", 30_000) as { status: string };
        assert.equal(result.status, "completed");
        assert.equal(primaryCalls, expectedPrimaryCalls + 3); assert.equal(embeddedItems, 7);
        assert.ok(held && heldConnectionClosed);
        assert.equal(JSON.stringify(store.getRunTrace(instance.runId)), checkpoint.trace);
        const nextTrace = store.getRunTrace(next.runId)! as any;
        assert.equal(nextTrace.run.status, "completed");
        assert.equal(nextTrace.decisionObservations.length, 3);
        interruption = { ...interruption, sameWorkerReused: true, nextInstanceId: next.id,
          totalPrimaryCalls: primaryCalls, totalEmbeddedItems: embeddedItems, nextTrace };
      }
      assert.deepEqual(await worker.stop(), { code: 0, signal: null }, worker.log());
      assert.deepEqual(await second.stop(), { code: 0, signal: null }, second.log());
      assert.deepEqual(await coordinator.stop(), { code: 0, signal: null }, coordinator.log());
      const evidenceRoot = globalThis.process.env.AGAT_TEMPORAL_RAG_EVIDENCE_DIR;
      if (evidenceRoot) {
        const target = path.resolve(evidenceRoot);
        assert.ok(target.startsWith(path.join(root, "docs") + path.sep));
        fs.mkdirSync(target, { recursive: true });
        fs.writeFileSync(path.join(target, `${transport}${completion !== "recover" ? `-${completion}` : ""}.json`), JSON.stringify({ transport, completion, workflowId, database: databaseEvidence,
          primaryCalls: checkpoint.primaryCalls, embeddedItems: checkpoint.embeddedItems,
          modelInputs: modelInputs.slice(0, checkpoint.primaryCalls), primaryOutputs: primaryOutputs.slice(0, checkpoint.primaryCalls),
          ingestion, ticks: ticks.slice(0, checkpoint.ticks), history: serializedHistory, trace, latePrimaryFailure, interruption, recovery,
          assertions: { activityRetry: true, workerRestart: true, nativeReplay: true, primaryPreserved: true,
            provenance: true, workersDrained: true,
            ...(cancelling ? { applicationCancelledBeforeLateResponse } : {}),
            ...(completion === "cancel" ? { latePrimaryRejected } : {}),
            ...(interrupted ? { workerSlotReused: true,
              ...(searchInterrupted ? { coordinatorConnectionClosed: heldConnectionClosed } : { modelConnectionClosed: heldConnectionClosed }),
              ...(beforePrimary ? { cancelledStageNeverCalledPrimary: true } : { primaryConnectionClosed: heldConnectionClosed }) } : {}) },
          qualification: "not_assessed" }, null, 2) + "\n", { flag: "wx" });
      }
    } finally {
      held?.();
      for (const child of children.reverse()) await child.stop();
      await close(proxy); await close(workerProxy); await close(modelServer); await connection?.close(); store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });
}
