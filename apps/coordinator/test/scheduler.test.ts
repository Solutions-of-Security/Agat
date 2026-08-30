import assert from "node:assert/strict";
import fs from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { afterEach, describe, it } from "node:test";

import { AgatStore } from "../src/database.js";
import type { AgentRuntime, AgentRuntimeProfile, ProcessGraph } from "../src/types.js";

const stores: AgatStore[] = [];
const temporaryDatabases: string[] = [];
const temporaryDirectories: string[] = [];

afterEach(() => {
  while (stores.length) stores.pop()?.close();
  while (temporaryDatabases.length) fs.rmSync(temporaryDatabases.pop()!, { force: true });
  while (temporaryDirectories.length) fs.rmSync(temporaryDirectories.pop()!, { force: true, recursive: true });
});

function createStore(options: { leaseTtlSeconds?: number; artifactsDir?: string } = {}) {
  const store = new AgatStore(":memory:", { seedDemo: false, ...options });
  stores.push(store);
  return store;
}

function addNode(
  store: AgatStore,
  name: string,
  maxConcurrency = 1,
  models = ["test-model"],
  agentRuntimes: AgentRuntime[] = ["single"],
  agentRuntimeProfiles: AgentRuntimeProfile[] = ["tool_loop_v1"],
) {
  const credentials = store.registerNode({
    enrollmentToken: "unused-at-store-layer",
    name,
    platform: "test",
    models,
    maxConcurrency,
    agentRuntimes,
    agentRuntimeProfiles,
  });
  return credentials.id;
}

function linearProcessGraph(agentId = "collector", approvalRequired = false): ProcessGraph {
  return {
    nodes: [
      { id: "start", type: "start", name: "Старт", position: { x: 0, y: 0 }, config: {} },
      {
        id: "agent",
        type: "agent",
        name: "Агент",
        position: { x: 200, y: 0 },
        config: { agentId, approvalRequired },
      },
      { id: "end", type: "end", name: "Завершение", position: { x: 400, y: 0 }, config: {} },
    ],
    edges: [
      { id: "start-agent", source: "start", target: "agent", branch: "default" },
      { id: "agent-end", source: "agent", target: "end", branch: "default" },
    ],
  };
}

function loopingProcessGraph(): ProcessGraph {
  return {
    nodes: [
      { id: "start", type: "start", name: "Старт", position: { x: 0, y: 0 }, config: {} },
      {
        id: "agent",
        type: "agent",
        name: "Редактор",
        position: { x: 200, y: 0 },
        config: { agentId: "editor", approvalRequired: false },
      },
      {
        id: "loop",
        type: "loop",
        name: "Повторить проверку",
        position: { x: 400, y: 0 },
        config: {
          condition: { source: "last_output", operator: "always", value: "", caseSensitive: false },
          maxIterations: 2,
        },
      },
      { id: "end", type: "end", name: "Завершение", position: { x: 600, y: 0 }, config: {} },
    ],
    edges: [
      { id: "start-agent", source: "start", target: "agent", branch: "default" },
      { id: "agent-loop", source: "agent", target: "loop", branch: "default" },
      { id: "loop-agent", source: "loop", target: "agent", branch: "repeat" },
      { id: "loop-end", source: "loop", target: "end", branch: "exit" },
    ],
  };
}

