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

for (const transport of ["isolated", "session"] as const) for (const completion of ["recover", "cancel", "interrupt"] as const) {
  const cancelling = completion !== "recover";
  test(`Temporal RAG ${stateStoreDriver}/${transport}/${completion}: ${completion === "interrupt" ? "interrupts blocked primary HTTP after lease rejection and reuses the worker slot" : cancelling
    ? "cancels an active primary call and rejects its late output"
    : "retries a lost tick reply, restores a killed worker and replays without model calls"}`,
    { skip: !address, timeout: 120_000 }, async () => {
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
    const modelServer = http.createServer(async (req, res) => {
      try {
        assert.equal(req.method, "POST");
        let raw = ""; for await (const chunk of req) raw += chunk;
        const body = JSON.parse(raw); res.setHeader("content-type", "application/json");
        if (req.url === "/v1/embeddings") {
          assert.equal(body.model, fixture.rag!.embeddingModel);
          embeddedItems += body.input.length;
          res.end(JSON.stringify({ model: body.model, data: body.input.map((text: string, index: number) =>
            ({ index, embedding: [1, text.length % 7 + 1, 1] })) }));
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
        if (primaryCalls === 2) {
          held = finish; res.once("close", () => { heldConnectionClosed = true; });
        } else finish();
      } catch (error) { serverErrors.push(error); res.writeHead(502).end(); }
    });
    const proxy = http.createServer(async (req, res) => {
      try {
        assert.equal(req.method, "POST"); assert.match(req.url!, /^\/api\/v1\/internal\/processes\/[^/]+\/(tick|cancel)$/);
        assert.equal(req.headers["x-agat-temporal-token"], "temporal-rag-internal");
        let body = ""; for await (const chunk of req) body += chunk;
        assert.deepEqual(JSON.parse(body), { projectId: "default" });
        const result = await fetch(`${coordinatorUrl}${req.url}`, { method: "POST", body,
          headers: { "content-type": "application/json", "x-agat-temporal-token": "temporal-rag-internal" },
          signal: AbortSignal.timeout(5_000) });
        assert.equal(result.status, 200); const text = await result.text();
        const cancelling = req.url!.endsWith("/cancel");
        const dropped = cancelling ? dropNextCancel : dropNextTick;
        if (cancelling) dropNextCancel = false; else dropNextTick = false;
        ticks.push({ path: req.url!, response: JSON.parse(text), dropped });
        // Lose acknowledgement only after the real coordinator committed the tick.
        if (dropped) res.destroy(); else res.writeHead(200, { "content-type": "application/json" }).end(text);
      } catch (error) { serverErrors.push(error); res.writeHead(502).end(); }
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
        ...(postgres ? { AGAT_STATE_STORE_DRIVER: "postgresql", AGAT_ARTIFACT_STORE_DRIVER: "postgresql",
          AGAT_POSTGRES_URL: globalThis.process.env.AGAT_POSTGRES_URL, AGAT_POSTGRES_TENANT_URL: globalThis.process.env.AGAT_POSTGRES_TENANT_URL,
          AGAT_POSTGRES_SSL_MODE: "disable", AGAT_POSTGRES_POOL_MAX: "2", AGAT_REGION: region, AGAT_RESIDENCY_DOMAIN: residencyDomain,
          AGAT_COORDINATOR_INSTANCE_ID: `${taskQueue}-coordinator`, AGAT_KNOWLEDGE_SEARCH_EXECUTION: "isolated",
          AGAT_KNOWLEDGE_SEARCH_MAX_PENDING: "4", AGAT_KNOWLEDGE_SEARCH_TIMEOUT_MS: "1000" } : {}),
      };
      const coordinator = processChild(globalThis.process.execPath, ["apps/coordinator/dist/server.js"], env);
      children.push(coordinator);
      coordinatorUrl = `http://127.0.0.1:${(await coordinator.ready(/АГАТ слушает http:\/\/127\.0\.0\.1:(\d+)/))[1]}`;
      const proxyUrl = await listen(proxy), modelUrl = await listen(modelServer);
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
      const worker = processChild("python3", ["workers/agat_worker.py", "--coordinator", coordinatorUrl,
        "--enrollment-token", "temporal-rag-enroll", "--credentials", path.join(directory, "worker.json"),
        "--name", `${taskQueue}-worker`, "--models", model, "--embedding-models", fixture.rag!.embeddingModel,
        "--region", region, "--residency-domain", residencyDomain,
        "--model-url", `${modelUrl}/v1`, "--model-api-key", "test-local", "--model-discovery", "off", "--no-web",
        "--poll-interval", "0.2", "--concurrency", "1", "--decision-url", decisionUrl], {
        ...cleanEnv(), AGAT_EMBEDDING_TRANSPORT: transport, OTEL_SDK_DISABLED: "true", NO_PROXY: "127.0.0.1,localhost" });
      children.push(worker);
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
      await eventually(() => Boolean(held), "Second primary stage did not start");
      await within(handle.query("processState"), "Workflow did not become queryable");
      await eventually(async () => {
        const history = await handle.fetchHistory();
        return Boolean(history.events?.some(event => Number(event.activityTaskStartedEventAttributes?.attempt) === 2));
      }, "The lost response must cause a real Temporal Activity retry");
      assert.equal(ticks.filter(row => row.dropped).length, 1);
      assert.equal(primaryCalls, 2);
      assert.deepEqual(await first.stop("SIGKILL"), { code: null, signal: "SIGKILL" });
      const second = await startTemporal(secondIdentity);
      // Query forces the replacement worker to replay the existing history before
      // releasing the still-running primary request on the independent Python worker.
      await within(handle.query("processState"), "Replacement worker did not restore workflow state");
      assert.equal(primaryCalls, 2, "Worker restart must not re-execute the completed primary stage");
      let cancelledLeaseId = "";
      if (cancelling) {
        cancelledLeaseId = String(store.db.prepare("SELECT lease_id FROM stages WHERE run_id = ? AND status = 'running'").get(instance.runId)!.lease_id);
        await handle.cancel();
        await assert.rejects(within(handle.result(), "RAG workflow did not cancel"));
        assert.equal((await handle.describe()).status.name, "CANCELLED");
        assert.equal(store.getProcessInstance(instance.id)!.status, "cancelled");
        assert.ok(held, "Primary response must remain pending until after application cancellation");
        applicationCancelledBeforeLateResponse = true;
        assert.equal(ticks.filter(row => row.path.endsWith("/cancel")).length, 2);
      }
      if (completion === "interrupt") {
        const cancelledAt = performance.now();
        await eventually(() => worker.log().includes(`Lease renewal failed for ${cancelledLeaseId}:`),
          "Real 45-second lease renewal did not reject the cancelled lease", 60_000);
        const rejectedAt = performance.now();
        await eventually(() => heldConnectionClosed, "Primary HTTP remained open after lease renewal rejection", 5_000);
        await eventually(() => worker.log().includes(`[${cancelledLeaseId.slice(0, 8)}] failed: RuntimeError: Model request cancelled`),
          "Worker did not stop the blocked primary request", 5_000);
        assert.ok(held, "Model fixture must not release the response to free the worker");
        interruption = { renewalRejectedAfterMs: rejectedAt - cancelledAt,
          clientDisconnectedAfterRejectionMs: performance.now() - rejectedAt, responseStillHeld: true };
      } else {
        held!(); held = undefined;
      }
      if (cancelling) {
        await eventually(() => worker.log().includes(`[${cancelledLeaseId.slice(0, 8)}] failed:`)
          && worker.log().includes("Could not report failure:"), "The real worker did not observe rejection of the late primary result");
        latePrimaryFailure = worker.log().split("\n").find(line => line.includes(`[${cancelledLeaseId.slice(0, 8)}] failed:`));
        assert.ok(latePrimaryFailure?.endsWith(completion === "interrupt"
          ? "failed: RuntimeError: Model request cancelled" : "failed: ApiError: Активная аренда не найдена"),
          latePrimaryFailure ?? "Missing primary failure");
        latePrimaryRejected = completion === "cancel";
      } else {
        const result = await within(handle.result(), "Recovered workflow did not finish") as { status: string };
        assert.equal(result.status, "completed");
      }
      const trace = store.getRunTrace(instance.runId)! as any;
      assert.equal(trace.truncated, false); assert.equal(trace.run.status, cancelling ? "cancelled" : "completed");
      assert.equal(primaryCalls, cancelling ? 2 : 3); assert.equal(embeddedItems, cancelling ? 4 : 5);
      const stages = trace.run.stages.filter((stage: any) => fixture.roles.some(role => role.id === stage.processNodeId));
      assert.equal(stages.length, cancelling ? 2 : 3);
      for (const [index, stage] of stages.entries()) {
        if (cancelling && index === 1) {
          assert.equal(stage.status, "cancelled"); assert.equal(stage.output, null);
          continue;
        }
        assert.equal(stage.status, "completed"); assert.equal(stage.output, primaryOutputs[index]);
        assert.equal(stage.metrics.modelCalls, 1);
        const retrieval = verifyRetrieval(trace, stage, ingestion);
        assert.ok(retrieval.bothSourcesCited); assert.deepEqual(retrieval.unknownMarkers, []);
      }
      assert.equal(trace.decisionObservations.length, cancelling ? 1 : 3);
      assert.ok(trace.decisionObservations.every((row: any) => row.observation.status === "ok"
        && row.observation.fallback === "primary"));
      assert.equal(store.getRunKnowledgeSources(instance.runId)!.length, cancelling ? 4 : 6);
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
          assert.deepEqual(visible, { own: [1, 1, cancelling ? 2 : 3, 2], foreign: [0, 0, 0, 0] });
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
      if (completion === "interrupt") {
        const resumed = await fetch(`${coordinatorUrl}/api/v1/processes/${process.id}/start`, { method: "POST",
          headers: { "x-agat-admin-token": "temporal-rag-admin", "content-type": "application/json" },
          body: JSON.stringify({ input: fixture.input }), signal: AbortSignal.timeout(5_000) });
        assert.equal(resumed.status, 201, await resumed.clone().text());
        const next = await resumed.json() as { id: string; runId: string };
        const result = await within(client.workflow.getHandle(`agat-process-${next.id}`).result(),
          "The same single-slot worker did not complete another RAG process", 30_000) as { status: string };
        assert.equal(result.status, "completed");
        assert.equal(primaryCalls, 5); assert.equal(embeddedItems, 7);
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
        fs.writeFileSync(path.join(target, `${transport}${cancelling ? `-${completion}` : ""}.json`), JSON.stringify({ transport, completion, workflowId, database: databaseEvidence,
          primaryCalls: checkpoint.primaryCalls, embeddedItems: checkpoint.embeddedItems,
          modelInputs: modelInputs.slice(0, checkpoint.primaryCalls), primaryOutputs: primaryOutputs.slice(0, checkpoint.primaryCalls),
          ingestion, ticks: ticks.slice(0, checkpoint.ticks), history: serializedHistory, trace, latePrimaryFailure, interruption,
          assertions: { activityRetry: true, workerRestart: true, nativeReplay: true, primaryPreserved: true,
            provenance: true, workersDrained: true,
            ...(cancelling ? { applicationCancelledBeforeLateResponse } : {}),
            ...(completion === "cancel" ? { latePrimaryRejected } : {}),
            ...(completion === "interrupt" ? { primaryConnectionClosed: heldConnectionClosed, workerSlotReused: true } : {}) },
          qualification: "not_assessed" }, null, 2) + "\n", { flag: "wx" });
      }
    } finally {
      held?.();
      for (const child of children.reverse()) await child.stop();
      await close(proxy); await close(modelServer); await connection?.close(); store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });
}
