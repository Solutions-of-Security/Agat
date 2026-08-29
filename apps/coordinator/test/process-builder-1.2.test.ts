import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { AgatStore } from "../src/database.js";
import { normalizeProcessScheduleInput, processScheduleDefinition } from "../src/process-runtime.js";
import type { ProcessGraph } from "../src/types.js";

const stores: AgatStore[] = [];

afterEach(() => {
  while (stores.length > 0) stores.pop()?.close();
});

function createStore(): AgatStore {
  const store = new AgatStore(":memory:", { seedDemo: false, credentialsKey: "process-1.2-tests" });
  stores.push(store);
  return store;
}

function node(id: string, type: ProcessGraph["nodes"][number]["type"], config: ProcessGraph["nodes"][number]["config"] = {}) {
  return { id, type, name: id, position: { x: 100, y: 100 }, config };
}

function parallelGraph(): ProcessGraph {
  return {
    nodes: [
      node("start", "start"),
      node("fork", "parallel_fork"),
      node("left", "transform", { template: "left:{{ input }}" }),
      node("right", "transform", { template: "right:{{ input }}" }),
      node("join", "parallel_join", { forkId: "fork" }),
      node("end", "end"),
    ],
    edges: [
      { id: "start-fork", source: "start", target: "fork", branch: "default" },
      { id: "fork-left", source: "fork", target: "left", branch: "default" },
      { id: "fork-right", source: "fork", target: "right", branch: "default" },
      { id: "left-join", source: "left", target: "join", branch: "default" },
      { id: "right-join", source: "right", target: "join", branch: "default" },
      { id: "join-end", source: "join", target: "end", branch: "default" },
    ],
  };
}

function httpGraph(withCompensation: boolean): ProcessGraph {
  const firstConfig: ProcessGraph["nodes"][number]["config"] = {
    method: "POST",
    url: "https://example.test/orders",
    headers: { "Content-Type": "application/json" },
    body: '{"value":"{{ input }}"}',
    timeoutSeconds: 10,
    idempotencyHeader: "Idempotency-Key",
    ...(withCompensation ? {
      compensation: {
        method: "POST" as const,
        url: "https://example.test/orders/undo",
        headers: {},
        body: '{"source":{{ lastOutput }}}',
        timeoutSeconds: 10,
      },
    } : {}),
  };
  const nodes = [
    node("start", "start"),
    node("create", "http", firstConfig),
    ...(withCompensation
      ? [node("follow-up", "http", {
        method: "POST",
        url: "https://example.test/fail",
        headers: {},
        body: "{}",
        timeoutSeconds: 10,
      })]
      : []),
    node("end", "end"),
  ];
  return {
    nodes,
    edges: nodes.slice(0, -1).map((source, index) => ({
      id: `${source.id}-${nodes[index + 1]!.id}`,
      source: source.id,
      target: nodes[index + 1]!.id,
      branch: "default",
    })),
  };
}

function registerWorker(store: AgatStore): string {
  return store.registerNode({
    enrollmentToken: "unused",
    name: "process-worker",
    platform: "test",
    models: [],
    maxConcurrency: 2,
    agentRuntimes: ["single"],
  }).id;
}

