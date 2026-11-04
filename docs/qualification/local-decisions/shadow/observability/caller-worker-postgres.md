# Python caller recovery после неизвестного PostgreSQL COMMIT

07.10.2026. [PostgreSQL transaction gate](./caller-postgres.md) завершал
primary через независимый store. Новый gate выполняет настоящую
`_execute_lease_body` на Python: begin/return теряет успешный COMMIT ACK,
worker завершает primary через тот же coordinator и исполняет следующую
lease в том же process.

Primary заменён фиксированным fixture text; retrieval отключён только в
probe. `CoordinatorClient`, LocalDecisionClient, renewal, observation и
completion используют действующий код и реальные loopback HTTP/SQL.
Backend возвращает controlled busy. Это проверка восстановления и
persistence, не MLX latency, качество модели или клиентский SLO.

Probe приостанавливается перед выбранным intent/return. PostgreSQL wire
proxy включается в этой точке и удерживает фактический successful COMMIT
reply. Так fault относится к caller transaction, а не к более раннему
model event. Далее проверяются disconnect и withhold до bounded timeout.

| Операция с потерянным ACK | Первый caller | Следующая lease |
|---|---|---|
| Intent, disconnect/withhold | HTTP 503; backend не вызван; return_missing | Новый intent, один HTTP backend call, persisted busy return |
| Return, disconnect/withhold | HTTP 503; return/event уже durable; backend не повторён | Новый intent и один отдельный HTTP backend call |

Во всех случаях два primary completions идут через тот же coordinator.
Worker не сообщает failed lease и не повторяет unknown caller operation.
Renewers и watchdog завершены до следующей команды; process завершается
штатно после двух leases. По [HTTP retry semantics](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.2)
повтор POST требует знания его семантики; worker сохраняет ограниченный
shadow path и завершает primary при отсутствующем receipt.

Первый probe назначал HTTP 200 каждому успешному request и терял actual
renewal status 204. Два реальных HTTP regression tests выявили это:
204 failed, 503 passed. Исправленный observer читает `response.status`
и HTTPError code из действующего CoordinatorClient frame. Статус без
полученного HTTP response остаётся null; success code не подставляется.
Повтор обеих regressions на committed source прошёл. Оригинальный native
прогон и failing-before evidence сохраняются отдельно.

## Проверка

Docs checks прошли: 718 Python tests (четыре opt-in skips), 12 Node tests,
2415 local link targets и process catalog. Первый запуск в sandbox не мог
создать loopback servers; его failed log сохранён, native repeat прошёл.

Исходный и исправленный прогоны прошли по 12 targeted и 112 Fleet/HA tests.
Независимый audit прошёл 11 checks: 80 raw SQL snapshots, 78 committed
contributors и 49 compiled files на command. В двух исправленных commands
восемь настоящих Python workers завершили 16 leases: 16 intents,
12 returns, четыре missing returns и 12 actual backend HTTP calls.
У missing return после потерянного intent ACK backend не вызывался;
длительность такого call не создаётся.

Все 258 записанных временных процессов отсутствуют, четыре собственных
PostgreSQL containers удалены. Permanent resident сохранил четыре PID,
34 dependencies, 35 frozen sources, fresh scrape и counters 1/0/0.

[Публичная сводка](../evidence/2026-10-07/caller-worker-postgres/result-summary.json)
содержит только counts, hashes и repository contributors. Private ZIP:
326 files, два измеренных Git states, 83 654 786 bytes; SHA-256
`0b6b04f67737700e04d6fac2070482b4fa0449b6b9fe0f41e94b1bdf70728a40`.
CRC, SHA и размер каждого файла, inventory и идентичная копия в исходном
workspace проверены. Первоначальный probe, failing HTTP 204 regression,
исправленный repeat и источники сохранены отдельно.

Optional SLI bridge выявил другую границу: coordinator закрепляет точные
bytes `profileJson`, а SLI CLI ожидает runtime fingerprint разобранного JSON.
Whitespace и literal `1.0` сохраняют другой SHA при одинаковом содержимом.
Raw profile и actual CLI rejection сохранены; следующий этап добавит
явный identity mode без переписывания historical hashes. Последующий
[configured profile identity gate](./caller-profile-identity.md) проверил
этот режим на native SQL traces и сохранил historical default metrics.

Owner/SLO agreement, customer population, actual boot/login и независимая
human qualification остаются неподтверждёнными. Routing выключен.
