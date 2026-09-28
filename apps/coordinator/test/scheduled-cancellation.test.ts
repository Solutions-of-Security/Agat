import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import { AgatStore, POSTGRES_SCHEMA_VERSION, ScheduledStartCancelledError, ScheduledStartConflictError } from "../src/database.js";
import { loadConfig } from "../src/config.js";
import { createCoordinatorServer } from "../src/server.js";
import { listen, close } from "./helpers/temporal.js";
import type { ProcessGraph } from "../src/types.js";

const key = (character: string) => `agat-scheduled-v1:${character.repeat(64)}`;
const input = { input: "Synthetic", priority: 50, knowledgeCollectionIds: [] };
const graph: ProcessGraph = {
  nodes: [
    { id: "start", type: "start", name: "Start", position: { x: 0, y: 0 }, config: {} },
    { id: "hold", type: "signal", name: "Hold", position: { x: 100, y: 0 }, config: { signalName: "resume", signalTimeoutSeconds: 3600 } },
    { id: "end", type: "end", name: "End", position: { x: 200, y: 0 }, config: {} },
  ], edges: [{ id: "a", source: "start", target: "hold", branch: "default" }, { id: "b", source: "hold", target: "end", branch: "default" }],
};

test("scheduled cancellation survives schema 29 upgrade, restart and retained or absent creation receipts", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-scheduled-cancel-upgrade-"));
  const database = path.join(directory, "state.sqlite");
  const options = { seedDemo: false, temporalProcesses: true, artifactsDir: path.join(directory, "artifacts") };
  let store = new AgatStore(database, options);
  try {
    const processId = String(store.createProcess({ name: "Cancel creation", graph }).id); store.publishProcess(processId);
    const created = store.startScheduledProcess(processId, input, "default", key("a"))!;
    store.close();
    const legacy = new DatabaseSync(database);
    legacy.exec("ALTER TABLE process_scheduled_start_receipts DROP COLUMN cancel_requested_at; PRAGMA user_version = 29"); legacy.close();
    store = new AgatStore(database, options);
    assert.equal(store.db.prepare("PRAGMA user_version").get()!.user_version, POSTGRES_SCHEMA_VERSION);
    assert.deepEqual(store.startScheduledProcess(processId, input, "default", key("a")), created);
    assert.throws(() => store.cancelScheduledProcess(processId, { ...input, input: "Changed" }, "default", key("a")), ScheduledStartConflictError);
    assert.equal(store.getProcessInstance(created.instanceId)!.status, "waiting_external");
    const cancelled = store.cancelScheduledProcess(processId, input, "default", key("a"));
    assert.equal(cancelled!.status, "cancelled");
    assert.equal(store.cancelScheduledProcess(processId, input, "default", key("b")), null);
    const receipt = () => store.db.prepare("SELECT * FROM process_scheduled_start_receipts WHERE idempotency_key = ?").get(key("b"));
    const firstIntent = receipt(); assert.equal(firstIntent!.response_json, "null");
    assert.equal(store.cancelScheduledProcess(processId, input, "default", key("b")), null); assert.deepEqual(receipt(), firstIntent);
    store.close(); store = new AgatStore(database, options);
    assert.deepEqual(store.cancelScheduledProcess(processId, input, "default", key("a")), cancelled);
    for (const item of ["a", "b"]) assert.throws(() => store.startScheduledProcess(processId, input, "default", key(item)), ScheduledStartCancelledError);
    assert.throws(() => store.startScheduledProcess(processId, { ...input, priority: 40 }, "default", key("b")), ScheduledStartConflictError);
    assert.equal(store.listProcessInstances().length, 1);
    const runId = store.getProcessInstance(created.instanceId)!.runId;
    store.db.prepare("DELETE FROM runs WHERE id = ?").run(String(runId));
    assert.equal(store.cancelScheduledProcess(processId, input, "default", key("a")), null);
    assert.throws(() => store.startScheduledProcess(processId, input, "default", key("a")), ScheduledStartCancelledError);
    assert.ok(store.startScheduledProcess(processId, input, "default", key("c")));
  } finally { store.close(); fs.rmSync(directory, { recursive: true, force: true }); }
});

