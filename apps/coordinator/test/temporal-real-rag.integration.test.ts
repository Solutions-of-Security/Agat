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
import type { ProcessGraph } from "../src/types.js";
import { normalizeDecisionShadowConfig } from "../src/local-decisions.js";
import { decisionProfile, digest, generation, json, origin, primaryIdentity, type Fixture } from "../../../scripts/lib/decision-primary-workflow.js";
import { embeddingIdentity, forwardEmbedding, verifyIngestion, verifyRetrieval } from "../../../scripts/lib/decision-rag.js";
import { forwardDecisionRequest } from "../../../scripts/lib/decision-shadow-proxy.js";

const address = process.env.AGAT_TEST_TEMPORAL_ADDRESS;
const modelUrl = process.env.AGAT_TEMPORAL_REAL_MODEL_URL;
const planPath = process.env.AGAT_TEMPORAL_REAL_RAG_PLAN;
const selectedTransport = process.env.AGAT_TEMPORAL_REAL_RAG_TRANSPORT;
const decisionUrl = process.env.AGAT_TEMPORAL_REAL_DECISION_URL;
const shadowEnabled = Boolean(decisionUrl);
const enabled = Boolean(modelUrl && planPath && address);
if (modelUrl || planPath || decisionUrl) {
  assert.ok(enabled, "Real-model qualification requires a model URL, frozen plan and Temporal address");
  assert.equal(process.env.AGAT_TEST_TEMPORAL_STATE_STORE, "postgresql");
  assert.ok(selectedTransport === "isolated" || selectedTransport === "session", "Each transport requires its own database/server");
}
const fixturePath = "docs/qualification/local-decisions/performance/rag-workflow.fixture.json";
const fixture = JSON.parse(fs.readFileSync(path.join(root, fixturePath), "utf8")) as Fixture;
const workflowBundle = { codePath: path.join(root, "apps/temporal-worker/dist/workflow-bundle.js") };
const primaryName = "qwen3:8b";

async function body(req: http.IncomingMessage) {
  const chunks: Buffer[] = []; let size = 0;
  for await (const chunk of req) { size += chunk.length; assert.ok(size <= 131072); chunks.push(chunk); }
  return Buffer.concat(chunks).toString("utf8");
}

