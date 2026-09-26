import React from "react";
import type { Run, RunDecisionObservation } from "../types";

const statuses = {
  ok: "Порог пройден",
  abstain: "Воздержалась",
  error: "Ошибка вычисления",
  unavailable: "Проверка недоступна",
};

const reasons: Record<string, string> = {
  accepted: "Вариант прошёл заданные пороги",
  below_threshold: "Вероятность или отрыв от следующего варианта ниже порога",
  abstain_option: "Выбран вариант недостаточности данных",
  disabled: "Shadow-профиль выключен на coordinator",
  unsupported_worker: "На узле не настроена локальная проверка",
  invalid_input: "Вход не соответствует ограничениям проверки",
  invalid_request: "Runtime отклонил запрос",
  busy: "Локальная модель занята",
  timeout: "Истёк срок ожидания локальной модели",
  cancelled: "Проверка отменена",
  unreachable: "Локальный runtime недоступен",
  profile_mismatch: "Профиль runtime изменился",
  invalid_response: "Ответ не прошёл проверку coordinator",
  missing_result: "Узел завершил этап без наблюдения",
  safe_replay_unavailable: "Для безопасного повтора нет подходящего наблюдения",
  context_too_long: "Вход превышает лимит контекста",
  calibration_out_of_scope: "Калибровка не применима к этому вопросу",
  backend_error: "Ошибка локального вычисления",
  inference_timeout: "Сервер остановил вычисление по deadline; требуется перезапуск runtime",
  backend_unavailable: "Вычислительный процесс недоступен; требуется перезапуск runtime",
  invalid_scores: "Некорректные оценки вариантов",
  unsupported_tokenizer: "Tokenizer не поддерживает схему вариантов",
};

const percent = new Intl.NumberFormat("ru-RU", { style: "percent", maximumFractionDigits: 1 });
const decimal = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });
function probability(value: number | undefined) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1 ? percent.format(value) : "—";
}
function marginPoints(value: number | undefined) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1 ? `${decimal.format(value * 100)} п. п.` : "—";
}

function outcome({ status, result }: RunDecisionObservation["observation"]) {
  if (status === "abstain") return "Решение не принято";
  if (status !== "ok" || result?.value === null || result?.value === undefined) return "—";
  if (typeof result.value === "boolean") return result.value ? "Да (true)" : "Нет (false)";
  return String(result.value);
}

export function DecisionObservations({ observations, stages }: {
  observations: RunDecisionObservation[];
  stages: Run["stages"];
}) {
  if (observations.length === 0) return null;
  const stageNames = new Map(stages.map((stage) => [stage.id, `${stage.position + 1}. ${stage.agent.name}`]));
  return (
    <section className="decision-observations" aria-label="Локальные решения (shadow)">
      <header>
        <h3>Локальные решения <span>shadow</span></h3>
        <p>Основной агент определяет результат и маршрут. Эти ответы сохранены для проверки локальной модели.</p>
      </header>
      {observations.map(({ stageId, profileSha256, context, observation }) => {
        const result = observation.result;
        const duration = result?.durationMs;
        return (
          <article className={`decision-observation decision-observation--${observation.status}`} key={stageId}>
            <div className="decision-observation__heading">
              <strong>{stageNames.get(stageId) ?? stageId}</strong>
              <span className="decision-observation__status">{statuses[observation.status] ?? "Неизвестный статус"}</span>
            </div>
            {context?.question ? <p className="decision-observation__question"><strong>Вопрос:</strong> {context.question}</p> : null}
            <p>{reasons[observation.reason] ?? observation.reason}</p>
            <dl className="decision-observation__metrics">
              <div><dt>{context?.kind === "score" ? "Среднее по шкале" : "Ответ"}</dt><dd>{outcome(observation)}</dd></div>
              <div><dt>Вероятность варианта</dt><dd title={result?.selectedProbability?.toString()}>{probability(result?.selectedProbability)}</dd></div>
              <div><dt>Время runtime</dt><dd>{typeof duration === "number" && Number.isFinite(duration) && duration >= 0 ? `${decimal.format(duration)} мс` : "—"}</dd></div>
              <div><dt>Калибровка</dt><dd>{result?.calibration?.status === "fitted" ? "Температурная" : result?.calibration?.status === "uncalibrated" ? "Без калибровки" : "—"}</dd></div>
            </dl>
            {observation.reusedFromStageId ? <p>Сохранённое наблюдение из этапа <code>{observation.reusedFromStageId}</code>.</p> : null}
            <details className="decision-observation__details">
              <summary>Распределение и профиль</summary>
              {result?.distribution && result.distribution.length > 0 ? (
                <>
                  <p>Вероятности относятся к заданному списку вариантов. Качество проверяется отдельно.</p>
                  {context?.kind === "score" ? <p>Score — среднее числовых уровней, взвешенное по этому распределению.</p> : null}
                  <table aria-label={`Распределение вариантов · ${stageNames.get(stageId) ?? stageId}`}>
                    <thead><tr><th scope="col">Вариант</th><th scope="col">Вероятность</th><th scope="col">Logit</th></tr></thead>
                    <tbody>{result.distribution.map((option) => (
                      <tr key={option.id}>
                        <th scope="row"><code>{option.id}</code><small>{context?.options.find((candidate) => candidate.id === option.id)?.description}</small>{context?.kind === "score" ? <small>Уровень: {String(context.options.find((candidate) => candidate.id === option.id)?.value ?? "—")}</small> : null}{option.id === result.selectedOptionId ? <small>Выбран моделью</small> : null}</th>
                        <td title={option.probability.toString()}>{probability(option.probability)}</td>
                        <td>{Number.isFinite(option.logit) ? decimal.format(option.logit) : "—"}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </>
              ) : null}
              <dl className="decision-observation__identity">
                <div><dt>Порог вероятности</dt><dd>{probability(result?.policy?.minProbability)}</dd></div>
                <div><dt>Отрыв от следующего варианта / порог</dt><dd>{marginPoints(result?.margin)} / {marginPoints(result?.policy?.minMargin)}</dd></div>
                <div><dt>Модель</dt><dd>{result?.model?.repository ?? "—"}</dd></div>
                <div><dt>Revision</dt><dd>{result?.model?.revision ?? "—"}</dd></div>
                <div><dt>Runtime</dt><dd>{result?.runtimeVersion ?? "—"}</dd></div>
                <div><dt>Профиль SHA-256</dt><dd>{profileSha256 ?? "—"}</dd></div>
                <div><dt>Вход SHA-256</dt><dd>{result?.inputSha256 ?? "—"}</dd></div>
              </dl>
            </details>
          </article>
        );
      })}
    </section>
  );
}
