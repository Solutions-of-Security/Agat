import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import { AgatStore } from "../../apps/coordinator/src/database.js";
import { loadConfig } from "../../apps/coordinator/src/config.js";
import { createCoordinatorServer } from "../../apps/coordinator/src/server.js";
import { normalizeDecisionShadowConfig } from "../../apps/coordinator/src/local-decisions.js";
import type { ProcessGraph } from "../../apps/coordinator/src/types.js";
import { DecisionProxyCancelled, forwardDecisionRequest } from "./decision-shadow-proxy.js";
import { forwardEmbedding, validateRag, verifyIngestion, verifyRetrieval, type RagFixture } from "./decision-rag.js";

const root = fileURLToPath(new URL("../../", import.meta.url));
export const digest = (value: string | Buffer) => createHash("sha256").update(value).digest("hex");
export const generation = { think: false, options: { temperature: 0.2, seed: 0, num_ctx: 8192, num_predict: 384 } };
export type Phase = { id: string; concurrency: number; runs: number; shadow: boolean };
export type Fixture = { schemaVersion: string; provenance: string; input: string;
  roles: Array<{ id: string; name: string; instruction: string }>; shadow: Record<string, unknown>; rag?: RagFixture };

export function origin(value: string) {
  const url = new URL(value);
  assert.ok(url.protocol === "http:" && url.hostname === "127.0.0.1" && url.port
    && !url.username && !url.password && !url.search && !url.hash && url.pathname === "/", "Explicit loopback URL required");
  return url.origin;
}
export function validatePlan(fixture: Fixture, phases: Phase[]) {
  assert.equal(fixture.schemaVersion, "agat.decision.workflow-fixture.v1");
  assert.ok(typeof fixture.input === "string" && fixture.input.length > 0 && fixture.input.length < 8000);
  assert.equal(fixture.roles.length, 3);
  assert.equal(new Set(fixture.roles.map(role => role.id)).size, 3);
  assert.ok(fixture.roles.every(role => /^[a-z][a-z0-9_]{0,30}$/.test(role.id)
    && typeof role.instruction === "string" && role.instruction.length > 0 && role.instruction.length < 2000));
  assert.ok(phases.length > 0 && phases.length <= 4 && new Set(phases.map(p => p.id)).size === phases.length);
  assert.ok(phases.every(p => /^[a-z0-9_-]{1,40}$/.test(p.id) && Number.isInteger(p.concurrency) && p.concurrency >= 1
    && p.concurrency <= 2 && Number.isInteger(p.runs) && p.runs >= 1 && p.runs <= 4 && typeof p.shadow === "boolean"));
  assert.ok(phases.reduce((sum, p) => sum + p.runs, 0) <= 12, "At most twelve repeated workflows");
  if (fixture.rag) validateRag(fixture.rag);
}
export async function json(url: string, body?: unknown, timeoutMs = 5000, cancelled?: AbortSignal) {
  const response = await fetch(url, { method: body === undefined ? "GET" : "POST", redirect: "error",
    headers: { "content-type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body),
    signal: cancelled ? AbortSignal.any([cancelled, AbortSignal.timeout(timeoutMs)]) : AbortSignal.timeout(timeoutMs) });
  assert.ok(response.ok, "Probe endpoint failed");
  const text = await response.text();assert.ok(Buffer.byteLength(text) <= 512_000, "Probe response too large");
  return JSON.parse(text);
}
export async function primaryIdentity(url: string, name: string, expectedDigest: string) {
  origin(url);assert.match(expectedDigest, /^[a-f0-9]{64}$/);
  const [tags, show, version] = await Promise.all([json(`${url}/api/tags`), json(`${url}/api/show`, { model: name }), json(`${url}/api/version`)]);
  const model = tags.models?.find((item: Record<string, unknown>) => item.name === name);
  assert.equal(model?.digest, expectedDigest, "Primary weights changed");
  assert.ok(!model.remote_host && !model.remote_model && !show.remote_host && !show.remote_model && show.capabilities?.includes("completion"));
  assert.ok(show.details?.family === "qwen3", "This bounded diagnostic is for the installed Qwen3 model");
  return { name, digest: expectedDigest, version: version.version, details: show.details,
    descriptionSha256: digest(JSON.stringify(show)), generation };
}
export async function decisionProfile(url: string) {
  origin(url);const health = await json(`${url}/health`);
  assert.equal(health.status, "ready");assert.equal(health.mode, "shadow");
  assert.equal(digest(health.profileJson), health.profileSha256);
  normalizeDecisionShadowConfig({ mode: "shadow", profileJson: health.profileJson, timeoutMs: 10_000, kind: "boolean", question: "Проверка профиля",
    options: [{ id: "yes", description: "Да", value: true }, { id: "no", description: "Нет", value: false }] });
  return { profileJson: health.profileJson as string, profileSha256: health.profileSha256 as string };
}
async function listen(server: http.Server) {
  await new Promise<void>((resolve, reject) => { server.once("error", reject);server.listen(0, "127.0.0.1", resolve); });
  const address = server.address();assert.ok(address && typeof address === "object");
  return `http://127.0.0.1:${address.port}`;
}
async function close(server: http.Server) {
  server.closeAllConnections();
  if (server.listening) await new Promise<void>(resolve => server.close(() => resolve()));
}
async function stop(worker: ChildProcess | undefined) {
  if (!worker || worker.exitCode !== null || worker.signalCode !== null) return;
  const ended = new Promise<void>(resolve => worker.once("exit", () => resolve()));
  worker.kill("SIGTERM");
  const timer = setTimeout(() => worker.kill("SIGKILL"), 5000);
  try { await ended; } finally { clearTimeout(timer); }
}
async function body(req: http.IncomingMessage) {
  let size = 0;const chunks: Buffer[] = [];
  for await (const chunk of req) { size += chunk.length;assert.ok(size <= 131072, "Proxy input too large");chunks.push(chunk); }
  return Buffer.concat(chunks).toString("utf8");
}

/** Full coordinator/worker chain. The adapter forwards every primary request to native Ollama. */
export async function workflowPhase(options: { phase: Phase; fixture: Fixture; model: string; primaryUrl: string;
  decisionUrl: string; profileJson: string; timeoutMs?: number; metadataId?: string }) {
  const { phase, fixture, model, primaryUrl, decisionUrl, profileJson } = options;
  validatePlan(fixture, [phase]);origin(primaryUrl);origin(decisionUrl);
  const metadataId = options.metadataId ?? phase.id;
  assert.match(metadataId, /^[a-z0-9_-]{1,40}$/);
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "agat-primary-workflow-"));
  const store = new AgatStore(path.join(temporary, "state.sqlite"), { seedDemo: false, decisionShadowEnabled: true,
    artifactsDir: path.join(temporary, "artifacts") });
  // Worker slots alone do not override the coordinator's default global limit of one.
  store.updateScheduler("sequential", phase.concurrency);
  const started = performance.now();const clock = () => Number((performance.now() - started).toFixed(3));
  const primaryCalls: Array<Record<string, any>> = [], decisionCalls: Array<Record<string, any>> = [], embeddingCalls: Array<Record<string, any>> = [];
  const cancelled = new AbortController();
  let active = 0, maxActive = 0, embeddingActive = 0, maxEmbeddingActive = 0, failure: string | undefined;
  let ragEvidence: (ReturnType<typeof verifyIngestion> & { ingestionMs: number }) | undefined;
  const proxy = http.createServer(async (req, res) => {
    if (fixture.rag && req.method === "POST" && req.url === "/v1/embeddings") {
      const row: Record<string, any> = { startedMs: clock() };embeddingCalls.push(row);
      embeddingActive++;maxEmbeddingActive = Math.max(maxEmbeddingActive, embeddingActive);
      try {
        assert.ok(embeddingCalls.length <= 2 + phase.runs * 3, "Unexpected embedding retry");
        const result = await forwardEmbedding(JSON.parse(await body(req)), fixture.rag.embeddingModel, primaryUrl, json, cancelled.signal);
        Object.assign(row, result.evidence, { finishedMs: clock(), status: "completed" });
        res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(result.body));
      } catch { failure = "embedding_adapter_failed";Object.assign(row, { finishedMs: clock(), status: "failed" });res.writeHead(502).end(); }
      finally { embeddingActive--; }
      return;
    }
    if (req.method !== "POST" || req.url !== "/v1/chat/completions") { res.writeHead(404).end();return; }
    const row: Record<string, any> = { startedMs: clock() };primaryCalls.push(row);active++;maxActive = Math.max(maxActive, active);
    try {
      assert.ok(primaryCalls.length <= phase.runs * 3, "Unexpected primary retry");
      const input = JSON.parse(await body(req));assert.equal(input.model, model);assert.equal(input.stream, false);
      assert.ok(Array.isArray(input.messages) && !input.tools && input.temperature === 0.2, "Unexpected primary request");
      row.messagesSha256 = digest(JSON.stringify(input.messages));
      if (fixture.rag) {
        const prompt = input.messages.map((message: any) => message.content).join("\n");
        row.retrievedSourceIds = fixture.rag.sources.filter(source => prompt.includes(source.content)).map(source => source.id);
        assert.equal(row.retrievedSourceIds.length, 2, "Both complete RAG sources must reach the primary model");
      }
      const result = await json(`${primaryUrl}/api/chat`, { model, messages: input.messages, stream: false, keep_alive: "5m", ...generation }, 45_000, cancelled.signal);
      Object.assign(row, { doneReason: result.done_reason, inputTokens: result.prompt_eval_count, outputTokens: result.eval_count });
      assert.ok(result.done === true && result.done_reason === "stop" && result.model === model, "Incomplete primary generation");
      assert.ok(typeof result.message?.content === "string" && result.message.content.trim() && !result.message.tool_calls?.length
        && !result.message.thinking?.trim(), "Unexpected primary output");
      assert.ok(Number.isInteger(result.prompt_eval_count) && result.prompt_eval_count > 0 && Number.isInteger(result.eval_count) && result.eval_count > 0);
      const content = result.message.content;
      Object.assign(row, { outputSha256: digest(content.trim()), output: content.trim(), inputTokens: result.prompt_eval_count,
        outputTokens: result.eval_count, nativeTotalMs: result.total_duration / 1e6, finishedMs: clock(), status: "completed" });
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ choices: [{ message: { role: "assistant", content }, finish_reason: "stop" }],
        usage: { prompt_tokens: result.prompt_eval_count, completion_tokens: result.eval_count } }));
    } catch { failure = "primary_adapter_failed";row.status = "failed";row.finishedMs = clock();res.writeHead(502).end(); }
    finally { active--; }
  });
  const shadowProxy = http.createServer(async (req, res) => {
    if (!((req.method === "GET" && req.url === "/health") || (req.method === "POST" && req.url === "/v1/decisions"))) {
      res.writeHead(404).end();return;
    }
    const row: Record<string, any> = { startedMs: clock() };
    try {
      const raw = req.method === "POST" ? await body(req) : undefined;
      if (raw) { row.stageId = JSON.parse(raw).id;decisionCalls.push(row);assert.ok(decisionCalls.length <= phase.runs * 3); }
      const upstream = await forwardDecisionRequest(req, res, decisionUrl, raw, cancelled.signal);
      Object.assign(row, { httpStatus: upstream.httpStatus, finishedMs: clock(), result: JSON.parse(upstream.text) });
      res.writeHead(upstream.httpStatus, { "content-type": "application/json" });res.end(upstream.text);
    } catch (error) {
      failure = error instanceof DecisionProxyCancelled ? "decision_client_disconnected" : "decision_transport_failed";
      Object.assign(row, { finishedMs: clock(), failure });
      if (!res.destroyed) res.writeHead(502).end();
    }
  });
  const coordinator = createCoordinatorServer({ ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false,
    adminToken: "workflow-probe-admin", enrollmentToken: "workflow-probe-enrollment", oidcEnabled: false,
    mcpEnabled: false, sandboxEnabled: false, a2aEnabled: false, localWorkerLauncherEnabled: false }, store);
  let worker: ChildProcess | undefined;
  try {
    const primaryProxyUrl = await listen(proxy), shadowUrl = await listen(shadowProxy), coordinatorUrl = await listen(coordinator);
    const deadline = performance.now() + (options.timeoutMs ?? 180_000);
    const startWorker = () => {
      worker = spawn("python3", ["workers/agat_worker.py", "--coordinator", coordinatorUrl, "--enrollment-token", "workflow-probe-enrollment",
        "--credentials", path.join(temporary, "worker.json"), "--name", "workflow-probe-worker", "--models", model,
        "--model-url", `${primaryProxyUrl}/v1`, "--model-api-key", "probe-local", "--model-discovery", "off", "--no-web", "--poll-interval", "0.2",
        "--concurrency", String(phase.concurrency), ...(phase.shadow ? ["--decision-url", shadowUrl] : []),
        ...(fixture.rag ? ["--embedding-models", fixture.rag.embeddingModel] : [])],
      { cwd: root, stdio: ["ignore", "ignore", "ignore"], env: { ...globalThis.process.env, AGAT_OTEL_ENABLED: "false",
        OTEL_SDK_DISABLED: "true", NO_PROXY: "127.0.0.1,localhost" } });
      worker.once("error", () => { failure = "worker_start_failed"; });
    };
    const collectionIds: string[] = [];
    if (fixture.rag) {
      const ingestionStart = clock();
      const collection = store.createKnowledgeCollection({ name: `RAG sources ${metadataId}`, embeddingModel: fixture.rag.embeddingModel,
        chunkSize: 4000, chunkOverlap: 0, topK: 2 });
      collectionIds.push(String(collection.id));
      for (const source of fixture.rag.sources) {
        const document = store.ingestKnowledgeDocument(String(collection.id), { name: source.name, sourceUri: source.sourceUri,
          mediaType: "text/markdown", content: source.content });
        assert.equal(document.chunkCount, 1, "This diagnostic requires one chunk per source");
      }
      startWorker();
      const ingestionDeadline = Math.min(deadline, performance.now() + 60_000);
      while (true) {
        assert.ok(performance.now() < ingestionDeadline && !failure, "RAG indexing failed or timed out");
        assert.equal(worker!.exitCode, null);assert.equal(worker!.signalCode, null);
        const exported = store.exportKnowledge() as any;
        const documents = exported.collections.find((entry: any) => entry.id === collection.id)?.documents;
        assert.ok(Array.isArray(documents) && documents.length === 2);
        assert.ok(documents.every((document: any) => document.status !== "failed"), "RAG indexing failed");
        if (documents.every((document: any) => document.status === "ready")) {
          ragEvidence = { ...verifyIngestion(exported, String(collection.id), fixture.rag), ingestionMs: Number((clock() - ingestionStart).toFixed(3)) };
          break;
        }
        await delay(50);
      }
    }
    const agents = fixture.roles.map(role => store.createAgent({ name: role.name, role: role.id, systemPrompt: role.instruction,
      model, runtime: "single", runtimeConfig: { profile: "tool_loop_v1", maxIterations: 1 } }));
    const shadow = normalizeDecisionShadowConfig({ mode: "shadow", profileJson, timeoutMs: 10_000, ...fixture.shadow });
    const ids = ["start", ...fixture.roles.map(role => role.id), "end"];
    const graph: ProcessGraph = { nodes: [
      { id: "start", name: "Start", type: "start", position: { x: 0, y: 0 }, config: {} },
      ...fixture.roles.map((role, index) => ({ id: role.id, name: role.name, type: "agent" as const,
        position: { x: 200 * (index + 1), y: 0 }, config: { agentId: String(agents[index]!.id), ...(phase.shadow ? { decisionShadow: shadow } : {}) } })),
      { id: "end", name: "End", type: "end", position: { x: 800, y: 0 }, config: {} },
    ], edges: ids.slice(1).map((id, index) => ({ id: `edge-${index}`, source: ids[index]!, target: id, branch: "default" })),
    ...(collectionIds.length ? { requiredKnowledgeCollectionIds: collectionIds } : {}) };
    const process = store.createProcess({ name: `Primary workflow ${metadataId}`, graph });store.publishProcess(String(process.id));
    const runs = Array.from({ length: phase.runs }, () => {
      const begin = clock();const instance = store.startProcess(String(process.id), { input: fixture.input })!;
      return { runId: String(instance.runId), startedMs: begin, finishedMs: 0 };
    });
    if (!worker) startWorker();
    while (runs.some(run => !run.finishedMs)) {
      assert.ok(performance.now() < deadline, "Workflow batch exceeded its time budget");
      assert.ok(!failure, failure ?? "Proxy failed");assert.equal(worker!.exitCode, null, "Worker exited before completion");
      assert.equal(worker!.signalCode, null, "Worker terminated before completion");
      for (const run of runs.filter(row => !row.finishedMs)) {
        const current = store.getRun(run.runId)!;
        assert.ok(!["failed", "cancelled"].includes(String(current.status)), "Primary workflow failed");
        if (current.status === "completed") run.finishedMs = clock();
      }
      if (runs.some(run => !run.finishedMs)) await delay(50);
    }
    await stop(worker);
    assert.equal(primaryCalls.length, phase.runs * 3, "Every primary stage must execute exactly once");
    assert.equal(decisionCalls.length, phase.shadow ? phase.runs * 3 : 0);
    if (fixture.rag) {
      assert.equal(embeddingCalls.reduce((count, call) => count + call.items, 0), 2 + phase.runs * 3,
        "Each source and stage query must be embedded once");
      const { ingestionMs: _ingestionMs, ...expected } = ragEvidence!;
      assert.deepEqual(verifyIngestion(store.exportKnowledge(), collectionIds[0]!, fixture.rag), expected, "RAG sources changed during workload");
    }
    const outputs = primaryCalls.map(call => call.outputSha256).sort();const storedOutputs: string[] = [];
    const evidence = runs.map(run => {
      const trace = store.getRunTrace(run.runId)! as Record<string, any>;assert.equal(trace.truncated, false);
      const stages = trace.run.stages.filter((stage: Record<string, any>) => fixture.roles.some(role => role.id === stage.processNodeId));
      assert.equal(stages.length, 3);
      const observations = trace.decisionObservations as Array<Record<string, any>>;
      assert.equal(observations.length, phase.shadow ? 3 : 0);
      const rows = stages.map((stage: Record<string, any>) => {
        assert.equal(stage.status, "completed");assert.equal(stage.metrics.model, model);assert.equal(stage.metrics.modelCalls, 1);
        const outputSha256 = digest(stage.output);storedOutputs.push(outputSha256);
        const found = observations.filter(row => row.stageId === stage.id);
        if (phase.shadow) { assert.equal(found.length, 1);assert.equal(found[0]!.observation.fallback, "primary"); }
        return { id: stage.id, nodeId: stage.processNodeId, position: stage.position, outputSha256, output: stage.output, metrics: stage.metrics,
          ...(ragEvidence ? { retrieval: verifyRetrieval(trace, stage, ragEvidence) } : {}),
          ...(phase.shadow ? { observation: found[0]!.observation } : {}) };
      });
      return { ...run, wallMs: Number((run.finishedMs - run.startedMs).toFixed(3)), stages: rows };
    });
    assert.deepEqual(storedOutputs.sort(), outputs, "Shadow must not replace primary outputs");
    assert.ok(maxActive >= Math.min(phase.concurrency, phase.runs), "Configured concurrency was not exercised");
    const overlap = decisionCalls.filter(decision => primaryCalls.some(primary =>
      primary.startedMs < decision.finishedMs && primary.finishedMs > decision.startedMs)).length;
    return { phase, status: "observed", failure: null, elapsedMs: clock(), primaryCalls, decisionCalls, workflows: evidence,
      ...(ragEvidence ? { rag: ragEvidence, embeddingCalls, maxEmbeddingRequestsInFlight: maxEmbeddingActive } : {}),
      maxPrimaryRequestsInFlight: maxActive, shadowCallsOverlappingPrimaryHttp: overlap,
      scheduler: { mode: "sequential", globalMaxConcurrency: phase.concurrency },
      checks: { allWorkflowsCompleted: true, everyStageExecutedOnce: true, allPrimaryOutputsPreserved: true,
        expectedShadowObservationCount: true, configuredConcurrencyObserved: true },
      limitations: ["Overlap counts HTTP intervals including Ollama queue time, not measured GPU kernels.",
        "One authored task fixture repeated for throughput; no independent quality or production SLO claim."] };
  } catch {
    return { phase, status: "incomplete", failure: failure ?? "workflow_validation_failed", elapsedMs: clock(),
      primaryCalls, decisionCalls, ...(fixture.rag ? { rag: ragEvidence ?? null, embeddingCalls, maxEmbeddingRequestsInFlight: maxEmbeddingActive } : {}),
      workflows: [], maxPrimaryRequestsInFlight: maxActive, shadowCallsOverlappingPrimaryHttp: null,
      scheduler: { mode: "sequential", globalMaxConcurrency: phase.concurrency },
      checks: { allWorkflowsCompleted: false, everyStageExecutedOnce: false, allPrimaryOutputsPreserved: false,
        expectedShadowObservationCount: false, configuredConcurrencyObserved: false }, limitations: ["Incomplete batch; captured calls are diagnostic data only."] };
  } finally {
    cancelled.abort();await stop(worker);await close(coordinator);await close(proxy);await close(shadowProxy);store.close();
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}
