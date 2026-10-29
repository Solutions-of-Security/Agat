import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { IncomingMessage } from "node:http";
import { Socket } from "node:net";
import test from "node:test";
import { AgatStore } from "../src/database.js";
import { LocalIdentityProvider } from "../src/local-identities.js";
import { localAuthCommand } from "../src/local-auth-cli.js";
import { loadConfig } from "../src/config.js";
import { selectProject } from "../src/auth.js";
import { scopesForRoles, type IssueLocalCredential, type LocalPrincipal } from "../src/local-identity-contract.js";
import { enterLocalIdentity, withLocalIdentityScope } from "../src/local-identity-context.js";
import { McpGateway, normalizeCatalogTools, type McpUpstreamClient } from "../src/mcp.js";
import { CoordinatorTelemetry } from "../src/telemetry.js";
import type { ProcessGraph, ProcessGraphNode } from "../src/types.js";
import { migrateSqliteToPostgres } from "../src/sqlite-postgres-migrator.js";

function input(principal: LocalPrincipal, now = Date.now(), overrides: Partial<IssueLocalCredential> = {}): IssueLocalCredential {
  return { label: "Test key", purpose: principal.kind === "user" ? "personal" : "api", application: "fixture-app", roles: principal.roles,
    projectIds: principal.projectIds, scopes: scopesForRoles(principal.roles).filter(scope => principal.kind === "user" || !["approve", "review"].includes(scope)),
    expiresAt: new Date(now + 3600000).toISOString(), ...overrides };
}
function request(headers: Record<string, string>, method = "GET"): IncomingMessage {
  const result = new IncomingMessage(new Socket()); result.headers = headers; result.method = method; result.url = "/api/v1/overview"; return result;
}

test("offline bootstrap is explicit and one-time; only hashes persist, recovery retains the original subject", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-local-cli-"));
  const config = { ...loadConfig(), authMode: "local" as const, stateStoreDriver: "sqlite" as const, dbPath: path.join(directory, "authority.db"), artifactsDir: path.join(directory, "artifacts") };
  try {
    assert.throws(() => localAuthCommand(["bootstrap", "--username", "owner"], config), /Остановите/);
    assert.equal(fs.existsSync(config.dbPath), false);
    assert.throws(() => localAuthCommand(["bootstrap", "--confirm-offline", "COORDINATOR_STOPPED"], config), /явный username/);
    const first = localAuthCommand(["bootstrap", "--username", "owner", "--confirm-offline", "COORDINATOR_STOPPED"], config) as { principal: LocalPrincipal; token: string };
    assert.notEqual(first.principal.subject, "local-admin");
    assert.throws(() => localAuthCommand(["bootstrap", "--username", "second", "--confirm-offline", "COORDINATOR_STOPPED"], config), /уже выполнен/);
    const recovered = localAuthCommand(["recover", "--subject", first.principal.subject, "--confirm-offline", "COORDINATOR_STOPPED"], config) as { token: string };
    const store = new AgatStore(config.dbPath, { seedDemo: false });
    try {
      const provider = new LocalIdentityProvider(store.db);
      assert.throws(() => provider.verify(first.token));
      assert.equal(provider.verify(recovered.token).subject, first.principal.subject);
      assert.equal(provider.listPrincipals().length, 1);
      const credentials = JSON.stringify(store.db.prepare("SELECT * FROM local_identity_credentials").all());
      assert.equal(credentials.includes(first.token), false); assert.equal(credentials.includes(recovered.token), false);
      assert.equal(JSON.stringify(store.db.prepare("SELECT data_json FROM events").all()).includes(recovered.token), false);
      assert.throws(() => provider.updatePrincipal(first.principal.subject, { disabled: true }), /последнего/);
    } finally { store.close(); }
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});

test("credential grants cannot exceed the principal; project-scoped admin has no global bypass; current grants are intersected", () => {
  const store = new AgatStore(":memory:", { seedDemo: false });
  try {
    store.createProject({ id: "alpha", name: "Alpha" }); store.createProject({ id: "beta", name: "Beta" });
    const provider = new LocalIdentityProvider(store.db); provider.bootstrap("owner");
    const principal = provider.createPrincipal({ username: "alice", kind: "user", roles: ["admin"], projectIds: ["alpha"] });
    const key = provider.issueCredential(principal.subject, input(principal));
    const context = provider.verify(key.token);
    assert.equal(selectProject(context, "alpha"), "alpha"); assert.throws(() => selectProject(context, "beta"));
    assert.throws(() => provider.issueCredential(principal.subject, input(principal, Date.now(), { projectIds: ["*"] })), /расширять/);
    assert.throws(() => provider.issueCredential(principal.subject, input(principal, Date.now(), { expiresAt: new Date(Date.now() + 91 * 86400000).toISOString() })), /90 дней/);
    const updated = provider.updatePrincipal(principal.subject, { roles: ["viewer"] });
    assert.throws(() => provider.verify(key.token));
    const reduced = provider.verify(provider.issueCredential(principal.subject, input(updated)).token);
    assert.deepEqual([...reduced.roles], ["viewer"]); assert.deepEqual([...reduced.scopes!], ["read"]);
    assert.throws(() => provider.authorizeOperation(request({}, "POST"), reduced, ["operator"]));
    assert.throws(() => provider.createPrincipal({ username: "root-service", kind: "service", roles: ["admin"], projectIds: ["*"] }), /Service/);
    const service = provider.createPrincipal({ username: "robot", kind: "service", roles: ["operator"], projectIds: ["alpha"] });
    assert.throws(() => provider.issueCredential(service.subject, input(service, Date.now(), { scopes: ["approve"] })), /human/);
    assert.throws(() => provider.issueCredential(service.subject, input(service, Date.now(), { purpose: "personal" })), /Персональный/);
  } finally { store.close(); }
});

