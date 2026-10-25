import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { AgatStore } from "../src/database.js";
import { BusinessReportEvalError } from "../src/business-report-eval.js";
import { normalizeReportEvalImport, reportEvalDatasetId } from "../src/business-report-eval-import.js";
import { completedOfficeReport } from "./business-report-office-fixtures.js";

function fixture() {
  const directory = mkdtempSync(path.join(os.tmpdir(), "agat-report-import-"));
  const store = new AgatStore(":memory:", { seedDemo: false, artifactsDir: directory });
  const report = completedOfficeReport(store), artifactId = String(report.canonical.id), expectedSha256 = String(report.canonical.sha256);
  store.recordBusinessReportDecision(artifactId, { requestId: randomUUID(), expectedSha256, expectedRevision: 0, decision: "accepted", reason: "Synthetic fixture acceptance" });
  const candidate = store.prepareBusinessReportEvalCandidate(artifactId, { expectedSha256, expectedRevision: 1 })!;
  const body = { requestId: randomUUID(), expectedSha256, expectedRevision: 1, expectedCandidateSha256: candidate.contentSha256,
    name: "Development report fixture", description: "Synthetic; not qualification", referenceOutput: candidate.observedOutput,
    rubric: [{ id: "evidence", label: "Источники и числа", description: "Проверить ссылки, период и расчёты", weight: 1 }], reviewConfirmed: true };
  return { ...report, store, artifactId, candidate, body,
    create: (value = body, actor = "test-designer") => store.createBusinessReportEvalDataset(artifactId, value, "default", actor)!,
    cleanup: () => { store.close(); rmSync(directory, { recursive: true, force: true }); } };
}
const sha = (value: string) => createHash("sha256").update(value).digest("hex");

