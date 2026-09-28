import assert from "node:assert/strict";
import test from "node:test";
import { AgatStore } from "../src/database.js";
import { loadConfig } from "../src/config.js";
import { createCoordinatorServer } from "../src/server.js";
import type { ProcessGraph } from "../src/types.js";
import { listen, close } from "./helpers/temporal.js";

const graph: ProcessGraph = {
  nodes: [
    { id: "start", type: "start", name: "Start", position: { x: 0, y: 0 }, config: {} },
    { id: "wait", type: "signal", name: "Hold", position: { x: 100, y: 0 }, config: { signalName: "resume", signalTimeoutSeconds: 3600 } },
    { id: "end", type: "end", name: "End", position: { x: 200, y: 0 }, config: {} },
  ], edges: [{ id: "a", source: "start", target: "wait", branch: "default" }, { id: "b", source: "wait", target: "end", branch: "default" }],
};

test("Temporal cancellation endpoint requires internal auth, project ownership and Temporal runtime; retry preserves terminal state", async () => {
  const store = new AgatStore(":memory:", { seedDemo: false, temporalProcesses: true });
  const server = createCoordinatorServer({ ...loadConfig(), serveWeb: false, temporalEnabled: true,
    temporalInternalToken: "cancel-test-only" }, store);
  try {
    store.createProject({ id: "foreign", name: "Foreign" });
    const processId = String(store.createProcess({ name: "Cancel", graph }).id); store.publishProcess(processId);
    const instanceId = String(store.startProcess(processId, { input: "Synthetic" })!.id);
    const url = await listen(server);
    const send = async (projectId: string | undefined, token = "cancel-test-only") => {
      const response = await fetch(`${url}/api/v1/internal/processes/${instanceId}/cancel`, { method: "POST",
        headers: { "content-type": "application/json", "x-agat-temporal-token": token }, body: JSON.stringify({ projectId }) });
      return { status: response.status, body: await response.json() };
    };
    assert.equal((await send("default", "wrong")).status, 401);
    assert.equal((await send(undefined)).status, 400); assert.equal((await send("foreign")).status, 404);
    assert.equal(store.getProcessInstance(instanceId)!.status, "waiting_external");
    store.db.prepare("UPDATE process_instances SET runtime = 'database' WHERE id = ?").run(instanceId);
    assert.equal((await send("default")).status, 404);
    store.db.prepare("UPDATE process_instances SET runtime = 'temporal' WHERE id = ?").run(instanceId);
    const first = await send("default"); assert.equal(first.status, 200);
    assert.equal((first.body as { status: string }).status, "cancelled");
    const events = store.db.prepare("SELECT COUNT(*) AS count FROM events").get()!.count;
    assert.deepEqual(await send("default"), first);
    assert.equal(store.db.prepare("SELECT COUNT(*) AS count FROM events").get()!.count, events);
    assert.equal(store.db.prepare("SELECT COUNT(*) AS count FROM process_signal_waits WHERE status = 'waiting'").get()!.count, 0);

    const doneGraph = { nodes: [graph.nodes[0]!, graph.nodes[2]!], edges: [{ id: "done", source: "start", target: "end", branch: "default" as const }] };
    const doneProcess = String(store.createProcess({ name: "Completed", graph: doneGraph }).id); store.publishProcess(doneProcess);
    const doneId = String(store.startProcess(doneProcess, { input: "Already complete" })!.id);
    assert.equal(store.temporalProcessCancel(doneId, "default")!.status, "completed");
  } finally { await close(server); store.close(); }
});