test("cookie sessions survive a database reopen, have bounded TTL, CSRF, strict origin and no credential fallback", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-local-session-")); const dbPath = path.join(directory, "state.db");
  let store = new AgatStore(dbPath, { seedDemo: false }); let now = Date.now();
  try {
    let provider = new LocalIdentityProvider(store.db, "https://agat.example", undefined, () => now);
    const owner = provider.bootstrap("owner");
    const login = provider.login(request({ origin: "https://agat.example" }, "POST"), owner.token);
    assert.match(login.cookie, /^__Host-agat-session=/); assert.match(login.cookie, /HttpOnly; SameSite=Strict/); assert.match(login.cookie, /; Secure$/);
    const cookie = login.cookie.split(";")[0]!;
    assert.equal(provider.authenticate(request({ cookie })).subject, owner.principal.subject);
    assert.throws(() => provider.authenticate(request({ cookie, authorization: "Bearer invalid" })));
    assert.throws(() => provider.authenticate(request({ cookie, "x-agat-admin-token": "invalid" })));
    assert.throws(() => provider.authenticate(request({ cookie, origin: "https://agat.example" }, "POST")), /CSRF/);
    assert.throws(() => provider.authenticate(request({ cookie, origin: "https://evil.example", "x-agat-csrf-token": login.csrfToken }, "POST")), /Origin/);
    assert.throws(() => provider.session(request({ cookie: `${cookie}; ${cookie}` })));
    store.close();
    const backupPath = path.join(directory, "restored.db"); fs.copyFileSync(dbPath, backupPath);
    store = new AgatStore(backupPath, { seedDemo: false });
    provider = new LocalIdentityProvider(store.db, "https://agat.example", undefined, () => now);
    assert.equal(provider.session(request({ cookie })).csrfToken, login.csrfToken);
    assert.equal(provider.authenticate(request({ cookie, origin: "https://agat.example", "x-agat-csrf-token": login.csrfToken }, "POST")).subject, owner.principal.subject);
    provider.logout(request({ cookie, origin: "https://agat.example", "x-agat-csrf-token": login.csrfToken }, "POST"));
    assert.throws(() => provider.session(request({ cookie })));
    const second = provider.login(request({ origin: "https://agat.example" }, "POST"), owner.token);
    now += 8 * 3600000 + 1; assert.throws(() => provider.session(request({ cookie: second.cookie.split(";")[0]! })));
    assert.throws(() => new LocalIdentityProvider(store.db, "http://external.example"), /HTTPS/);
    assert.throws(() => new LocalIdentityProvider(store.db, "https://agat.example/path"), /origin/);
  } finally { store.close(); fs.rmSync(directory, { recursive: true, force: true }); }
});

test("offline PostgreSQL migration refuses local authority before connecting or copying subjects", async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-local-pg-migration-")); const sourcePath = path.join(directory, "source.db");
  const store = new AgatStore(sourcePath, { seedDemo: false });
  new LocalIdentityProvider(store.db).bootstrap("owner"); store.close();
  try {
    await assert.rejects(() => migrateSqliteToPostgres({ sourcePath, artifactsDir: path.join(directory, "artifacts"), reportPath: path.join(directory, "report.json"), mode: "apply",
      targetUrl: "postgresql://migration:synthetic@127.0.0.1:1/test", targetRuntimeUrl: "postgresql://runtime:synthetic@127.0.0.1:1/test", targetTenantUrl: "postgresql://tenant:synthetic@127.0.0.1:1/test",
      sslMode: "disable", batchRows: 10, offlineConfirmation: "SOURCE_AND_WRITERS_STOPPED" }), /Local identity SQLite schema/);
    assert.equal(fs.existsSync(path.join(directory, "report.json")), false);
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});

