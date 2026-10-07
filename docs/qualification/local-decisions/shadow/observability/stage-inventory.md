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
