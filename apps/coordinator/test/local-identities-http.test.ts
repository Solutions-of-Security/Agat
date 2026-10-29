import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import test from "node:test";
import { loadConfig } from "../src/config.js";
import { AgatStore } from "../src/database.js";
import { createCoordinatorServer } from "../src/server.js";
import { LocalIdentityProvider } from "../src/local-identities.js";
import { scopesForRoles, type LocalPrincipal } from "../src/local-identity-contract.js";
import { McpGateway, normalizeCatalogTools, type McpUpstreamClient } from "../src/mcp.js";
import { CoordinatorTelemetry } from "../src/telemetry.js";

const origin = "https://agat.example";
async function fixture(run: (f: Awaited<ReturnType<typeof setup>>) => Promise<void>) {
  const f = await setup();
  try { await run(f); }
  finally { f.server.closeAllConnections(); await new Promise<void>(resolve => f.server.close(() => resolve())); f.store.close(); fs.rmSync(f.directory, { recursive: true, force: true }); }
}
async function setup() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-local-http-"));
  const store = new AgatStore(":memory:", { seedDemo: false, credentialsKey: "local-http-fixture-key", artifactsDir: path.join(directory, "artifacts") });
  store.createProject({ id: "alpha", name: "Alpha" }); store.createProject({ id: "beta", name: "Beta" });
  const identities = new LocalIdentityProvider(store.db, origin, (type, data) => store.recordPlatformEvent(type, "Fixture identity audit", data));
  const owner = identities.bootstrap("owner");
  const principals = ["alpha", "beta"].map((project, index) => identities.createPrincipal({ username: index ? "bob" : "alice", kind: "user", roles: ["admin"], projectIds: [project] }));
  const issue = (principal: LocalPrincipal, overrides = {}) => identities.issueCredential(principal.subject, { label: "Fixture", purpose: principal.kind === "service" ? "api" : "personal", application: "fixture-client", roles: principal.roles,
    projectIds: principal.projectIds, scopes: scopesForRoles(principal.roles).filter(scope => principal.kind !== "service" || !["approve", "review"].includes(scope)), expiresAt: new Date(Date.now() + 3600000).toISOString(), ...overrides });
  const keys = principals.map(principal => issue(principal));
  const agents = ["alpha", "beta"].map(project => store.createAgent({ name: `Agent ${project}`, role: "Reader", systemPrompt: "Read synthetic input", model: "test-model" }, project));
  let effects = 0;
  const upstream: McpUpstreamClient = {
    async listTools(server) { return { tools: normalizeCatalogTools(server.namespace, [{ name: "destroy", description: "Destructive fixture", inputSchema: { type: "object" }, annotations: { destructiveHint: true } }]), ttlMs: 60000, cacheScope: "private" }; },
    async callTool(_server, _tool, _input, assertAllowed) { assertAllowed?.(); effects++; return { content: [] }; },
  };
  const gateway = new McpGateway(store, new CoordinatorTelemetry({ enabled: false, serviceName: "test", exporterEndpoint: "" }), { enabled: true, requestTimeoutSeconds: 5, maxResponseBytes: 100000, approvalTtlSeconds: 300 }, upstream);
  const server = createCoordinatorServer({ ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false, authMode: "local", deploymentProfile: "lite", localAuthOrigin: origin,
    adminToken: "unused-legacy-token", oidcEnabled: false, temporalEnabled: false, mcpEnabled: true, sandboxEnabled: false, a2aEnabled: false, localWorkerLauncherEnabled: false, allowedOrigins: [origin] }, store, undefined, undefined, gateway);
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address(); assert.ok(address && typeof address !== "string"); const base = `http://127.0.0.1:${address.port}/api/v1`;
  const api = async (route: string, token?: string, project = "default", method = "GET", body?: unknown, headers: Record<string, string> = {}) => fetch(`${base}${route}`, {
    method, headers: { ...(token ? { authorization: `Bearer ${token}` } : {}), "x-agat-project-id": project, ...(body !== undefined ? { "content-type": "application/json" } : {}), ...headers }, body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { directory, store, identities, server, base, api, principals, keys, owner, issue, agents, gateway, effects: () => effects };
}

test("SQLite lite isolates two users/projects across runs, history, artifacts, credentials, approvals and bindings", async () => fixture(async f => {
  assert.equal((await f.api("/overview")).status, 401);
  assert.equal((await f.api("/overview", undefined, "alpha", "GET", undefined, { "x-agat-admin-token": "unused-legacy-token" })).status, 401);
  const deployment = await (await f.api("/deployment", f.keys[0]!.token, "alpha")).json();
  assert.equal(deployment.profile, "lite"); assert.equal(deployment.ready, true); assert.equal(deployment.runtime, "database"); assert.equal(deployment.stateStore, "sqlite");
  const runs: string[] = []; const credentialIds: string[] = []; const bindingIds: string[] = [];
  for (const [index, project] of ["alpha", "beta"].entries()) {
    const token = f.keys[index]!.token;
    const projects = await (await f.api("/projects", token, project)).json(); assert.deepEqual(projects.projects.map((p: { id: string }) => p.id), [project]);
    const me = await (await f.api("/auth/me", token, project, "GET", undefined, { "x-agat-username": "local-admin", "x-agat-subject": f.owner.principal.subject })).json();
    assert.equal(me.subject, f.principals[index]!.subject); assert.equal(me.authenticationMode, "local"); assert.equal(me.globalProjectAccess, false);
    const created = await f.api("/runs", token, project, "POST", { name: `Private ${project}`, input: `Secret ${project}`, agentIds: [f.agents[index]!.id], approvalRequired: false, resultDestination: "artifacts", artifactPath: "private-result", username: "owner", subject: f.owner.principal.subject }); assert.equal(created.status, 201);
    runs.push((await created.json()).id);
    const credential = await f.api("/credentials", token, project, "POST", { name: `Secret ${project}`, type: "api_key", data: { apiKey: `synthetic-${project}` } }); assert.equal(credential.status, 201); credentialIds.push((await credential.json()).id);
    const binding = await f.api("/auth/channel-bindings", token, project, "POST", { provider: "telegram", installationId: "fixture", externalUserId: String(index + 1), subject: f.principals[index]!.subject }); assert.equal(binding.status, 201); bindingIds.push((await binding.json()).id);
    assert.equal((await f.api("/auth/principals", token, project)).status, 403);
    assert.equal((await f.api("/auth/audit", token, project)).status, 403);
    assert.equal((await f.api("/mcp/servers", token, project, "POST", { name: "Forbidden isolation", namespace: "forbidden", transport: "container", sandbox: {} })).status, 403);
    assert.equal((await f.api("/projects", token, project, "POST", { id: "intruder", name: "Intruder" })).status, 403);
  }
  const worker = f.store.registerNode({ enrollmentToken: "unused", name: "fixture-worker", platform: "test", models: ["test-model"] });
  for (let i = 0; i < 2; i++) { const lease = f.store.leaseNext(worker.id); assert.ok(lease); f.store.completeLease(worker.id, lease.leaseId, "Synthetic result", [{ name: "private.txt", content: `Private result ${i}` }]); }
  for (const [index, project] of ["alpha", "beta"].entries()) {
    const token = f.keys[index]!.token; const other = 1 - index; const otherProject = index ? "alpha" : "beta";
    const overview = await (await f.api("/overview", token, project)).json();
    assert.equal(overview.events.some((event: { type: string }) => ["identity.principal.created", "identity.credential.created", "identity.bootstrap"].includes(event.type)), false);
    assert.deepEqual(overview.runs.map((r: { id: string }) => r.id), [runs[index]]);
    const events = f.store.listEvents(0, 1000, project); const created = events.find(event => event.type === "run.created")!;
    assert.equal((created.data?.identityActor as { subject: string }).subject, f.principals[index]!.subject);
    assert.equal((created.data?.initiator as { application: string }).application, "fixture-client");
    const artifacts = await (await f.api(`/runs/${runs[index]}/artifacts`, token, project)).json();
    const artifactId = (Array.isArray(artifacts) ? artifacts : artifacts.artifacts)[0].id;
    assert.equal((await f.api(`/artifacts/${artifactId}/download`, token, project)).status, 200);
    assert.equal((await f.api(`/artifacts/${artifactId}/download`, f.keys[other]!.token, otherProject)).status, 404);
    for (const route of [`/runs/${runs[other]}`, `/runs/${runs[other]}/trace`, `/runs/${runs[other]}/artifacts`]) assert.equal((await f.api(route, token, project)).status, 404, route);
    for (const route of ["/overview", "/credentials", "/auth/channel-bindings", `/runs/${runs[other]}`]) assert.equal((await f.api(route, token, otherProject)).status, 403, route);
    assert.equal((await f.api(`/credentials/${credentialIds[other]}`, token, project, "DELETE")).status, 404);
    assert.equal((await f.api(`/auth/channel-bindings/${bindingIds[other]}`, token, project, "DELETE")).status, 404);
    assert.equal((await f.api("/auth/channel-bindings", token, project, "POST", { provider: "telegram", installationId: "fixture", externalUserId: "unknown", subject: f.principals[other]!.subject })).status, 403);
    const bindings = await (await f.api("/auth/channel-bindings", token, project)).json(); assert.deepEqual(bindings.bindings.map((b: { id: string }) => b.id), [bindingIds[index]]);
  }
  // Stage approval isolation uses the same project check and records a stable actor.
  const pending = await (await f.api("/runs", f.keys[0]!.token, "alpha", "POST", { name: "Approval", input: "Review", agentIds: [f.agents[0]!.id], approvalRequired: true })).json();
  const stage = (f.store.getOverview("alpha").approvals as Array<{ stageId: string }>)[0]!;
  assert.equal((await f.api(`/approvals/${stage.stageId}`, f.keys[1]!.token, "beta", "POST", { decision: "approve" })).status, 404);
  assert.equal((await f.api(`/approvals/${stage.stageId}`, f.keys[1]!.token, "alpha", "POST", { decision: "approve" })).status, 403);
  assert.equal((await f.api(`/approvals/${stage.stageId}`, f.keys[0]!.token, "alpha", "POST", { decision: "reject", reason: "Synthetic rejection" })).status, 204);
  assert.equal(f.store.getRun(pending.id, "alpha")?.status, "cancelled");
  const audit = await (await f.api("/auth/audit", f.owner.token)).json();
  assert.equal(audit.events.some((event: { type: string }) => event.type === "identity.bootstrap"), true);
}));

test("UI session endpoints enforce origin/CSRF, do not leak personal bearer, rotate/revoke and close live streams", async () => fixture(async f => {
  assert.equal((await f.api("/auth/local/login", undefined, "default", "POST", { token: f.owner.token }, { origin: "https://evil.example" })).status, 403);
  const login = await f.api("/auth/local/login", undefined, "default", "POST", { token: f.owner.token }, { origin }); assert.equal(login.status, 200);
  const cookie = login.headers.get("set-cookie")!.split(";")[0]!; const payload = await login.json(); const csrf = payload.csrfToken;
  assert.equal(JSON.stringify(payload).includes(f.owner.token), false); assert.match(login.headers.get("set-cookie")!, /Secure/); assert.equal(login.headers.get("cache-control"), "no-store");
  assert.equal((await f.api("/auth/me", undefined, "default", "GET", undefined, { cookie })).status, 200);
  assert.equal((await f.api("/auth/me", "invalid", "default", "GET", undefined, { cookie })).status, 401);
  const userInput = { username: "new-user", kind: "user", roles: ["viewer"], projectIds: ["alpha"] };
  assert.equal((await f.api("/auth/principals", undefined, "default", "POST", userInput, { cookie, origin })).status, 403);
  const created = await f.api("/auth/principals", undefined, "default", "POST", userInput, { cookie, origin, "x-agat-csrf-token": csrf }); assert.equal(created.status, 201);
  const streamAbort = new AbortController(); const stream = await fetch(`${f.base}/events`, { headers: { cookie }, signal: streamAbort.signal }); const reader = stream.body!.getReader(); await reader.read();
  const rotated = await f.api(`/auth/credentials/${f.owner.credential.id}/rotate`, undefined, "default", "POST", {}, { cookie, origin, "x-agat-csrf-token": csrf }); assert.equal(rotated.status, 201);
  const next = await rotated.json(); assert.notEqual(next.token, f.owner.token);
  assert.equal((await f.api("/auth/me", f.owner.token)).status, 401); assert.equal((await f.api("/auth/local/session", undefined, "default", "GET", undefined, { cookie })).status, 401);
  const closed = await Promise.race([(async () => { for (;;) { const item = await reader.read(); if (item.done) return true; } })(), new Promise<boolean>(resolve => setTimeout(() => resolve(false), 2000))]);
  streamAbort.abort(); assert.equal(closed, true);
  assert.equal((await f.api("/auth/me", next.token)).status, 200);
  assert.equal((await f.api(`/auth/credentials/${next.credential.id}/revoke`, next.token, "default", "POST", {})).status, 204);
  assert.equal((await f.api("/auth/me", next.token)).status, 401);
}));

test("service principals cannot enter UI, impersonate users, expand scopes or perform human approval", async () => fixture(async f => {
  const principal = f.identities.createPrincipal({ username: "service", kind: "service", roles: ["operator"], projectIds: ["alpha"] }); const service = f.issue(principal);
  assert.equal((await f.api("/auth/local/login", undefined, "alpha", "POST", { token: service.token }, { origin })).status, 403);
  const me = await (await f.api("/auth/me", service.token, "alpha", "GET", undefined, { "x-agat-subject": f.owner.principal.subject, "x-agat-username": "owner" })).json();
  assert.equal(me.subject, principal.subject); assert.equal(me.principalKind, "service");
  assert.equal((await f.api("/auth/principals", service.token, "alpha", "POST", { username: "root", kind: "user", roles: ["admin"], projectIds: ["*"] })).status, 403);
  assert.equal((await f.api("/approvals/unknown", service.token, "alpha", "POST", { decision: "approve", subject: f.owner.principal.subject })).status, 403);
  const readOnly = f.issue(f.principals[0]!, { scopes: ["read"] });
  assert.equal((await f.api("/overview", readOnly.token, "alpha")).status, 200);
  assert.equal((await f.api("/runs", readOnly.token, "alpha", "POST", { name: "Forbidden", input: "x", agentIds: [f.agents[0]!.id] })).status, 403);
  assert.equal((await f.api("/credentials", readOnly.token, "alpha", "POST", { name: "Forbidden", type: "api_key", data: { apiKey: "x" } })).status, 403);
}));

test("revocation while an authenticated request body is arriving prevents the later write", async () => fixture(async f => {
  let arrived!: () => void;
  const waiting = new Promise<void>(resolve => { arrived = resolve; });
  f.server.on("request", request => { if (request.headers["x-test-slow-body"] === "yes") setImmediate(arrived); });
  const response = new Promise<number>((resolve, reject) => {
    const request = http.request(`${f.base}/credentials`, { method: "POST", headers: { authorization: `Bearer ${f.keys[0]!.token}`, "x-agat-project-id": "alpha", "x-test-slow-body": "yes", "content-type": "application/json" } }, result => { result.resume(); resolve(result.statusCode!); });
    request.on("error", reject);
    request.write('{"name":');
    void waiting.then(() => {
      f.identities.revokeCredential(f.keys[0]!.credential.id);
      request.end('"Forbidden after revoke","type":"api_key","data":{"apiKey":"synthetic"}}');
    });
  });
  assert.equal(await response, 401);
  assert.equal(f.store.listCredentials("alpha").length, 0);
}));

test("two local users cannot satisfy critical/four-eyes assurance; rejection remains available", async () => fixture(async f => {
  const tool = await f.gateway.createServer({ name: "Critical", namespace: "critical", endpoint: "https://mcp.example/mcp", defaultPolicy: "auto", trustAnnotations: true }, "alpha"); assert.ok(tool);
  const run = await f.api("/runs", f.keys[0]!.token, "alpha", "POST", { name: "Critical", input: "Synthetic", agentIds: [f.agents[0]!.id], approvalRequired: false }); assert.equal(run.status, 201);
  const worker = f.store.registerNode({ enrollmentToken: "unused", name: "fixture", platform: "test", models: ["test-model"] }); const lease = f.store.leaseNext(worker.id); assert.ok(lease);
  const call = await f.gateway.callLeaseTool({ nodeId: worker.id, leaseId: lease.leaseId, publicName: "critical__destroy", clientCallId: "critical-1", arguments: {} }); assert.equal(call.status, "waiting_approval");
  const other = f.identities.createPrincipal({ username: "second-approver", kind: "user", roles: ["operator"], projectIds: ["alpha"] }); const second = f.issue(other);
  for (const token of [f.keys[0]!.token, second.token]) assert.equal((await f.api(`/mcp/tool-calls/${call.callId}/decision`, token, "alpha", "POST", { decision: "approve", comment: "Checked" })).status, 403);
  assert.equal(f.effects(), 0); assert.equal(f.store.db.prepare("SELECT COUNT(*) AS count FROM mcp_tool_call_approvals WHERE call_id = ?").get(call.callId)?.count, 0);
  assert.equal((await f.api(`/mcp/tool-calls/${call.callId}/decision`, second.token, "alpha", "POST", { decision: "reject", reason: "Not qualified" })).status, 200);
}));

test("switching a populated local authority to legacy or OIDC fails closed without rewriting subjects", async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-local-migrate-")); const store = new AgatStore(":memory:", { seedDemo: false });
  const provider = new LocalIdentityProvider(store.db); const owner = provider.bootstrap("owner");
  try {
    for (const mode of ["legacy", "oidc"] as const) {
      const server = createCoordinatorServer({ ...loadConfig(), authMode: mode, oidcEnabled: false, oidcIssuer: "https://id.example/realms/test", host: "127.0.0.1", port: 0, serveWeb: false, localWorkerLauncherEnabled: false }, store);
      try {
        await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve)); const address = server.address(); assert.ok(address && typeof address !== "string");
        const response = await fetch(`http://127.0.0.1:${address.port}/api/v1/auth/me`); assert.equal(response.status, 503); assert.equal((await response.json()).code, "identity_migration_required");
        assert.equal(provider.listPrincipals()[0]!.subject, owner.principal.subject);
      } finally { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); }
    }
  } finally { store.close(); fs.rmSync(directory, { recursive: true, force: true }); }
});

