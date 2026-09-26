import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { DecisionObservations } from "../src/components/DecisionObservations.tsx";
import type { RunDecisionObservation } from "../src/types.ts";

const observation: RunDecisionObservation = {
  stageId: "stage-one", profileSha256: "a".repeat(64),
  context: { kind: "boolean", question: "Подтверждён ли запуск?", options: [
    { id: "no", description: "Не подтверждён", value: false, abstain: false },
    { id: "yes", description: "Подтверждён", value: true, abstain: false }] }, observation: {
    mode: "shadow", fallback: "primary", status: "ok", reason: "accepted",
    result: { value: false, selectedOptionId: "no", selectedProbability: 0.95, margin: 0.9, durationMs: 0,
      policy: { minProbability: 0.8, minMargin: 0.1 },
      calibration: { status: "uncalibrated", temperature: 1 }, model: { repository: "test-only" },
      distribution: [{ id: "no", probability: 0.95, logit: Math.log(0.95) }, { id: "yes", probability: 0.05, logit: Math.log(0.05) }] },
  },
};
function render(value = observation) {
  return renderToStaticMarkup(createElement(DecisionObservations, { observations: [value], stages: [] }));
}

test("false and zero latency remain visible; passing a threshold does not imply quality approval", () => {
  const html = render();
  assert.match(html, /Нет \(false\)/);
  assert.match(html, /Подтверждён ли запуск\?/);
  assert.match(html, /Не подтверждён/);
  assert.match(html, /90 п. п. \/ 10 п. п./);
  assert.match(html, /0 мс/);
  assert.match(html, /Порог пройден/);
  assert.match(html, /Без калибровки/);
  assert.match(html, /Качество проверяется отдельно/);
  assert.match(html, /Основной агент определяет результат и маршрут/);
  assert.match(html, /scope="row"/);
  assert.ok(!html.includes("PASS"));
});

test("abstention and unavailable inference cannot display an accepted negative answer", () => {
  const abstained = render({ ...observation, observation: { ...observation.observation,
    status: "abstain", reason: "below_threshold" } });
  assert.match(abstained, /Решение не принято/);
  assert.ok(!abstained.includes("Нет (false)"));
  const timeout = render({ stageId: "stage", profileSha256: null,
    observation: { mode: "shadow", fallback: "primary", status: "unavailable", reason: "timeout" } });
  assert.match(timeout, /Проверка недоступна/);
  assert.match(timeout, /Истёк срок ожидания/);
  assert.ok(!timeout.includes("Нет (false)") && !timeout.includes("0 мс") && !timeout.includes("Без калибровки"));
});

test("unknown reason and identifiers are escaped, reused observations are identified, empty traces stay unchanged", () => {
  const html = render({ ...observation, observation: { ...observation.observation,
    reason: "<script>alert(1)</script>", reusedFromStageId: "original-stage" } });
  assert.ok(!html.includes("<script>"));
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /Сохранённое наблюдение из этапа/);
  assert.match(html, /original-stage/);
  assert.equal(renderToStaticMarkup(createElement(DecisionObservations, { observations: [], stages: [] })), "");
});

test("Score displays the numeric scale and weighted mean without treating it as a Boolean", () => {
  const score = structuredClone(observation);
  score.context!.kind = "score";
  score.context!.options[0]!.value = 0;
  score.context!.options[1]!.value = 1e-7;
  score.observation.result!.value = 0;
  const html = render(score);
  assert.match(html, /Среднее по шкале/);
  assert.match(html, /<dd>0<\/dd>/);
  assert.match(html, /Уровень: 1e-7/);
  assert.match(html, /взвешенное по этому распределению/);
  assert.ok(!html.includes("Нет (false)"));
});
