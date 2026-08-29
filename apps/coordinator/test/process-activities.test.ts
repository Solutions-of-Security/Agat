import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, it } from "node:test";

import { AgatStore } from "../src/database.js";
import { renderProcessTemplate } from "../src/process-expressions.js";
import type { ProcessGraph } from "../src/types.js";

const stores: AgatStore[] = [];
const temporaryDirectories: string[] = [];

afterEach(() => {
  while (stores.length) stores.pop()?.close();
  while (temporaryDirectories.length) fs.rmSync(temporaryDirectories.pop()!, { recursive: true, force: true });
});

function storeWithArtifacts(): { store: AgatStore; artifactsDir: string } {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "agat-process-activities-"));
  temporaryDirectories.push(root);
  const artifactsDir = path.join(root, "artifacts");
  const store = new AgatStore(":memory:", {
    seedDemo: false,
    artifactsDir,
    credentialsKey: "test-credentials-key",
  });
  stores.push(store);
  return { store, artifactsDir };
}

function activityGraph(credentialId: string): ProcessGraph {
  const nodes: ProcessGraph["nodes"] = [
    { id: "start", type: "start", name: "Старт", position: { x: 0, y: 0 }, config: {} },
    {
      id: "transform",
      type: "transform",
      name: "Собрать JSON",
      position: { x: 160, y: 0 },
      config: { template: '{"id":"42","source":"{{ input }}"}' },
    },
    {
      id: "http",
      type: "http",
      name: "Вызвать API",
      position: { x: 320, y: 0 },
      config: {
        method: "POST",
        url: "https://example.com/items/{{ json.id }}",
        headers: { "Content-Type": "application/json", "X-Trace": "{{ json.id }}" },
        body: '{"payload":{{ lastOutput }}}',
        credentialId,
        timeoutSeconds: 20,
      },
    },
    {
      id: "approval",
      type: "approval",
      name: "Проверить ответ",
      position: { x: 480, y: 0 },
      config: { approvalMessage: "Разрешить сохранение ответа API?" },
    },
    {
      id: "wait",
      type: "wait",
      name: "Короткая пауза",
      position: { x: 640, y: 0 },
      config: { waitSeconds: 1 },
    },
    {
      id: "artifact",
      type: "artifact",
      name: "Сохранить ответ",
      position: { x: 800, y: 0 },
      config: {
        artifactName: "payload.json",
        artifactMediaType: "application/json",
        artifactContent: "{{ lastOutput }}",
      },
    },
    { id: "end", type: "end", name: "Готово", position: { x: 960, y: 0 }, config: {} },
  ];
  return {
    nodes,
    edges: nodes.slice(0, -1).map((node, index) => ({
      id: `${node.id}-${nodes[index + 1]!.id}`,
      source: node.id,
      target: nodes[index + 1]!.id,
      branch: "default",
    })),
  };
}

