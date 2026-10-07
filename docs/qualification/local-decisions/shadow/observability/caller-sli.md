# Caller timing и описательные SLI

07.10.2026. Это средство измерения предоставленных coordinator traces.
Оно не назначает владельцев, не принимает SLO и не включает routing.
[Owner/SLO proposal](./resident-service-acceptance.md) остаётся draft до
согласования сценария, нагрузки, owners и targets.

Новый lease объявляет `callerTimingVersion=agat.decision.caller-timing.v1`.
Worker измеряет monotonic interval от входа `LocalDecisionClient.decide`
до возврата после response parsing, закрытия транспорта и завершения
watchdog. Измерение относится к `local_http_call`: оно включает валидацию
lease и HTTP overhead, но не lease renewal, запись observation или полный
primary workflow. `result.durationMs` остаётся отдельным временем scoring.
Deadline модели и serving profile этим изменением не меняются.

Для старого lease или неизвестной версии worker возвращает прежний
envelope. Новый coordinator принимает старый envelope без timing; новое
поле принимается только после negotiation, с точной схемой, границей и
finite nonnegative числом. Malformed metadata даёт `invalid_response` с
primary fallback. Корректное timing сохраняется также у timeout, busy,
cancelled и результатов, отвергнутых coordinator validation. Safe replay
хранит исходное timing с provenance, но не является новым HTTP attempt.

Trace теперь экспортирует `inputSha256` и `callerTimeoutMs` из сохранённого
lease рядом с `profileSha256`. Это fingerprints и deadline, а не исходный
текст. Анализатор сверяет profile fields, result/stage identity, input pin
и recorded result/status/reason. Boolean не приравнивается к числу; JSON
представления `1` и `1.0` совместимы. Старый trace без lease bindings
получает явный data gap.

Denominator — сохранённые `decisionObservations` предоставленных traces: ok, abstain, error,
unavailable, missing result и timeout. Быстрая ошибка не улучшает долю
своевременных валидных результатов. Disabled/unsupported/invalid-input
checks без lease, safe replay и safe-replay-unavailable учитываются
отдельно. Повтор одного run/stage в inputs отклоняется до расчёта.

`boundResultRatio` описывает ok/abstain с совпавшими доступными bindings;
при недостающих bindings это не подтверждённая валидность всего потока.
`withinCallerDeadlineRatio` считает такие ответы в пределах recorded
caller deadline. `timelyBoundResultRatio` дополнительно применяет явный
latency threshold. Для неизвестного timing/deadline возвращается интервал
lower/upper; scoring latency не заменяет caller latency. Late response
остаётся в denominator. Нулевая denominator даёт null, а не 100%.

Nearest-rank p50/p95/max считаются отдельно для известных caller timings
всех исходов и для recorded scoring timings. Они сопровождаются sample
count. Missing timing, missing lease bindings, profile drift и truncated
trace дают `insufficient_data`. Scope всегда `provided_traces_only`;
`populationCoverageVerified=false`: полноту реального клиентского потока
невозможно установить по произвольно выбранным trace exports.
Этап, отменённый или ещё работающий до записи observation, может отсутствовать
в этом массиве. Поэтому текущий CLI не подтверждает полный denominator
назначенных attempts. [Stage inventory](./stage-inventory.md) расширяет
представление такими stages и отдельно отражает pending outcomes.

[CLI](../../../../../scripts/summarize-decision-shadow-sli.py) требует
независимые SHA-256 каждого input и profile file, content profile SHA,
явные threshold и traffic kind. Девять measurement/transport/validation
sources должны совпадать с HEAD до и после расчёта. Новый sealed output
создаётся только в `docs/private`, с mode 0600; прежний не перезаписывается.
Source drift или нарушенный pin дают failed receipt и exit 1. Недостаток
данных даёт exit 2. Diagnostic fixture сохраняет `diagnostic_only` даже
при идеальных ratios. `sloAccepted=false`, `routingEnabled=false` и
`qualification=not_assessed` обязательны во всех случаях.

```bash
python3 scripts/summarize-decision-shadow-sli.py \
  --trace docs/private/pilot/trace.json \
  --trace-sha256 '<independently-recorded-file-sha256>' \
  --profile docs/qualification/local-decisions/performance/profiles/runtime-0.12.3-wired-4096.json \
  --profile-file-sha256 786902a051c862a805197adfb7def29f4482df914f828a61768e64a07bca0e6e \
  --profile-sha256 776fba7fa1244e13292ac3605a53a6d6c6cf557901c8d863cab9489d6d2f47ce \
  --latency-threshold-ms 5000 --traffic-kind observed_workflow \
  --output docs/private/pilot/caller-sli.json
```

Для нескольких run повторяются обе пары `--trace`/`--trace-sha256` в одном
порядке. Максимум 32 files по 16 MiB, всего 128 MiB; профиль до 1 MiB.
Диагностические, warmup и реальные workflow exports нужно подавать
отдельными measurements; CLI не может достоверно определить их происхождение
по телу JSON и сохраняет явно заявленный traffic kind.