test("channel IDs are explicit tuple bindings; unknown, disabled, changed-project or service identities never resolve", () => {
  const store = new AgatStore(":memory:", { seedDemo: false });
  try {
    store.createProject({ id: "alpha", name: "Alpha" }); store.createProject({ id: "beta", name: "Beta" });
    const provider = new LocalIdentityProvider(store.db); provider.bootstrap("owner");
    const principal = provider.createPrincipal({ username: "alice", kind: "user", roles: ["operator"], projectIds: ["alpha"] });
    assert.equal(provider.resolveBinding("telegram", "install", "42", "alpha"), null);
    const binding = provider.createBinding({ provider: "telegram", installationId: "install", externalUserId: "42", projectId: "alpha", subject: principal.subject });
    assert.equal(provider.resolveBinding("telegram", "install", "42", "alpha"), principal.subject);
    for (const [providerName, installation, external, project] of [["other", "install", "42", "alpha"], ["telegram", "other", "42", "alpha"], ["telegram", "install", "43", "alpha"], ["telegram", "install", "42", "beta"]]) assert.equal(provider.resolveBinding(providerName!, installation!, external!, project!), null);
    assert.equal(provider.deleteBinding(binding.id, "beta"), false);
    provider.updatePrincipal(principal.subject, { projectIds: ["beta"] }); assert.equal(provider.resolveBinding("telegram", "install", "42", "alpha"), null);
    provider.updatePrincipal(principal.subject, { projectIds: ["alpha"], disabled: true }); assert.equal(provider.resolveBinding("telegram", "install", "42", "alpha"), null);
  } finally { store.close(); }
});

for (const change of ["revoke", "expiry", "permissions"] as const) test(`${change} is checked at final upstream admission; no tool effect occurs and no lease is renewed`, async () => {
  const store = new AgatStore(":memory:", { seedDemo: false }); let now = Date.now(); let effects = 0;
  const provider = new LocalIdentityProvider(store.db, undefined, undefined, () => now);
  const owner = provider.bootstrap("owner"); store.setRunIdentityGuard(provider);
  let beforeCall = () => {};
  const upstream: McpUpstreamClient = {
    async listTools(server) { return { tools: normalizeCatalogTools(server.namespace, [{ name: "read", description: "Read", inputSchema: { type: "object" }, annotations: { readOnlyHint: true } }]), ttlMs: 60000, cacheScope: "private" }; },
    async callTool(_server, _tool, _arguments, assertAllowed) { beforeCall(); assertAllowed?.(); effects++; return { resultType: "complete", content: [{ type: "text", text: "done" }] }; },
  };
  const gateway = new McpGateway(store, new CoordinatorTelemetry({ enabled: false, serviceName: "test", exporterEndpoint: "" }), { enabled: true, requestTimeoutSeconds: 5, maxResponseBytes: 100000, approvalTtlSeconds: 300 }, upstream);
  try {
    const worker = store.registerNode({ enrollmentToken: "unused", name: "fixture", platform: "test", models: ["test-model"] });
    await gateway.createServer({ name: "Test MCP", namespace: "test", endpoint: "https://mcp.example/mcp", defaultPolicy: "auto", trustAnnotations: true }, "default");
    const run = withLocalIdentityScope(() => { enterLocalIdentity(provider.verify(owner.token)); return store.createRun({ name: "Bound", input: "Read", agentIds: ["collector"], approvalRequired: false }); });
    const lease = store.leaseNext(worker.id); assert.ok(lease);
    beforeCall = () => {
      if (change === "revoke") provider.revokeCredential(owner.credential.id);
      else if (change === "expiry") now += 31 * 86400000;
      else { const other = provider.createPrincipal({ username: "second-admin", kind: "user", roles: ["admin"], projectIds: ["*"] }); assert.ok(other); provider.updatePrincipal(owner.principal.subject, { roles: ["viewer"], projectIds: ["default"] }); }
    };
    const response = await gateway.callLeaseTool({ nodeId: worker.id, leaseId: lease.leaseId, publicName: "test__read", arguments: {}, clientCallId: "first" });
    assert.notEqual(response.status, "completed"); assert.equal(effects, 0);
    assert.equal(store.renewLease(worker.id, lease.leaseId), false); assert.equal(store.getRun(run.id)?.status, "cancelled");
    assert.throws(() => store.completeLease(worker.id, lease.leaseId, "FORGED RESULT"));
  } finally { store.close(); }
});

