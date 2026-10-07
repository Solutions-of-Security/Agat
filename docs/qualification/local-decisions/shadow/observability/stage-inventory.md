# Inventory сохранённых shadow stages

07.10.2026. [Caller SLI](./caller-sli.md) рассчитывает записанные observations.
Новый readonly inventory позволяет отдельно увидеть назначенные stages,
которые ещё не записали observation или завершились без него. Это census
сохранённых стадий выбранных traces; каждый retry HTTP и весь реальный
клиентский поток им не подтверждаются. SLO, routing и qualification не
принимаются этим этапом.

В `getRunTrace` добавлен `decisionStageInventory` со schema
`agat.decision.shadow-stage-inventory.v1`, scope `stored_shadow_stages` и
`stages`. Inventory и прежний `decisionObservations` строятся из одного
SQL read. Учитываются stages с shadow config, lease или observation.
Row содержит stageId, stageStatus, assigned, observationRecorded,
profileSha256, inputSha256 и callerTimeoutMs. State, profileJson, leaseId,
node identity и synthetic model result в inventory не добавляются.

| Состояние | Census |
|---|---|
| Config есть, lease ещё нет | Unassigned stage, исключён из assigned denominator |
| Lease есть, observation записано | Recorded assigned stage |
| Lease есть, observation нет, stage running/queued/pending/awaiting | Pending assigned stage; outcome неизвестен |
| Lease есть, observation нет, stage completed/failed/cancelled | Missing terminal result, известное отсутствие записанного результата |
| Safe replay или safe-replay-unavailable | Отдельный replay count, новый HTTP attempt не приписывается |

Worker делает shadow после primary и renewal. Assignment не доказывает
начало HTTP, а возраст stage не заменяет caller timing. Terminal stage
без observation может завершиться до HTTP; он не превращается в
synthetic `timeout` или `backend_error`. Обычный `completeLease` по-прежнему
записывает существующий missing_result; новые readonly fields не меняют
workflow, worker и serving profile.

[Census](../../../../../scripts/lib/decision_stage_inventory.py) требует
точную схему, уникальные run/stage IDs, enum status и strict Boolean markers.
Recorded observations должны полностью входить в inventory; marker,
profile/input/deadline должны совпасть. Duplicate pending stages между
inputs, ложные markers, missing rows, extra fields, Boolean/numeric aliases
и противоречивые bindings дают failed receipt.

[SLI CLI](../../../../../scripts/summarize-decision-shadow-sli.py) сохраняет
прежние caller ratios/quantiles и добавляет `stageInventory`. Assigned-stage
denominator включает recorded assigned, missing terminal и pending;
unassigned/replay остаются отдельными counts. Lower учитывает подтверждённые
bound results; upper консервативно допускает неизвестный outcome pending
и legacy binding/timing. Functional bound ratio и timely ratio разделены.
Ошибки не создают successful numerator. Отсутствующее caller timing не
генерируется по scoring или stage age.

Pending даёт `pending_assigned_stages` и overall insufficient_data,
хотя inventory покрывает все предоставленные stored stages. Legacy trace
без inventory получает `missing_stage_inventory`; старый observation ratio
сохраняется, stage coverage остаётся unknown. Profile/binding gaps также
видны. Нулевая denominator даёт null. `storedStageCoverageVerified` относится
только к предоставленному inventory; `httpAttemptInventoryVerified=false`
и `populationCoverageVerified=false` обязательны. Последняя сохранённая
lease может перезаписываться при retry, поэтому stage census не является
полной историей всех HTTP attempts.

Current CLI закрепляет девять measurement sources, включая census helper.
Profile/input files, private exclusive output, source recheck и traffic kind
сохраняют прежние guards. Historical caller proof использовал восемь sources
на своём зафиксированном commit; его raw evidence не пересчитано молча.
[Owner/SLO proposal](./resident-service-acceptance.md) остаётся draft до
согласования владельцев, scope и реального потока.

До изменения реальный SQLite coordinator сохранил три назначенных stages:
completed с observation, cancelled без observation и running без observation.
Прежний trace экспортировал один observation, прежний CLI — ratio 1/1,
с явным populationCoverageVerified=false. Captured database создан через
`VACUUM INTO`, read-only rows сверены с live rows: consistent snapshot
содержит все три stages. [SQLite documentation](https://www.sqlite.org/lang_vacuum.html#vacuum_with_an_into_clause)
описывает transactional snapshot этого метода. Этот опыт — controlled
fixture, он не свидетельствует о частоте отказов на клиентском потоке.

## Native proof и повторная проверка

Два committed source states прошли по восемь native-команд. В каждом повторе
настоящий SQLite coordinator сохранил восемь traces: computed, cancelled без
observation, failed после трёх primary lease assignments, missing_result
после completion, safe replay, live replay, assigned running и unassigned
queued. Два настоящих LocalDecisionClient calls использовали контролируемый
HTTP backend fixture. Primary failure произошёл до shadow HTTP; три retry
оставили один failed stored stage. Это подтверждает ограничение stage census.

Consistent `VACUUM INTO` snapshot независимо сверена с trace и raw SQL rows;
reopen сохранил traces полностью. После capture pending/queued jobs отменены.
Settled CLI дал exit 0, mixed/pending-only/legacy/replay-only — exit 2,
contradictory marker и duplicate pending — exit 1. Functional caller ratio
settled равен 2/2; assigned-stage ratio — 2/4, поскольку две terminal stages
не записали observation. Mixed inventory содержит шесть assigned stages,
три recorded, два terminal missing и один pending: stage ratio 2/6 → 3/6.
Pending-only даёт interval 0 → 1 без caller sample; legacy trace сохраняет
прежнее observation ratio и получает missing_stage_inventory.

Independent native audit прошёл восемь checks, before audit — 22. Перепроверены
raw SHA, десять native sources, девять CLI sources, отдельные caller/scoring
quantiles, SQLite rows, fallback/replay и все recorded temporary PID.
Постоянный resident сохранил четыре PID, 34 dependencies и fresh scrape;
computed/rejected/failed counters остались 1/0/0 без новых inference.

24 coordinator targeted checks, полный coordinator набор 327 tests
(299 pass, 28 optional skips), полный npm test/typecheck и 713 Python /
12 Node docs checks прошли. Дополнительный regression проверяет, что reason
сам по себе не превращает computed observation в safe replay. Первый
успешный повтор, ранние неудачные fixtures и их frozen sources сохранены.
Все 170 files private ZIP, включая три Git source states, проверены по
CRC/SHA/size; идентичная копия сохранена в исходном workspace `docs/private`.
[Allowlist summary](../evidence/2026-10-07/stage-inventory/result-summary.json)
не экспортирует native paths, run/stage identities, state, prompts или PID.

Дальнейший telemetry gate — отдельная история shadow lease assignments и
caller attempts при retry; существующий stage census и provided traces
не дают полной production denominator. Owner/SLO, фактический boot/login
и human reviews/calibration/holdout остаются открытыми.
