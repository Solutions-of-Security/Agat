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

Denominator — все предоставленные назначенные checks: ok, abstain, error,
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

[CLI](../../../../../scripts/summarize-decision-shadow-sli.py) требует
независимые SHA-256 каждого input и profile file, content profile SHA,
явные threshold и traffic kind. Восемь measurement/transport/validation
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
Regression требует valid trace ID уже до lease, совпадение dispatch trace
и неизменность полного saved trace после reopen. Формат identity согласован
с [W3C Trace Context](https://www.w3.org/TR/trace-context/#trace-id).

Методика good/total и необходимость согласования SLO взяты из
[Google SRE Implementing SLOs](https://sre.google/workbook/implementing-slos/).
Текущие targets в proposal выбраны для обсуждения по инженерным evidence.
