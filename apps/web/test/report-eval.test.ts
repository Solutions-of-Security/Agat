import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";
import { reportEvalDraftError, verifiedReportEvalCandidate, verifiedReportEvalReceipt } from "../src/reportEval.ts";
import type { ReportEvalCandidate, ReportEvalImportRequest } from "../src/types.ts";

const sha = (value: string) => createHash("sha256").update(value).digest("hex");
const artifact = { id: "artifact-1", sha256: "a".repeat(64), runId: "run-1" };
const basis = { revision: 1, decisionId: "decision-1" };
const input = "Exact stage input", output = JSON.stringify({ schemaVersion: "business-report.v1", status: "ready" });
function candidate(): ReportEvalCandidate {
  const body: Omit<ReportEvalCandidate, "contentSha256"> = { profile: "business-report-eval-candidate.v1", purpose: "development", holdoutEligible: false, referenceReviewRequired: true,
    replay: { automatic: false, scope: "single_agent", context: "requires_review" },
    source: { projectId: "default", artifactId: artifact.id, sourceSha256: artifact.sha256, runId: artifact.runId, reportStageId: "report-stage",
      processInstanceId: "instance", processId: "process", processVersion: 1, graphSha256: "b".repeat(64), decisionId: basis.decisionId, decisionRevision: 1,
      decision: "accepted", reason: "Reviewed", agentStageId: "agent-stage", agentId: "agent", promptSha256: "c".repeat(64), definitionSha256: "d".repeat(64), requestedModel: null,
      runtime: "ollama", runtimeProfile: "plain_v1", inputSha256: sha(input), observedOutputSha256: sha(output), schemaSha256: "e".repeat(64) },
    example: { name: "Example", input, referenceOutput: "", requiredTerms: [], forbiddenTerms: [], knowledgeCollectionIds: ["collection-1"] }, observedOutput: output,
    currentKnowledgeSnapshot: { schemaVersion: 1, collectionIds: ["collection-1"], missingCollectionIds: [], fingerprint: "f".repeat(64) } };
  return { ...body, contentSha256: sha(JSON.stringify(body)) };
}

test("candidate verification binds source, project and read decision before accepting content", async () => {
  const value = candidate(); assert.equal(await verifiedReportEvalCandidate(value, artifact, basis, "default"), value);
  await assert.rejects(verifiedReportEvalCandidate(value, { ...artifact, id: "foreign" }, basis, "default"));
  await assert.rejects(verifiedReportEvalCandidate(value, { ...artifact, runId: "foreign" }, basis, "default"));
  await assert.rejects(verifiedReportEvalCandidate(value, artifact, { ...basis, revision: 2 }, "default"));
  await assert.rejects(verifiedReportEvalCandidate(value, artifact, { ...basis, decisionId: "foreign" }, "default"));
  await assert.rejects(verifiedReportEvalCandidate(value, artifact, basis, "foreign"));
});

test("tampered input/output/digest and automatic reference or holdout claims fail verification", async () => {
  for (const mutate of [
    (value: ReportEvalCandidate) => { value.example.input = "Changed input"; },
    (value: ReportEvalCandidate) => { value.observedOutput = "Changed output"; },
    (value: ReportEvalCandidate) => { value.contentSha256 = "0".repeat(64); },
    (value: ReportEvalCandidate) => { value.example.referenceOutput = "Auto reference" as ""; },
    (value: ReportEvalCandidate) => { value.holdoutEligible = true as false; },
    (value: ReportEvalCandidate) => { value.replay.automatic = true as false; },
  ]) { const value = candidate(); mutate(value); await assert.rejects(verifiedReportEvalCandidate(value, artifact, basis, "default")); }
});

test("draft requires explicit bounded reference, criteria and attestation", () => {
  const reference = JSON.stringify({ schemaVersion: "business-report.v1" });
  assert.equal(reportEvalDraftError("Dataset", reference, ["Sources"], true), null);
  assert.match(reportEvalDraftError("Dataset", reference, ["Sources"], false)!, /Подтвердите/);
  assert.match(reportEvalDraftError("Dataset", "", ["Sources"], true)!, /JSON/);
  assert.match(reportEvalDraftError("Dataset", "[", ["Sources"], true)!, /синтаксис/);
  assert.match(reportEvalDraftError("Dataset", "null", ["Sources"], true)!, /контракту/);
  assert.match(reportEvalDraftError("Dataset", reference, [" "], true)!, /критерии/);
  assert.match(reportEvalDraftError(" ", reference, ["Sources"], true)!, /название/);
});

test("a save receipt must confirm the same candidate and an actual versioned dataset", () => {
  const request = { expectedCandidateSha256: candidate().contentSha256 } as ReportEvalImportRequest;
  const receipt = { datasetId: "11111111-1111-8111-8111-111111111111", datasetVersion: 1 as const, datasetContentSha256: "a".repeat(64), candidateSha256: request.expectedCandidateSha256, created: true };
  assert.equal(verifiedReportEvalReceipt(receipt, request), receipt);
  assert.equal(verifiedReportEvalReceipt({ ...receipt, created: false }, request).datasetVersion, 1);
  assert.throws(() => verifiedReportEvalReceipt({ ...receipt, candidateSha256: "0".repeat(64) }, request));
  assert.throws(() => verifiedReportEvalReceipt({ ...receipt, datasetId: "foreign" }, request));
  assert.throws(() => verifiedReportEvalReceipt({ ...receipt, datasetVersion: 2 as 1 }, request));
});
