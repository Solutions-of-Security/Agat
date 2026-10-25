import assert from "node:assert/strict";
import { randomUUID, createHash, createSign, generateKeyPairSync } from "node:crypto";
import http from "node:http";
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { AgatStore } from "../src/database.js";
import { loadConfig } from "../src/config.js";
import { createCoordinatorServer } from "../src/server.js";
import { BusinessReportDecisionError, normalizeBusinessReportDecision, normalizeDecisionHistoryCursor } from "../src/business-report-decisions.js";
import { completedOfficeReport } from "./business-report-office-fixtures.js";

function fixture() {
  const directory = mkdtempSync(path.join(os.tmpdir(), "agat-report-decision-"));
  const store = new AgatStore(":memory:", { seedDemo: false, artifactsDir: directory });
  const report = completedOfficeReport(store);
  const body = { requestId: randomUUID(), expectedSha256: String(report.canonical.sha256), expectedRevision: 0,
    decision: "rework_requested", reason: "Проверьте выбранный период и объясните ограничение расчёта." };
  return { ...report, store, body, cleanup: () => { store.close(); rmSync(directory, { recursive: true, force: true }); } };
}

test("decisions append exact frozen bindings, one retry-safe manual task, and metadata-only audit without changing execution", () => {
  const f = fixture();
  try {
    const original = readFileSync(f.store.getArtifactDownload(String(f.canonical.id))!.filePath);
    const runBefore = f.store.getRun(f.runId), runCount = Number(f.store.db.prepare("SELECT COUNT(*) AS count FROM runs").get()!.count);
    const first = f.store.recordBusinessReportDecision(String(f.canonical.id), f.body, "default", "reviewer-a")!;
    assert.equal(first.created, true); assert.equal(first.decision.revision, 1);
    assert.ok(first.reworkTask); assert.equal(first.reworkTask.execution, "manual"); assert.equal(first.reworkTask.state, "open");
    for (const table of ["business_report_decisions", "business_report_rework_tasks"]) {
      assert.throws(() => f.store.db.prepare(`UPDATE ${table} SET created_at = created_at`).run(), /append-only/);
      assert.throws(() => f.store.db.prepare(`DELETE FROM ${table}`).run(), /append-only/);
    }
    assert.equal(first.reworkTask.reason, f.body.reason); assert.equal(first.reworkTask.artifactId, f.canonical.id);
    assert.equal(first.decision.binding.sourceSha256, f.canonical.sha256);
    const instance = f.store.db.prepare("SELECT id, process_id, process_version, graph_json FROM process_instances WHERE run_id = ?").get(f.runId)!;
    assert.equal(first.decision.binding.processInstanceId, instance.id);
    assert.equal(first.decision.binding.processVersion, Number(instance.process_version));
    assert.equal(first.decision.binding.graphSha256, createHash("sha256").update(String(instance.graph_json)).digest("hex"));
    const retry = f.store.recordBusinessReportDecision(String(f.canonical.id), f.body, "default", "reviewer-a")!;
    assert.equal(retry.created, false); assert.deepEqual(retry.decision, first.decision); assert.deepEqual(retry.reworkTask, first.reworkTask);
    const accepted = f.store.recordBusinessReportDecision(String(f.canonical.id), { ...f.body, requestId: randomUUID(), expectedRevision: 1, decision: "accepted", reason: "Проверено для этого задания." }, "default", "reviewer-b")!;
    assert.equal(accepted.decision.revision, 2); assert.equal(accepted.reworkTask, null);
    assert.equal(f.store.getBusinessReportReworkTask(first.reworkTask.id)!.state, "superseded");
    const history = f.store.getBusinessReportDecisions(String(f.canonical.id))!;
    assert.equal(history.revision, 2); assert.deepEqual(history.decisions.map(row => row.id), [accepted.decision.id, first.decision.id]);
    assert.equal(history.decisions[1]!.reason, f.body.reason, "later acceptance must preserve the original request");
    const oldRetry = f.store.recordBusinessReportDecision(String(f.canonical.id), f.body, "default", "reviewer-a")!;
    assert.equal(oldRetry.decision.id, first.decision.id); assert.equal(oldRetry.reworkTask!.id, first.reworkTask.id);
    assert.equal(oldRetry.reworkTask!.state, "superseded");
    assert.equal(Number(f.store.db.prepare("SELECT COUNT(*) AS count FROM business_report_rework_tasks").get()!.count), 1);
    assert.equal(Number(f.store.db.prepare("SELECT COUNT(*) AS count FROM runs").get()!.count), runCount);
    assert.deepEqual(f.store.getRun(f.runId), runBefore, "report acceptance must not alter execution or tool approval");
    assert.deepEqual(readFileSync(f.store.getArtifactDownload(String(f.canonical.id))!.filePath), original);
    const events = f.store.listEvents(0, 1000).filter(event => event.type === "business.report.decision.recorded");
    assert.equal(events.length, 2);
    for (const event of events) {
      assert.ok(!JSON.stringify(event).includes(f.body.reason));
      assert.ok(!JSON.stringify(event).includes("Технический пример"));
      assert.ok(!JSON.stringify(event).includes("https://"));
    }
  } finally { f.cleanup(); }
});