test("scheduled cancellation atomically rolls back the intent if application cleanup fails", () => {
  const store = new AgatStore(":memory:", { seedDemo: false, temporalProcesses: true });
  try {
    const processId = String(store.createProcess({ name: "Atomic cancellation", graph }).id); store.publishProcess(processId);
    const created = store.startScheduledProcess(processId, input, "default", key("a"))!;
    store.db.exec("CREATE TRIGGER cancel_failure BEFORE UPDATE OF status ON process_instances WHEN NEW.status = 'cancelled' BEGIN SELECT RAISE(ABORT, 'cleanup fault'); END;");
    assert.throws(() => store.cancelScheduledProcess(processId, input, "default", key("a")), /cleanup fault/);
    assert.equal(store.db.prepare("SELECT cancel_requested_at FROM process_scheduled_start_receipts").get()!.cancel_requested_at, null);
    assert.equal(store.getProcessInstance(created.instanceId)!.status, "waiting_external");
    assert.equal(store.db.prepare("SELECT COUNT(*) AS count FROM process_signal_waits WHERE status = 'waiting'").get()!.count, 1);
    store.db.exec("DROP TRIGGER cancel_failure");
    assert.equal(store.cancelScheduledProcess(processId, input, "default", key("a"))!.status, "cancelled");
  } finally { store.close(); }
});

test("scheduled-cancel HTTP validates auth and key, fences late creates, and preserves project boundaries", async () => {
  const store = new AgatStore(":memory:", { seedDemo: false, temporalProcesses: true });
  const server = createCoordinatorServer({ ...loadConfig(), serveWeb: false, temporalEnabled: true,
    temporalInternalToken: "creation-cancel-test" }, store);
  try {
    store.createProject({ id: "foreign", name: "Foreign" });
    const processId = String(store.createProcess({ name: "HTTP cancellation", graph }).id); store.publishProcess(processId);
    const created = store.startScheduledProcess(processId, input, "default", key("a"))!;
    const url = await listen(server);
    const send = async (action: string, projectId: string | undefined, requestKey: string, token = "creation-cancel-test", text = input.input) => {
      const response = await fetch(`${url}/api/v1/internal/processes/${processId}/${action}`, { method: "POST",
        headers: { "content-type": "application/json", "x-agat-temporal-token": token, "idempotency-key": requestKey },
        body: JSON.stringify({ ...input, input: text, projectId }) });
      return { status: response.status, body: await response.json() };
    };
    assert.equal((await send("scheduled-cancel", "default", key("a"), "wrong")).status, 401);
    assert.equal((await send("scheduled-cancel", undefined, key("a"))).status, 400);
    assert.equal((await send("scheduled-cancel", "default", "invalid")).status, 400);
    assert.equal((await send("scheduled-cancel", "default", key("a"), undefined, "Changed")).status, 409);
    assert.deepEqual(await send("scheduled-cancel", "foreign", key("a")), { status: 200, body: null });
    assert.equal(store.getProcessInstance(created.instanceId)!.status, "waiting_external");
    assert.deepEqual(await send("scheduled-cancel", "default", key("b")), { status: 200, body: null });
    const late = await send("scheduled-start", "default", key("b"));
    assert.equal(late.status, 409); assert.equal((late.body as { code: string }).code, "SCHEDULED_START_CANCELLED");
    const cancelled = await send("scheduled-cancel", "default", key("a"));
    assert.equal(cancelled.status, 200); assert.equal((cancelled.body as { status: string }).status, "cancelled");
    assert.deepEqual(await send("scheduled-cancel", "default", key("a")), cancelled);
    assert.equal(store.listProcessInstances().length, 1);
  } finally { await close(server); store.close(); }
});
