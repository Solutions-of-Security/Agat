import assert from "node:assert/strict";
import test from "node:test";
import { AgatStore } from "../src/database.js";
import { loadConfig } from "../src/config.js";
import { createCoordinatorServer } from "../src/server.js";
import type { ScenarioPreflight } from "../src/scenario-preflight.js";
import type { ProcessGraph } from "../src/types.js";

test("preflight HTTP is project-scoped and read-only; start returns actionable conflicts and preserves the reviewed pin", async () => {
  const store = new AgatStore(":memory:", { seedDemo: false, credentialsKey: "preflight-http-test-only" });
  const server = createCoordinatorServer({ ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false,
    adminToken: "preflight-http-test-only", oidcEnabled: false, mcpEnabled: true, sandboxEnabled: false, a2aEnabled: false, localWorkerLauncherEnabled: false }, store);
  try {
    store.createProject({ id: "isolated", name: "Isolated" });
    const process = store.createProcess({ name: "Scenario", catalogTemplateId: "research-to-report", catalogTemplateVersion: 1,
      templateBindings: { researcher: "collector", reviewer: "editor" } }, "isolated");
    const id = String(process.id);
    await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
    const address = server.address();
    assert.ok(address && typeof address === "object");
    const base = `http://127.0.0.1:${address.port}/api/v1`;
    const headers = { "content-type": "application/json", "x-agat-admin-token": "preflight-http-test-only", "x-agat-project-id": "isolated" };
    const post = (path: string, body: unknown, requestHeaders = headers) => fetch(`${base}${path}`, { method: "POST", headers: requestHeaders, body: JSON.stringify(body) });
    const before = store.listEvents(0, 100, "isolated").length;
    const draft = await post(`/processes/${id}/preflight`, { version: 0 });
    assert.equal(draft.status, 200);
    const saved = await draft.json() as ScenarioPreflight;
    assert.equal(saved.saved, true);
    assert.equal(saved.queueable, false);
    assert.ok(saved.blockers.some((item) => item.category === "version" && item.recovery.href.includes(`processId=${id}`)));
    assert.equal(store.listEvents(0, 100, "isolated").length, before);
    const foreign = await post(`/processes/${id}/preflight`, { version: 1 }, { ...headers, "x-agat-project-id": "default" });
    assert.equal(foreign.status, 404);
    const preview = await post("/process-templates/research-to-report/preflight", { catalogTemplateVersion: 1 });
    assert.equal(preview.status, 200);
    assert.equal((await preview.json() as ScenarioPreflight).saved, false);
    store.publishProcess(id, "isolated");
    const missingPin = await post(`/processes/${id}/preflight`, {});
    assert.equal((await missingPin.json() as ScenarioPreflight).queueable, false);
    const now = await post(`/processes/${id}/start`, { input: "Test", version: 1, startMode: "now" });
    assert.equal(now.status, 409);
    const conflict = await now.json() as { preflight: ScenarioPreflight };
    assert.equal(conflict.preflight.queueable, true);
    assert.equal(conflict.preflight.runnableNow, false);
    assert.ok(conflict.preflight.blockers.every((item) => item.recovery.href));
    const denied = await post(`/processes/${id}/start`, { input: "Test", version: 1, startMode: "queue" }, { ...headers, "x-agat-admin-token": "wrong" });
    assert.equal(denied.status, 401);
    const queued = await post(`/processes/${id}/start`, { input: "Test", version: 1, startMode: "queue" });
    assert.equal(queued.status, 201);
    assert.equal((await queued.json() as { processVersion: number }).processVersion, 1);
    store.createMcpServer({ name: "Local tool", namespace: "local", transport: "wasi", enabled: true, defaultPolicy: "auto", trustAnnotations: true,
      sandbox: { moduleBase64: Buffer.from([0, 97, 115, 109, 1, 0, 0, 0]).toString("base64"),
        tool: { name: "read", inputSchema: { type: "object" }, annotations: { readOnlyHint: true } } } }, "isolated");
    store.updateProcess(id, { name: "Scenario", graph: { ...(process.draftGraph as ProcessGraph), requiredTools: ["local__read"] } }, "isolated");
    store.publishProcess(id, "isolated");
    const isolated = await post(`/processes/${id}/preflight`, { version: 2 });
    const isolatedReport = await isolated.json() as ScenarioPreflight;
    assert.ok(isolatedReport.blockers.some((item) => item.code === "tool_sandbox:local__read"), "HTTP preflight includes the actual sandbox runtime state");
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
    store.close();
  }
});
