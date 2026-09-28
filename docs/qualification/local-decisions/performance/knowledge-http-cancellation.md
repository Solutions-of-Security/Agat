# Отмена HTTP ожидания coordinator search

Дата: **28.09.2026**. Продолжение [отмены query embedding](./rag-query-cancellation.md). Gate закрывает последний HTTP wait в RAG retrieval: ответ coordinator мог задержаться уже после сохранения retrieval record, пока процесс отменён и renewal отверг аренду.

## Воспроизведение и изменение

[Baseline](./evidence/2026-09-28/knowledge-http-cancellation/baseline.log) завершился `Coordinator search HTTP remained open after lease renewal rejection`. Fixture получил настоящий второй search response от coordinator и удержал его перед Python worker. Workflow/application cancellation и отказ renewal уже произошли, но обычный `urllib` продолжал ждать.

`CoordinatorClient.knowledge_search` теперь использует [управляемый HTTP helper](../../../../workers/embedding_http.py) с event обычной process lease. Отмена завершает и собирает только helper этого запроса до освобождения worker slot. Установлены **120 секунд общего HTTP deadline**, **2 МиБ** успешного ответа и **4096 байт** читаемого error body. Прежние 120 секунд были socket timeout, а response read не имел byte limit. Coordinator возвращает не более 20 hits с фрагментами до 4000 символов; новый лимит оставляет запас для provenance и JSON encoding.

Сохраняются bearer token, User-Agent, trace headers, query payload и `ApiError.status` для HTTP errors. Helper возвращает bounded JSON error body отдельно от короткого текста диагностики, чтобы сообщение coordinator не требовало разбора строки HTTP-ошибки. Для transient transport/deadline failure сохраняется статус `0`; cancellation остаётся отдельным отказом запроса. Нет автоматического повторения search: ранее завершённая транзакция могла уже сохранить provenance.

Другие coordinator endpoints используют прежний transport. Embeddings/session и primary сохраняют свои строки диагностики; расширение общего helper перепроверено worker regression. Embedding probes исключают и primary, и coordinator helpers из embedding counters.

## Сквозной сценарий

[Temporal RAG fixture](../../../../apps/coordinator/test/temporal-rag.integration.test.ts) выполняет `search-interrupt` для SQLite/PostgreSQL и обоих embedding transports. Отдельный loopback proxy worker повторяет обычные coordinator requests и удерживает только ответ второго search **после** завершения coordinator HTTP. Это проверяет неизвестность результата запроса для worker при уже совершённом application commit.

После потерянного tick reply, restart Temporal worker и отмены с потерянным cleanup acknowledgement тест ждёт настоящий отказ 45-секундного renewal. Проверяются закрытие coordinator-соединения, `Knowledge request cancelled` у worker и отсутствие второго primary вызова. Старый ответ остаётся удержанным, а тот же worker с `--concurrency 1` полностью выполняет следующий RAG-процесс.

У отменённого процесса остаются **1 primary request**, **4 отправленных embedding items**, **1 shadow observation**, **2 retrieval records** и **4 source references**. Второй retrieval уже был принят coordinator до потери ответа; его provenance сохраняется, но output отменённого stage не появляется. Вместе со следующим успешным процессом — **4 primary requests** и **7 embedding items**. Первый output и trace отменённого процесса не меняются; history replay не создаёт I/O, model calls или новых записей.

В PostgreSQL проверяются runtime role, tenant role без superuser/BYPASSRLS, отказ чтения release registry и видимость `[1 run, 1 instance, 2 retrievals, 2 chunks]` только собственному tenant. Schema **30** и Workflow commands не менялись.

## Проверки

- [Targeted-набор](./evidence/2026-09-28/knowledge-http-cancellation/targeted.log): **54 pass**, включая отмену headers/success/error wait, полный deadline при медленной передаче, отсутствие spawn до регистрации/pre-cancel, auth/trace/body, шесть HTTP error statuses, длинное JSON-сообщение, malformed error и byte limit.
- [Полный worker regression](./evidence/2026-09-28/knowledge-http-cancellation/worker.log): **143 pass**, с настоящим LangGraph **1.2.11**. [Два probe-теста](./evidence/2026-09-28/knowledge-http-cancellation/probe-tracking.log) выполняют embedding, primary и search HTTP и проверяют ровно один embedding owner с закрытыми pipes.
- **20/20** живых SQLite Temporal сценариев и **10/10** PostgreSQL RAG сценариев прошли. Шесть дополнительных Node file entries без совпавшего имени не считаются RAG сценариями. Coordinator: **293 pass, 20 gated skip**; native replay: **34 histories** и **3 configuration tests**.
- [SQLite Temporal](./evidence/2026-09-28/knowledge-http-cancellation/sqlite.log), [PostgreSQL RAG](./evidence/2026-09-28/knowledge-http-cancellation/postgres.log), [coordinator regression](./evidence/2026-09-28/knowledge-http-cancellation/coordinator.log) и [native replay](./evidence/2026-09-28/knowledge-http-cancellation/replay.log) сохраняют проверки предыдущих gates.
- Четыре новых trace: [SQLite isolated](./evidence/2026-09-28/knowledge-http-cancellation/sqlite/isolated-search-interrupt.json), [SQLite session](./evidence/2026-09-28/knowledge-http-cancellation/sqlite/session-search-interrupt.json), [PostgreSQL isolated](./evidence/2026-09-28/knowledge-http-cancellation/postgres/isolated-search-interrupt.json), [PostgreSQL session](./evidence/2026-09-28/knowledge-http-cancellation/postgres/session-search-interrupt.json). Следующий успешный процесс сохранён в `interruption.nextTrace`.
- [Workspace types](./evidence/2026-09-28/knowledge-http-cancellation/typecheck.log), [strict fixture types](./evidence/2026-09-28/knowledge-http-cancellation/test-typecheck.log), [docs](./evidence/2026-09-28/knowledge-http-cancellation/docs.log) и [SHA manifest](./evidence/2026-09-28/knowledge-http-cancellation/checks.json). Documentation checks: **12 Node + 318 Python tests**, **1926 local link targets** в **215 Markdown files**.

```bash
npm run test:worker
npm run fleet:test-temporal-rag
npm run fleet:test-temporal-postgres-rag
npm run test:temporal
```

Это проверка корректности с synthetic model responses. Disconnect не отменяет уже совершённую транзакцию coordinator и не доказывает остановку ещё выполняющегося поиска на сервере. До подтверждённого отказа renewal действует прежний период 45 секунд. Общий deadline относится к HTTP, не ко всей многошаговой lease; ограничения системного запуска subprocess описаны в [предыдущем gate](./primary-http-cancellation.md). Ни latency SLO, ни предметная qualification не заявлены.

Следующий gate — потеря самого Python worker при активном primary HTTP, прекращение owned helper и восстановление процесса после настоящего истечения lease с сохранением уже принятого output. Предыдущие gates перезапускали Temporal worker; это отдельная граница владения.