for (const transport of ["isolated", "session"] as const) {
  test(`Temporal RAG real-model ${transport}${shadowEnabled ? " shadow" : ""}: preserves real outputs across retry, worker restart and native replay`,
    { skip: !enabled || selectedTransport !== transport, timeout: 240_000 }, async () => {
    const target = origin(modelUrl!), planFile = path.resolve(planPath!);
    assert.ok(planFile.startsWith(path.join(root, "docs") + path.sep));
    const directory = path.dirname(planFile), output = path.join(directory, `${transport}.json`);
    assert.ok(!fs.existsSync(output));
    const planBytes = fs.readFileSync(planFile), plan = JSON.parse(planBytes.toString());
    assert.equal(plan.schema, `agat.temporal.real-rag-plan.v${shadowEnabled ? 2 : 1}`);
    assert.equal(plan.sourceSha256[fixturePath], digest(fs.readFileSync(path.join(root, fixturePath))));
    assert.deepEqual(plan.fixture, fixture);
    assert.equal(plan.shadow, shadowEnabled); assert.deepEqual(plan.transports, ["isolated", "session"]);
    const primary = await primaryIdentity(target, primaryName, plan.models[primaryName]);
    const embedding = await embeddingIdentity(target, fixture.rag!.embeddingModel, plan.models[fixture.rag!.embeddingModel], json);
    const decision = shadowEnabled ? await decisionProfile(origin(decisionUrl!)) : undefined;
    if (decision) assert.deepEqual(decision, plan.decision.profile);
    const shadow = decision ? normalizeDecisionShadowConfig({ mode: "shadow", profileJson: decision.profileJson,
      timeoutMs: 10_000, ...fixture.shadow }) : undefined;
    const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "agat-temporal-real-rag-"));
    const dbPath = path.join(temporary, "state.sqlite"), artifacts = path.join(temporary, "artifacts");
    const taskQueue = `real-rag-${randomUUID()}`;
    const store = new AgatStore(dbPath, { seedDemo: false, temporalProcesses: true, decisionShadowEnabled: shadowEnabled, artifactsDir: artifacts,
      stateStoreDriver: "postgresql", region: "eu-test-1", residencyDomain: "eu-test", postgresSchemaMode: "runtime",
      coordinatorInstanceId: `${taskQueue}-observer`, postgres: {
        systemUrl: process.env.AGAT_POSTGRES_URL!, tenantUrl: process.env.AGAT_POSTGRES_TENANT_URL!, roleMode: "runtime",
        applicationName: "temporal-real-rag-observer", poolMax: 1, connectTimeoutMs: 5_000, idleTimeoutMs: 30_000,
        statementTimeoutMs: 30_000, sslMode: "disable" } });
    const children: ReturnType<typeof processChild>[] = [];
    const primaryCalls: Array<Record<string, any>> = [], embeddingCalls: Array<Record<string, any>> = [];
    const decisionCalls: Array<Record<string, any>> = [];
    const ticks: Array<{ path: string; response: unknown; dropped: boolean }> = [];
    const serverErrors: string[] = [], cancelled = new AbortController();
    const started = performance.now(), clock = () => Number((performance.now() - started).toFixed(3));
    let coordinatorUrl = "", dropNextTick = true, release: (() => void) | undefined;
    let connection: Connection | undefined, result: Record<string, unknown> | undefined;
    let failure: string | undefined;
    const modelProxy = http.createServer(async (req, res) => {
      if (serverErrors.length) { req.resume(); res.writeHead(503).end(); return; }
      const row: Record<string, any> = { startedMs: clock() };
      try {
        assert.equal(req.method, "POST");
        const input = JSON.parse(await body(req));
        if (req.url === "/v1/embeddings") {
          embeddingCalls.push(row); assert.ok(embeddingCalls.length <= 5);
          const forwarded = await forwardEmbedding(input, embedding.name, target, json, cancelled.signal);
          Object.assign(row, forwarded.evidence, { status: "completed", finishedMs: clock() });
          res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(forwarded.body));
          return;
        }
        assert.equal(req.url, "/v1/chat/completions");
        primaryCalls.push(row); assert.ok(primaryCalls.length <= 3);
        assert.equal(input.model, primaryName); assert.equal(input.stream, false);
        assert.ok(Array.isArray(input.messages) && !input.tools && input.temperature === .2);
        const prompt = input.messages.map((message: any) => message.content).join("\n");
        row.messagesSha256 = digest(JSON.stringify(input.messages));
        row.retrievedSourceIds = fixture.rag!.sources.filter(source => prompt.includes(source.content)).map(source => source.id);
        assert.equal(row.retrievedSourceIds.length, 2);
        const answer = await json(`${target}/api/chat`, { model: primaryName, messages: input.messages,
          stream: false, keep_alive: "5m", ...generation }, 45_000, cancelled.signal);
        assert.ok(answer.done && answer.done_reason === "stop" && answer.model === primaryName);
        assert.ok(typeof answer.message?.content === "string" && answer.message.content.trim()
          && !answer.message.tool_calls?.length && !answer.message.thinking?.trim());
        assert.ok(Number.isInteger(answer.prompt_eval_count) && answer.prompt_eval_count > 0);
        assert.ok(Number.isInteger(answer.eval_count) && answer.eval_count > 0);
        Object.assign(row, { output: answer.message.content.trim(), outputSha256: digest(answer.message.content.trim()),
          inputTokens: answer.prompt_eval_count, outputTokens: answer.eval_count, nativeTotalMs: answer.total_duration / 1e6,
          modelFinishedMs: clock(), doneReason: answer.done_reason });
        // Keep a real completed response at the HTTP boundary during Temporal
        // restart. The Python worker and model result stay unchanged.
        if (primaryCalls.length === 2) await new Promise<void>(resolve => { release = resolve; });
        res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({
          choices: [{ message: { role: "assistant", content: answer.message.content }, finish_reason: "stop" }],
          usage: { prompt_tokens: answer.prompt_eval_count, completion_tokens: answer.eval_count } }));
        Object.assign(row, { status: "completed", finishedMs: clock() });
      } catch (error) {
        serverErrors.push(error instanceof Error ? error.name : "proxy_failure");
        Object.assign(row, { status: "failed", finishedMs: clock() });
        if (!res.destroyed) res.writeHead(502).end();
      }
    });
    const tickProxy = http.createServer(async (req, res) => {
      try {
        assert.equal(req.method, "POST");
        assert.match(req.url!, /^\/api\/v1\/internal\/processes\/[a-z0-9-]+\/tick$/);
        assert.equal(req.headers["x-agat-temporal-token"], "real-rag-internal");
        const raw = await body(req); assert.deepEqual(JSON.parse(raw), { projectId: "default" });
        const response = await fetch(`${coordinatorUrl}${req.url}`, { method: "POST", body: raw,
          headers: { "content-type": "application/json", "x-agat-temporal-token": "real-rag-internal" },
          signal: AbortSignal.timeout(5_000) });
        assert.equal(response.status, 200); const text = await response.text();
        const dropped = dropNextTick; dropNextTick = false;
        ticks.push({ path: req.url!, response: JSON.parse(text), dropped });
        if (dropped) res.destroy(); else res.writeHead(200, { "content-type": "application/json" }).end(text);
      } catch (error) {
        serverErrors.push(error instanceof Error ? error.name : "tick_proxy_failure");
        if (!res.destroyed) res.writeHead(502).end();
      }
    });
    const shadowProxy = http.createServer(async (req, res) => {
      const row: Record<string, any> = { startedMs: clock() };
      try {
        assert.ok(decisionUrl && decision);
        const raw = req.method === "POST" ? await body(req) : undefined;
        if (raw) {
          const request = JSON.parse(raw); Object.assign(row, { stageId: request.id, request });
          decisionCalls.push(row); assert.ok(decisionCalls.length <= 3);
          assert.equal(req.headers["x-agat-decision-profile"], decision.profileSha256);
        }
        const upstream = await forwardDecisionRequest(req, res, decisionUrl!, raw, cancelled.signal);
        assert.equal(upstream.httpStatus, 200);
        Object.assign(row, { httpStatus: upstream.httpStatus, result: JSON.parse(upstream.text), finishedMs: clock() });
        res.writeHead(upstream.httpStatus, { "content-type": "application/json" }).end(upstream.text);
      } catch (error) {
        serverErrors.push(error instanceof Error ? error.name : "shadow_proxy_failure");
        if (!res.destroyed) res.writeHead(502).end();
      }
    });
    try {
      const collection = store.createKnowledgeCollection({ name: "real_temporal_rag sources", embeddingModel: embedding.name,
        chunkSize: 4000, chunkOverlap: 0, topK: 2 });
      for (const source of fixture.rag!.sources) store.ingestKnowledgeDocument(String(collection.id),
        { name: source.name, sourceUri: source.sourceUri, mediaType: "text/markdown", content: source.content });
      const agents = fixture.roles.map(role => store.createAgent({ name: role.name, role: role.id, systemPrompt: role.instruction,
        model: primaryName, runtime: "single", runtimeConfig: { profile: "tool_loop_v1", maxIterations: 1 } }));
      const ids = ["start", "recovery-wait", ...fixture.roles.map(role => role.id), "end"];
      const graph: ProcessGraph = { nodes: [
        { id: "start", name: "Start", type: "start", position: { x: 0, y: 0 }, config: {} },
        { id: "recovery-wait", name: "Durable timer", type: "wait", position: { x: 100, y: 100 }, config: { waitSeconds: 5 } },
        ...fixture.roles.map((role, index) => ({ id: role.id, name: role.name, type: "agent" as const,
          position: { x: 200 * (index + 1), y: 0 }, config: { agentId: String(agents[index]!.id), ...(shadow ? { decisionShadow: shadow } : {}) } })),
        { id: "end", name: "End", type: "end", position: { x: 800, y: 0 }, config: {} },
      ], edges: ids.slice(1).map((id, index) => ({ id: `edge-${index}`, source: ids[index]!, target: id, branch: "default" })),
      requiredKnowledgeCollectionIds: [String(collection.id)] };
      const processDefinition = store.createProcess({ name: "Primary workflow real_temporal_rag", graph });
      store.publishProcess(String(processDefinition.id));
      const env = { ...cleanEnv(), AGAT_HOST: "127.0.0.1", AGAT_PORT: "0", AGAT_DB_PATH: dbPath,
        AGAT_ARTIFACTS_DIR: artifacts, AGAT_SEED_DEMO: "false", AGAT_SERVE_WEB: "false", AGAT_MCP_ENABLED: "false",
        AGAT_A2A_ENABLED: "false", AGAT_SANDBOX_ENABLED: "false", AGAT_LOCAL_WORKER_LAUNCHER: "false",
        AGAT_ADMIN_TOKEN: "real-rag-admin", AGAT_ENROLLMENT_TOKEN: "real-rag-enroll",
        AGAT_REQUIRE_SIGNED_WORKER_RELEASES: "false", AGAT_REQUIRE_WORKER_PROVENANCE: "false",
        AGAT_REQUIRE_WORKER_RUNTIME_ATTESTATION: "false", AGAT_DECISION_SHADOW_ENABLED: String(shadowEnabled),
        AGAT_TEMPORAL_ENABLED: "true", AGAT_TEMPORAL_ADDRESS: address!, AGAT_TEMPORAL_NAMESPACE: "default",
        AGAT_TEMPORAL_TASK_QUEUE: taskQueue, AGAT_TEMPORAL_INTERNAL_TOKEN: "real-rag-internal",
        AGAT_STATE_STORE_DRIVER: "postgresql", AGAT_ARTIFACT_STORE_DRIVER: "postgresql",
        AGAT_POSTGRES_URL: globalThis.process.env.AGAT_POSTGRES_URL, AGAT_POSTGRES_TENANT_URL: globalThis.process.env.AGAT_POSTGRES_TENANT_URL,
        AGAT_POSTGRES_SSL_MODE: "disable", AGAT_POSTGRES_POOL_MAX: "2", AGAT_REGION: "eu-test-1", AGAT_RESIDENCY_DOMAIN: "eu-test",
        AGAT_COORDINATOR_INSTANCE_ID: `${taskQueue}-coordinator`, AGAT_KNOWLEDGE_SEARCH_EXECUTION: "isolated",
        AGAT_KNOWLEDGE_SEARCH_MAX_PENDING: "4", AGAT_KNOWLEDGE_SEARCH_TIMEOUT_MS: "1000" };
      const coordinator = processChild(globalThis.process.execPath, ["apps/coordinator/dist/server.js"], env);
      children.push(coordinator);
      coordinatorUrl = `http://127.0.0.1:${(await coordinator.ready(/АГАТ слушает http:\/\/127\.0\.0\.1:(\d+)/))[1]}`;
      const tickUrl = await listen(tickProxy), proxyUrl = await listen(modelProxy);
      const shadowProxyUrl = shadowEnabled ? await listen(shadowProxy) : undefined;
      const startTemporal = async (identity: string) => {
        const child = processChild(globalThis.process.execPath, ["apps/temporal-worker/dist/worker.js"], {
          ...env, AGAT_COORDINATOR_INTERNAL_URL: tickUrl, AGAT_TEMPORAL_METRICS_ADDRESS: "127.0.0.1:0", AGAT_TEMPORAL_WORKER_ID: identity });
        children.push(child); await child.ready(/Temporal worker слушает/); return child;
      };
      const firstIdentity = `${taskQueue}-before`, secondIdentity = `${taskQueue}-after`;
      const first = await startTemporal(firstIdentity);
      connection = await Connection.connect({ address, connectTimeout: 5_000 });
      const client = new Client({ connection, namespace: "default" });
      const python = processChild("python3", ["workers/agat_worker.py", "--coordinator", coordinatorUrl,
        "--enrollment-token", "real-rag-enroll", "--credentials", path.join(temporary, "worker.json"),
        "--name", "real-temporal-rag-worker", "--models", primaryName, "--embedding-models", embedding.name,
        "--region", "eu-test-1", "--residency-domain", "eu-test", "--model-url", `${proxyUrl}/v1`, "--model-api-key", "local-probe",
        "--model-discovery", "off", "--no-web", "--poll-interval", "0.2", "--concurrency", "1", "--embedding-transport", transport,
        ...(shadowProxyUrl ? ["--decision-url", shadowProxyUrl] : [])],
        { ...cleanEnv(), AGAT_EMBEDDING_IDLE_TIMEOUT: "0", AGAT_OTEL_ENABLED: "false", OTEL_SDK_DISABLED: "true", NO_PROXY: "127.0.0.1,localhost" });
      children.push(python);
      await eventually(() => {
        assert.equal(python.child.exitCode, null, python.log()); assert.equal(python.child.signalCode, null, python.log());
        const docs = (store.exportKnowledge() as any).collections.find((row: any) => row.id === collection.id)?.documents;
        return docs?.length === 2 && docs.every((doc: any) => doc.status === "ready");
      }, "Real embedding ingestion did not finish", 60_000);
      const ingestion = verifyIngestion(store.exportKnowledge(), String(collection.id), fixture.rag!);
      assert.equal(ingestion.dimensions, 768);
      const readiness = await fetch(`${coordinatorUrl}/api/v1/processes/${processDefinition.id}/preflight`, { method: "POST",
        headers: { "x-agat-admin-token": "real-rag-admin", "content-type": "application/json" }, body: JSON.stringify({ version: 1 }),
        signal: AbortSignal.timeout(5_000) });
      assert.equal(readiness.status, 200); assert.equal((await readiness.json() as any).runnableNow, true);
      const start = await fetch(`${coordinatorUrl}/api/v1/processes/${processDefinition.id}/start`, { method: "POST",
        headers: { "x-agat-admin-token": "real-rag-admin", "content-type": "application/json" }, body: JSON.stringify({ input: fixture.input }),
        signal: AbortSignal.timeout(5_000) });
      assert.equal(start.status, 201, await start.clone().text());
      const instance = await start.json() as { id: string; runId: string };
      assert.equal(store.getProcessInstance(instance.id)!.runtime, "temporal");
      const workflowId = `agat-process-${instance.id}`, handle = client.workflow.getHandle(workflowId);
      const workflowRunId = (await handle.describe()).runId;
      await eventually(() => { assert.deepEqual(serverErrors, []); return Boolean(release); }, "Second real model response did not reach the hold", 90_000);
      await eventually(async () => Boolean((await handle.fetchHistory()).events?.some(event =>
        Number(event.activityTaskStartedEventAttributes?.attempt) >= 2)), "Lost tick reply did not cause a real Activity retry");
      assert.equal(ticks.filter(row => row.dropped).length, 1); assert.equal(primaryCalls.length, 2);
      const acceptedFirst = (store.getRunTrace(instance.runId)! as any).run.stages.find((stage: any) => stage.processNodeId === fixture.roles[0]!.id);
      assert.equal(acceptedFirst.status, "completed"); const firstSnapshot = JSON.stringify(acceptedFirst);
      const recovery = { firstAcceptedStageSha256: digest(firstSnapshot), heldResponseAtMs: clock(), killedAtMs: 0,
        restoredAtMs: 0, releasedAtMs: 0, firstIdentity, secondIdentity, primaryCallsBeforeRelease: 0 };
      const firstObservation = shadowEnabled ? (store.getRunTrace(instance.runId)! as any).decisionObservations
        .find((row: any) => row.stageId === acceptedFirst.id) : undefined;
      if (shadowEnabled) { assert.ok(firstObservation); assert.equal(decisionCalls.length, 1); }
      const observationSnapshot = shadowEnabled ? JSON.stringify(firstObservation) : undefined;
      assert.deepEqual(await first.stop("SIGKILL"), { code: null, signal: "SIGKILL" });
      recovery.killedAtMs = clock();
      const second = await startTemporal(secondIdentity);
      await within(handle.query("processState"), "Replacement Temporal worker could not restore the workflow", 30_000);
      recovery.restoredAtMs = clock(); recovery.primaryCallsBeforeRelease = primaryCalls.length;
      assert.equal(primaryCalls.length, 2); assert.equal((await handle.describe()).runId, workflowRunId);
      if (shadowEnabled) assert.equal(decisionCalls.length, 1);
      recovery.releasedAtMs = clock();
      release!(); release = undefined;
      assert.equal((await within(handle.result(), "Real RAG did not finish after recovery", 60_000) as any).status, "completed");
      assert.deepEqual(await python.stop(), { code: 0, signal: null }, python.log());
      assert.deepEqual(await second.stop(), { code: 0, signal: null }, second.log());
      const trace = store.getRunTrace(instance.runId)! as any;
      assert.equal(trace.truncated, false); assert.equal(trace.run.status, "completed");
      const stages = trace.run.stages.filter((stage: any) => fixture.roles.some(role => role.id === stage.processNodeId));
      assert.equal(stages.length, 3); assert.equal(primaryCalls.length, 3);
      assert.equal(embeddingCalls.reduce((sum, call) => sum + call.items, 0), 5);
      assert.equal(JSON.stringify(stages[0]), firstSnapshot); assert.equal(trace.decisionObservations.length, shadowEnabled ? 3 : 0);
      assert.equal(decisionCalls.length, shadowEnabled ? 3 : 0);
      if (shadowEnabled) {
        assert.equal(JSON.stringify(trace.decisionObservations.find((row: any) => row.stageId === stages[0].id)), observationSnapshot);
        const expectedProfile = JSON.parse(decision!.profileJson);
        for (const stage of stages) {
          const rows = trace.decisionObservations.filter((row: any) => row.stageId === stage.id);
          const calls = decisionCalls.filter(row => row.stageId === stage.id);
          assert.equal(rows.length, 1); assert.equal(calls.length, 1);
          const observation = rows[0].observation;
          assert.equal(observation.mode, "shadow"); assert.equal(observation.fallback, "primary");
          assert.ok(["ok", "abstain"].includes(observation.status));
          assert.deepEqual(observation.result, calls[0]!.result);
          for (const field of ["model", "policy", "calibration", "runtimeVersion", "schemaVersion", "inputFingerprintVersions"])
            assert.deepEqual(observation.result[field], expectedProfile[field]);
          assert.equal(observation.result.generatedTokens, 0);
          assert.equal(calls[0]!.request.state, stage.input ?? fixture.input);
        }
      }
      const retrieval = stages.map((stage: any, index: number) => {
        assert.equal(stage.status, "completed"); assert.equal(stage.attempt, 1);
        assert.equal(stage.output, primaryCalls[index]!.output); assert.equal(stage.metrics.modelCalls, 1);
        const value = verifyRetrieval(trace, stage, ingestion);
        assert.ok(value.bothSourcesCited); assert.deepEqual(value.unknownMarkers, []); return value;
      });
      assert.equal(store.getRunKnowledgeSources(instance.runId)!.length, 6);
      assert.equal(store.db.prepare("SELECT current_user AS role").get()!.role, "agat_system");
      const foreign = `real-rag-foreign-${randomUUID()}`; store.createProject({ id: foreign, name: foreign });
      const tenant = new pg.Client({ connectionString: globalThis.process.env.AGAT_POSTGRES_TENANT_URL, ssl: false });
      const visibility: Record<string, number[]> = {};
      try {
        await tenant.connect();
        assert.deepEqual((await tenant.query("SELECT current_user AS role, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).rows[0],
          { role: "agat_tenant", rolsuper: false, rolbypassrls: false });
        await assert.rejects(tenant.query("SELECT id FROM worker_releases"), { code: "42501" });
        for (const project of ["default", foreign]) {
          await tenant.query("BEGIN"); await tenant.query("SELECT set_config('agat.current_project_id', $1, true)", [project]);
          visibility[project === "default" ? "own" : "foreign"] = [
            (await tenant.query("SELECT id FROM runs WHERE id = $1", [instance.runId])).rowCount!,
            (await tenant.query("SELECT id FROM knowledge_retrievals WHERE run_id = $1", [instance.runId])).rowCount!,
            (await tenant.query("SELECT id FROM knowledge_chunks WHERE collection_id = $1", [collection.id])).rowCount!];
          await tenant.query("ROLLBACK");
        }
        assert.deepEqual(visibility, { own: [1, 3, 2], foreign: [0, 0, 0] });
      } finally { await tenant.end(); }
      const history = await handle.fetchHistory();
      assert.ok(history.events?.some(event => event.timerFiredEventAttributes));
      const identities = new Set(history.events?.map(event => event.workflowTaskStartedEventAttributes?.identity).filter(Boolean));
      assert.ok(identities.has(firstIdentity) && identities.has(secondIdentity));
      const serializedHistory = JSON.parse(historyToJSON(history), (key, value) => key === "identity" && typeof value === "string"
        && value !== firstIdentity && value !== secondIdentity ? "fixture-client" : value);
      const checkpoint = { primary: primaryCalls.length, embedding: embeddingCalls.length, decision: decisionCalls.length,
        ticks: ticks.length, trace: JSON.stringify(trace) };
      await Worker.runReplayHistory({ workflowBundle }, serializedHistory, workflowId);
      assert.deepEqual({ primary: primaryCalls.length, embedding: embeddingCalls.length, decision: decisionCalls.length, ticks: ticks.length,
        trace: JSON.stringify(store.getRunTrace(instance.runId)) }, checkpoint);
      assert.deepEqual(serverErrors, []);
      assert.deepEqual(await primaryIdentity(target, primaryName, plan.models[primaryName]), primary);
      assert.deepEqual(await embeddingIdentity(target, embedding.name, plan.models[embedding.name], json), embedding);
      if (decision) assert.deepEqual(await decisionProfile(decisionUrl!), decision);
      assert.deepEqual(await coordinator.stop(), { code: 0, signal: null }, coordinator.log());
      result = { workflowId, workflowRunId, instanceId: instance.id, primary, embedding, ingestion, primaryCalls, embeddingCalls,
        ticks, trace, retrieval, recovery, history: serializedHistory,
        ...(decision ? { decision, decisionCalls, shadowRecovery: { firstAcceptedObservationSha256: digest(observationSnapshot!),
          decisionCallsBeforeRelease: 1, primaryFallbackPreserved: true } } : {}),
        workflowBundleSha256: digest(fs.readFileSync(workflowBundle.codePath)),
        children: children.map(child => ({ pid: child.child.pid, exitCode: child.child.exitCode, signal: child.child.signalCode })),
        database: { driver: "postgresql", runtimeRole: "agat_system",
          tenantRole: "agat_tenant", visibility, releaseRegistryDenied: true, retrievalExecution: "isolated" },
        checks: { activityRetried: true, temporalWorkerKilledAndReplaced: true, sameWorkflowRun: true,
          firstAcceptedStageUnchanged: true, realModelOutputsPreserved: true, durableTimerFired: true, nativeReplayWithoutSideEffects: true,
          ...(shadowEnabled ? { shadowObservationsPreserved: true } : { shadowDisabled: true }), childrenDrained: true }, qualification: "not_assessed" };
    } catch (error) {
      failure = error instanceof Error ? error.name : "qualification_failed";
      throw error;
    } finally {
      release?.(); cancelled.abort();
      for (const child of children.reverse()) await child.stop();
      await close(modelProxy); await close(tickProxy); await close(shadowProxy); await connection?.close(); store.close();
      fs.rmSync(temporary, { recursive: true, force: true });
      fs.writeFileSync(output, JSON.stringify({ schema: `agat.temporal.real-rag.v${shadowEnabled ? 2 : 1}`, status: result ? "pass" : "fail", failure: failure ?? null,
        transport, planSha256: digest(planBytes), elapsedMs: clock(), ...result,
        ...(result ? {} : { primaryCalls, embeddingCalls, decisionCalls, ticks, serverErrors }) }, null, 2) + "\n", { flag: "wx" });
    }
  });
}