test("one explicit import atomically stores a versioned example, human rubric and lineage without starting execution", () => {
  const f = fixture();
  try {
    const run = structuredClone(f.store.getRun(f.runId));
    const counts = () => Object.fromEntries(["runs", "stages", "artifacts", "business_report_decisions", "eval_experiments"].map(table => [table, f.store.db.prepare(`SELECT COUNT(*) AS count FROM ${table}`).get()!.count]));
    const before = counts(), receipt = f.create();
    assert.equal(receipt.created, true); assert.equal(receipt.datasetVersion, 1); assert.equal(receipt.candidateSha256, f.candidate.contentSha256);
    assert.match(receipt.datasetId, /^[a-f0-9]{8}-[a-f0-9]{4}-8[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
    const version = f.store.db.prepare("SELECT * FROM eval_dataset_versions WHERE dataset_id = ? AND version = 1").get(receipt.datasetId)!;
    const example = f.store.db.prepare("SELECT * FROM eval_examples WHERE dataset_id = ?").get(receipt.datasetId)!;
    const metadata = JSON.parse(String(version.knowledge_snapshot_json)).reportImport;
    assert.equal(metadata.profile, "business-report-eval-import.v1"); assert.equal(metadata.purpose, "development"); assert.equal(metadata.holdoutEligible, false);
    assert.equal(metadata.reviewConfirmed, true); assert.equal(metadata.actorSha256, sha("test-designer"));
    assert.equal(metadata.source.artifactId, f.artifactId); assert.equal(metadata.source.sourceSha256, f.body.expectedSha256);
    assert.equal(metadata.source.decisionId, f.candidate.source.decisionId); assert.equal(metadata.source.inputSha256, f.candidate.source.inputSha256);
    assert.equal("reason" in metadata.source, false); assert.equal("runtimeConfig" in metadata.source, false);
    assert.equal(metadata.referenceSha256, sha(f.body.referenceOutput)); assert.equal(metadata.referenceValidation.status, "validated");
    assert.equal(example.input, f.candidate.example.input); assert.equal(example.reference_output, f.body.referenceOutput);
    assert.equal(version.content_sha256, receipt.datasetContentSha256); assert.equal(JSON.parse(String(version.rubric_json))[0].label, f.body.rubric[0]!.label);
    const events = f.store.listEvents(0, 1000).filter(event => event.type === "business.report.eval.dataset.created");
    assert.equal(events.length, 1);
    for (const text of [f.body.referenceOutput, f.body.name, f.body.rubric[0]!.description, "Synthetic fixture acceptance"]) assert.ok(!JSON.stringify(events[0]).includes(text));
    const repeat = f.create(); assert.equal(repeat.created, false); assert.equal(repeat.datasetId, receipt.datasetId); assert.equal(repeat.datasetContentSha256, receipt.datasetContentSha256);
    assert.equal(f.store.listEvalDatasets().length, 1); assert.deepEqual(counts(), before); assert.deepEqual(f.store.getRun(f.runId), run);
  } finally { f.cleanup(); }
});

test("lost-response retries keep the original version after a later decision or source purge; changed intent conflicts", () => {
  const f = fixture();
  try {
    const first = f.create();
    f.store.createEvalDatasetVersion(first.datasetId, { description: "Explicit subsequent revision", changeNote: "Development edit",
      rubric: f.body.rubric, examples: [{ ...f.candidate.example, referenceOutput: f.body.referenceOutput }] });
    assert.equal(f.store.listEvalDatasets()[0]!.currentVersion, 2);
    assert.equal(f.create().datasetVersion, 1); assert.equal(f.create().datasetContentSha256, first.datasetContentSha256);
    f.store.recordBusinessReportDecision(f.artifactId, { requestId: randomUUID(), expectedSha256: f.body.expectedSha256, expectedRevision: 1,
      decision: "rework_requested", reason: "Later feedback" });
    assert.equal(f.create().datasetId, first.datasetId);
    assert.throws(() => f.create({ ...f.body, name: "Different intent" }), (error: unknown) => error instanceof BusinessReportEvalError && error.code === "idempotency_conflict");
    f.store.db.prepare("DELETE FROM runs WHERE id = ?").run(f.runId);
    assert.equal(f.create().datasetId, first.datasetId, "the independently retained dataset remains the durable retry receipt");
    assert.equal(f.create({ ...f.body, requestId: randomUUID() }), null, "a new import cannot resurrect a purged source");
    assert.equal(f.store.listEvalDatasets().length, 1);
  } finally { f.cleanup(); }
});

test("request namespaces use the full actor and project, and a source cannot be imported across projects", () => {
  const f = fixture();
  try {
    const first = f.create(), second = f.create({ ...f.body, name: "Second author" }, "other-designer");
    assert.notEqual(first.datasetId, second.datasetId);
    assert.equal(reportEvalDatasetId("default", "test-designer", f.body.requestId), first.datasetId);
    assert.notEqual(reportEvalDatasetId("foreign", "test-designer", f.body.requestId), first.datasetId);
    f.store.createProject({ id: "foreign", name: "Foreign" });
    assert.equal(f.store.createBusinessReportEvalDataset(f.artifactId, f.body, "foreign", "test-designer"), null);
    assert.equal(f.store.listEvalDatasets("foreign").length, 0);
  } finally { f.cleanup(); }
});

test("stale context, invalid references and missing human confirmation never leave partial datasets", () => {
  const f = fixture();
  try {
    const invalidCalculation = JSON.parse(f.body.referenceOutput); invalidCalculation.calculations[0].result = 999;
    const invalidSource = JSON.parse(f.body.referenceOutput); invalidSource.claims[0].sourceRefs = ["FOREIGN"];
    for (const value of [{ ...f.body, reviewConfirmed: false }, { ...f.body, rubric: [] }, { ...f.body, referenceOutput: "" },
      { ...f.body, expectedRevision: 2 }, { ...f.body, expectedCandidateSha256: "0".repeat(64) },
      { ...f.body, referenceOutput: JSON.stringify(invalidCalculation) }, { ...f.body, referenceOutput: JSON.stringify(invalidSource) }, { ...f.body, referenceOutput: "<script>not a typed reference</script>" }]) {
      assert.throws(() => f.store.createBusinessReportEvalDataset(f.artifactId, value));
    }
    for (const table of ["eval_datasets", "eval_dataset_versions", "eval_examples"]) assert.equal(f.store.db.prepare(`SELECT COUNT(*) AS count FROM ${table}`).get()!.count, 0);
    assert.equal(f.store.listEvents(0, 1000).filter(event => event.type === "business.report.eval.dataset.created").length, 0);
    const created = f.create();
    assert.throws(() => f.create({ ...f.body, requestId: randomUUID() }), (error: unknown) => error instanceof BusinessReportEvalError && error.code === "dataset_name_conflict");
    assert.equal(f.store.listEvalDatasets().length, 1); assert.equal(created.created, true);
  } finally { f.cleanup(); }
});

test("a failed example insert rolls back dataset, version and event, and an exact retry can succeed", () => {
  const f = fixture();
  try {
    f.store.db.exec("CREATE TRIGGER report_import_fixture_failure BEFORE INSERT ON eval_examples BEGIN SELECT RAISE(ABORT, 'Synthetic example write failure'); END");
    assert.throws(f.create, /Synthetic example write failure/);
    for (const table of ["eval_datasets", "eval_dataset_versions", "eval_examples"]) assert.equal(f.store.db.prepare(`SELECT COUNT(*) AS count FROM ${table}`).get()!.count, 0);
    assert.equal(f.store.listEvents(0, 1000).filter(event => event.type === "business.report.eval.dataset.created").length, 0);
    f.store.db.exec("DROP TRIGGER report_import_fixture_failure");
    assert.equal(f.create().created, true); assert.equal(f.create().created, false);
  } finally { f.cleanup(); }
});

test("the exact stage input survives dataset normalization, and insufficient typed references remain reviewed examples", () => {
  const f = fixture();
  try {
    const input = " \nExact upstream result\n ";
    f.store.db.prepare("UPDATE stages SET stage_input = ? WHERE id = ?").run(input, f.candidate.source.agentStageId);
    const packet = f.store.prepareBusinessReportEvalCandidate(f.artifactId, { expectedSha256: f.body.expectedSha256, expectedRevision: 1 })!;
    const reference = { schemaVersion: "business-report.v1", status: "insufficient", summary: "Требуется уточнение", claims: [], calculations: [], artifactRefs: [], limitations: ["Нет подтверждения причин"] };
    const created = f.create({ ...f.body, expectedCandidateSha256: packet.contentSha256, referenceOutput: JSON.stringify(reference) });
    assert.equal(f.store.db.prepare("SELECT input FROM eval_examples WHERE dataset_id = ?").get(created.datasetId)!.input, input);
    const metadata = JSON.parse(String(f.store.db.prepare("SELECT knowledge_snapshot_json FROM eval_dataset_versions WHERE dataset_id = ?").get(created.datasetId)!.knowledge_snapshot_json)).reportImport;
    assert.equal(metadata.referenceValidation.status, "review_required"); assert.equal(metadata.holdoutEligible, false);
    const snapshot = JSON.parse(String(f.store.db.prepare("SELECT agent_snapshot_json FROM stages WHERE id = ?").get(f.candidate.source.agentStageId)!.agent_snapshot_json));
    const prompt = f.store.createPrompt({ name: "Explicit frozen prompt fixture", content: snapshot.systemPrompt });
    const experiment = f.store.createEvalExperiment({ name: "Explicit experiment after import", datasetId: created.datasetId, datasetVersion: 1,
      agentId: packet.source.agentId, promptId: String(prompt.id), promptVersion: 1 });
    const item = f.store.db.prepare("SELECT run_id FROM eval_experiment_items WHERE experiment_id = ?").get(experiment.id)!;
    const evaluationRun = f.store.getRun(String(item.run_id))!;
    assert.equal(evaluationRun.input, input, "the existing executor receives exact stored input after an explicit experiment action");
    assert.equal(evaluationRun.status, "queued");
  } finally { f.cleanup(); }
});

test("the import request rejects caller-supplied input, purpose, authority or implicit confirmation", () => {
  const f = fixture();
  try {
    assert.equal(normalizeReportEvalImport(f.body).reviewConfirmed, true);
    for (const value of [null, [], { ...f.body, input: "Injected" }, { ...f.body, purpose: "holdout" }, { ...f.body, actor: "admin" },
      { ...f.body, requestId: "not-a-uuid" }, { ...f.body, reviewConfirmed: "true" }, { ...f.body, referenceOutput: "x".repeat(100_001) }]) {
      assert.throws(() => normalizeReportEvalImport(value), BusinessReportEvalError);
    }
  } finally { f.cleanup(); }
});
