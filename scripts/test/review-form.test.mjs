import { strict as assert } from "node:assert";
import { execFileSync } from "node:child_process";
import { test } from "node:test";
import "../review-form/core.js";

const Core = globalThis.AgatReviewForm;
const bundle = JSON.parse(execFileSync("python3", ["-c", "import json; from scripts.test.test_decision_review_form import bundle_fixture; print(json.dumps(bundle_fixture(), ensure_ascii=False))"], { encoding: "utf8" }));
const now = "2026-10-10T10:00:00.000Z";
function filled(role = "first") { const state = Core.createState(bundle, role, now); state.participantName = "Синтетический участник"; for (const a of state.answers) Object.assign(a, { optionId: "other", optionLabelRu: "Прочая помощь", rationale: "Синтетическое обоснование, не экспертная оценка." }); return state; }

test("empty and partially typed drafts round-trip without default answers", () => {
  const state = Core.createState(bundle, "first", now);
  assert.equal(Core.count(state), 0); assert.equal(state.submissionConfirmed, false);
  Object.assign(state.answers[0], { optionId: "incident", optionLabelRu: "Инцидент" });
  state.answers[1].rationale = "Незавершённый текст.";
  const file = Core.makeExport(state, bundle, { now });
  assert.deepEqual(Core.load(Core.parseJson(JSON.stringify(file)), bundle, "first"), file);
  assert.equal(Core.count(file), 0); assert.throws(() => Core.makeExport(state, bundle, { submit: true, now }));
});
test("all answers remain a draft until explicit final export; edits reopen completion", () => {
  const state = filled();
  assert.equal(Core.count(state), 3); assert.equal(Core.makeExport(state, bundle, { now }).submittedAt, null);
  const final = Core.makeExport(state, bundle, { submit: true, now });
  assert.equal(final.status, "submitted"); assert.equal(final.submittedAt, now);
  assert.deepEqual(Core.load(final, bundle, "first"), final);
  const reopened = { ...final, status: "draft", submittedAt: null, submissionConfirmed: false };
  assert.equal(Core.makeExport(reopened, bundle, { now }).submittedAt, null);
  assert.equal(state.status, "draft");
});
test("source bindings, question order, Russian labels, ownership and language cannot change", () => {
  for (const change of [v => v.formId = "0".repeat(64), v => v.localizatonSha256 = "0", v => v.poolSha256 = "0", v => v.language = "en", v => v.answers.reverse(), v => v.answers.pop(), v => v.answers[0].optionId = "unknown", v => v.answers[0].optionLabelRu = "Other", v => v.answers[0].rationale = "English rationale", v => v.participantRole = ["first"], v => v.reviewerId = "foreign", v => v.position = true, v => v.startedAt = "2026-02-31T10:00:00Z", v => v.participantName = " ", v => v.humanExecutionVerified = true]) {
    const final = Core.makeExport(filled(), bundle, { submit: true, now }); change(final);
    assert.throws(() => Core.load(final, bundle, "first"));
  }
});
test("legacy unfinished review imports exact original bindings and preserves ownership", () => {
  const legacy = structuredClone(bundle.blank); legacy.reviewerId = Core.ROLES.expert;
  Object.assign(legacy.labels[0], { expectedOptionId: "access", rationale: "Синтетический запрос изменения прав." });
  const state = Core.load(legacy, bundle, "first", now);
  assert.equal(state.participantRole, "expert"); assert.equal(state.answers[0].optionLabelRu, "Изменение доступа");
  assert.equal(state.position, 1); assert.equal(state.status, "draft");
  for (const change of [v => v.pool.cases[0].request.state += " changed", v => v.labels.reverse(), v => v.reviewerId = "foreign", v => v.reviewedAt = now]) {
    const value = structuredClone(legacy); change(value); assert.throws(() => Core.load(value, bundle, "first", now));
  }
});
test("participant states are independent and a clear does not clear other questions", () => {
  const a = filled(), b = Core.createState(bundle, "expert", now);
  Object.assign(a.answers[1], { optionId: null, optionLabelRu: null, rationale: "" });
  assert.equal(Core.count(a), 2); assert.equal(Core.count(b), 0); assert.notEqual(a.reviewerId, b.reviewerId);
});
test("malformed and duplicate JSON, escaped duplicate keys and excess nesting are rejected", () => {
  for (const raw of ['{"a":1,"a":2}', '{"a":1,"\\u0061":2}', '{"nested":{"a":1,"a":2}}', '{bad}', '['.repeat(90) + '0' + ']'.repeat(90)]) assert.throws(() => Core.parseJson(raw));
  const value = { text: 'Текст с "кавычками", [скобками], {фигурными} и \\ символом', data: [null, true, 4, { a: "я" }] };
  assert.deepEqual(Core.parseJson(JSON.stringify(value)), value);
});
