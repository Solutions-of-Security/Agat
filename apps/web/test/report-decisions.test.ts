import assert from "node:assert/strict";
import { test } from "node:test";
import { correctionTaskBrief, reportDecisionRequest, verifiedDecisionHistory, verifiedDecisionReceipt, verifiedReworkTask } from "../src/reportDecisions.ts";
import type { ReportDecisionHistory, ReportDecisionRecord, ReportReworkTask } from "../src/types.ts";

const source = { id: "artifact-1", sha256: "a".repeat(64), runId: "run-1" };
const requestId = "00000000-0000-4000-8000-000000000001";
const decision: ReportDecisionRecord = { id: "decision-1", profile: "business-report-decision.v1", artifactId: source.id, sourceSha256: source.sha256,
  runId: source.runId, revision: 1, decision: "rework_requested", reason: "Check period", actor: "operator", reworkTaskId: "task-1", createdAt: "2026-10-25T10:00:00Z",
  binding: { artifactId: source.id, sourceSha256: source.sha256, runId: source.runId, stageId: "stage-1", processInstanceId: "instance-1", processId: "process-1",
    processVersion: 2, graphSha256: "b".repeat(64), inputSha256: "c".repeat(64), schemaSha256: "d".repeat(64) } };
const task: ReportReworkTask = { id: "task-1", title: "Исправить отчёт", kind: "business_report_rework", execution: "manual", state: "open", decisionId: decision.id,
  artifactId: source.id, sourceSha256: source.sha256, runId: source.runId, sourceAvailable: true, reason: decision.reason, requestedBy: decision.actor,
  binding: decision.binding, createdAt: decision.createdAt };
const history: ReportDecisionHistory = { artifactId: source.id, sourceSha256: source.sha256, sourceReady: true, revision: 1, decisions: [decision], nextBeforeRevision: null };

test("review requires a deliberate supported decision, exact version and bounded plain comment", () => {
  assert.deepEqual(reportDecisionRequest(source, 0, "rework_requested", " Check period\r\n<script>literal</script> ", requestId), {
    requestId, expectedSha256: source.sha256, expectedRevision: 0, decision: "rework_requested", reason: "Check period\n<script>literal</script>" });
  assert.equal(reportDecisionRequest(source, 0, "accepted", "", requestId).reason, "");
  for (const reason of [" ", "x\0y", "a".repeat(4001)]) assert.throws(() => reportDecisionRequest(source, 0, "rework_requested", reason, requestId));
  for (const revision of [-1, 1.5, 1000]) assert.throws(() => reportDecisionRequest(source, revision, "accepted", "", requestId));
  assert.throws(() => reportDecisionRequest({ ...source, sha256: "bad" }, 0, "accepted", "", requestId));
  assert.throws(() => reportDecisionRequest(source, 0, "approve" as "accepted", "", requestId));
});

test("history rejects a foreign file, hash, run or binding and preserves retained decisions", () => {
  assert.equal(verifiedDecisionHistory(history, source), history);
  assert.equal(verifiedDecisionHistory({ ...history, sourceReady: false }, source).decisions.length, 1);
  for (const changed of [{ ...source, id: "foreign" }, { ...source, sha256: "f".repeat(64) }, { ...source, runId: "foreign" }]) {
    assert.throws(() => verifiedDecisionHistory(history, changed));
  }
  assert.throws(() => verifiedDecisionHistory({ ...history, decisions: [{ ...decision, binding: { ...decision.binding, artifactId: "foreign" } }] }, source));
  assert.throws(() => verifiedDecisionHistory({ ...history, decisions: [{ ...decision, decision: "accepted" }] }, source));
  assert.throws(() => verifiedDecisionHistory({ ...history, revision: 2 }, source));
});

test("history pagination cannot repeat or reorder a page and allows an exhausted cursor", () => {
  const decisions = Array.from({ length: 50 }, (_, index) => ({ ...decision, id: `decision-${100 - index}`, revision: 100 - index }));
  assert.equal(verifiedDecisionHistory({ ...history, revision: 100, decisions, nextBeforeRevision: 51 }, source).nextBeforeRevision, 51);
  assert.throws(() => verifiedDecisionHistory({ ...history, revision: 100, decisions: [...decisions].reverse(), nextBeforeRevision: 100 }, source));
  assert.throws(() => verifiedDecisionHistory(history, source, 1));
  assert.equal(verifiedDecisionHistory({ ...history, decisions: [] }, source, 1).decisions.length, 0);
  assert.throws(() => verifiedDecisionHistory({ ...history, nextBeforeRevision: 1 }, source));
});

test("a retry receipt must confirm the same intent, revision and one linked manual task", () => {
  const request = reportDecisionRequest(source, 0, "rework_requested", decision.reason, requestId);
  for (const created of [true, false]) assert.equal(verifiedDecisionReceipt({ decision, reworkTask: task, created }, source, request).reworkTask?.id, task.id);
  assert.throws(() => verifiedDecisionReceipt({ decision: { ...decision, revision: 2 }, reworkTask: task, created: true }, source, request));
  assert.throws(() => verifiedDecisionReceipt({ decision: { ...decision, reason: "Other intent" }, reworkTask: task, created: true }, source, request));
  assert.throws(() => verifiedDecisionReceipt({ decision, reworkTask: { ...task, decisionId: "other" }, created: true }, source, request));
  assert.throws(() => verifiedDecisionReceipt({ decision, reworkTask: null, created: true }, source, request));
  assert.equal(verifiedDecisionReceipt({ decision: { ...decision, decision: "accepted", reason: "", reworkTaskId: null }, reworkTask: null, created: true }, source,
    reportDecisionRequest(source, 0, "accepted", "", requestId)).decision.decision, "accepted");
});

test("task handoff retains the exact source and cannot silently become an automatic run", () => {
  assert.equal(verifiedReworkTask(task, source, task.id), task);
  assert.throws(() => verifiedReworkTask({ ...task, execution: "automatic" as "manual" }, source, task.id));
  assert.throws(() => verifiedReworkTask({ ...task, binding: { ...task.binding, graphSha256: "bad" } }, source, task.id));
  assert.throws(() => verifiedReworkTask(task, source, "foreign"));
  const brief = correctionTaskBrief({ ...task, state: "superseded", sourceAvailable: false }, "Отчёт.md", "https://agat.example");
  for (const value of [source.sha256, task.reason, "версия 2", "сейчас недоступен", "заменён", "ещё не выполнено", "https://agat.example/#runs/run-1"]) assert.ok(brief.includes(value), value);
  assert.throws(() => correctionTaskBrief(task, "Отчёт.md", "javascript:alert(1)"));
  assert.throws(() => correctionTaskBrief(task, "Отчёт.md", "https://agat.example/foreign"));
});
