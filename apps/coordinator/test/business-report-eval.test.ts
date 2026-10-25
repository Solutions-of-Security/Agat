import assert from "node:assert/strict";
import { createHash, createSign, generateKeyPairSync, randomUUID } from "node:crypto";
import http from "node:http";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { AgatStore } from "../src/database.js";
import { loadConfig } from "../src/config.js";
import { createCoordinatorServer } from "../src/server.js";
import { BusinessReportEvalError, normalizeReportEvalRequest } from "../src/business-report-eval.js";
import { reportEvalDatasetId } from "../src/business-report-eval-import.js";
import { completedOfficeReport } from "./business-report-office-fixtures.js";

const sha = (value: string) => createHash("sha256").update(value).digest("hex");
function fixture() {
  const directory = mkdtempSync(path.join(os.tmpdir(), "agat-report-eval-"));
  const store = new AgatStore(":memory:", { seedDemo: false, artifactsDir: directory });
  const report = completedOfficeReport(store);
  const artifactId = String(report.canonical.id), sourceSha256 = String(report.canonical.sha256);
  const decision = store.recordBusinessReportDecision(artifactId, { requestId: randomUUID(), expectedSha256: sourceSha256,
    expectedRevision: 0, decision: "accepted", reason: "Synthetic acceptance; not qualification" }, "default", "test-operator")!.decision;
  const request = { expectedSha256: sourceSha256, expectedRevision: 1 };
  return { ...report, store, artifactId, sourceSha256, decision, request,
    prepare: () => store.prepareBusinessReportEvalCandidate(artifactId, request)!, cleanup: () => { store.close(); rmSync(directory, { recursive: true, force: true }); } };
}

test("handoff binds the actual agent JSON and input, remains an unqualified development candidate and changes no execution or dataset", () => {
  const f = fixture();
  try {
    const counts = () => Object.fromEntries(["runs", "stages", "artifacts", "events", "business_report_decisions", "eval_datasets", "eval_experiments"]
      .map(table => [table, Number(f.store.db.prepare(`SELECT COUNT(*) AS count FROM ${table}`).get()!.count)]));
    const before = counts(), run = structuredClone(f.store.getRun(f.runId));
    const packet = f.prepare();
    assert.equal(packet.profile, "business-report-eval-candidate.v1");
    assert.equal(packet.purpose, "development"); assert.equal(packet.holdoutEligible, false); assert.equal(packet.referenceReviewRequired, true);
    assert.deepEqual(packet.replay, { automatic: false, scope: "single_agent", context: "requires_review" });
    assert.equal(packet.example.referenceOutput, ""); assert.deepEqual(packet.example.requiredTerms, []); assert.deepEqual(packet.example.forbiddenTerms, []);
    assert.equal(JSON.parse(packet.observedOutput).schemaVersion, "business-report.v1");
    const sourceStage = f.store.db.prepare("SELECT * FROM stages WHERE id = ?").get(packet.source.agentStageId)!;
    assert.equal(sourceStage.stage_kind, "agent"); assert.equal(sourceStage.output, packet.observedOutput);
    assert.equal(packet.example.input, sourceStage.stage_input ?? run!.input);
    assert.equal(packet.source.inputSha256, sha(packet.example.input));
    assert.equal(packet.source.observedOutputSha256, f.decision.binding.inputSha256);
    assert.equal(packet.source.observedOutputSha256, sha(packet.observedOutput));
    assert.equal(packet.source.artifactId, f.artifactId); assert.equal(packet.source.sourceSha256, f.sourceSha256);
    assert.equal(packet.source.decisionId, f.decision.id); assert.equal(packet.source.graphSha256, f.decision.binding.graphSha256);
    assert.equal(packet.source.processVersion, f.decision.binding.processVersion);
    assert.deepEqual(packet.example.knowledgeCollectionIds, [f.collection]);
    assert.deepEqual(packet.currentKnowledgeSnapshot.collectionIds, [f.collection]);
    assert.equal("collections" in packet.currentKnowledgeSnapshot, false, "handoff contains a fingerprint, not a duplicate corpus");
    const { contentSha256, ...body } = packet; assert.equal(contentSha256, sha(JSON.stringify(body)));
    assert.equal("runtimeConfig" in packet.source, false); assert.equal("systemPrompt" in packet.source, false);
    assert.deepEqual(f.prepare(), packet, "a read-only retry yields the same packet");
    assert.deepEqual(counts(), before); assert.deepEqual(f.store.getRun(f.runId), run);
  } finally { f.cleanup(); }
});