test("additive identity migration preserves historical run IDs and local-admin audit; newer schemas fail closed", () => {
  const store = new AgatStore(":memory:", { seedDemo: false });
  try {
    const historical = store.createRun({ name: "Old", input: "Old input", agentIds: ["collector"], approvalRequired: false });
    store.recordPlatformEvent("historical.identity", "Historical", { actorSubject: "local-admin" });
    const before = store.db.prepare("SELECT * FROM runs WHERE id = ?").get(historical.id);
    const provider = new LocalIdentityProvider(store.db); provider.bootstrap("owner");
    assert.deepEqual(store.db.prepare("SELECT * FROM runs WHERE id = ?").get(historical.id), before);
    assert.equal(store.listEvents(0, 1000).find(event => event.type === "historical.identity")?.data?.actorSubject, "local-admin");
    store.setRunIdentityGuard(provider); store.enforceRunIdentities(); assert.equal(store.getRun(historical.id)?.status, "cancelled");
    store.db.prepare("UPDATE local_identity_schema SET version = 999").run();
    assert.throws(() => new LocalIdentityProvider(store.db), /Неподдерживаемая/);
  } finally { store.close(); }
});

const node = (id: string, type: ProcessGraphNode["type"], config: ProcessGraphNode["config"] = {}): ProcessGraphNode => ({ id, type, name: id, position: { x: 0, y: 0 }, config });
const linear = (nodes: ProcessGraphNode[]): ProcessGraph => ({ nodes, edges: nodes.slice(1).map((next, index) => ({ id: `edge-${index}`, source: nodes[index]!.id, target: next.id, branch: "default" })) });
test("a subprocess inherits its initiating credential, even when a different local operator approves its start", () => {
  const store = new AgatStore(":memory:", { seedDemo: false });
  try {
    const identities = new LocalIdentityProvider(store.db); const owner = identities.bootstrap("owner");
    const approver = identities.createPrincipal({ username: "approver", kind: "user", roles: ["operator"], projectIds: ["default"] }); const key = identities.issueCredential(approver.subject, input(approver));
    const child = store.createProcess({ name: "Child", graph: linear([node("start", "start"), node("wait", "approval"), node("end", "end")]) }); store.publishProcess(String(child.id));
    const parent = store.createProcess({ name: "Parent", graph: linear([node("start", "start"), node("approve", "approval"), node("child", "subprocess", { subprocessProcessId: String(child.id), subprocessInputTemplate: "{{ input }}" }), node("end", "end")]) }); store.publishProcess(String(parent.id));
    store.setRunIdentityGuard(identities);
    const started = withLocalIdentityScope(() => { enterLocalIdentity(identities.verify(owner.token)); return store.startProcess(String(parent.id), { input: "Synthetic" })!; });
    const approval = (store.getOverview().approvals as Array<{ stageId: string }>)[0]!;
    withLocalIdentityScope(() => { enterLocalIdentity(identities.verify(key.token)); store.decideApproval(approval.stageId, true); });
    const bindings = store.db.prepare("SELECT * FROM local_run_identities").all(); assert.equal(bindings.length, 2);
    assert.ok(bindings.every(binding => binding.credential_id === owner.credential.id));
    identities.revokeCredential(owner.credential.id); store.enforceRunIdentities();
    assert.equal(store.getRun(String(started.runId))?.status, "cancelled");
    assert.ok(store.db.prepare("SELECT status FROM runs").all().every(run => run.status === "cancelled"));
  } finally { store.close(); }
});

test("revocation stops an armed compensation without creating a new side effect", () => {
  const store = new AgatStore(":memory:", { seedDemo: false });
  try {
    const identities = new LocalIdentityProvider(store.db); const owner = identities.bootstrap("owner");
    const process = store.createProcess({ name: "Compensation", graph: linear([node("start", "start"), node("write", "http", {
      method: "POST", url: "https://example.test/orders", headers: {}, body: "{}", timeoutSeconds: 10,
      compensation: { method: "POST", url: "https://example.test/orders/undo", headers: {}, body: "{}", timeoutSeconds: 10 },
    }), node("wait", "approval"), node("end", "end")]) }); store.publishProcess(String(process.id));
    store.setRunIdentityGuard(identities);
    const started = withLocalIdentityScope(() => { enterLocalIdentity(identities.verify(owner.token)); return store.startProcess(String(process.id), { input: "Synthetic" })!; });
    const worker = store.registerNode({ enrollmentToken: "unused", name: "fixture", platform: "test", models: [] }); const lease = store.leaseNext(worker.id); assert.ok(lease);
    store.completeLease(worker.id, lease.leaseId, '{"synthetic":true}');
    assert.equal(store.db.prepare("SELECT status FROM process_compensations").get()?.status, "armed");
    identities.revokeCredential(owner.credential.id); store.enforceRunIdentities();
    assert.equal(store.getRun(String(started.runId))?.status, "cancelled");
    assert.equal(store.db.prepare("SELECT status FROM process_compensations").get()?.status, "cancelled");
    assert.equal(store.db.prepare("SELECT COUNT(*) AS count FROM stages WHERE stage_kind = 'compensation'").get()?.count, 0);
    assert.equal(store.leaseNext(worker.id), null);
  } finally { store.close(); }
});