describe("scheduler", () => {
  it("globally serializes work in sequential mode", () => {
    const store = createStore();
    const firstNode = addNode(store, "node-a");
    const secondNode = addNode(store, "node-b");
    store.createRun({ name: "Run A", input: "Input A", approvalRequired: false });
    store.createRun({ name: "Run B", input: "Input B", approvalRequired: false });

    const firstLease = store.leaseNext(firstNode);
    const blockedLease = store.leaseNext(secondNode);

    assert.ok(firstLease);
    assert.equal(blockedLease, null);
  });

  it("uses independent nodes in parallel mode", () => {
    const store = createStore();
    const firstNode = addNode(store, "node-a");
    const secondNode = addNode(store, "node-b");
    store.updateScheduler("parallel");
    store.createRun({ name: "Run A", input: "Input A", approvalRequired: false });
    store.createRun({ name: "Run B", input: "Input B", approvalRequired: false });

    assert.ok(store.leaseNext(firstNode));
    assert.ok(store.leaseNext(secondNode));
  });

  it("respects node load in auto mode", () => {
    const store = createStore();
    const busyNode = addNode(store, "busy-node");
    const availableNode = addNode(store, "available-node");
    store.updateScheduler("auto");
    store.heartbeatNode(busyNode, { cpuPercent: 95, memoryPercent: 50 });
    store.heartbeatNode(availableNode, { cpuPercent: 25, memoryPercent: 40 });
    store.createRun({ name: "Auto run", input: "Input", approvalRequired: false });

    assert.equal(store.leaseNext(busyNode), null);
    assert.ok(store.leaseNext(availableNode));
  });

  it("refreshes worker models and limits through heartbeat capabilities", () => {
    const store = createStore();
    const nodeId = addNode(store, "refreshable-node", 1, ["old-model"]);

    store.heartbeatNode(
      nodeId,
      { cpuPercent: 20 },
      {
        endpoint: "http://model-server:11434/v1",
        models: ["new-model", "new-model", ""],
        maxConcurrency: 2,
        labels: { runtime: "kubernetes" },
        agentRuntimes: ["single", "langgraph", "langgraph"],
        agentRuntimeProfiles: ["tool_loop_v1", "specialist_team_v1", "specialist_team_v1"],
      },
    );

    const node = store.getOverview().nodes.find((candidate) => candidate.id === nodeId);
    assert.ok(node);
    assert.deepEqual(node.models, ["new-model"]);
    assert.equal(node.maxConcurrency, 2);
    assert.equal(node.endpoint, "http://model-server:11434/v1");
    assert.deepEqual(node.labels, { runtime: "kubernetes" });
    assert.deepEqual(node.agentRuntimes, ["single", "langgraph"]);
    assert.deepEqual(node.agentRuntimeProfiles, ["tool_loop_v1", "specialist_team_v1"]);
  });

  it("removes only stale nodes that belonged to deleted managed worker pools", () => {
    const store = createStore();
    store.registerNode({
      enrollmentToken: "unused",
      name: "managed-active",
      platform: "test",
      models: ["test-model"],
      labels: { managed: "local-launcher", pool: "active-pool" },
    });
    store.registerNode({
      enrollmentToken: "unused",
      name: "managed-stale",
      platform: "test",
      models: ["test-model"],
      labels: { managed: "local-launcher", pool: "stale-pool" },
    });
    addNode(store, "external-worker");

    assert.equal(store.reconcileWorkerPoolNodes(new Set(["active-pool"])), 1);
    const names = (store.getOverview().nodes as Array<{ name: string }>).map((node) => node.name);
    assert.deepEqual(new Set(names), new Set(["managed-active", "external-worker"]));
  });

  it("runs a chain in order and waits for the approval gate", () => {
    const store = createStore();
    const nodeId = addNode(store, "node-a");
    const run = store.createRun({
      name: "Approval run",
      input: "Analyze this",
      approvalRequired: true,
    });

    const collector = store.leaseNext(nodeId);
    assert.equal(collector?.agent.id, "collector");
    store.completeLease(nodeId, collector!.leaseId, "facts");

    const analyst = store.leaseNext(nodeId);
    assert.equal(analyst?.agent.id, "analyst");
    assert.deepEqual(analyst?.context, [{ agentName: "Сборщик", output: "facts" }]);
    store.completeLease(nodeId, analyst!.leaseId, "analysis");

    const overviewBeforeApproval = store.getOverview();
    const approvals = overviewBeforeApproval.approvals as Array<{ stageId: string; runId: string }>;
    assert.equal(approvals.length, 1);
    assert.equal(approvals[0]?.runId, run.id);
    assert.equal(store.leaseNext(nodeId), null);

    store.decideApproval(approvals[0]!.stageId, true);
    const editor = store.leaseNext(nodeId);
    assert.equal(editor?.agent.id, "editor");
    assert.equal(editor?.context.length, 2);
    store.completeLease(nodeId, editor!.leaseId, "final");

    assert.equal(store.getRun(run.id)?.status, "completed");
  });

  it("persists a full run trace with exact input and observable worker calls", () => {
    const store = createStore();
    const nodeId = addNode(store, "trace-node");
    const run = store.createRun({
      name: "Trace run",
      input: "Exact user input",
      approvalRequired: false,
      agentIds: ["collector"],
    });
    const lease = store.leaseNext(nodeId)!;
    store.appendLeaseEvent(nodeId, lease.leaseId, "info", "Вызвана локальная модель test-model", {
      kind: "model_call",
      phase: "started",
      model: "test-model",
    });
    store.appendLeaseEvent(nodeId, lease.leaseId, "info", "Агент запросил web_search", {
      kind: "tool_call",
      phase: "started",
      tool: "web_search",
      arguments: { query: "[скрыто политикой trace]" },
    });
    store.completeLease(nodeId, lease.leaseId, "Exact output");

    const trace = store.getRunTrace(run.id)!;
    const traceRun = trace.run as { input: string; stages: Array<{ output: string }> };
    const events = trace.events as Array<{ type: string; data: Record<string, unknown> | null }>;
    assert.equal(traceRun.input, "Exact user input");
    assert.equal(traceRun.stages[0]?.output, "Exact output");
    assert.equal(events.some((event) => event.type === "trace.input"), true);
    assert.equal(events.some((event) => event.data?.kind === "model_call"), true);
    assert.equal(events.some((event) => event.data?.kind === "tool_call"), true);
    assert.deepEqual(trace.tracePolicy, {
      rawReasoningStored: false,
      observableProgressStored: true,
      exactInputsStored: true,
      exactOutputsStored: true,
      toolArguments: "redacted",
    });
  });

  it("propagates W3C trace context and persists execution metrics", () => {
    const store = createStore();
    const nodeId = addNode(store, "trace-context-node");
    const run = store.createRun({
      name: "Measured run",
      input: "Measure this",
      approvalRequired: false,
      agentIds: ["collector"],
    });

    const lease = store.leaseNext(nodeId, "0.4.0")!;
    assert.match(lease.traceContext.traceparent, /^00-[0-9a-f]{32}-[0-9a-f]{16}-0[01]$/);
    assert.equal(lease.traceContext.traceId, lease.traceContext.traceparent.split("-")[1]);
    store.completeLease(nodeId, lease.leaseId, "Measured output", [], {
      durationMs: 1_250,
      modelCalls: 2,
      inputTokens: 120,
      outputTokens: 42,
      toolCalls: 1,
      model: "test-model",
      provider: "openai-compatible",
      toolSchemaVersion: "agat.web_tools.v1",
    });

    const trace = store.getRunTrace(run.id)!;
    const tracedRun = trace.run as {
      traceId: string;
      stages: Array<{ traceparent: string; metrics: Record<string, unknown>; worker: Record<string, unknown> }>;
    };
    const manifest = trace.manifest as { manifestSha256: string; inputSha256: string; stages: unknown[] };
    assert.equal(tracedRun.traceId, lease.traceContext.traceId);
    assert.equal(tracedRun.stages[0]?.traceparent, lease.traceContext.traceparent);
    assert.equal(tracedRun.stages[0]?.metrics.outputTokens, 42);
    assert.equal(tracedRun.stages[0]?.worker.workerVersion, "0.4.0");
    assert.equal(manifest.manifestSha256.length, 64);
    assert.equal(manifest.inputSha256.length, 64);
    assert.equal(manifest.stages.length, 1);
  });

  it("pins agent snapshots and creates safe side-by-side replay runs", () => {
    const store = createStore();
    const originalNode = addNode(store, "original-model-node", 1, ["model-original"]);
    const agent = store.createAgent({
      name: "Evaluator",
      role: "Evaluates a fixed prompt",
      systemPrompt: "ORIGINAL IMMUTABLE PROMPT",
      model: "model-original",
    });
    const source = store.createRun({
      name: "Replay source",
      input: "Exact replay input",
      approvalRequired: false,
      agentIds: [String(agent.id)],
    });

    store.updateAgent(String(agent.id), {
      name: "Changed evaluator",
      role: "Changed role",
      systemPrompt: "ORIGINAL IMMUTABLE PROMPT",
      model: "model-original",
    });
    const originalLease = store.leaseNext(originalNode)!;
    assert.equal(originalLease.agent.name, "Evaluator");
    assert.equal(originalLease.agent.systemPrompt, "ORIGINAL IMMUTABLE PROMPT");
    assert.equal(originalLease.agent.model, "model-original");
    store.completeLease(originalNode, originalLease.leaseId, "Baseline", [], {
      durationMs: 1_000,
      modelCalls: 1,
      inputTokens: 20,
      outputTokens: 10,
      model: "model-original",
    });
    const sourceManifestBeforeReplay = (store.getRunTrace(source.id)!.manifest as {
      manifestSha256: string;
    }).manifestSha256;

    const replay = store.replayRun(source.id, {
      variants: [
        { name: "Model A", modelOverrides: { [String(agent.id)]: "model-a" } },
        { name: "Model B", modelOverrides: { [String(agent.id)]: "model-b" } },
      ],
    });
    assert.equal(replay.runs.length, 2);
    const sourceManifestAfterReplay = (store.getRunTrace(source.id)!.manifest as {
      manifestSha256: string;
    }).manifestSha256;
    assert.equal(sourceManifestAfterReplay, sourceManifestBeforeReplay);
    const nodeA = addNode(store, "model-a-node", 1, ["model-a"]);
    const nodeB = addNode(store, "model-b-node", 1, ["model-b"]);
    const leaseA = store.leaseNext(nodeA)!;
    assert.equal(leaseA.run.input, "Exact replay input");
    assert.equal(leaseA.agent.systemPrompt, "ORIGINAL IMMUTABLE PROMPT");
    assert.equal(leaseA.agent.model, "model-a");
    store.completeLease(nodeA, leaseA.leaseId, "Candidate A", [], {
      durationMs: 1_100,
      modelCalls: 1,
      inputTokens: 20,
      outputTokens: 11,
      model: "model-a",
    });
    const leaseB = store.leaseNext(nodeB)!;
    assert.equal(leaseB.agent.systemPrompt, "ORIGINAL IMMUTABLE PROMPT");
    assert.equal(leaseB.agent.model, "model-b");
    store.completeLease(nodeB, leaseB.leaseId, "Candidate B", [], {
      durationMs: 1_400,
      modelCalls: 1,
      inputTokens: 20,
      outputTokens: 15,
      model: "model-b",
    });

    const trace = store.getRunTrace(replay.runs[0]!.id)!;
    const comparison = trace.comparison as {
      runs: Array<{ variantName: string; gates: null | Record<string, string> }>;
    };
    assert.equal(comparison.runs.length, 3);
    assert.deepEqual(comparison.runs.map((run) => run.variantName), ["Оригинал", "Model A", "Model B"]);
    assert.equal(comparison.runs[1]?.gates?.completion, "pass");
    assert.equal(comparison.runs[1]?.gates?.latency, "pass");
    assert.equal(comparison.runs[1]?.gates?.quality, "not_evaluated");
    assert.equal(comparison.runs[2]?.gates?.latency, "fail");
    assert.equal(comparison.runs[2]?.gates?.tokenBudget, "fail");
  });

  it("rejects replay when a run contains a side-effecting stage", () => {
    const store = createStore();
    const nodeId = addNode(store, "unsafe-replay-node");
    const run = store.createRun({
      name: "Unsafe replay source",
      input: "Input",
      approvalRequired: false,
      agentIds: ["collector"],
    });
    const lease = store.leaseNext(nodeId)!;
    store.completeLease(nodeId, lease.leaseId, "Done");
    store.db.prepare("UPDATE stages SET stage_kind = 'http' WHERE run_id = ?").run(run.id);

    assert.throws(() => store.replayRun(run.id, {}), /только для запусков, состоящих из агентных этапов/);
  });

  it("stores stage output, final result and agent artifacts under the configured root", () => {
    const artifactsDir = fs.mkdtempSync("/tmp/agat-artifacts-");
    temporaryDirectories.push(artifactsDir);
    const store = createStore({ artifactsDir });
    const nodeId = addNode(store, "artifact-node");
    const run = store.createRun({
      name: "Artifact run",
      input: "Build report",
      approvalRequired: false,
      agentIds: ["editor"],
      resultDestination: "artifacts",
      artifactPath: "reports/releases",
    });
    const lease = store.leaseNext(nodeId)!;
    store.completeLease(nodeId, lease.leaseId, "# Final report", [
      { name: "facts.json", mediaType: "application/json", content: "{\"ok\":true}" },
    ]);

    const trace = store.getRunTrace(run.id)!;
    const artifacts = trace.artifacts as Array<{
      id: string;
      name: string;
      kind: string;
      relativePath: string;
      sha256: string;
    }>;
    assert.deepEqual(artifacts.map((artifact) => artifact.kind).sort(), ["agent_artifact", "result", "stage_output"]);
    assert.equal(artifacts.every((artifact) => artifact.relativePath.startsWith(`reports/releases/${run.id}/`)), true);
    assert.equal(artifacts.every((artifact) => artifact.sha256.length === 64), true);
    for (const artifact of artifacts) {
      const download = store.getArtifactDownload(artifact.id)!;
      assert.equal(download.artifact.name, artifact.name);
      assert.equal(fs.statSync(download.filePath).isFile(), true);
    }
  });

  it("rejects artifact paths that can escape the configured store", () => {
    const store = createStore();
    assert.throws(
      () => store.createRun({
        name: "Unsafe artifact path",
        input: "Input",
        approvalRequired: false,
        resultDestination: "artifacts",
        artifactPath: "../outside",
      }),
      /недопустимый сегмент/,
    );
    assert.throws(
      () => store.createRun({
        name: "Absolute artifact path",
        input: "Input",
        approvalRequired: false,
        resultDestination: "artifacts",
        artifactPath: "/tmp/outside",
      }),
      /относительным/,
    );
  });

  it("returns expired work to the queue", () => {
    const store = createStore({ leaseTtlSeconds: -1 });
    const firstNode = addNode(store, "node-a");
    const secondNode = addNode(store, "node-b");
    store.createRun({ name: "Recoverable", input: "Input", approvalRequired: false });

    const stale = store.leaseNext(firstNode);
    assert.ok(stale);

    const recovered = store.leaseNext(secondNode);
    assert.ok(recovered);
    assert.equal(recovered.stage.id, stale.stage.id);
    assert.notEqual(recovered.leaseId, stale.leaseId);
  });

  it("fails an endlessly expiring lease after the configured attempt budget", () => {
    const store = createStore({ leaseTtlSeconds: -1 });
    const nodeId = addNode(store, "node-a");
    const created = store.createRun({
      name: "Expiring",
      input: "Input",
      approvalRequired: false,
      agentIds: ["collector"],
    });

    assert.equal(store.leaseNext(nodeId)?.stage.attempt, 1);
    assert.equal(store.leaseNext(nodeId)?.stage.attempt, 2);
    assert.equal(store.leaseNext(nodeId)?.stage.attempt, 3);
    assert.equal(store.leaseNext(nodeId), null);

    const run = store.getRun(created.id)!;
    assert.equal(run.status, "failed");
    assert.match(String(run.completedAt), /^\d{4}-\d{2}-\d{2}T/);
  });

  it("timestamps a run when the final worker attempt fails", () => {
    const store = createStore();
    const nodeId = addNode(store, "node-a");
    const created = store.createRun({
      name: "Eventually failed",
      input: "Input",
      approvalRequired: false,
      agentIds: ["collector"],
    });

    for (let attempt = 1; attempt <= 3; attempt += 1) {
      const lease = store.leaseNext(nodeId);
      assert.ok(lease);
      const failed = store.failLease(nodeId, lease.leaseId, "ModelError: unavailable");
      assert.equal(failed.retrying, attempt < 3);
    }

    const run = store.getRun(created.id)!;
    assert.equal(run.status, "failed");
    assert.match(String(run.completedAt), /^\d{4}-\d{2}-\d{2}T/);
  });

  it("creates, updates and reports real agent usage", () => {
    const store = createStore();
    assert.deepEqual(store.listAgents().slice(0, 3).map((agent) => agent.id), ["collector", "analyst", "editor"]);
    const created = store.createAgent({
      name: "Юрист",
      role: "Проверяет внутренние документы",
      systemPrompt: "Проверь документ, перечисли риски и не добавляй неподтверждённых фактов.",
      model: "legal-model",
    });
    assert.equal(created.name, "Юрист");
    assert.equal(created.isBuiltIn, false);
    assert.equal(created.totalRuns, 0);
    assert.equal(created.runtime, "single");
    assert.deepEqual(created.runtimeConfig, { profile: "tool_loop_v1", maxIterations: 6 });

    const updated = store.updateAgent(String(created.id), {
      name: "Юрист по договорам",
      role: "Проверяет договоры",
      systemPrompt: "Проверь документ, перечисли риски и не добавляй неподтверждённых фактов.",
      model: "legal-model",
      runtime: "langgraph",
      runtimeConfig: { profile: "tool_loop_v1", maxIterations: 8 },
    });
    assert.equal(updated?.name, "Юрист по договорам");
    assert.equal(updated?.runtime, "langgraph");
    assert.deepEqual(updated?.runtimeConfig, { profile: "tool_loop_v1", maxIterations: 8 });
    const legacyCompatibleUpdate = store.updateAgent(String(created.id), {
      name: "Юрист по договорам",
      role: "Проверяет договоры и приложения",
      systemPrompt: "Проверь документ, перечисли риски и не добавляй неподтверждённых фактов.",
      model: "legal-model",
    });
    assert.equal(legacyCompatibleUpdate?.runtime, "langgraph");
    assert.deepEqual(legacyCompatibleUpdate?.runtimeConfig, {
      profile: "tool_loop_v1",
      maxIterations: 8,
    });
    assert.throws(() => store.updateAgent(String(created.id), {
      name: "Юрист по договорам",
      role: "Проверяет договоры и приложения",
      systemPrompt: "Новый prompt без quality gate",
      model: "legal-model",
    }), /Golden eval/iu);

    store.createRun({
      name: "Проверка договора",
      input: "Проверь условия договора",
      approvalRequired: false,
      agentIds: [String(created.id)],
    });
    const agent = store.listAgents().find((item) => item.id === created.id);
    assert.equal(agent?.totalRuns, 1);
    assert.equal(agent?.activeRuns, 1);
    const overview = store.getOverview();
    assert.deepEqual(overview.models, ["legal-model"]);
    assert.equal((overview.counts as { totalModels: number }).totalModels, 1);
  });

  it("routes a model-pinned agent only to a compatible node", () => {
    const store = createStore();
    const genericNode = addNode(store, "generic-node");
    const compatibleNode = addNode(store, "legal-node", 1, ["legal-model"]);
    const agent = store.createAgent({
      name: "Юрист",
      role: "Проверяет документы",
      systemPrompt: "Проверь переданный документ и верни структурированный список рисков.",
      model: "legal-model",
    });
    store.createRun({
      name: "Legal run",
      input: "Analyze",
      approvalRequired: false,
      agentIds: [String(agent.id)],
    });

    assert.equal(store.leaseNext(genericNode), null);
    assert.equal(store.leaseNext(compatibleNode)?.agent.id, agent.id);
  });

  it("selects the smallest profiled model that passes balanced policy constraints", () => {
    const store = createStore();
    const largeNode = store.registerNode({
      enrollmentToken: "unused",
      name: "large-node",
      platform: "test",
      models: ["large-model"],
      memoryMb: 64_000,
      vramMb: 24_000,
      modelProfiles: [{
        name: "large-model",
        provider: "ollama",
        contextWindow: 131_072,
        parameterCount: 32_000_000_000,
        capabilities: ["completion", "tools"],
        qualityScore: 92,
      }],
    }).id;
    const smallNode = store.registerNode({
      enrollmentToken: "unused",
      name: "small-node",
      platform: "test",
      models: ["small-model"],
      memoryMb: 32_000,
      vramMb: 8_000,
      modelProfiles: [{
        name: "small-model",
        provider: "ollama",
        contextWindow: 65_536,
        parameterCount: 8_000_000_000,
        capabilities: ["completion", "tools"],
        qualityScore: 81,
      }],
    }).id;
    store.updateModelRouterPolicy({
      strategy: "balanced",
      minContextTokens: 32_000,
      minQualityScore: 80,
      allowUnknownProfiles: false,
    });
    store.createRun({
      name: "Balanced route",
      input: "Analyze",
      approvalRequired: false,
      agentIds: ["collector"],
    });

    assert.equal(store.leaseNext(largeNode), null);
    const lease = store.leaseNext(smallNode);
    assert.equal(lease?.agent.model, "small-model");
    assert.equal(lease?.routing?.strategy, "balanced");
    assert.equal(lease?.routing?.alternativesConsidered, 2);
    assert.equal(lease?.routing?.signals.parameterCount, 8_000_000_000);
    const node = (store.getOverview().nodes as Array<Record<string, unknown>>)
      .find((candidate) => candidate.id === smallNode)!;
    assert.equal(node.vramMb, 8_000);
  });

  it("learns passive throughput benchmarks and performance-routes to the fastest node", () => {
    const store = createStore();
    store.updateModelRouterPolicy({ enabled: false });
    const fastNode = addNode(store, "fast-node", 1, ["shared-model"]);
    const slowNode = addNode(store, "slow-node", 1, ["shared-model"]);
    const createMeasuredRun = (name: string) => store.createRun({
      name,
      input: "Measure",
      approvalRequired: false,
      agentIds: ["collector"],
    });

    createMeasuredRun("Fast sample");
    const fastLease = store.leaseNext(fastNode)!;
    store.completeLease(fastNode, fastLease.leaseId, "fast", [], {
      model: "shared-model",
      modelCalls: 1,
      outputTokens: 100,
      modelDurationMs: 1_000,
      energyJoules: 20,
    });
    createMeasuredRun("Slow sample");
    const slowLease = store.leaseNext(slowNode)!;
    store.completeLease(slowNode, slowLease.leaseId, "slow", [], {
      model: "shared-model",
      modelCalls: 1,
      outputTokens: 100,
      modelDurationMs: 2_000,
      energyJoules: 30,
    });

    store.updateModelRouterPolicy({ enabled: true, strategy: "performance" });
    createMeasuredRun("Performance route");
    assert.equal(store.leaseNext(slowNode), null);
    const routed = store.leaseNext(fastNode);
    assert.equal(routed?.routing?.signals.tokensPerSecond, 100);
    assert.equal(routed?.routing?.signals.joulesPer1kTokens, 200);
    const fastProfile = (store.getOverview().nodes as Array<{
      id: string;
      modelProfiles: Array<{ benchmark: { samples: number; tokensPerSecond: number } | null }>;
    }>).find((node) => node.id === fastNode)?.modelProfiles[0];
    assert.deepEqual(fastProfile?.benchmark && {
      samples: fastProfile.benchmark.samples,
      tokensPerSecond: fastProfile.benchmark.tokensPerSecond,
    }, { samples: 1, tokensPerSecond: 100 });
  });

  it("falls back to the next node after a routed attempt fails", () => {
    const store = createStore();
    const primary = addNode(store, "a-primary", 1, ["fallback-model"]);
    const secondary = addNode(store, "b-secondary", 1, ["fallback-model"]);
    store.createRun({
      name: "Fallback route",
      input: "Retry elsewhere",
      approvalRequired: false,
      agentIds: ["collector"],
    });

    const first = store.leaseNext(primary)!;
    assert.equal(first.routing?.nodeId, primary);
    store.failLease(primary, first.leaseId, "Model endpoint unavailable");
    assert.equal(store.leaseNext(primary), null);
    const fallback = store.leaseNext(secondary);
    assert.equal(fallback?.routing?.fallbackFrom?.nodeId, primary);
    assert.equal(fallback?.routing?.nodeId, secondary);
  });

  it("routes a LangGraph agent only to a worker that advertises the runtime", () => {
    const store = createStore();
    const singleNode = addNode(store, "single-runtime-node");
    const graphNode = addNode(
      store,
      "langgraph-runtime-node",
      1,
      ["test-model"],
      ["single", "langgraph"],
      ["tool_loop_v1", "specialist_team_v1"],
    );
    const agent = store.createAgent({
      name: "Graph researcher",
      role: "Uses a bounded agent graph",
      systemPrompt: "Use tools when necessary and return a sourced result.",
      runtime: "langgraph",
      runtimeConfig: { profile: "tool_loop_v1", maxIterations: 5 },
    });
    store.createRun({
      name: "Graph run",
      input: "Analyze",
      approvalRequired: false,
      agentIds: [String(agent.id)],
    });

    assert.equal(store.leaseNext(singleNode), null);
    const lease = store.leaseNext(graphNode);
    assert.equal(lease?.agent.id, agent.id);
    assert.equal(lease?.agent.runtime, "langgraph");
    assert.deepEqual(lease?.agent.runtimeConfig, {
      profile: "tool_loop_v1",
      maxIterations: 5,
    });
  });

  it("pins ordered specialist subgraphs and requires every member model on one worker", () => {
    const store = createStore();
    const researcher = store.createAgent({
      name: "Team researcher",
      role: "Collects verified facts",
      systemPrompt: "Return verified facts with concise provenance.",
      model: "research-model",
      runtime: "langgraph",
      runtimeConfig: { profile: "tool_loop_v1", maxIterations: 3 },
    });
    const reviewer = store.createAgent({
      name: "Team reviewer",
      role: "Challenges unsupported conclusions",
      systemPrompt: "Review the supplied evidence and identify unsupported claims.",
      model: "review-model",
      runtime: "langgraph",
      runtimeConfig: { profile: "tool_loop_v1", maxIterations: 2 },
    });
    const team = store.createAgent({
      name: "Research team",
      role: "Supervises bounded specialist work",
      systemPrompt: "Delegate only when needed and synthesize the final answer.",
      model: "supervisor-model",
      runtime: "langgraph",
      runtimeConfig: {
        profile: "specialist_team_v1",
        maxIterations: 4,
        maxHandoffs: 3,
        stateSchema: "specialist_team_state_v1",
        specialistAgentIds: [String(researcher.id), String(reviewer.id)],
      },
    });
    const incompleteNode = addNode(
      store,
      "team-node-missing-review-model",
      1,
      ["supervisor-model", "research-model"],
      ["single", "langgraph"],
      ["tool_loop_v1", "specialist_team_v1"],
    );
    const completeNode = addNode(
      store,
      "team-node-complete",
      1,
      ["supervisor-model", "research-model", "review-model"],
      ["single", "langgraph"],
      ["tool_loop_v1", "specialist_team_v1"],
    );
    const legacyProfileNode = addNode(
      store,
      "team-node-old-profile",
      1,
      ["supervisor-model", "research-model", "review-model"],
      ["single", "langgraph"],
      ["tool_loop_v1"],
    );
    const run = store.createRun({
      name: "Pinned team",
      input: "Investigate the release",
      approvalRequired: false,
      agentIds: [String(team.id)],
    });

    const updatedResearcher = store.updateAgent(String(researcher.id), {
      name: "Renamed researcher",
      role: "Updated role after run creation",
      systemPrompt: "Return verified facts with concise provenance.",
      model: "research-model",
      runtime: "langgraph",
      runtimeConfig: { profile: "tool_loop_v1", maxIterations: 5 },
    });
    assert.equal(updatedResearcher?.name, "Renamed researcher");
    assert.equal(store.leaseNext(incompleteNode), null);
    assert.equal(store.leaseNext(legacyProfileNode), null);
    const lease = store.leaseNext(completeNode);
    assert.ok(lease);
    assert.equal(lease.agent.runtimeConfig.profile, "specialist_team_v1");
    assert.deepEqual(lease.agent.specialists.map((specialist) => specialist.id), [
      researcher.id,
      reviewer.id,
    ]);
    assert.equal(lease.agent.specialists[0]?.name, "Team researcher");
    assert.equal(lease.agent.specialists[0]?.runtimeConfig.maxIterations, 3);
    assert.equal(lease.agent.specialists[0]?.definitionVersion.length, 64);
    assert.equal(lease.agent.specialists[1]?.model, "review-model");
    const pinnedVersions = lease.agent.specialists.map((specialist) => specialist.definitionVersion);
    store.completeLease(completeNode, lease.leaseId, "Team result");
    const manifest = store.getRunTrace(run.id)?.manifest as {
      schemaVersion: number;
      stages: Array<{ agent: { specialists: Array<{ definitionVersion: string }> } }>;
    };
    assert.equal(manifest.schemaVersion, 3);
    assert.deepEqual(
      manifest.stages[0]?.agent.specialists.map((specialist) => specialist.definitionVersion),
      pinnedVersions,
    );
    const replay = store.replayRun(run.id, { variants: [{ name: "Team replay" }] });
    assert.equal(replay.runs.length, 1);
    const replayLease = store.leaseNext(completeNode);
    assert.ok(replayLease);
    assert.deepEqual(
      replayLease.agent.specialists.map((specialist) => specialist.definitionVersion),
      pinnedVersions,
    );
  });

  it("rejects unsafe or recursive specialist team definitions", () => {
    const store = createStore();
    const first = store.createAgent({
      name: "First specialist",
      role: "First bounded role",
      systemPrompt: "Return a bounded first result.",
    });
    const second = store.createAgent({
      name: "Second specialist",
      role: "Second bounded role",
      systemPrompt: "Return a bounded second result.",
    });
    const teamConfig = {
      profile: "specialist_team_v1" as const,
      maxIterations: 4,
      maxHandoffs: 2,
      stateSchema: "specialist_team_state_v1" as const,
      specialistAgentIds: [String(first.id), String(second.id)],
    };
    assert.throws(() => store.createAgent({
      name: "Single member team",
      role: "Invalid team",
      systemPrompt: "Never runs.",
      runtime: "langgraph",
      runtimeConfig: { ...teamConfig, specialistAgentIds: [String(first.id)] },
    }), /от 2 до 8/);
    assert.throws(() => store.createAgent({
      name: "Unknown member team",
      role: "Invalid team",
      systemPrompt: "Never runs.",
      runtime: "langgraph",
      runtimeConfig: { ...teamConfig, specialistAgentIds: [String(first.id), "missing-agent"] },
    }), /не найден/);

    const team = store.createAgent({
      name: "Valid team",
      role: "Coordinates two specialists",
      systemPrompt: "Use a bounded supervisor policy.",
      runtime: "langgraph",
      runtimeConfig: teamConfig,
    });
    assert.throws(() => store.createAgent({
      name: "Nested team",
      role: "Invalid nested team",
      systemPrompt: "Never runs.",
      runtime: "langgraph",
      runtimeConfig: {
        ...teamConfig,
        specialistAgentIds: [String(team.id), String(first.id)],
      },
    }), /Вложенные specialist teams запрещены/);
    assert.throws(() => store.updateAgent(String(first.id), {
      name: "First specialist",
      role: "Attempts to become a nested team",
      systemPrompt: "Return a bounded first result.",
      model: null,
      runtime: "langgraph",
      runtimeConfig: {
        ...teamConfig,
        specialistAgentIds: [String(second.id), "collector"],
      },
    }), /используется как specialist/);
    assert.throws(() => store.updateAgent(String(team.id), {
      name: "Valid team",
      role: "Attempts self reference",
      systemPrompt: "Use a bounded supervisor policy.",
      model: null,
      runtime: "langgraph",
      runtimeConfig: { ...teamConfig, specialistAgentIds: [String(team.id), String(second.id)] },
    }), /не может включать саму себя/);
  });

  it("rejects unknown or unbounded agent runtime configuration", () => {
    const store = createStore();
    const base = {
      name: "Invalid runtime",
      role: "Validation fixture",
      systemPrompt: "Return a concise result.",
    };
    assert.throws(
      () => store.createAgent({ ...base, runtime: "unknown" as AgentRuntime }),
      /Неизвестный runtime/,
    );
    assert.throws(
      () => store.createAgent({
        ...base,
        runtime: "langgraph",
        runtimeConfig: { maxIterations: 13 },
      }),
      /от 1 до 12/,
    );
  });

  it("requires approval before a single-agent final stage", () => {
    const store = createStore();
    const nodeId = addNode(store, "node-a");
    const created = store.createRun({
      name: "One agent",
      input: "Check this",
      approvalRequired: true,
      agentIds: ["analyst"],
    });

    assert.equal(created.status, "waiting_approval");
    assert.equal(store.leaseNext(nodeId), null);
    const approvals = store.getOverview().approvals as Array<{ stageId: string }>;
    assert.equal(approvals.length, 1);
    store.decideApproval(approvals[0]!.stageId, true);
    assert.equal(store.leaseNext(nodeId)?.agent.id, "analyst");
  });

  it("removes only known demo fixtures when live mode opens an existing database", () => {
    const dbPath = `/tmp/agat-demo-cleanup-${Date.now()}-${Math.random().toString(16).slice(2)}.db`;
    temporaryDatabases.push(dbPath);
    const seeded = new AgatStore(dbPath, { seedDemo: true });
    const custom = seeded.createRun({ name: "Пользовательский запуск", input: "Не удалять", approvalRequired: false });
    seeded.close();

    const live = new AgatStore(dbPath, { seedDemo: false });
    stores.push(live);
    const overview = live.getOverview();
    const nodes = overview.nodes as Array<{ id: string }>;
    const runs = overview.runs as Array<{ id: string; name: string }>;
    assert.equal(nodes.some((node) => node.id.startsWith("demo-")), false);
    assert.equal(runs.some((run) => run.name === "Сводка инцидентов"), false);
    assert.equal(runs.some((run) => run.id === custom.id), true);

    live.close();
    stores.pop();
    fs.rmSync(dbPath, { force: true });
    temporaryDatabases.pop();
  });

  it("migrates existing agents and nodes to safe single-runtime defaults", () => {
    const dbPath = `/tmp/agat-runtime-migration-${Date.now()}-${Math.random().toString(16).slice(2)}.db`;
    temporaryDatabases.push(dbPath);
    const legacy = new DatabaseSync(dbPath);
    legacy.exec(`
      CREATE TABLE agents (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        role TEXT NOT NULL,
        system_prompt TEXT NOT NULL,
        model TEXT,
        is_builtin INTEGER NOT NULL DEFAULT 0,
        project_id TEXT NOT NULL DEFAULT 'default',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
      INSERT INTO agents(
        id, name, role, system_prompt, model, is_builtin, project_id, created_at, updated_at
      ) VALUES (
        'legacy-agent', 'Legacy agent', 'Legacy role', 'Legacy prompt', NULL, 0,
        'default', '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z'
      );
      CREATE TABLE nodes (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        platform TEXT NOT NULL,
        architecture TEXT NOT NULL DEFAULT '',
        endpoint TEXT NOT NULL DEFAULT '',
        models_json TEXT NOT NULL DEFAULT '[]',
        labels_json TEXT NOT NULL DEFAULT '{}',
        cpu_cores INTEGER NOT NULL DEFAULT 1,
        memory_mb INTEGER NOT NULL DEFAULT 0,
        gpu TEXT NOT NULL DEFAULT '',
        max_concurrency INTEGER NOT NULL DEFAULT 1,
        token_hash TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'online',
        metrics_json TEXT NOT NULL DEFAULT '{}',
        last_seen TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
      INSERT INTO nodes(
        id, name, platform, token_hash, last_seen, created_at, updated_at
      ) VALUES (
        'legacy-node', 'Legacy node', 'test', 'legacy-token-hash',
        '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z'
      );
      PRAGMA user_version = 6;
    `);
    legacy.close();

    const migrated = new AgatStore(dbPath, { seedDemo: false });
    stores.push(migrated);
    const agent = migrated.listAgents().find((candidate) => candidate.id === "legacy-agent");
    const node = migrated.getOverview().nodes.find((candidate) => candidate.id === "legacy-node");
    assert.equal(agent?.runtime, "single");
    assert.deepEqual(agent?.runtimeConfig, { profile: "tool_loop_v1", maxIterations: 6 });
    assert.deepEqual(node?.agentRuntimes, ["single"]);
    assert.deepEqual(node?.agentRuntimeProfiles, ["tool_loop_v1"]);
    assert.equal(node?.vramMb, 0);
    assert.deepEqual(node?.modelProfiles, []);
    const inspection = new DatabaseSync(dbPath);
    const userVersion = inspection.prepare("PRAGMA user_version").get() as { user_version: number };
    const benchmarkTable = inspection.prepare(`
      SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'model_benchmarks'
    `).get() as { name?: string } | undefined;
    const knowledgeTable = inspection.prepare(`
      SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'knowledge_collections'
    `).get() as { name?: string } | undefined;
    const a2aEndpointTable = inspection.prepare(`
      SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'a2a_endpoints'
    `).get() as { name?: string } | undefined;
    const a2aTaskTable = inspection.prepare(`
      SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'a2a_tasks'
    `).get() as { name?: string } | undefined;
    const mcpPolicyTable = inspection.prepare(`
      SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'mcp_policy_versions'
    `).get() as { name?: string } | undefined;
    const mcpApprovalsTable = inspection.prepare(`
      SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'mcp_tool_call_approvals'
    `).get() as { name?: string } | undefined;
    const processTokensTable = inspection.prepare(`
      SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'process_tokens'
    `).get() as { name?: string } | undefined;
    const processWebhooksTable = inspection.prepare(`
      SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'process_webhooks'
    `).get() as { name?: string } | undefined;
    const credentialColumns = inspection.prepare("PRAGMA table_info(credentials)").all() as Array<{ name: string }>;
    const routerSetting = inspection.prepare(
      "SELECT value FROM settings WHERE key = 'model_router_policy'",
    ).get() as { value?: string } | undefined;
    inspection.close();
    assert.equal(userVersion.user_version, 19);
    assert.equal(benchmarkTable?.name, "model_benchmarks");
    assert.equal(knowledgeTable?.name, "knowledge_collections");
    assert.equal(a2aEndpointTable?.name, "a2a_endpoints");
    assert.equal(a2aTaskTable?.name, "a2a_tasks");
    assert.equal(mcpPolicyTable?.name, "mcp_policy_versions");
    assert.equal(mcpApprovalsTable?.name, "mcp_tool_call_approvals");
    assert.equal(processTokensTable?.name, "process_tokens");
    assert.equal(processWebhooksTable?.name, "process_webhooks");
    assert.ok(credentialColumns.some((column) => column.name === "scope_json"));
    assert.deepEqual(node?.embeddingModels, []);
    assert.equal(JSON.parse(routerSetting?.value ?? "{}").strategy, "balanced");
  });

  it("adds trace and evaluation columns before creating their index on a legacy runs table", () => {
    const dbPath = `/tmp/agat-trace-migration-${Date.now()}-${Math.random().toString(16).slice(2)}.db`;
    temporaryDatabases.push(dbPath);
    const legacy = new DatabaseSync(dbPath);
    legacy.exec(`
      CREATE TABLE runs (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        input TEXT NOT NULL,
        status TEXT NOT NULL,
        execution_mode TEXT NOT NULL DEFAULT 'sequential',
        priority INTEGER NOT NULL DEFAULT 50,
        approval_required INTEGER NOT NULL DEFAULT 1,
        result_destination TEXT NOT NULL DEFAULT 'history',
        artifact_path TEXT NOT NULL DEFAULT '',
        project_id TEXT NOT NULL DEFAULT 'default',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT
      );
      INSERT INTO runs(
        id, name, input, status, created_at, updated_at
      ) VALUES (
        'legacy-run', 'Legacy run', 'Legacy input', 'completed',
        '2026-01-01T00:00:00.000Z', '2026-01-01T00:01:00.000Z'
      );
      PRAGMA user_version = 7;
    `);
    legacy.close();

    const migrated = new AgatStore(dbPath, { seedDemo: false });
    stores.push(migrated);
    const run = migrated.getRun("legacy-run")!;
    assert.match(String(run.traceId), /^[a-f0-9]{32}$/);
    assert.equal(run.evaluationGroupId, null);

    const inspection = new DatabaseSync(dbPath);
    const index = inspection.prepare(`
      SELECT name FROM sqlite_master
      WHERE type = 'index' AND name = 'idx_runs_evaluation_group'
    `).get() as { name?: string } | undefined;
    const storedRun = inspection.prepare(
      "SELECT root_span_id FROM runs WHERE id = 'legacy-run'",
    ).get() as { root_span_id?: string } | undefined;
    inspection.close();
    assert.equal(index?.name, "idx_runs_evaluation_group");
    assert.match(String(storedRun?.root_span_id), /^[a-f0-9]{16}$/);
  });

  it("executes a published process snapshot through the existing worker queue", () => {
    const store = createStore();
    const nodeId = addNode(store, "node-a");
    const process = store.createProcess({
      name: "Линейный процесс",
      description: "Проверка durable-исполнения",
      graph: linearProcessGraph(),
    });
    assert.equal(process.publishedVersion, 0);
    const published = store.publishProcess(String(process.id));
    assert.equal(published?.publishedVersion, 1);

    const instance = store.startProcess(String(process.id), { input: "Собери факты" });
    assert.equal(instance?.processVersion, 1);
    const lease = store.leaseNext(nodeId);
    assert.equal(lease?.agent.id, "collector");
    assert.equal(lease?.stage.processNodeId, "agent");
    const result = store.completeLease(nodeId, lease!.leaseId, "Готово");

    assert.equal(result.runCompleted, true);
    assert.equal(store.getRun(String(instance!.runId))?.status, "completed");
    assert.equal(store.getProcessInstance(String(instance!.id))?.status, "completed");
  });

  it("bounds a loop and records every repeated agent visit", () => {
    const store = createStore();
    const nodeId = addNode(store, "node-a");
    const process = store.createProcess({ name: "Цикл проверки", graph: loopingProcessGraph() });
    store.publishProcess(String(process.id));
    const instance = store.startProcess(String(process.id), { input: "Редактируй до лимита" })!;

    for (let visit = 1; visit <= 3; visit += 1) {
      const lease = store.leaseNext(nodeId);
      assert.equal(lease?.agent.id, "editor");
      assert.equal(lease?.stage.position, visit - 1);
      const result = store.completeLease(nodeId, lease!.leaseId, `Версия ${visit}`);
      assert.equal(result.runCompleted, visit === 3);
    }

    const run = store.getRun(String(instance.runId))!;
    assert.equal(run.status, "completed");
    assert.equal((run.stages as unknown[]).length, 3);
    const completed = store.getProcessInstance(String(instance.id))!;
    assert.equal(completed.currentIteration, 2);
    assert.equal(completed.maxIterations, 2);
    assert.deepEqual(completed.loopCounts, { loop: 2 });
  });

  it("pins running instances to immutable published versions", () => {
    const store = createStore();
    const nodeId = addNode(store, "node-a");
    const process = store.createProcess({ name: "Версионируемый процесс", graph: linearProcessGraph("collector") });
    store.publishProcess(String(process.id));
    const first = store.startProcess(String(process.id), { input: "Версия один" })!;

    store.updateProcess(String(process.id), {
      name: "Версионируемый процесс",
      description: "Новая логика",
      graph: linearProcessGraph("analyst"),
    });
    const secondVersion = store.publishProcess(String(process.id));
    assert.equal(secondVersion?.publishedVersion, 2);
    const second = store.startProcess(String(process.id), { input: "Версия два" })!;

    assert.equal(first.processVersion, 1);
    assert.equal(second.processVersion, 2);
    const firstLease = store.leaseNext(nodeId);
    assert.equal(firstLease?.agent.id, "collector");
    store.completeLease(nodeId, firstLease!.leaseId, "v1 done");
    const secondLease = store.leaseNext(nodeId);
    assert.equal(secondLease?.agent.id, "analyst");
  });

  it("supports approval gates on visual agent steps", () => {
    const store = createStore();
    const nodeId = addNode(store, "node-a");
    const process = store.createProcess({
      name: "Процесс с подтверждением",
      graph: linearProcessGraph("analyst", true),
    });
    store.publishProcess(String(process.id));
    const instance = store.startProcess(String(process.id), { input: "Проверь" })!;

    assert.equal(instance.status, "waiting_approval");
    assert.equal(store.leaseNext(nodeId), null);
    const approvals = store.getOverview().approvals as Array<{ stageId: string; runId: string }>;
    assert.equal(approvals[0]?.runId, instance.runId);
    store.decideApproval(approvals[0]!.stageId, true);
    assert.equal(store.leaseNext(nodeId)?.agent.id, "analyst");
  });

  it("rejects an unsafe loop without an explicit exit branch", () => {
    const store = createStore();
    const graph = loopingProcessGraph();
    graph.edges = graph.edges.filter((edge) => edge.branch !== "exit");
    assert.throws(
      () => store.createProcess({ name: "Небезопасный процесс", graph }),
      /ожидаются ветки repeat и exit/,
    );
  });

  it("stores an incomplete draft but rejects it on publish", () => {
    const store = createStore();
    const process = store.createProcess({ name: "Черновик", graph: linearProcessGraph() });
    const draft = linearProcessGraph();
    draft.edges = [];
    const updated = store.updateProcess(String(process.id), {
      name: "Черновик",
      description: "Редактируется на canvas",
      graph: draft,
    });

    assert.equal((updated?.draftGraph as { edges: unknown[] }).edges.length, 0);
    assert.throws(() => store.publishProcess(String(process.id)), /ожидаются ветки default/);
  });

  it("tests a draft node and can start a published process from a selected step", () => {
    const store = createStore();
    const graph = linearProcessGraph();
    const process = store.createProcess({ name: "Пошаговая отладка", graph });
    const agentNode = graph.nodes.find((node) => node.type === "agent")!;
    const queuedTest = store.testProcessNode({ node: agentNode, input: "Проверочный ввод" });
    assert.equal(queuedTest.kind, "queued");
    assert.equal(store.getRun(queuedTest.runId!)?.stages[0]?.processNodeId, agentNode.id);

    const conditionTest = store.testProcessNode({
      input: "Результат готов",
      node: {
        id: "condition-test",
        type: "condition",
        name: "Готово?",
        position: { x: 0, y: 0 },
        config: {
          condition: { source: "last_output", operator: "contains", value: "готов", caseSensitive: false },
        },
      },
    });
    assert.equal(conditionTest.branch, "true");

    store.publishProcess(String(process.id));
    const endNode = graph.nodes.find((node) => node.type === "end")!;
    const instance = store.startProcess(String(process.id), { input: "Продолжить", startNodeId: endNode.id });
    assert.equal(instance?.status, "completed");
    const trace = store.getRunTrace(String(instance!.runId));
    assert.equal(trace?.events.some((event) => event.type === "process.instance.resumed_from_node"), true);
  });
});