test("local preflight and API keep unqualified endpoint/schedule identities blocked", async () => fixture(async f => {
  const process = f.store.createProcess({ name: "Manual only", graph: {
    nodes: [{ id: "start", name: "Start", type: "start", position: { x: 0, y: 0 }, config: {} }, { id: "end", name: "End", type: "end", position: { x: 200, y: 0 }, config: {} }],
    edges: [{ id: "one", source: "start", target: "end", branch: "default" }],
  } }, "alpha"); f.store.publishProcess(String(process.id), "alpha");
  for (const [trigger, code] of [[{ kind: "schedule" }, "deployment:schedule_identity_unqualified"], [{ kind: "webhook", webhookId: "unknown" }, "deployment:service_binding_unqualified"]] as const) {
    const response = await f.api(`/processes/${process.id}/preflight`, f.keys[0]!.token, "alpha", "POST", { trigger }); assert.equal(response.status, 200);
    const preflight = await response.json(); assert.equal(preflight.queueable, false); assert.ok(preflight.blockers.some((issue: { code: string }) => issue.code === code));
  }
  assert.equal((await f.api(`/processes/${process.id}/schedule`, f.keys[0]!.token, "alpha", "PUT", {})).status, 503);
  assert.equal((await f.api("/process-webhooks/unknown", "unknown-endpoint-token", "alpha", "POST", {})).status, 503);
  const response = await fetch(f.base.replace("/api/v1", "/a2a/unknown"), { method: "POST", body: "{}" }); assert.equal(response.status, 503);
  const started = await f.api(`/processes/${process.id}/start`, f.keys[0]!.token, "alpha", "POST", { input: "Manual synthetic" }); assert.equal(started.status, 201);
}));
