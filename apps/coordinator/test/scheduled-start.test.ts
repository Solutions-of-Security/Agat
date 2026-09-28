import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import { AgatStore, ScheduledStartConflictError } from "../src/database.js";
import { loadConfig } from "../src/config.js";
import { createCoordinatorServer } from "../src/server.js";
import type { ProcessGraph } from "../src/types.js";

const key = (value: string) => `agat-scheduled-v1:${value.repeat(64)}`;
const graph: ProcessGraph = {
  nodes: [
    { id: "start", type: "start", name: "Start", position: { x: 0, y: 0 }, config: {} },
    { id: "end", type: "end", name: "End", position: { x: 200, y: 0 }, config: {} },
  ], edges: [{ id: "e", source: "start", target: "end", branch: "default" }],
};

test("scheduled-start receipt survives restart, publication, capacity changes and run retention", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-scheduled-start-"));
  const database = path.join(directory, "state.sqlite");
  const options = { seedDemo: false, temporalProcesses: true, artifactsDir: path.join(directory, "artifacts") };
  let store = new AgatStore(database, options);
  try {
    store.close();
    const legacy = new DatabaseSync(database);
    legacy.exec("DROP TABLE process_scheduled_start_receipts; PRAGMA user_version = 27"); legacy.close();
    store = new AgatStore(database, options);
    assert.equal(store.db.prepare("PRAGMA user_version").get()!.user_version, 28);
    const processId = String(store.createProcess({ name: "Scheduled", graph }).id);
    store.publishProcess(processId);
    const input = { input: "Synthetic input", priority: 50, knowledgeCollectionIds: [] };
    const first = store.startScheduledProcess(processId, input, "default", key("a"))!;
    const runId = String(store.getProcessInstance(first.instanceId)!.runId);
    const before = store.listEvents(0, 100).length;
    store.close(); store = new AgatStore(database, options);
    assert.deepEqual(store.startScheduledProcess(processId, { knowledgeCollectionIds: [], priority: 50, input: "Synthetic input" }, "default", key("a")), first);
    assert.equal(store.listEvents(0, 100).length, before, "A receipt replay must not repeat events or create stages");
    store.updateProcess(processId, { name: "Scheduled v2", graph }); store.publishProcess(processId);
    assert.deepEqual(store.startScheduledProcess(processId, input, "default", key("a")), first);
    assert.equal(store.getProcessInstance(first.instanceId)!.processVersion, 1);
    const second = store.startScheduledProcess(processId, input, "default", key("b"))!;
    assert.notEqual(second.instanceId, first.instanceId);
    assert.equal(store.getProcessInstance(second.instanceId)!.processVersion, 2);
    for (const changed of [{ ...input, input: "Changed" }, { ...input, priority: 90 }, { ...input, knowledgeCollectionIds: ["other"] }]) {
      assert.throws(() => store.startScheduledProcess(processId, changed, "default", key("a")), ScheduledStartConflictError);
    }
    assert.throws(() => store.startScheduledProcess("other-process", input, "default", key("a")), ScheduledStartConflictError);
    const db = new DatabaseSync(database);
    try {
      db.exec("PRAGMA foreign_keys=ON");
      db.prepare("DELETE FROM runs WHERE id = ?").run(runId);
      db.exec("UPDATE projects SET max_queued_tasks = 0 WHERE id = 'default'");
      assert.deepEqual(store.startScheduledProcess(processId, input, "default", key("a")), first);
      assert.equal(store.getProcessInstance(first.instanceId), null);
      assert.equal(store.listProcessInstances().length, 1, "Retention must not allow a retry to recreate its deleted run");
    } finally { db.close(); }
  } finally { store.close(); fs.rmSync(directory, { recursive: true, force: true }); }
});

