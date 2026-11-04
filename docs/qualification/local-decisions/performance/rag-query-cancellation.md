# Отмена query embedding до primary

Дата: **28.09.2026**. Продолжение [прерывания primary HTTP](./primary-http-cancellation.md). Этот gate проверяет RAG retrieval внутри обычной process lease, до вызова основной модели.

## Дефект и изменение

[Baseline](./evidence/2026-09-28/rag-query-cancellation/baseline.log) воспроизвёл `Query embedding HTTP remained open after lease renewal rejection`. Первый RAG stage уже завершился. Embedding второго поискового запроса оставался заблокированным после Temporal/application cancellation и настоящего отказа lease renewal: `retrieve_knowledge` не передавал cancellation event в `embed`.

[Worker](../../../../workers/agat_worker.py) теперь передаёт event обычной process lease через `retrieve_knowledge` в каждый query embedding. Используются существующие cancellation paths isolated и session transport; общий HTTP helper не менялся. До retrieval, между группами запросов и до/после coordinator search проверяется актуальность отмены. Поздний результат поиска не попадает в primary. Прямой вызов `complete` также передаёт event из своего context; без cancellation поведение прежнее.

При отмене активный embedding helper завершается и собирается до возврата из вызова. Session pool создаёт новый helper для следующего запроса; отменённый helper не возвращается в работу. Это относится к обычным process leases: cancellation индексационных knowledge leases уже была реализована раньше.

## Живой сценарий

[Общий Temporal RAG fixture](../../../../apps/coordinator/test/temporal-rag.integration.test.ts) дополнен `query-interrupt` для SQLite/PostgreSQL и isolated/session embeddings:

1. Два документа проиндексированы; первый primary output, shadow и retrieval provenance приняты.
2. Второй query embedding удерживается до ответа. Уже проверены потерянный tick reply и SIGKILL/replacement Temporal worker; завершённый первый stage не выполняется повторно.
3. Workflow и application отменяются с повторной cleanup Activity после потерянного ответа. Fixture ждёт реальный renewal с периодом 45 секунд, его отказ и закрытие embedding-соединения.
4. Worker сообщает `Embedding request cancelled`. Второй primary request отсутствует, второй stage имеет cancelled/NULL output, новый shadow и третий stage не появились.
5. Тот же worker с `--concurrency 1` завершает следующий трёхэтапный RAG, пока прежний embedding response всё ещё удержан. Trace отменённого процесса не меняется. История проходит native replay без нового I/O; workers и coordinator завершаются штатно.

В отменённом процессе — **1 primary request**, **4 отправленных embedding items**, **1 shadow observation**, **1 retrieval record** и **2 source references**. Четыре embedding inputs включают два документа, первый успешный query и второй отменённый query; это не четыре принятых результата. После последующего успешного процесса суммарно отправлены **4 primary requests** и **7 embedding items**.

PostgreSQL запускается после migration/admission с runtime roles. Собственный tenant видит `[1 run, 1 instance, 1 retrieval, 2 chunks]`, чужой проект — нули; доступ tenant к release registry остаётся запрещён. Schema **30** и Temporal Workflow commands не менялись.

## Проверки

- [15 targeted tests](./evidence/2026-09-28/rag-query-cancellation/query-tests.log) и [полный worker regression](./evidence/2026-09-28/rag-query-cancellation/worker.log): **137 pass** с настоящим LangGraph **1.2.11**. Проверены оба transport, headers/body/error wait, освобождение процесса и повторный запрос, передача event из worker, отмена между группами, pre-cancel и отказ позднего результата поиска до primary.
- **18/18** живых SQLite Temporal сценариев и **8/8** PostgreSQL RAG сценариев прошли. Шесть дополнительных Node file entries без совпавшего test name не считаются RAG сценариями.
- [SQLite Temporal](./evidence/2026-09-28/rag-query-cancellation/sqlite.log) и [PostgreSQL RAG](./evidence/2026-09-28/rag-query-cancellation/postgres.log) сохраняют старые recovery/late-response/primary-interruption scenarios и добавляют query interruption.
- Evidence четырёх новых границ: [SQLite isolated](./evidence/2026-09-28/rag-query-cancellation/sqlite/isolated-query-interrupt.json), [SQLite session](./evidence/2026-09-28/rag-query-cancellation/sqlite/session-query-interrupt.json), [PostgreSQL isolated](./evidence/2026-09-28/rag-query-cancellation/postgres/isolated-query-interrupt.json), [PostgreSQL session](./evidence/2026-09-28/rag-query-cancellation/postgres/session-query-interrupt.json). Поле `interruption.nextTrace` содержит следующий успешный процесс; `cancelledStageNeverCalledPrimary` подтверждено счётчиком настоящего model HTTP fixture.
- [Coordinator regression](./evidence/2026-09-28/rag-query-cancellation/coordinator.log): **293 pass, 18 gated skip**; gated cases выполняются в live suites. Остальные проверки: [34 native replay histories](./evidence/2026-09-28/rag-query-cancellation/replay.log), [workspace typecheck](./evidence/2026-09-28/rag-query-cancellation/typecheck.log), [strict fixture typecheck](./evidence/2026-09-28/rag-query-cancellation/test-typecheck.log), [docs](./evidence/2026-09-28/rag-query-cancellation/docs.log) и [SHA manifest](./evidence/2026-09-28/rag-query-cancellation/checks.json).

```bash
npm run test:worker
npm run fleet:test-temporal-rag
npm run fleet:test-temporal-postgres-rag
npm run test:temporal
```

Это synthetic проверка корректности. Renewal по-прежнему периодический, с интервалом 45 секунд; измерения после замеченного отказа включают polling fixture и не являются SLO. Отмена клиентского запроса не доказывает прекращение вычислений embedding server. Принятый ранее output и provenance сохраняются; предметная qualification не оценивалась.

Следующий gate — прерывание самого HTTP ожидания `knowledge_search` у coordinator. Текущий шаг проверяет отмену до/после этого вызова и отбрасывает поздний ответ, но не прерывает ещё ожидающий сетевой вызов. Для него сохраняется прежний socket timeout 120 секунд.
