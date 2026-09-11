import assert from "node:assert/strict";
import test from "node:test";
import { AgatStore } from "../src/database.js";
import { loadConfig } from "../src/config.js";
import { createCoordinatorServer } from "../src/server.js";

test("catalog HTTP route and create endpoint preserve admin checks, project isolation and version validation", async () => {
  const store = new AgatStore(":memory:", { seedDemo: false });
  const server = createCoordinatorServer({ ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false,
    adminToken: "catalog-http-test-only", oidcEnabled: false, mcpEnabled: false, a2aEnabled: false, localWorkerLauncherEnabled: false }, store);
  try {
    store.createProject({ id: "catalog-test", name: "Catalog Test" });
    await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
    const address = server.address();
    assert.ok(address && typeof address === "object");
    const base = `http://127.0.0.1:${address.port}/api/v1`;
    const response = await fetch(`${base}/process-templates`);
    assert.equal(response.status, 200);
    const catalog = await response.json() as { categories: unknown[]; templates: unknown[] };
    assert.equal(catalog.categories.length, 7);
    assert.equal(catalog.templates.length, 13);
    const body = { name: "HTTP catalog copy", catalogTemplateId: "research-to-report", catalogTemplateVersion: 1 };
    const denied = await fetch(`${base}/processes`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    assert.equal(denied.status, 401);
    const headers = { "content-type": "application/json", "x-agat-admin-token": "catalog-http-test-only", "x-agat-project-id": "catalog-test" };
    const stale = await fetch(`${base}/processes`, { method: "POST", headers, body: JSON.stringify({ ...body, catalogTemplateVersion: 999 }) });
    assert.equal(stale.status, 400);
    const created = await fetch(`${base}/processes`, { method: "POST", headers, body: JSON.stringify(body) });
    assert.equal(created.status, 201);
    const process = await created.json() as { id: string; status: string; publishedVersion: number };
    assert.equal(process.status, "draft");
    assert.equal(process.publishedVersion, 0);
    assert.equal(store.getProcess(process.id), null);
    assert.ok(store.getProcess(process.id, "catalog-test"));
    const event = store.listEvents(0, 100, "catalog-test").find((item) => item.type === "process.created");
    assert.ok(event);
    assert.ok(event.data);
    assert.equal(event.data.catalogTemplateId, body.catalogTemplateId);
    assert.equal(event.data.catalogTemplateVersion, 1);
    assert.equal(event.data.categoryId, "knowledge");
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
    store.close();
  }
});