test("scheduled-start rolls back all process writes if receipt persistence fails and leaves rejected starts retryable", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-scheduled-rollback-"));
  const database = path.join(directory, "state.sqlite");
  const store = new AgatStore(database, { seedDemo: false, temporalProcesses: true, artifactsDir: path.join(directory, "artifacts") });
  const db = new DatabaseSync(database);
  try {
    const processId = String(store.createProcess({ name: "Atomic scheduled", graph }).id);
    assert.throws(() => store.startScheduledProcess(processId, { input: "Synthetic" }, "default", key("a")));
    assert.equal(db.prepare("SELECT COUNT(*) AS n FROM process_scheduled_start_receipts").get()!.n, 0);
    store.publishProcess(processId);
    const before = store.listEvents(0, 100).length;
    db.exec(`CREATE TRIGGER fail_scheduled_receipt BEFORE INSERT ON process_scheduled_start_receipts
      BEGIN SELECT RAISE(ABORT, 'fixture receipt write failure'); END`);
    assert.throws(() => store.startScheduledProcess(processId, { input: "Synthetic" }, "default", key("a")), /fixture receipt write failure/);
    assert.equal(store.listProcessInstances().length, 0);
    assert.equal(db.prepare("SELECT COUNT(*) AS n FROM runs").get()!.n, 0);
    assert.equal(store.listEvents(0, 100).length, before);
    db.exec("DROP TRIGGER fail_scheduled_receipt");
    assert.ok(store.startScheduledProcess(processId, { input: "Synthetic" }, "default", key("a")));
    assert.equal(store.listProcessInstances().length, 1);
  } finally { db.close(); store.close(); fs.rmSync(directory, { recursive: true, force: true }); }
});

test("scheduled-start HTTP requires a bounded key and internal auth, rejects mismatches and isolates projects", async () => {
  const store = new AgatStore(":memory:", { seedDemo: false, temporalProcesses: true });
  const server = createCoordinatorServer({ ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false,
    oidcEnabled: false, mcpEnabled: false, sandboxEnabled: false, a2aEnabled: false, localWorkerLauncherEnabled: false,
    temporalEnabled: true, temporalInternalToken: "scheduled-test-only" }, store);
  try {
    store.createProject({ id: "isolated", name: "Isolated" });
    const processId = String(store.createProcess({ name: "HTTP scheduled", graph }).id); store.publishProcess(processId);
    const otherId = String(store.createProcess({ name: "Other scheduled", graph }, "isolated").id); store.publishProcess(otherId, "isolated");
    await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
    const address = server.address(); assert.ok(address && typeof address === "object");
    const send = async (id: string, projectId: string, idempotencyKey: string | undefined, input = "Synthetic", token = "scheduled-test-only") => {
      const response = await fetch(`http://127.0.0.1:${address.port}/api/v1/internal/processes/${id}/scheduled-start`, {
        method: "POST", headers: { "content-type": "application/json", "x-agat-temporal-token": token,
          ...(idempotencyKey === undefined ? {} : { "idempotency-key": idempotencyKey }) },
        body: JSON.stringify({ projectId, input }),
      });
      return { status: response.status, body: await response.json() };
    };
    assert.equal((await send(processId, "default", key("a"), "Synthetic", "wrong")).status, 401);
    for (const invalid of [undefined, "", "a".repeat(512), "agat-scheduled-v1:bad"]) {
      assert.equal((await send(processId, "default", invalid)).status, 400);
    }
    assert.equal(store.listProcessInstances().length, 0);
    assert.equal((await send(processId, "isolated", key("a"))).status, 404);
    const first = await send(processId, "default", key("a")); assert.equal(first.status, 201);
    assert.deepEqual(await send(processId, "default", key("a")), first);
    const conflict = await send(processId, "default", key("a"), "Changed");
    assert.equal(conflict.status, 409);
    assert.equal((conflict.body as { code: string }).code, "SCHEDULED_START_IDEMPOTENCY_CONFLICT");
    const other = await send(otherId, "isolated", key("a")); assert.equal(other.status, 201);
    assert.notDeepEqual(other.body, first.body);
    assert.equal(store.listProcessInstances().length, 1); assert.equal(store.listProcessInstances(100, "isolated").length, 1);
  } finally {
    server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); store.close();
  }
});
