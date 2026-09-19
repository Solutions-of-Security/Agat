import assert from "node:assert/strict";
import test from "node:test";
import { AgatStore } from "../src/database.js";
import { loadConfig } from "../src/config.js";
import { createCoordinatorServer } from "../src/server.js";
import type { ProcessGraph } from "../src/types.js";

test("human forms HTTP validates before advancing, respects project scope and audits the submitted answers", async () => {
  const store = new AgatStore(":memory:", { seedDemo: false });
  const server = createCoordinatorServer({ ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false,
    adminToken: "forms-http-test-only", oidcEnabled: false, mcpEnabled: false, sandboxEnabled: false, a2aEnabled: false, localWorkerLauncherEnabled: false }, store);
  try {
    store.createProject({ id: "isolated", name: "Isolated" });
    const graph: ProcessGraph = {
      nodes: [
        { id: "start", name: "Start", type: "start", position: { x: 0, y: 0 }, config: {} },
        { id: "form", name: "Form", type: "approval", position: { x: 200, y: 0 }, config: { approvalMode: "input", approvalForm: { title: "Review", description: "", fields: [{ id: "count", label: "Count", type: "number", required: true }] } } },
        { id: "end", name: "End", type: "end", position: { x: 400, y: 0 }, config: {} },
      ], edges: [{ id: "one", source: "start", target: "form", branch: "default" }, { id: "two", source: "form", target: "end", branch: "default" }],
    };
    const process = store.createProcess({ name: "Human form", graph }, "isolated"); store.publishProcess(String(process.id), "isolated");
    const instance = store.startProcess(String(process.id), { input: "Initial" }, "isolated")!;
    const stage = (store.getOverview("isolated").approvals as Array<{ stageId: string }>)[0]!;
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address(); assert.ok(address && typeof address === "object");
    const base = `http://127.0.0.1:${address.port}/api/v1`;
    const headers = { "content-type": "application/json", "x-agat-admin-token": "forms-http-test-only", "x-agat-project-id": "isolated" };
    const submit = (body: unknown, project = "isolated") => fetch(`${base}/approvals/${stage.stageId}`, { method: "POST", headers: { ...headers, "x-agat-project-id": project }, body: JSON.stringify(body) });
    assert.equal((await submit({ decision: "approve", formData: { count: 2 } }, "default")).status, 404);
    for (const body of [{ decision: "approve" }, { decision: "approve", formData: { count: "2" } }, { decision: "approve", formData: { count: 2, injected: true } }]) {
      assert.equal((await submit(body)).status, 400);
      assert.equal(store.getProcessInstance(String(instance.id), "isolated")?.status, "waiting_approval");
    }
    assert.equal((await submit({ decision: "approve", formData: { count: 0 }, comment: "Checked" })).status, 204);
    assert.equal(store.getProcessInstance(String(instance.id), "isolated")?.status, "completed");
    assert.equal((await submit({ decision: "approve", formData: { count: 0 } })).status, 404);
    const overview = await (await fetch(`${base}/overview`, { headers })).json() as { events: Array<{ type: string; data: Record<string, unknown> }> };
    const decision = overview.events.find((event) => event.type === "approval.decision.recorded")!;
    assert.deepEqual(decision.data.formData, { count: 0 });
    assert.equal((decision.data.snapshot as Record<string, unknown>).mode, "input");
    assert.equal(decision.data.comment, "Checked");
  } finally {
    server.closeAllConnections(); await new Promise<void>((resolve) => server.close(() => resolve())); store.close();
  }
});