test("a rework decision retains observed output separately from a blank reference and stale revisions fail", () => {
  const f = fixture();
  try {
    const saved = f.store.recordBusinessReportDecision(f.artifactId, { requestId: randomUUID(), expectedSha256: f.sourceSha256,
      expectedRevision: 1, decision: "rework_requested", reason: "<script>literal</script> — перепроверьте числа" })!;
    assert.throws(f.prepare, (error: unknown) => error instanceof BusinessReportEvalError && error.code === "decision_changed");
    const packet = f.store.prepareBusinessReportEvalCandidate(f.artifactId, { ...f.request, expectedRevision: 2 })!;
    assert.equal(packet.source.decisionId, saved.decision.id); assert.equal(packet.source.decision, "rework_requested");
    assert.equal(packet.source.reason, saved.decision.reason); assert.equal(packet.example.referenceOutput, ""); assert.ok(packet.observedOutput);
    assert.throws(() => f.store.prepareBusinessReportEvalCandidate(f.artifactId, { ...f.request, expectedSha256: "0".repeat(64) }), /Версия отчёта/);
    f.store.createProject({ id: "foreign", name: "Foreign" });
    assert.equal(f.store.prepareBusinessReportEvalCandidate(f.artifactId, f.request, "foreign"), null);
  } finally { f.cleanup(); }
});

test("missing frozen context, oversized input and corrupt source cannot silently become a candidate", () => {
  const f = fixture();
  try {
    const packet = f.prepare(), stage = f.store.db.prepare("SELECT * FROM stages WHERE id = ?").get(packet.source.agentStageId)!;
    for (const mutation of [null, JSON.stringify({})]) {
      f.store.db.prepare("UPDATE stages SET agent_snapshot_json = ? WHERE id = ?").run(mutation, stage.id);
      assert.throws(f.prepare, (error: unknown) => error instanceof BusinessReportEvalError && error.code === "context_unavailable");
    }
    f.store.db.prepare("UPDATE stages SET agent_snapshot_json = ?, stage_input = ? WHERE id = ?").run(stage.agent_snapshot_json, "x".repeat(100_001), stage.id);
    assert.throws(f.prepare, (error: unknown) => error instanceof BusinessReportEvalError && error.code === "example_too_large");
    f.store.db.prepare("UPDATE stages SET stage_input = ? WHERE id = ?").run(stage.stage_input, stage.id);
    const source = f.store.getArtifactDownload(f.artifactId)!; const bytes = readFileSync(source.filePath);
    writeFileSync(source.filePath, "corrupt");
    assert.throws(f.prepare, (error: unknown) => error instanceof BusinessReportEvalError && error.code === "source_unavailable" && error.status === 503);
    writeFileSync(source.filePath, bytes);
    f.store.db.prepare("UPDATE stages SET stage_input = 'changed' WHERE id = ?").run(f.canonical.stageId);
    assert.throws(f.prepare, (error: unknown) => error instanceof BusinessReportEvalError && error.code === "context_unavailable");
  } finally { f.cleanup(); }
});

test("candidate preparation requires exact non-coerced expectations and rejects caller-supplied authority or reference", () => {
  const valid = { expectedSha256: "a".repeat(64), expectedRevision: 1 };
  assert.deepEqual(normalizeReportEvalRequest(valid), valid);
  for (const raw of [null, [], {}, { ...valid, expectedRevision: "1" }, { ...valid, expectedRevision: 0 }, { ...valid, expectedRevision: 1001 },
    { ...valid, expectedRevision: 1.1 }, { ...valid, expectedSha256: "bad" }, { ...valid, actor: "admin" }, { ...valid, referenceOutput: "trusted" }, { ...valid, purpose: "holdout" }]) {
    assert.throws(() => normalizeReportEvalRequest(raw), BusinessReportEvalError);
  }
});

test("unreviewed and ambiguous persisted results require manual preparation, and frozen graph drift is rejected", () => {
  const f = fixture();
  try {
    const unreviewed = completedOfficeReport(f.store);
    assert.throws(() => f.store.prepareBusinessReportEvalCandidate(String(unreviewed.canonical.id), {
      expectedSha256: String(unreviewed.canonical.sha256), expectedRevision: 1 }), (error: unknown) => error instanceof BusinessReportEvalError && error.code === "decision_changed");
    const instance = f.store.db.prepare("SELECT id, graph_json FROM process_instances WHERE run_id = ?").get(f.runId)!;
    f.store.db.prepare("UPDATE process_instances SET graph_json = '{}' WHERE id = ?").run(instance.id);
    assert.throws(f.prepare, (error: unknown) => error instanceof BusinessReportEvalError && error.code === "context_unavailable");
    f.store.db.prepare("UPDATE process_instances SET graph_json = ? WHERE id = ?").run(instance.graph_json, instance.id);
    const source = f.prepare();
    const stages = f.store.db.prepare("SELECT id, position FROM stages WHERE run_id = ? ORDER BY position DESC").all(f.runId);
    for (const stage of stages) f.store.db.prepare("UPDATE stages SET position = ? WHERE id = ?").run(Number(stage.position) * 10, stage.id);
    f.store.db.prepare(`INSERT INTO stages(id, run_id, agent_id, position, status, output, stage_kind, stage_input, activity_json, agent_snapshot_json, created_at, updated_at)
      SELECT ?, run_id, agent_id, position + 1, status, output, stage_kind, stage_input, activity_json, agent_snapshot_json, created_at, updated_at FROM stages WHERE id = ?`)
      .run(randomUUID(), source.source.agentStageId);
    assert.throws(f.prepare, (error: unknown) => error instanceof BusinessReportEvalError && error.code === "context_unavailable");
  } finally { f.cleanup(); }
});