describe("process activities", () => {
  it("renders only bounded declarative expressions", () => {
    assert.equal(renderProcessTemplate("{{ input }} · {{ json.user.name }} · {{ loop.review }}", {
      input: "source",
      lastOutput: '{"user":{"name":"Ada"}}',
      loopCounts: { review: 2 },
    }), "source · Ada · 2");
    assert.equal(renderProcessTemplate("missing={{ json.absent }}", { input: "", lastOutput: "{}" }), "missing=");
    assert.throws(
      () => renderProcessTemplate("{{ globalThis.process.env }}", { input: "", lastOutput: null }),
      /Неизвестное выражение/,
    );
  });

  it("encrypts credentials and executes HTTP, approval, wait and artifact stages in one durable run", () => {
    const { store, artifactsDir } = storeWithArtifacts();
    const credential = store.createCredential({
      name: "Example API",
      type: "http_header",
      data: { headerName: "Authorization", headerValue: "Bearer super-secret-value" },
    });
    const storedCredential = store.db.prepare("SELECT secret_blob FROM credentials WHERE id = ?").get(String(credential.id)) as { secret_blob: string };
    assert.equal(storedCredential.secret_blob.includes("super-secret-value"), false);
    assert.deepEqual(store.listCredentials()[0]?.fields, ["headerName", "headerValue"]);
    assert.equal("data" in store.listCredentials()[0]!, false);

    const worker = store.registerNode({
      enrollmentToken: "unused",
      name: "activity-worker",
      platform: "test",
      models: ["test-model"],
    }).id;
    const process = store.createProcess({ name: "Интеграционный процесс", graph: activityGraph(String(credential.id)) });
    store.publishProcess(String(process.id));
    const instance = store.startProcess(String(process.id), {
      input: "initial",
      resultDestination: "artifacts",
      artifactPath: "process-tests",
    })!;

    const httpLease = store.leaseNext(worker)!;
    assert.equal(httpLease.activity?.kind, "http");
    assert.equal(httpLease.activity?.request.url, "https://example.com/items/42");
    assert.equal(httpLease.activity?.request.headers.Authorization, "Bearer super-secret-value");
    assert.match(httpLease.activity?.request.headers["Idempotency-Key"] ?? "", /^agat-[0-9a-f]{64}$/);
    assert.equal(httpLease.activity?.request.headers["X-Trace"], "42");
    assert.equal(httpLease.activity?.request.body, '{"payload":{"id":"42","source":"initial"}}');
    store.completeLease(worker, httpLease.leaseId, '{"status":200,"body":"ok"}');

    const approval = (store.getOverview().approvals as Array<{ stageId: string; summary: string }>)[0]!;
    assert.equal(approval.summary, "Разрешить сохранение ответа API?");
    store.decideApproval(approval.stageId, true);
    store.db.prepare("UPDATE stages SET available_at = ? WHERE stage_kind = 'wait'").run("2000-01-01T00:00:00.000Z");
    store.maintenanceTick();

    const run = store.getRun(String(instance.runId))!;
    assert.equal(run.status, "completed");
    assert.deepEqual(
      (run.stages as Array<{ kind: string }>).map((stage) => stage.kind),
      ["transform", "http", "approval", "wait", "artifact"],
    );
    const trace = store.getRunTrace(String(instance.runId))!;
    assert.equal(trace.artifacts.length >= 2, true);
    const explicitArtifact = trace.artifacts.find((artifact) => artifact.kind === "process_artifact")!;
    assert.equal(explicitArtifact.name, "payload.json");
    assert.equal(fs.readFileSync(path.join(artifactsDir, explicitArtifact.relativePath), "utf8"), '{"status":200,"body":"ok"}');
    assert.equal(JSON.stringify(trace.events).includes("super-secret-value"), false);
    assert.throws(() => store.deleteCredential(String(credential.id)), /Credentials используются/);
  });

  it("rejects MCP-scoped credentials in HTTP process steps", () => {
    const { store } = storeWithArtifacts();
    const credential = store.createCredential({
      name: "MCP-only token",
      type: "api_key",
      data: { apiKey: "mcp-only-secret" },
      scope: {
        kind: "mcp",
        serverNamespaces: ["crm"],
        toolPatterns: ["find_*"],
        risks: ["read"],
        allowCatalog: true,
        expiresAt: null,
      },
    });
    assert.throws(() => store.testProcessNode({
      input: "probe",
      node: {
        id: "http-with-mcp-secret",
        type: "http",
        name: "Unsafe HTTP reuse",
        position: { x: 0, y: 0 },
        config: {
          method: "GET",
          url: "https://example.com/items",
          credentialId: String(credential.id),
          timeoutSeconds: 10,
        },
      },
    }), /MCP-scoped credentials нельзя использовать/);

    const process = store.createProcess({
      name: "Unsafe published reuse",
      graph: activityGraph(String(credential.id)),
    });
    assert.throws(() => store.publishProcess(String(process.id)), /credentials не найдены/);
  });

  it("tests transform and HTTP draft nodes without publishing a process", () => {
    const { store } = storeWithArtifacts();
    const transformed = store.testProcessNode({
      input: "hello",
      node: {
        id: "transform-test",
        type: "transform",
        name: "Шаблон",
        position: { x: 0, y: 0 },
        config: { template: "Result: {{ input }}" },
      },
    });
    assert.equal(transformed.output, "Result: hello");

    const http = store.testProcessNode({
      input: "42",
      node: {
        id: "http-test",
        type: "http",
        name: "API",
        position: { x: 0, y: 0 },
        config: { method: "GET", url: "https://example.com/{{ input }}", headers: {}, timeoutSeconds: 5 },
      },
    });
    assert.equal(http.kind, "queued");
    assert.equal((store.getRun(http.runId!)!.stages as Array<{ kind: string }>)[0]?.kind, "http");
  });

  it("lets a Temporal workflow own durable wait timers without polling them from overview or workers", () => {
    const store = new AgatStore(":memory:", {
      seedDemo: false,
      temporalProcesses: true,
      credentialsKey: "temporal-test-key",
    });
    stores.push(store);
    const graph: ProcessGraph = {
      nodes: [
        { id: "start", type: "start", name: "Старт", position: { x: 0, y: 0 }, config: {} },
        { id: "wait", type: "wait", name: "Таймер", position: { x: 160, y: 0 }, config: { waitSeconds: 1 } },
        { id: "end", type: "end", name: "Готово", position: { x: 320, y: 0 }, config: {} },
      ],
      edges: [
        { id: "start-wait", source: "start", target: "wait", branch: "default" },
        { id: "wait-end", source: "wait", target: "end", branch: "default" },
      ],
    };
    const process = store.createProcess({ name: "Temporal timer", graph });
    store.publishProcess(String(process.id));
    const instance = store.startProcess(String(process.id), { input: "wait" })!;

    assert.equal(instance.runtime, "temporal");
    assert.equal(instance.workflowId, `agat-process-${String(instance.id)}`);
    assert.deepEqual(store.listActiveDurableProcesses().map((item) => item.instanceId), [instance.id]);
    store.db.prepare("UPDATE stages SET available_at = ? WHERE run_id = ? AND stage_kind = 'wait'")
      .run("2000-01-01T00:00:00.000Z", String(instance.runId));

    store.getOverview();
    store.maintenanceTick();
    assert.equal(store.getProcessInstance(String(instance.id))?.status, "queued");

    const state = store.temporalProcessTick(String(instance.id), "default");
    assert.equal(state?.status, "completed");
    assert.equal(state?.currentNodeType, "end");
    assert.equal(store.listActiveDurableProcesses().length, 0);
  });
});
