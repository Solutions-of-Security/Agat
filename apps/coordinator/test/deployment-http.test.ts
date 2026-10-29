import assert from "node:assert/strict";
import test from "node:test";
import { loadConfig, type CoordinatorConfig } from "../src/config.js";
import { AgatStore } from "../src/database.js";
import { createCoordinatorServer } from "../src/server.js";

async function fixture(overrides: Partial<CoordinatorConfig>, run: (base: string, store: AgatStore) => Promise<void>) {
  const store = new AgatStore(":memory:", { seedDemo: false });
  const server = createCoordinatorServer({ ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false,
    adminToken: "deployment-test-token", authMode: undefined, deploymentProfile: undefined, oidcEnabled: false, temporalEnabled: false,
    localWorkerLauncherEnabled: false, sandboxEnabled: false, ...overrides }, store);
  try {
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    assert.ok(address && typeof address !== "string");
    await run(`http://127.0.0.1:${address.port}/api/v1`, store);
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
    store.close();
  }
}

test("UI overview, capability API and process preflight share the versioned deployment contract", async () => {
  await fixture({}, async (base) => {
    const deployment = await (await fetch(`${base}/deployment`)).json();
    assert.equal(deployment.contractVersion, 1);
    const overview = await (await fetch(`${base}/overview`)).json();
    assert.deepEqual(overview.deployment, deployment);
    const scenario = await (await fetch(`${base}/process-templates/research-to-report/preflight`, { method: "POST", headers: { "content-type": "application/json" }, body: "{}" })).json();
    assert.deepEqual(scenario.deployment, deployment);
    const response = await fetch(`${base}/deployment/preflight`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ operations: ["stdio", "workspace"] }) });
    assert.equal(response.status, 200);
    const result = await response.json();
    assert.equal(result.allowed, false);
    assert.equal(result.capabilities[0].code, "stdio_not_implemented");
    assert.equal((await fetch(`${base}/deployment/preflight`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ operations: ["unknown"] }) })).status, 400);
  });
});

test("explicit multiuser profile blocks every control entry instead of using local-admin", async () => {
  await fixture({ deploymentProfile: "lite" }, async (base) => {
    const config = await (await fetch(`${base}/auth/config`)).json();
    assert.equal(config.deployment.blockers[0].code, "individual_identity_required");
    for (const path of ["/auth/me", "/overview", "/deployment", "/processes"]) {
      const response = await fetch(base + path, { headers: { "x-agat-admin-token": "deployment-test-token" } });
      assert.equal(response.status, 503, path);
    }
    assert.equal((await fetch(`${base}/health`)).status, 503);
  });
});

test("invalid or unsupported credentials cannot become local-admin even on read-only routes", async () => {
  await fixture({}, async (base) => {
    for (const headers of [{ authorization: "Bearer wrong" }, { "x-agat-admin-token": "wrong" }]) {
      assert.equal((await fetch(`${base}/auth/me`, { headers })).status, 401);
      assert.equal((await fetch(`${base}/overview`, { headers })).status, 401);
    }
    const valid = await fetch(`${base}/auth/me`, { headers: { "x-agat-admin-token": "deployment-test-token" } });
    assert.equal(valid.status, 200);
    assert.equal((await valid.json()).authenticationMode, "legacy");
  });
});

test("missing OIDC identity and unavailable local provider fail closed", async () => {
  await fixture({ authMode: "local", deploymentProfile: "lite" }, async (base) => {
    assert.equal((await fetch(`${base}/auth/me`)).status, 503);
  });
  await fixture({ oidcEnabled: true, oidcIssuer: "https://issuer.invalid/realms/agat" }, async (base) => {
    assert.equal((await fetch(`${base}/auth/me`)).status, 401);
    assert.equal((await fetch(`${base}/auth/me`, { headers: { "x-agat-admin-token": "deployment-test-token" } })).status, 401);
  });
  await fixture({ authMode: "oidc", oidcEnabled: false, oidcIssuer: "https://issuer.invalid/realms/agat" }, async (base) => {
    const config = await (await fetch(`${base}/auth/config`)).json();
    assert.equal(config.enabled, true);
    assert.equal(config.url, "https://issuer.invalid");
    assert.equal(config.realm, "agat");
    assert.equal((await fetch(`${base}/auth/me`)).status, 401);
  });
});
