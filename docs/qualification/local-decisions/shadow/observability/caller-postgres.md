# Caller accounting: PostgreSQL transactions и expiry

07.10.2026. [Caller intent/return](./caller-accounting.md) проверен на
PostgreSQL 17.6 с двумя отдельными coordinator processes. Concurrent begin
разрешает один invocation; потерянный COMMIT ACK сохраняет неизвестный
для HTTP caller исход, даже если запись уже durable. Routing, qualification
и owner/SLO agreement остаются выключенными или неподтверждёнными.

Первый native gate выявил ошибку readonly inventory: наличие `lease_id`
считалось активным ownership без проверки `lease_expires_at`. После expiry
API уже отклонял begin/return, но trace ещё показывал `pending` и
`intent_pending` до maintenance. Два SQLite regression tests failed до fix;
сырые PostgreSQL rows воспроизвели blind spot на трёх SQL boundaries.

Reader теперь берёт expiry из того же SQL read, что activity/history,
и сравнивает его с одним временем наблюдения после чтения. При истёкшем
ownership assignment получает `ended_without_observation`, intent без
return — `return_missing`. Stored stage может ещё иметь status `running`:
это состояние lifecycle до maintenance, а не разрешение выполнить callback.
Изменение readonly; activity, observation, event и исторические durations
не переписываются. Известный return сохраняет outcome `returned`.

По [PostgreSQL row-lock semantics](https://www.postgresql.org/docs/17/explicit-locking.html)
операция может ждать другую transaction. Поэтому ownership/deadline
перепроверяются после ожидания и перед завершением записи. Использование
[одного checked-out client для transaction](https://node-postgres.com/features/transactions)
позволяет сохранить intent/observation/event вместе или откатить изменения.

## Проверяемые сценарии

| Сценарий | Проверка |
|---|---|
| Два concurrent begin по HTTP | Два 200 receipts, ровно один `mayInvoke=true` |
| Return и повторный callback | Одна observation, один event; persisted return неизменен |
| Restart и project isolation | Trace совпадает после нового store; другой tenant не читает stage |
| Stage row-lock до expiry | HTTP 400, intent не записан, activity/event неизменны |
| Duplicate intent row-lock до expiry | HTTP 400, сохранённый intent не изменён, return_missing |
| Event table-lock до expiry | HTTP 400, return/observation/event откатываются вместе |
| Intent COMMIT: disconnect/withhold | Реальный COMMIT выполнен; HTTP 503; повторный begin не разрешает call |
| Return COMMIT: disconnect/withhold | Return и event durable; HTTP 503; повтор не создаёт запись |

Loopback PostgreSQL wire proxy пропускает COMMIT и удерживает его фактический
успешный server reply для выбранного connection/application. Затем он
разрывает соединение или удерживает ACK до bounded timeout. Это проверяет
commit uncertainty, а не отказ до записи. Independent connection читает
committed SQL до ответа HTTP. Auth bytes и SQL payloads proxy не сохраняет.

Во всех четырёх unknown-COMMIT сценариях primary завершён через независимый
parent store. На этом этапе продолжение настоящего Python worker после
PostgreSQL commit uncertainty ещё не проверено. Следующий gate закроет
именно эту границу. Synthetic unavailable callback с duration 1000 ms —
контрактный fixture, не измерение LocalDecisionClient или MLX latency.

## Доказательства и ограничения

42 targeted coordinator tests и полный coordinator suite прошли:
345 tests, 317 pass, 28 opt-in skips. Typecheck прошёл.
Docs checks прошли: 718 Python tests (четыре opt-in skips), 12 Node tests,
2410 local link targets и process catalog.
Оба native прогона прошли по 8 targeted и 108 Fleet/HA PostgreSQL tests.
Измеренные sources до/после fix и failing-before evidence сохранены отдельно.
Независимый audit прошёл 11 checks по 48 raw case snapshots, 77 committed
contributors и 49 compiled files каждого command. Все 226 recorded
temporary processes отсутствуют, четыре собственных containers удалены.
35 frozen resident/session sources, четыре permanent processes,
34 dependencies, fresh scrape и counters 1/0/0 неизменны.

[Публичная сводка](../evidence/2026-10-07/caller-postgres/result-summary.json)
содержит counts, committed sources и fingerprints. Private ZIP: 284 files,
два измеренных Git states, 83 376 032 bytes, SHA-256
`f2b649c65e489c847ce8f1d62d83ba4889d2245cb0752979a32464558548c78e`.
Все CRC/SHA/size и идентичная копия в исходном workspace проверены.
Raw IDs, credentials, native paths, PID, prompt и state не экспортированы.

Permanent resident не используется для inference в этом gate. Actual
boot/login, реальный customer denominator, владельцы/SLO и independent
human qualification остаются открытыми.