test("stale revisions, reused keys with changed intent, foreign projects and corrupt source bytes cannot record decisions", () => {
  const f = fixture();
  try {
    const artifactId = String(f.canonical.id);
    const first = f.store.recordBusinessReportDecision(artifactId, f.body)!;
    for (const [body, code] of [
      [{ ...f.body, reason: "Другое намерение" }, "idempotency_conflict"],
      [{ ...f.body, requestId: randomUUID() }, "decision_changed"],
      [{ ...f.body, requestId: randomUUID(), expectedSha256: "0".repeat(64) }, "source_changed"],
    ] as const) assert.throws(() => f.store.recordBusinessReportDecision(artifactId, body), (error: unknown) => error instanceof BusinessReportDecisionError && error.code === code);
    f.store.createProject({ id: "foreign", name: "Foreign" });
    assert.equal(f.store.recordBusinessReportDecision(artifactId, f.body, "foreign"), null);
    assert.equal(f.store.getBusinessReportDecisions(artifactId, "foreign"), null);
    assert.equal(f.store.getBusinessReportReworkTask(first.reworkTask!.id, "foreign"), null);
    const source = f.store.getArtifactDownload(artifactId)!;
    writeFileSync(source.filePath, "corrupt source");
    assert.throws(() => f.store.recordBusinessReportDecision(artifactId, { ...f.body, requestId: randomUUID(), expectedRevision: 1 }),
      (error: unknown) => error instanceof BusinessReportDecisionError && error.code === "source_unavailable" && error.status === 503);
    assert.equal(f.store.getBusinessReportDecisions(artifactId)!.revision, 1);
  } finally { f.cleanup(); }
});

test("history pagination retains every revision and rejects unsupported or ambiguous decision payloads", () => {
  const f = fixture();
  try {
    assert.deepEqual(f.store.getBusinessReportDecisions(String(f.canonical.id))!.decisions, []);
    for (let revision = 0; revision < 52; revision++) f.store.recordBusinessReportDecision(String(f.canonical.id), {
      ...f.body, requestId: randomUUID(), expectedRevision: revision, decision: "accepted", reason: "" });
    const first = f.store.getBusinessReportDecisions(String(f.canonical.id))!;
    assert.equal(first.decisions.length, 50); assert.equal(first.nextBeforeRevision, 3);
    const last = f.store.getBusinessReportDecisions(String(f.canonical.id), "default", first.nextBeforeRevision)!;
    assert.deepEqual(last.decisions.map(row => row.revision), [2, 1]); assert.equal(last.nextBeforeRevision, null); assert.equal(last.revision, 52);
    const empty = f.store.getBusinessReportDecisions(String(f.canonical.id), "default", 1)!;
    assert.equal(empty.revision, 52); assert.deepEqual(empty.decisions, []); assert.equal(empty.nextBeforeRevision, null);
    for (const body of [null, [], { ...f.body, reason: " " }, { ...f.body, expectedRevision: -1 }, { ...f.body, expectedRevision: 1001 },
      { ...f.body, decision: "approve" }, { ...f.body, requestId: "invented" }, { ...f.body, actor: "admin" }, { ...f.body, reason: "x".repeat(4001) }]) {
      assert.throws(() => normalizeBusinessReportDecision(body), BusinessReportDecisionError);
    }
    for (const cursor of ["0", "1e2", "1002", "bad", 10]) assert.throws(() => normalizeDecisionHistoryCursor(cursor), BusinessReportDecisionError);
    assert.equal(normalizeDecisionHistoryCursor("51"), 51);
  } finally { f.cleanup(); }
});