describe("process builder 1.2", () => {
  it("forks deterministic tokens, joins every branch once and preserves branch order", () => {
    const store = createStore();
    const process = store.createProcess({ name: "Parallel", graph: parallelGraph() });
    store.publishProcess(String(process.id));

    const instance = store.startProcess(String(process.id), { input: "payload" })!;
    assert.equal(instance.status, "completed");
    const persisted = store.db.prepare("SELECT last_output FROM process_instances WHERE id = ?")
      .get(String(instance.id)) as { last_output: string };
    assert.deepEqual(JSON.parse(persisted.last_output), [
      { branch: "fork-left", output: "left:payload" },
      { branch: "fork-right", output: "right:payload" },
    ]);
    const arrivals = store.db.prepare("SELECT COUNT(*) AS count FROM process_join_arrivals").get() as { count: number };
    assert.equal(arrivals.count, 2);
  });

  it("rejects a fork branch that can reach end without its paired join", () => {
    const store = createStore();
    const graph: ProcessGraph = {
      nodes: [
        node("start", "start"),
        node("fork", "parallel_fork"),
        node("choice", "condition", {
          condition: { source: "last_output", operator: "contains", value: "join", caseSensitive: false },
        }),
        node("right", "transform", { template: "right" }),
        node("join", "parallel_join", { forkId: "fork" }),
        node("end", "end"),
      ],
      edges: [
        { id: "start-fork", source: "start", target: "fork", branch: "default" },
        { id: "fork-choice", source: "fork", target: "choice", branch: "default" },
        { id: "fork-right", source: "fork", target: "right", branch: "default" },
        { id: "choice-join", source: "choice", target: "join", branch: "true" },
        { id: "choice-end", source: "choice", target: "end", branch: "false" },
        { id: "right-join", source: "right", target: "join", branch: "default" },
        { id: "join-end", source: "join", target: "end", branch: "default" },
      ],
    };
    assert.throws(() => store.createProcess({ name: "Bypass join", graph }), /обойти join/);
  });

  it("waits on a separately addressable external signal and exposes an idempotent webhook trigger", () => {
    const store = createStore();
    const graph: ProcessGraph = {
      nodes: [
        node("start", "start"),
        node("signal", "signal", {
          signalName: "order.confirmed",
          signalCorrelationKey: "{{ input }}",
          signalTimeoutSeconds: 3_600,
        }),
        node("end", "end"),
      ],
      edges: [
        { id: "start-signal", source: "start", target: "signal", branch: "default" },
        { id: "signal-end", source: "signal", target: "end", branch: "default" },
      ],
    };
    const process = store.createProcess({ name: "Signal", graph });
    store.publishProcess(String(process.id));
    const instance = store.startProcess(String(process.id), { input: "order-7" })!;
    assert.equal(instance.status, "waiting_external");
    const pendingSignals = instance.pendingSignals as Array<Record<string, unknown>>;
    assert.equal(pendingSignals.length, 1);
    assert.equal(pendingSignals[0]?.name, "order.confirmed");
    assert.equal(pendingSignals[0]?.correlationKey, "order-7");
    assert.equal(typeof pendingSignals[0]?.expiresAt, "string");

    const webhook = store.createProcessWebhook(String(process.id), {
      name: "Order callback",
      kind: "signal",
      signalName: "order.confirmed",
    });
    assert.equal(store.invokeProcessWebhook(String(webhook.id), "wrong", { correlationKey: "order-7" }, "delivery-1"), null);
    const delivered = store.invokeProcessWebhook(String(webhook.id), String(webhook.token), {
      correlationKey: "order-7",
      payload: { accepted: true },
    }, "delivery-1")!;
    assert.equal(delivered.delivered, true);
    assert.equal(store.getProcessInstance(String(instance.id))?.status, "completed");
    const duplicate = store.invokeProcessWebhook(String(webhook.id), String(webhook.token), {}, "delivery-1")!;
    assert.equal(duplicate.duplicate, true);
  });

  it("starts webhook instances exactly once for one Idempotency-Key", () => {
    const store = createStore();
    const graph: ProcessGraph = {
      nodes: [node("start", "start"), node("end", "end")],
      edges: [{ id: "start-end", source: "start", target: "end", branch: "default" }],
    };
    const process = store.createProcess({ name: "Webhook start", graph });
    store.publishProcess(String(process.id));
    const webhook = store.createProcessWebhook(String(process.id), { name: "Start", kind: "start" });
    assert.throws(
      () => store.invokeProcessWebhook(String(webhook.id), String(webhook.token), { input: "x" }, null),
      /Idempotency-Key/,
    );
    const first = store.invokeProcessWebhook(String(webhook.id), String(webhook.token), { input: "x" }, "request-1")!;
    const second = store.invokeProcessWebhook(String(webhook.id), String(webhook.token), { input: "changed" }, "request-1")!;
    const firstInstance = first.instance as Record<string, unknown>;
    const secondInstance = second.instance as Record<string, unknown>;
    assert.equal(second.duplicate, true);
    assert.equal(secondInstance.id, firstInstance.id);
    assert.equal(store.listProcessInstances().length, 1);
    const rotated = store.rotateProcessWebhook(String(webhook.id))!;
    assert.equal(store.invokeProcessWebhook(String(webhook.id), String(webhook.token), { input: "x" }, "request-1"), null);
    const afterRotation = store.invokeProcessWebhook(String(webhook.id), String(rotated.token), { input: "x" }, "request-1")!;
    assert.equal(afterRotation.duplicate, true);
    assert.equal((afterRotation.instance as Record<string, unknown>).id, firstInstance.id);
    assert.equal(store.listProcessInstances().length, 1);
    assert.throws(
      () => store.invokeProcessWebhook(
        String(webhook.id),
        String(rotated.token),
        { input: "x".repeat(100_001) },
        "request-too-large",
      ),
      /максимум 100000/,
    );
    assert.equal(store.listProcessInstances().length, 1);
  });

  it("pins subprocess versions and clones reusable templates", () => {
    const store = createStore();
    const childGraph: ProcessGraph = {
      nodes: [
        node("start", "start"),
        node("transform", "transform", { template: "child:{{ input }}" }),
        node("end", "end"),
      ],
      edges: [
        { id: "start-transform", source: "start", target: "transform", branch: "default" },
        { id: "transform-end", source: "transform", target: "end", branch: "default" },
      ],
    };
    const child = store.createProcess({ name: "Child", graph: childGraph, isTemplate: true });
    store.publishProcess(String(child.id));
    const clone = store.createProcess({ name: "From template", templateId: String(child.id) });
    assert.deepEqual(clone.draftGraph, childGraph);

    const parentGraph: ProcessGraph = {
      nodes: [
        node("start", "start"),
        node("subprocess", "subprocess", {
          subprocessProcessId: String(child.id),
          subprocessInputTemplate: "{{ input }}",
        }),
        node("end", "end"),
      ],
      edges: [
        { id: "start-sub", source: "start", target: "subprocess", branch: "default" },
        { id: "sub-end", source: "subprocess", target: "end", branch: "default" },
      ],
    };
    const parent = store.createProcess({ name: "Parent", graph: parentGraph });
    store.publishProcess(String(parent.id));
    const published = store.getProcessVersion(String(parent.id), 1)!;
    const pinnedNode = (published.graph as ProcessGraph).nodes.find((candidate) => candidate.id === "subprocess")!;
    assert.equal(pinnedNode.config.subprocessVersion, 1);
    const instance = store.startProcess(String(parent.id), { input: "payload" })!;
    assert.equal(instance.status, "completed");
    assert.equal(store.db.prepare("SELECT last_output FROM process_instances WHERE id = ?").get(String(instance.id))?.last_output, "child:payload");
  });

  it("resolves embedded subprocess events to the root runtime owner", () => {
    const store = createStore();
    const childGraph: ProcessGraph = {
      nodes: [
        node("start", "start"),
        node("signal", "signal", { signalName: "child.ready", signalCorrelationKey: "{{ input }}", signalTimeoutSeconds: 60 }),
        node("end", "end"),
      ],
      edges: [
        { id: "start-signal", source: "start", target: "signal", branch: "default" },
        { id: "signal-end", source: "signal", target: "end", branch: "default" },
      ],
    };
    const child = store.createProcess({ name: "Waiting child", graph: childGraph });
    store.publishProcess(String(child.id));
    const parentGraph: ProcessGraph = {
      nodes: [
        node("start", "start"),
        node("child", "subprocess", { subprocessProcessId: String(child.id), subprocessInputTemplate: "{{ input }}" }),
        node("end", "end"),
      ],
      edges: [
        { id: "start-child", source: "start", target: "child", branch: "default" },
        { id: "child-end", source: "child", target: "end", branch: "default" },
      ],
    };
    const parent = store.createProcess({ name: "Waiting parent", graph: parentGraph });
    store.publishProcess(String(parent.id));
    const parentInstance = store.startProcess(String(parent.id), { input: "correlation-1" })!;
    const childInstance = (parentInstance.embeddedSubprocesses as Array<Record<string, unknown>>)[0]!;
    assert.equal(store.processRuntimeOwnerInstance(String(childInstance.id)), parentInstance.id);
    const delivered = store.deliverProcessSignal(String(child.id), "child.ready", {
      instanceId: String(childInstance.id),
      payload: "ready",
    })!;
    assert.equal(delivered.delivered, true);
    assert.equal(store.getProcessInstance(String(parentInstance.id))?.status, "completed");
    assert.equal(store.processRuntimeOwnerInstance(String(childInstance.id)), parentInstance.id);
  });

  it("cancels pending signals and embedded subprocesses without allowing late resurrection", () => {
    const store = createStore();
    const childGraph: ProcessGraph = {
      nodes: [
        node("start", "start"),
        node("signal", "signal", { signalName: "child.late", signalTimeoutSeconds: 3_600 }),
        node("end", "end"),
      ],
      edges: [
        { id: "start-signal", source: "start", target: "signal", branch: "default" },
        { id: "signal-end", source: "signal", target: "end", branch: "default" },
      ],
    };
    const child = store.createProcess({ name: "Cancelled child", graph: childGraph });
    store.publishProcess(String(child.id));
    const parentGraph: ProcessGraph = {
      nodes: [
        node("start", "start"),
        node("child", "subprocess", { subprocessProcessId: String(child.id), subprocessInputTemplate: "{{ input }}" }),
        node("end", "end"),
      ],
      edges: [
        { id: "start-child", source: "start", target: "child", branch: "default" },
        { id: "child-end", source: "child", target: "end", branch: "default" },
      ],
    };
    const parent = store.createProcess({ name: "Cancelled parent", graph: parentGraph });
    store.publishProcess(String(parent.id));
    const parentInstance = store.startProcess(String(parent.id), { input: "payload" })!;
    const childInstance = (parentInstance.embeddedSubprocesses as Array<Record<string, unknown>>)[0]!;

    assert.equal(store.cancelProcessInstance(String(parentInstance.id)), true);
    assert.equal(store.getProcessInstance(String(parentInstance.id))?.status, "cancelled");
    assert.equal(store.getProcessInstance(String(childInstance.id))?.status, "cancelled");
    const delivered = store.deliverProcessSignal(String(child.id), "child.late", {
      instanceId: String(childInstance.id),
      payload: "too late",
    })!;
    assert.equal(delivered.delivered, false);
    assert.equal(store.getProcessInstance(String(parentInstance.id))?.status, "cancelled");
    assert.equal(store.getProcessInstance(String(childInstance.id))?.status, "cancelled");
    assert.equal(
      (store.db.prepare("SELECT status FROM process_subprocess_links WHERE child_instance_id = ?")
        .get(String(childInstance.id)) as { status: string }).status,
      "cancelled",
    );
    assert.equal(
      (store.db.prepare("SELECT status FROM process_signal_waits WHERE instance_id = ?")
        .get(String(childInstance.id)) as { status: string }).status,
      "cancelled",
    );
  });

  it("diffs immutable versions and round-trips BPMN 2.0 extensions", () => {
    const store = createStore();
    const original = parallelGraph();
    original.nodes.find((candidate) => candidate.id === "left")!.id = "9 left/branch";
    original.edges.find((edge) => edge.id === "fork-left")!.target = "9 left/branch";
    original.edges.find((edge) => edge.id === "left-join")!.source = "9 left/branch";
    const process = store.createProcess({ name: "Versioned", description: "v1", graph: original });
    store.publishProcess(String(process.id));
    const changed = structuredClone(original);
    changed.nodes.find((candidate) => candidate.id === "9 left/branch")!.config.template = "changed:{{ input }}";
    store.updateProcess(String(process.id), { name: "Versioned", description: "v2", graph: changed });
    store.publishProcess(String(process.id));
    const diff = store.diffProcessVersions(String(process.id), 1, 2)!;
    assert.equal(diff.summary.changedNodes, 1);
    assert.equal(diff.summary.metadataChanged, true);

    const bpmn = store.exportProcessBpmn(String(process.id), 2)!;
    assert.match(bpmn, /xmlns:bpmn="http:\/\/www\.omg\.org\/spec\/BPMN\/20100524\/MODEL"/);
    assert.match(bpmn, /gatewayDirection="Diverging"/);
    assert.match(bpmn, /agat:id="9 left\/branch"/);
    assert.doesNotMatch(bpmn, /\sid="9 left\/branch"/);
    const imported = store.importProcessBpmn(bpmn, { name: "Imported version" });
    store.publishProcess(String(imported.id));
    const importedGraph = store.getProcessVersion(String(imported.id), 1)!.graph as ProcessGraph;
    assert.equal(importedGraph.nodes.find((candidate) => candidate.id === "join")?.config.forkId, "fork");
    assert.ok(importedGraph.nodes.some((candidate) => candidate.id === "9 left/branch"));
  });

  it("imports a standard BPMN parallel gateway without AGAT extensions", () => {
    const store = createStore();
    const xml = `<?xml version="1.0" encoding="UTF-8"?>
      <definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL">
        <process id="external" name="External BPMN" isExecutable="true">
          <startEvent id="start" name="Start" />
          <parallelGateway id="fork" name="Fork" gatewayDirection="Diverging" />
          <task id="left" name="Left" />
          <task id="right" name="Right" />
          <parallelGateway id="join" name="Join" gatewayDirection="Converging" />
          <endEvent id="end" name="End" />
          <sequenceFlow id="a" sourceRef="start" targetRef="fork" />
          <sequenceFlow id="b" sourceRef="fork" targetRef="left" />
          <sequenceFlow id="c" sourceRef="fork" targetRef="right" />
          <sequenceFlow id="d" sourceRef="left" targetRef="join" />
          <sequenceFlow id="e" sourceRef="right" targetRef="join" />
          <sequenceFlow id="f" sourceRef="join" targetRef="end" />
        </process>
      </definitions>`;
    const imported = store.importProcessBpmn(xml);
    const graph = imported.draftGraph as ProcessGraph;
    assert.equal(graph.nodes.find((candidate) => candidate.id === "fork")?.type, "parallel_fork");
    assert.equal(graph.nodes.find((candidate) => candidate.id === "join")?.config.forkId, "fork");
    assert.doesNotThrow(() => store.publishProcess(String(imported.id)));
  });

  it("rejects ambiguous multi-process BPMN instead of merging definitions", () => {
    const store = createStore();
    const xml = `<?xml version="1.0"?><definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL">
      <process id="one"><startEvent id="start-one"/><endEvent id="end-one"/><sequenceFlow id="one-flow" sourceRef="start-one" targetRef="end-one"/></process>
      <process id="two"><startEvent id="start-two"/><endEvent id="end-two"/><sequenceFlow id="two-flow" sourceRef="start-two" targetRef="end-two"/></process>
    </definitions>`;
    assert.throws(() => store.importProcessBpmn(xml), /ровно один process/);
  });

  it("reuses recorded side effects in safe instance replay and sends deterministic idempotency keys", () => {
    const store = createStore();
    const workerId = registerWorker(store);
    const process = store.createProcess({ name: "Replay", graph: httpGraph(false) });
    store.publishProcess(String(process.id));
    const source = store.startProcess(String(process.id), { input: "payload" })!;
    const lease = store.leaseNext(workerId)!;
    assert.equal(lease.activity?.kind, "http");
    const key = lease.activity?.request.headers["Idempotency-Key"];
    assert.match(key ?? "", /^agat-[0-9a-f]{64}$/);
    store.completeLease(workerId, lease.leaseId, '{"created":true}');
    assert.equal(store.getProcessInstance(String(source.id))?.status, "completed");

    const replay = store.replayProcessInstance(String(source.id), { mode: "safe" })!;
    assert.equal(replay.status, "completed");
    assert.equal(replay.replayOfInstanceId, source.id);
    assert.equal(store.leaseNext(workerId), null);
  });

  it("runs compensations in reverse order and keeps their calls idempotent", () => {
    const store = createStore();
    const workerId = registerWorker(store);
    const process = store.createProcess({ name: "Saga", graph: httpGraph(true) });
    store.publishProcess(String(process.id));
    const instance = store.startProcess(String(process.id), { input: "payload" })!;

    const createLease = store.leaseNext(workerId)!;
    const createKey = createLease.activity?.request.headers["Idempotency-Key"];
    store.completeLease(workerId, createLease.leaseId, '{"order":"7"}');
    const failingLease = store.leaseNext(workerId)!;
    store.db.prepare("UPDATE stages SET max_attempts = 1 WHERE lease_id = ?").run(failingLease.leaseId);
    const failure = store.failLease(workerId, failingLease.leaseId, "downstream failed");
    assert.equal(failure.retrying, false);
    assert.equal(store.getProcessInstance(String(instance.id))?.status, "compensating");

    const compensationLease = store.leaseNext(workerId)!;
    assert.equal(compensationLease.activity?.kind, "http");
    assert.equal(compensationLease.activity?.request.headers["Idempotency-Key"], `${createKey}-compensate`);
    assert.equal(compensationLease.activity?.request.body, '{"source":{"order":"7"}}');
    store.completeLease(workerId, compensationLease.leaseId, "undone");
    const terminal = store.getProcessInstance(String(instance.id))!;
    assert.equal(terminal.status, "failed");
    assert.deepEqual(terminal.compensations, [{ processNodeId: "create", sequence: 0, status: "completed", error: null }]);
  });

  it("does not compensate an HTTP side effect that never completed", () => {
    const store = createStore();
    const workerId = registerWorker(store);
    const process = store.createProcess({ name: "No phantom compensation", graph: httpGraph(true) });
    store.publishProcess(String(process.id));
    const instance = store.startProcess(String(process.id), { input: "payload" })!;
    const lease = store.leaseNext(workerId)!;
    store.db.prepare("UPDATE stages SET max_attempts = 1 WHERE lease_id = ?").run(lease.leaseId);
    store.failLease(workerId, lease.leaseId, "create failed");
    const terminal = store.getProcessInstance(String(instance.id))!;
    assert.equal(terminal.status, "failed");
    assert.deepEqual(terminal.compensations, []);
    assert.equal(store.leaseNext(workerId), null);
  });

  it("normalizes interval, cron and calendar schedule specs without accepting invalid timezone input", () => {
    const common = { input: "report", priority: 50, paused: false, knowledgeCollectionIds: [] };
    assert.equal(normalizeProcessScheduleInput({ ...common, everySeconds: 60 }, "p", "default").kind, "interval");
    const cron = normalizeProcessScheduleInput({
      ...common,
      kind: "cron",
      cronExpression: "0 8 * * MON-FRI",
      timezone: "Europe/Moscow",
    }, "p", "default");
    assert.equal(cron.cronExpression, "0 8 * * MON-FRI");
    assert.equal(cron.timezone, "Europe/Moscow");
    const calendar = normalizeProcessScheduleInput({
      ...common,
      kind: "calendar",
      calendar: { minute: 30, hour: [9, 18], dayOfWeek: ["MONDAY", "FRIDAY"] },
    }, "p", "default");
    assert.deepEqual(calendar.calendar, { minute: 30, hour: [9, 18], dayOfWeek: ["MONDAY", "FRIDAY"] });
    const definition = processScheduleDefinition(calendar, "agat-processes");
    assert.deepEqual(definition.spec, {
      calendars: [{ minute: 30, hour: [9, 18], dayOfWeek: ["MONDAY", "FRIDAY"] }],
      timezone: "UTC",
    });
    assert.equal(definition.action.args[0]?.kind, "calendar");
    assert.deepEqual(definition.action.args[0]?.calendar, calendar.calendar);
    assert.throws(() => normalizeProcessScheduleInput({
      ...common,
      kind: "cron",
      cronExpression: "0 8 * * *",
      timezone: "Mars/Olympus",
    }, "p", "default"), /timezone/);
  });
});
