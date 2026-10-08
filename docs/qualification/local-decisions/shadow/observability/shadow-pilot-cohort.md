# Полный сохранённый cohort shadow-пилота

08.10.2026 MSK. Новый authenticated endpoint:

```text
GET /api/v1/processes/{processId}/decision-shadow-cohort
    ?processVersion=1&startAt=2026-09-29T00:00:00.000Z&endAt=2026-09-30T00:00:00.000Z
```

Project берётся из auth scope; доступ соответствует защищённому run trace.
Process/version должны существовать в этом проекте, иначе 404. Версия —
положительное safe integer из `process_versions.version`. Query принимает
только три указанных поля, по одному разу. Фильтры status/run IDs/project
не допускаются. Все сохранённые экземпляры этой версии с
`created_at >= startAt AND created_at < endAt` входят в результат, включая
failed, cancelled, queued, running и replay. Порядок — creation timestamp / ID.
Окно UTC, до семи дней, полностью завершённое; незавершённые **запуски** в
завершённом окне включаются. Instance/run/project bindings сверяются.

## Snapshot и bounds

Version lookup, экземпляры и stages читаются из одной транзакции:
SQLite `BEGIN` удерживает read snapshot, PostgreSQL использует
`BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY`. Bridge передаёт режим до
первого tenant/timeout SELECT; раньше он всегда открывал обычный BEGIN.
[Read Committed PostgreSQL](https://www.postgresql.org/docs/current/transaction-iso.html)
допускает разные снимки последовательных запросов; режим нужно задавать до
запросов, согласно [SET TRANSACTION](https://www.postgresql.org/docs/current/sql-set-transaction.html).
Поведение SQLite описано в [read transactions](https://www.sqlite.org/lang_transaction.html).
Сетевые обращения и inference внутри snapshot не выполняются.

Лимиты: 1000 экземпляров, 10 000 stages, 16 MiB исходного activity JSON и
16 MiB сериализованного результата. Превышение, malformed activity/ledger,
неизвестные observation/result fields или ошибка transaction дают отказ
целиком. Partial result с complete=true не возвращается. Чтение не вызывает
refresh evals, запись events или изменение process state. Используются
существующие process/created и unique run/position indexes; новых schema
migrations или индексов без замера необходимости не добавлено.

`storedCohortComplete=true` относится только к перечисленным сохранённым
экземплярам под заданными bounds. Обращения, не создавшие instance, удалённые
записи, пригодность workload и поведение вне окна не доказаны:
`populationCoverageVerified=false`, `eligibleWorkloadVerified=false`,
`httpAttemptInventoryVerified=false`. Пустой cohort — census с нулём, не
успешный SLO. SLO, owners, qualification и routing этим export не утверждаются.

## Поля и продолжение

Schema `agat.decision.shadow-cohort.v1` содержит snapshot ID/time, exact scope,
instance/run inventory, статусы/replay, counts и SHA ordered run IDs. Каждый
run содержит stage inventory, assignment history, caller accounting и
observations в schemas обычного trace. Общий helper сохраняет одинаковую
трактовку ledger в обеих операциях. Caller intents/pending/missing returns
и legacy/replay gaps не удаляются.

Не экспортируются state/input, system prompt, question, описания вариантов,
текст основного output, events и artifacts. Сохраняются decision profile/result
metadata: option IDs, вероятности, числовые/boolean values, timings, input hashes
и machine reasons. Их также следует хранить приватно: endpoint не подтверждает
разрешение на распространение данных. Malformed metadata прерывает export.

Реализован [offline evaluator](./shadow-pilot-evaluation.md): independent SHA всего export, exact scope
с **prospective plan**, полный набор run IDs и профиль, затем caller ratios/gaps.
Старый CLI произвольных traces сохраняет предел 32 и provided_traces_only;
он не превращает ручную выборку в полный cohort.

## Проверки

Семь новых SQLite/HTTP checks и пять ledger regressions — 12 passed: statuses/
replay, UTC/version/project, равенство trace ledger, отсутствие task/primary
текста, row/byte bounds, malformed данные, rollback, auth/query fields.
Python pilot v2 / публичный importer — 18 passed. Весь coordinator — 324 passed
/ 28 optional integration skips; отдельный Docker PostgreSQL 17.6 test прошёл
без skips. Данные во всех этих checks явно синтетические, model calls — ноль.

В PostgreSQL настоящее второе соединение изменило stage status и переместило
instance за границу окна между SELECT экспортера. Первый snapshot сохранил
исходные membership/status; следующий export увидел новую границу. Проверены
repeatable read / read_only=on, отказ записи и освобождение соединения после
rollback, RLS чужого tenant. Постоянный resident не менялся.

Полный docs check прошёл: 777 Python tests / четыре optional skips,
12 Node checks и каталог 7 категорий / 13 шаблонов. После добавления этого
протокола повторная link-проверка сверила 2467 targets в 278 Markdown files.
Coordinator typecheck — pass.