test("file retention preserves the decision/task history while refusing new decisions for a deleted source", () => {
  const f = fixture();
  try {
    const saved = f.store.recordBusinessReportDecision(String(f.canonical.id), f.body)!;
    f.store.db.prepare("UPDATE artifacts SET storage_state = 'deleted', deleted_at = ? WHERE id = ?").run(new Date().toISOString(), String(f.canonical.id));
    const history = f.store.getBusinessReportDecisions(String(f.canonical.id))!;
    assert.equal(history.revision, 1); assert.equal(history.sourceReady, false); assert.equal(history.decisions[0]!.reason, f.body.reason);
    assert.equal(f.store.getBusinessReportReworkTask(saved.reworkTask!.id)!.sourceAvailable, false);
    assert.throws(() => f.store.recordBusinessReportDecision(String(f.canonical.id), { ...f.body, requestId: randomUUID(), expectedRevision: 1 }), /сохранённого проверенного отчёта/);
    f.store.db.prepare("DELETE FROM runs WHERE id = ?").run(f.runId);
    assert.equal(f.store.getBusinessReportDecisions(String(f.canonical.id)), null);
    assert.equal(f.store.getBusinessReportReworkTask(saved.reworkTask!.id), null);
  } finally { f.cleanup(); }
});

test("schema 34 layout upgrades without changing report bytes and decision retries survive a store restart", () => {
  const directory = mkdtempSync(path.join(os.tmpdir(), "agat-decision-upgrade-"));
  const databasePath = path.join(directory, "state.sqlite"), options = { seedDemo: false, artifactsDir: path.join(directory, "artifacts") };
  let store = new AgatStore(databasePath, options);
  try {
    const report = completedOfficeReport(store), artifactId = String(report.canonical.id);
    const original = readFileSync(store.getArtifactDownload(artifactId)!.filePath);
    // Reconstruct the preceding release's layout, retaining its real report,
    // process snapshot and rendering receipt; no decisions existed in v34.
    store.db.exec("DROP TABLE business_report_rework_tasks; DROP TABLE business_report_decisions; PRAGMA user_version = 34;");
    store.close(); store = new AgatStore(databasePath, options);
    assert.equal(Number(store.db.prepare("PRAGMA user_version").get()!.user_version), 35);
    assert.deepEqual(readFileSync(store.getArtifactDownload(artifactId)!.filePath), original);
    const body = { requestId: randomUUID(), expectedSha256: report.canonical.sha256, expectedRevision: 0, decision: "rework_requested", reason: "Persist this correction request" };
    const recorded = store.recordBusinessReportDecision(artifactId, body, "default", "persisted-operator")!;
    store.close(); store = new AgatStore(databasePath, options);
    const repeated = store.recordBusinessReportDecision(artifactId, body, "default", "persisted-operator")!;
    assert.equal(repeated.created, false); assert.deepEqual(repeated.decision, recorded.decision); assert.deepEqual(repeated.reworkTask, recorded.reworkTask);
    assert.equal(store.getBusinessReportDecisions(artifactId)!.revision, 1);
    assert.throws(() => store.db.prepare("UPDATE business_report_decisions SET reason = 'overwrite'").run(), /append-only/);
  } finally { store.close(); rmSync(directory, { recursive: true, force: true }); }
});

