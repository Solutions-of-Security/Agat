import assert from "node:assert/strict";
import test from "node:test";
import { normalizeDecisionShadowConfig } from "../../coordinator/src/local-decisions.ts";
import { decisionProfileJson } from "../../../tests/fixtures/decision-shadow-profile.mjs";
import { decisionShadowDraft, decisionShadowFormResult, defaultDecisionOptions,
  type DecisionKind } from "../src/decisionShadowForm.ts";

function draft(kind: DecisionKind = "boolean") {
  return { ...decisionShadowDraft(), kind, question: "Подтверждено исходными данными? 🧪",
    profileJson: decisionProfileJson, options: defaultDecisionOptions(kind).map((option, index) => ({
      ...option, description: `Уровень ${index}`, value: kind === "boolean" ? String(Boolean(index)) : String(index),
    })) };
}

for (const kind of ["boolean", "choice", "score"] as const) {
  test(`${kind}: editor produces the real server contract, with exact profile bytes and order`, () => {
    const input = draft(kind);
    input.options.reverse();
    if (kind === "choice") input.options[0]!.abstain = true;
    if (kind === "score") input.options[1]!.value = "-0";
    const before = structuredClone(input);
    const result = decisionShadowFormResult(input);
    assert.deepEqual(result.issues, []);
    assert.ok(result.config);
    assert.deepEqual(normalizeDecisionShadowConfig(result.config), result.config);
    assert.equal(result.config.profileJson, decisionProfileJson);
    assert.match(result.config.profileJson, /"temperature": 1\.0/);
    assert.deepEqual(result.config.options.map(option => option.id), input.options.map(option => option.id));
    if (kind === "boolean") assert.equal(result.config.options[1]!.value, false);
    if (kind === "score") assert.equal(Object.is(result.config.options[1]!.value, -0), false);
    if (kind === "choice") {
      assert.equal(result.config.options[0]!.abstain, true);
      assert.ok(result.config.options.every(option => !Object.hasOwn(option, "value")));
    }
    const reopened = decisionShadowDraft(result.config);
    assert.deepEqual(decisionShadowFormResult(reopened).config, result.config);
    reopened.options[0]!.description = "Изменён только черновик";
    assert.notEqual(reopened.options[0]!.description, result.config.options[0]!.description);
    assert.deepEqual(input, before);
  });
}

test("incomplete drafts, duplicate candidates, Unicode and size limits cannot be applied", () => {
  const mutations = [
    (d: ReturnType<typeof draft>) => { d.question = " "; },
    (d: ReturnType<typeof draft>) => { d.question = "x".repeat(2001); },
    (d: ReturnType<typeof draft>) => { d.question = "\ud800"; },
    (d: ReturnType<typeof draft>) => { d.options[0]!.id = "пробел и кириллица"; },
    (d: ReturnType<typeof draft>) => { d.options[0]!.description = "x".repeat(1001); },
    (d: ReturnType<typeof draft>) => { d.options[0]!.id = d.options[1]!.id; },
    (d: ReturnType<typeof draft>) => { d.options[0]!.description = d.options[1]!.description; },
    (d: ReturnType<typeof draft>) => { d.options[0]!.value = "true"; },
    (d: ReturnType<typeof draft>) => { d.options[0]!.value = "not a Boolean"; },
    (d: ReturnType<typeof draft>) => { d.options.pop(); },
    (d: ReturnType<typeof draft>) => { d.options.push({ ...d.options[0]! }); },
  ];
  for (const mutate of mutations) {
    const input = draft(); mutate(input);
    const result = decisionShadowFormResult(input);
    assert.equal(result.config, undefined);
    assert.ok(result.issues.length > 0);
  }
  const input = draft(); input.question = "🧪".repeat(2000);
  assert.deepEqual(decisionShadowFormResult(input).issues, []);
  assert.equal(decisionShadowFormResult(decisionShadowDraft()).config, undefined);
});

test("timeout and score values respect server ranges and reject ambiguous input", () => {
  for (const timeout of ["", "99", "10001", "100.5", "NaN"]) {
    assert.equal(decisionShadowFormResult({ ...draft(), timeout }).config, undefined);
  }
  for (const value of ["", "0x10", "1,5", "Infinity", "1e309", "1000001", "-1000001", "1"]) {
    const input = draft("score"); input.options[0]!.value = value;
    assert.equal(decisionShadowFormResult(input).config, undefined, value);
  }
  for (const value of ["-1000000", "1000000", "1e-7", ".25", "-0"]) {
    const input = draft("score"); input.options[0]!.value = value;
    const result = decisionShadowFormResult(input);
    assert.deepEqual(result.issues, [], value);
    assert.deepEqual(normalizeDecisionShadowConfig(result.config), result.config);
  }
  const input = draft("score"); input.options[0]!.value = "-0"; input.options[1]!.value = "0";
  assert.equal(decisionShadowFormResult(input).config, undefined);
});

test("profile syntax and Score capability are checked; the server still enforces model pinning", () => {
  for (const profileJson of ["", "{", "null", "[]", "{}", " ".repeat(16001) + decisionProfileJson]) {
    assert.equal(decisionShadowFormResult({ ...draft(), profileJson }).config, undefined);
  }
  const profile = JSON.parse(decisionProfileJson);
  delete profile.inputFingerprintVersions;
  assert.equal(decisionShadowFormResult({ ...draft("score"), profileJson: JSON.stringify(profile) }).config, undefined);
  assert.ok(decisionShadowFormResult({ ...draft(), profileJson: JSON.stringify(profile) }).config);
  profile.model.artifactSha256 = "unpinned";
  const result = decisionShadowFormResult({ ...draft(), profileJson: JSON.stringify(profile) });
  assert.throws(() => normalizeDecisionShadowConfig(result.config), /Unpinned decision model/);
});

test("Choice supports ten ordered candidates and explicit abstention, without hidden values", () => {
  const input = draft("choice");
  input.options = Array.from({ length: 10 }, (_, index) => ({ key: index, id: `option-${index}`,
    description: `Вариант ${index}`, abstain: index === 9, value: "ignored" }));
  const result = decisionShadowFormResult(input);
  assert.deepEqual(result.issues, []);
  assert.deepEqual(normalizeDecisionShadowConfig(result.config), result.config);
  input.options.push({ key: 10, id: "extra", description: "Лишний", abstain: false, value: "" });
  assert.equal(decisionShadowFormResult(input).config, undefined);
});
