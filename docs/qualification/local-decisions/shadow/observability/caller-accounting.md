# Учёт намерения и возврата shadow-вызова

07.10.2026. [История assignments](./assignment-history.md) показывает
назначенные проверки. Отдельный negotiated caller ledger сохраняет намерение
воркера вызвать LocalDecisionClient и подтверждённый возврат этого клиента.
Scope — операции выбранных traces. Полнота HTTP send attempts и клиентской
population остаётся неподтверждённой; routing и qualification выключены.

Воркер с настроенным decision URL объявляет capability
`decisionCallerAccounting=agat.decision.caller-accounting.v1`. Environment
labels не могут включить эту capability при отключённом клиенте. Coordinator
добавляет `callerAccountingVersion` и assignment UUID только при точном
совпадении capability. Старые и неизвестные capabilities сохраняют прежний
shadow path и явно отмечаются как unnegotiated.

После primary и bounded lease renewal воркер вызывает
`POST /api/v1/leases/:id/decision-shadow/intent` с version и assignmentId.
Coordinator проверяет worker identity, действующую lease/run, stageAttempt
и negotiated assignment. Запись выполняется в transaction; ownership
проверяется после row-lock и UPDATE. Истёкшая аренда откатывает изменение.
Первый receipt разрешает вызов (`mayInvoke=true`); повторный возвращает
false. Потерянный ACK не даёт повторному begin разрешить второй вызов.
Intent не доказывает, что HTTP bytes были отправлены после подтверждения.

Activity содержит отдельное `decisionShadowCallerAccounting` с schema
`agat.decision.caller-accounting.v1`, coverage и bounded assignments.
Каждый row сохраняет assignmentId, stageAttempt, negotiated, intent и
returned. Существующий assignment-history v1 не расширяется: старый
coordinator по-прежнему читает его точную схему. Caller return сохраняет
только validated status/reason и monotonic timing в той же transaction,
что observation/event. Historical durations не восстанавливаются по stage
age, lease TTL, scoring time или server timestamps.

`getRunTrace` возвращает readonly `decisionCallerAccounting` со schema
`agat.decision.caller-inventory.v1`, scope `caller_operation_intents`.
Данные взяты из того же SQL read, что observations и assignments.

| Outcome | Основание |
|---|---|
| returned | Intent и validated return сопоставлены с observation |
| intent_pending | Intent сохранён; текущая lease ещё активна без return |
| return_missing | Intent сохранён, ownership завершён без return |
| no_intent_recorded | Negotiated assignment не содержит intent receipt |
| unnegotiated | Capability caller accounting не согласована |
| data_gap | История, intent или return расходятся, включая старый writer |

No-intent и missing-return не объявляются фактическим HTTP timeout.
При worker death после intent остаётся неизвестный исход. То же происходит,
если HTTP результат получен, но воркер ушёл до сохранения observation.
При lost ACK shadow пропускается, primary завершается по прежним правилам.
Begin/record faults не превращают успешный primary в failed lease.
Dry-run/disabled не создают intent. Safe replay не создаёт новый caller;
live replay проходит обычное назначение.

[SLI CLI](../../../../../scripts/summarize-decision-shadow-sli.py) закрепляет
14 measurement/worker/API/validation sources. Новый caller census проверяет
точные поля, unique identities, порядок attempts, bindings, полноту stage
и assignment rows и согласованность return с observation. Legacy/old writer,
unnegotiated и truncated evidence не получают verified coverage.

`callerAccounting` содержит отдельный denominator сохранённых intents,
известные timely bound results и lower/upper interval для unknown returns.
Неизвестные returns не создают latency samples. Verified intent inventory
и return coverage — разные признаки. Даже полный provided ledger оставляет
`httpAttemptInventoryVerified=false`, `populationCoverageVerified=false`,
`sloAccepted=false`, `routingEnabled=false`, `qualification=not_assessed`.

[OpenTelemetry HTTP spans](https://opentelemetry.io/docs/specs/semconv/http/http-spans/)
различает операции клиента и конкретные попытки отправки. Здесь intent
сохраняется до операции клиента, поэтому не маркируется wire-level census.
[Google SRE](https://sre.google/workbook/implementing-slos/) связывает SLI
с определённым потоком и согласованной целью: [owner/SLO proposal](./resident-service-acceptance.md)
остаётся draft до назначения владельцев и подтверждения реальной нагрузки.

## Проверка

Целевые проверки охватывают negotiation, active ownership, rollback при
expiry во время SQL read/write и повторного begin, reopen/idempotency,
legacy/unknown worker, primary retry, cancellation, missing result,
некорректные receipts и сохранение primary при begin/record fault.
Полный npm test, typecheck и docs checks прошли: coordinator 343 tests
(315 pass, 28 opt-in skips), worker 149 / runtime 135 tests (по три штатных
skips), docs 718 Python tests (четыре opt-in skips) и 12 Node tests.
40 targeted coordinator, 36 SLI/CLI и три caller worker проверки — pass.

## Native proof

[Allowlisted summary](../evidence/2026-10-07/caller-accounting/result-summary.json)
закрепляет committed source и private archive. Финальный native прогон
прошёл восемь commands, десять traces и семь исполнений настоящего Python
worker через coordinator HTTP. Четыре backend calls выполнены управляемым
fixture; постоянный MLX resident не получил дополнительных inference.

Независимый read-only SQLite snapshot oracle подтвердил девять assignments,
семь intents и один сохранённый return. Outcomes: четыре return_missing,
по одному returned, no_intent_recorded, unnegotiated, data_gap и intent_pending.
Два SIGKILL выполнены после durable intent и после фактического caller return
до persistence. Lost intent ACK воспроизведён proxy, закрывшим соединение
после upstream 200. Duplicate/lost ACK не вызвали backend повторно, primary
сохранился. Авторизация и assignment isolation проверены по HTTP.

Старые coordinator и worker из 67 Git-pinned files записали bound result
и сохранили primary, не обновив caller ledger. Новый reader выдал legacy_gap;
stored marker не принят за доказательство полноты. Readonly traces сохранились
после reopen; исходный snapshot закреплён независимо.

Для пяти missing-return scenarios caller denominator равен пяти: один
известный timely result и четыре unknown дают interval 0.2–1.0. Mixed
семь intents дают interval 1/7–1.0. Caller latency samples — только
подтверждённые returns; fixture timing не является latency реального MLX.

Первый native прогон прошёл пять worker scenarios и остановился на lease
для legacy worker: default model router выбрал другой узел стенда. Повтор
явно отключил router только в diagnostic store. Failed receipt и исходники
первого прогона сохранены. Независимый audit прошёл девять checks; все 16
recorded temporary processes обоих прогонов отсутствуют, resident четыре
PID/34 dependencies и fresh scrape неизменны. Аудитор исправлен по фактическим
полям run stage output и resident snapshot; прежний failed audit сохранён.

Private ZIP содержит 158 files и два измеренных Git states, 81 999 387 bytes,
SHA-256 `b36386609476ea24437163f80a691e5258349d693adba4c39c2ce7a2ef16907c`.
Все CRC/SHA/size и копия в исходном workspace проверены. Public export
не содержит lease/run IDs, токенов, native paths, PID, prompt или state.

[PostgreSQL transaction gate](./caller-postgres.md) прошёл concurrent begin,
row/event-lock expiry, COMMIT uncertainty, restart и project isolation.
Следующая проверка — настоящий Python caller после unknown PostgreSQL COMMIT.
Actual boot/login, owner/SLO и independent human qualification остаются
открытыми.