test("HTTP decisions authenticate, scope history/tasks, return one receipt on retry and contain literal feedback", async () => {
  const f = fixture(), token = "report-decision-http-fixture";
  const server = createCoordinatorServer({ ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false, adminToken: token,
    oidcEnabled: false, mcpEnabled: false, a2aEnabled: false, sandboxEnabled: false, localWorkerLauncherEnabled: false }, f.store);
  try {
    await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
    const address = server.address(); assert.ok(address && typeof address === "object");
    const origin = `http://127.0.0.1:${address.port}`, url = `${origin}/api/v1/artifacts/${f.canonical.id}/decisions`;
    const headers = { "x-agat-admin-token": token, "content-type": "application/json" };
    const body = { ...f.body, reason: "<script>alert(1)</script> — проверьте период" };
    const post = (data = body, extra = {}) => fetch(url, { method: "POST", headers: { ...headers, ...extra }, body: JSON.stringify(data) });
    assert.equal((await post(body, { "x-agat-admin-token": "wrong" })).status, 401);
    const first = await post(); assert.equal(first.status, 201, await first.clone().text());
    const receipt = await first.json() as { reworkTask: { id: string; reason: string } };
    assert.equal(receipt.reworkTask.reason, body.reason);
    assert.equal((await post()).status, 200);
    const history = await fetch(url, { headers }); assert.equal(history.status, 200);
    assert.equal((await history.json() as { revision: number }).revision, 1);
    assert.equal((await fetch(`${url}?beforeRevision=0`, { headers })).status, 400);
    const taskUrl = `${origin}/api/v1/report-rework-tasks/${receipt.reworkTask.id}`;
    assert.equal((await fetch(taskUrl, { headers })).status, 200);
    f.store.createProject({ id: "foreign", name: "Foreign" });
    const foreign = { "x-agat-project-id": "foreign" };
    assert.equal((await post(body, foreign)).status, 404);
    assert.equal((await fetch(url, { headers: { ...headers, ...foreign } })).status, 404);
    assert.equal((await fetch(taskUrl, { headers: { ...headers, ...foreign } })).status, 404);
  } finally { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); f.cleanup(); }
});

test("OIDC viewers can read; only operator/admin can record business decisions, with actor bound to the verified subject", async () => {
  const f = fixture(), keys = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const jwk = { ...keys.publicKey.export({ format: "jwk" }), kid: "report-test", alg: "RS256", use: "sig" };
  const identity = http.createServer((_request, response) => { response.setHeader("content-type", "application/json"); response.end(JSON.stringify({ keys: [jwk] })); });
  let server: ReturnType<typeof createCoordinatorServer> | undefined;
  try {
    await new Promise<void>(resolve => identity.listen(0, "127.0.0.1", resolve));
    const identityAddress = identity.address(); assert.ok(identityAddress && typeof identityAddress === "object");
    const issuer = `http://127.0.0.1:${identityAddress.port}`;
    server = createCoordinatorServer({ ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false, oidcEnabled: true,
      oidcIssuer: issuer, oidcJwksUrl: `${issuer}/jwks`, oidcClientId: "agat-web", mcpEnabled: false, a2aEnabled: false,
      sandboxEnabled: false, localWorkerLauncherEnabled: false }, f.store);
    await new Promise<void>(resolve => server!.listen(0, "127.0.0.1", resolve));
    const address = server.address(); assert.ok(address && typeof address === "object");
    const url = `http://127.0.0.1:${address.port}/api/v1/artifacts/${f.canonical.id}/decisions`;
    const token = (role: string) => {
      const header = Buffer.from(JSON.stringify({ alg: "RS256", kid: "report-test" })).toString("base64url");
      const payload = Buffer.from(JSON.stringify({ iss: issuer, aud: "agat-web", sub: `subject-${role}`, preferred_username: role,
        exp: Math.floor(Date.now() / 1000) + 300, realm_access: { roles: [role] }, agat_projects: ["default"] })).toString("base64url");
      const signer = createSign("RSA-SHA256"); signer.update(`${header}.${payload}`); signer.end();
      return `${header}.${payload}.${signer.sign(keys.privateKey).toString("base64url")}`;
    };
    for (const role of ["viewer", "auditor", "designer"]) {
      const headers = { authorization: `Bearer ${token(role)}`, "content-type": "application/json" };
      assert.equal((await fetch(url, { headers })).status, 200);
      assert.equal((await fetch(url, { method: "POST", headers, body: JSON.stringify(f.body) })).status, 403);
    }
    const response = await fetch(url, { method: "POST", headers: { authorization: `Bearer ${token("operator")}`, "content-type": "application/json" }, body: JSON.stringify(f.body) });
    assert.equal(response.status, 201, await response.clone().text());
    assert.equal((await response.json() as { decision: { actor: string } }).decision.actor, "subject-operator");
  } finally {
    if (server) { server.closeAllConnections(); await new Promise<void>(resolve => server!.close(() => resolve())); }
    identity.closeAllConnections(); await new Promise<void>(resolve => identity.close(() => resolve())); f.cleanup();
  }
});
