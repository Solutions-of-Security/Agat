# Сверка cohort с prospective plan и targets

08.10.2026 MSK. [Evaluator CLI](../../../../../scripts/evaluate-decision-shadow-pilot.py)
принимает complete [cohort export](./shadow-pilot-cohort.md), заранее подготовленный
[plan v2](./shadow-pilot-plan.md), точные bytes профиля и **отдельные SHA всех трёх
файлов**. Output — новый private файл 0600. Источники анализа должны совпадать
с HEAD до и после расчёта. Сетевые обращения, inference и изменение process
state этим offline CLI не выполняются.

```bash
python3 scripts/evaluate-decision-shadow-pilot.py \
  --plan docs/private/pilot/plan.json --plan-file-sha256 PLAN_FILE_SHA \
  --cohort docs/private/pilot/cohort.json --cohort-file-sha256 COHORT_FILE_SHA \
  --profile docs/qualification/local-decisions/performance/profiles/runtime-0.12.3-wired-4096.json \
  --profile-file-sha256 786902a051c862a805197adfb7def29f4482df914f828a61768e64a07bca0e6e \
  --traffic-kind observed_workflow \
  --output docs/private/pilot/evaluation.json
```

## Что проверяется

Plan должен быть `ready_for_review`: все технические поля заполнены, начало
окна позже `preparedAt`, schema/config/profile/source seals валидны. Это не
подпись owners. Exact project/process/numeric version/half-open window/boundary
должны совпасть с cohort; capture time — после завершения окна и не в будущем.
Profile bytes и semantic fingerprint сверяются раздельно, в выбранном plan
identity mode. Перевод одного SHA в другой без проверки bytes не допускается.

Counts, все instance/run IDs и их порядок, creation/version, ordered run SHA,
отсутствие truncation, bounds, schemas и полный набор stage/assignment/caller
ledger IDs сверяются до SLI. Failed, cancelled, pending и replay не исключаются.
Report сохраняет bindings к plan и входным файлам. Server completeness —
**декларация authenticated snapshot exporter**, не подпись независимого
регистра запросов. SHA защищает сохранённые байты; он не доказывает отсутствие
изначально удалённых данных или подлинность произвольного файла с новым pin.
`cohortBindingVerified=true` означает успешную сверку этих declarations/bindings,
а `populationCoverageVerified` остаётся false.

## Знаменатель и сравнение

Используется существующий caller census: good — bound `ok`/`abstain` внутри
caller deadline, timely — тот же результат в пределах plan threshold. В
знаменателе все negotiated caller intents. Ошибки/timeout не исчезают;
pending/missing returns расширяют диапазон возможного результата и не получают
выдуманную длительность. Это диапазон **неизвестных исходов**, не статистический
confidence interval и не доказательство независимости наблюдений.

Кроме profile bindings проверяется timeout каждого assignment против plan.
Legacy/unnegotiated gaps, другой profile/timeout или ноль intents дают
`not_evaluable`. При известном знаменателе сравнение возвращает:

| Status | Условие |
|---|---|
| `conservative_target_met` | Даже lower bound не меньше предложенной цели |
| `target_not_met` | Даже upper bound меньше цели |
| `indeterminate` | Цель лежит между lower/upper bounds |
| `not_evaluable` | Нельзя подтвердить знаменатель/bindings, либо intents нет |

Общий `measurementStatus=insufficient_data` сохраняется при gaps, даже если
арифметический interval уже исключает достижение цели. Ratios/latency считаются
по всем данным cohort, до 1000 traces: quantiles не усредняются по страницам.
Обычный CLI ручных traces сохраняет default limit 32; пустой cohort обрабатывается
как ноль данных, а не синтетический успешный run.

`sloAccepted=false`, `agreementVerified=false`, routing false, qualification
`not_assessed` всегда. Logical intents не становятся физическими HTTP attempts;
разрешённость workload и полнота клиентского потока не подтверждаются. Для
синтетического опыта нужно явно выбрать `--traffic-kind diagnostic_fixture`:
report будет `diagnostic_only`, включая случаи недостигнутой цели.

Exit 0 — расчёт без gaps, в том числе известное недостижение цели; exit 2 —
insufficient data; exit 1 — нарушенные pins/schema/scope/source constraints.
Ошибки анализа сохраняются как failed receipt; существующий output или путь
вне `docs/private` отклоняются до анализа. Числовое достижение цели не закрывает
[согласование owner/SLO](./resident-service-acceptance.md) или предметное качество.

## Проверки

Девять новых tests проверяют good/failed/pending/missing/legacy/zero cohorts,
точные scope/version/time/profile pins, omitted run/stage ledgers, false
acceptance, timeout drift, private receipts и source drift. Корпус из 40 traces
считается целиком; default provided-trace limit 32 сохраняется.
Совместные regressions включают прежние SLI, caller inventory и pilot v2.
Synthetic tests не заменяют реальный permitted workflow, human reviews,
calibration или holdout измерение.