test("oversized knowledge context fails before building or exporting a duplicate corpus", () => {
  const f = fixture();
  try {
    const chunk = f.store.db.prepare("SELECT id FROM knowledge_chunks WHERE document_id = ? ORDER BY ordinal LIMIT 1").get(f.documentId)!;
    const insert = f.store.db.prepare(`INSERT INTO knowledge_chunks(id, collection_id, document_id, ordinal, content, content_sha256, char_start, char_end, created_at)
      SELECT ?, collection_id, document_id, ?, content, content_sha256, char_start, char_end, created_at FROM knowledge_chunks WHERE id = ?`);
    for (let ordinal = 1; ordinal <= 10_000; ordinal++) insert.run(randomUUID(), ordinal, chunk.id);
    assert.throws(f.prepare, (error: unknown) => error instanceof BusinessReportEvalError && error.code === "context_too_large");
    assert.equal(f.store.listEvalDatasets().length, 0);
  } finally { f.cleanup(); }
});

test("OIDC candidate/import require dataset-author roles and project scope, with imports bound to the verified subject", async () => {
  const f = fixture(), keys = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const jwk = { ...keys.publicKey.export({ format: "jwk" }), kid: "report-eval-test", alg: "RS256", use: "sig" };
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
    const url = `http://127.0.0.1:${address.port}/api/v1/artifacts/${f.artifactId}/eval-candidate`;
    const token = (role: string) => {
      const header = Buffer.from(JSON.stringify({ alg: "RS256", kid: jwk.kid })).toString("base64url");
      const payload = Buffer.from(JSON.stringify({ iss: issuer, aud: "agat-web", sub: `subject-${role}`, exp: Math.floor(Date.now() / 1000) + 300,
        realm_access: { roles: [role] }, agat_projects: ["default", "foreign"] })).toString("base64url");
      const signer = createSign("RSA-SHA256"); signer.update(`${header}.${payload}`); signer.end();
      return `${header}.${payload}.${signer.sign(keys.privateKey).toString("base64url")}`;
    };
    const post = (role: string, extra = {}, body = f.request) => fetch(url, { method: "POST", headers: { authorization: `Bearer ${token(role)}`, "content-type": "application/json", ...extra }, body: JSON.stringify(body) });
    const before = f.store.listEvents(0, 1000).length;
    assert.equal((await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(f.request) })).status, 401);
    for (const role of ["viewer", "auditor", "operator"]) assert.equal((await post(role)).status, 403);
    for (const role of ["admin", "designer"]) {
      const response = await post(role); assert.equal(response.status, 200, await response.clone().text());
      assert.equal(response.headers.get("cache-control"), "no-store"); assert.deepEqual(await response.json(), f.prepare());
    }
    assert.equal((await post("designer", {}, { ...f.request, expectedRevision: 2 })).status, 409);
    f.store.createProject({ id: "foreign", name: "Foreign" });
    assert.equal((await post("designer", { "x-agat-project-id": "foreign" })).status, 404);
    assert.equal(f.store.listEvalDatasets().length, 0); assert.equal(f.store.listEvents(0, 1000).length, before, "candidate preparation is read-only");
    const candidate = f.prepare(), importUrl = url.replace(/eval-candidate$/, "eval-dataset");
    const body = { ...f.request, requestId: randomUUID(), expectedCandidateSha256: candidate.contentSha256, name: "OIDC development import",
      referenceOutput: candidate.observedOutput, rubric: [{ label: "Источники и числа", weight: 1 }], reviewConfirmed: true };
    const importExample = (role: string, extra = {}, data = body) => fetch(importUrl, { method: "POST", headers: {
      authorization: `Bearer ${token(role)}`, "content-type": "application/json", ...extra }, body: JSON.stringify(data) });
    assert.equal((await fetch(importUrl, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) })).status, 401);
    for (const role of ["viewer", "auditor", "operator"]) assert.equal((await importExample(role)).status, 403);
    const created = await importExample("designer"); assert.equal(created.status, 201, await created.clone().text());
    assert.equal(created.headers.get("cache-control"), "no-store");
    const receipt = await created.json() as { datasetId: string };
    assert.equal(receipt.datasetId, reportEvalDatasetId("default", "subject-designer", body.requestId));
    assert.equal((await importExample("designer")).status, 200);
    assert.equal((await importExample("designer", {}, { ...body, name: "Different intent" })).status, 409);
    assert.equal((await importExample("designer", { "x-agat-project-id": "foreign" })).status, 404);
    assert.equal((await importExample("admin", {}, { ...body, name: "Admin import" })).status, 201);
    assert.equal(f.store.listEvalDatasets().length, 2);
  } finally {
    if (server) { server.closeAllConnections(); await new Promise<void>(resolve => server!.close(() => resolve())); }
    identity.closeAllConnections(); await new Promise<void>(resolve => identity.close(() => resolve())); f.cleanup();
  }
});