Native reopen проверка обнаружила прежний дефект `startProcessLocked`:
новый run имел пустые trace/root-span IDs, startup migration назначала их
только при следующем открытии SQLite и меняла manifest hash. Run теперь
получает root identity через существующий coordinator telemetry до
dispatch; она сохраняется вместе с run. При ошибке создания span закрывается.
Внешняя транзакция также учитывает созданные process root spans: post-start
SQL failure или неуспешный commit закрывает их, успешный commit оставляет
span на lifecycle run. Реальный in-memory OpenTelemetry exporter regression
воспроизводит post-start rollback без DB run и требует завершённый failed
span; до fix exporter не получал его.
Regression требует valid trace ID уже до lease, совпадение dispatch trace
и неизменность полного saved trace после reopen. Формат identity согласован
с [W3C Trace Context](https://www.w3.org/TR/trace-context/#trace-id).

## Проверенный native протокол

[Allowlist summary](../evidence/2026-10-07/caller-sli/result-summary.json)
сохраняет результаты опыта на measurement commit
`81c30f0bc49f3aec131cc52afcd125829221daad`. Выполнены 10 client steps через
настоящий loopback HTTP с явно контролируемым fixture backend, сохранены
12 run traces в обычном SQLite coordinator. Computed, abstain, backend
error, busy, timeout, cancellation, nonlistening-port fault, injected
input mismatch, legacy response и live replay сохранили primary output.
Safe replay сохранил исходное timing с provenance без нового HTTP call.
После close/reopen весь trace, включая manifest, остался неизменным.

Четыре pinned CLI measurements дали exit 0/2/2/1. Набор с известным
timing содержит девять observations: два ok, один abstain, один error и
пять unavailable; один safe replay исключён из новых calls. Известный
timely numerator — три, denominator — девять. Caller p50/p95/max для
всех исходов — 49,063 / 1000,657 / 1000,657 ms, scoring p50/p95/max для
четырёх computed/error результатов — 31,441 / 34,562 / 34,562 ms. В
длинном caller sample находится специально вызванный network timeout;
быстрая ошибка не стала успешным latency observation.

Добавление legacy и missing-result traces даёт 11 observations, девять
known timings и два unknown, `insufficient_data` / exit 2. Replay-only
measurement имеет нулевую denominator, null ratios и exit 2. Повтор одного
run/stage отклонён с failed receipt / exit 1. Никакой из этих measurements
не принят за real workflow SLO или предметную qualification.

Независимый audit прошёл девять checks: raw file pins, committed/current
восемь source bindings, timing boundaries, counts/ratios/nearest-rank
quantiles, stored result/profile/input bindings, replay/fallback и cleanup.
11 PID финальной попытки отсутствуют; ещё 17 recorded PID из первых двух
попыток и 11 из прежнего успешного повтора также завершены. Resident сохранил четыре PID, 34 dependency pins,
profile/registration, fresh scrape и counters 1/0/0 без новых MLX calls.

Первая попытка сохранила ошибочное ожидание одного TCP errno для bound
nonlistening socket: actual caller вернул timeout. В отдельном повторе
сценарий принимает наблюдаемые unreachable/timeout; это не изменение
worker поведения. [Python socket documentation](https://docs.python.org/3.13/library/socket.html#timeouts-and-the-connect-method)
описывает timeout на connect и возможность отдельного timeout OS stack;
конкретная причина данного errno не установлена. Вторая попытка выявила
пустой process trace ID; failing regression воспроизведён до product fix,
21 targeted checks и полный coordinator набор 324 tests после него прошли.
Дополнительный exporter regression выявил незавершённый process root span
при rollback внешней транзакции. Исправленный final набор — 22 targeted,
325 coordinator tests (297 pass, 28 opt-in skips); оба воспроизведённых
product faults и все неуспешные test fixtures сохранены. Финальный native
повтор выполнен уже на committed rollback fix.

Полный `npm test` прошёл; после trace fix весь coordinator набор перепроверен.
Worker — 146 tests / три optional skips, runtime — 135 / три optional skips,
web — 61, Temporal/replay и process pack — pass. Полная typecheck прошла;
после trace fix coordinator typecheck повторена. SLI targeted — 18,
docs — 700 Python / четыре Docker opt-in skips и 12 Node checks, links и
process catalog — pass. Необязательные native Docker scenarios выполняются
отдельными CI integration jobs, а не приписываются локальному тесту.

Все четыре попытки, raw traces/wire metrics, failed/passed regression logs,
audits и три measured Git sources сохранены в private архиве:
174 files, 123 015 376 bytes, SHA-256
`c2e8b58f91eb4e3d591c9a159e83f6fe42ce9898a612b2ba7e6c70b36c1c4fc5`.
Каждый file SHA/size и CRC проверены; идентичная копия находится в
исходном workspace `docs/private`. Следующие gates — assigned-attempt
inventory, фактический boot/login, согласование owner/SLO и независимые
business data/reviews/calibration/holdout.

Методика good/total и необходимость согласования SLO взяты из
[Google SRE Implementing SLOs](https://sre.google/workbook/implementing-slos/).
Текущие targets в proposal выбраны для обсуждения по инженерным evidence.
