# Прерывание primary HTTP при потере аренды

Дата: **28.09.2026**. Продолжение [отмены полного RAG](./temporal-rag-cancellation.md). Gate закрывает удержание worker slot после подтверждённой отмены аренды; backend inference остаётся ответственностью model server.

## Подтверждённый дефект и исправление

[Baseline](./evidence/2026-09-28/primary-http-cancellation/baseline.log) на реальном Temporal/coordinator/Python worker завершился ошибкой `Primary HTTP remained open after lease renewal rejection`. Application и Workflow уже отменены, штатный renewal через 45 секунд получил отказ аренды, но ожидающий primary `urllib` не наблюдал cancellation event. Fixture сохранял ответ модели невыпущенным ещё пять секунд после отказа.

`LocalModelClient.complete` теперь получает cancellation event аренды. Каждый primary HTTP request выполняется в отдельном управляемом процессе через существующий [HTTP helper](../../../../workers/embedding_http.py). Родитель проверяет отмену и общий deadline, завершает только свой helper и собирает его exit status до освобождения слота. Запрос не повторяется автоматически. При штатном ответе сохраняются payload, authentication, proxy/TLS/redirect semantics urllib, trace headers и token metrics. Секреты и prompt передаются через pipe, не через argv или временный файл.

Сигнал передаётся через `ContextVar` и сбрасывается в `finally`: параллельные leases одного model client изолированы, а копирование context настоящим LangGraph сохраняет отмену в supervisor и вложенном specialist graph. HTTP status передаётся структурированно; диагностическое сообщение несовместимости tools для 400/404/422 сохранено. Строковый error protocol embeddings/session остаётся совместимым.

Primary теперь имеет **900 секунд общего deadline на один HTTP request**, **16 МиБ** максимального успешного тела и **4096 байт** читаемого HTTP error body; диагностическая часть ограничена дополнительно. До изменения 900 секунд были socket timeout, а чтение тела не имело верхней границы. Это не общий deadline многошаговой lease. Тесты используют короткий deadline, чтобы проверить медленные headers и непрерывную передачу небольших фрагментов тела.

## Сквозной gate

[Живой RAG fixture](../../../../apps/coordinator/test/temporal-rag.integration.test.ts) выполняет recovery, поздний ответ после отмены и новый `interrupt` для SQLite/PostgreSQL и isolated/session embeddings. В последнем сценарии:

1. Принят первый primary output с shadow и provenance; второй HTTP response удерживается. Потеря tick reply и restart Temporal worker уже проверены.
2. Workflow отменяется; потерянный cleanup reply вызывает повторную идемпотентную отмену. Первый результат сохранён, второй stage cancelled с NULL output.
3. Fixture ждёт настоящий периодический lease renewal и его отказ. До выпуска ответа проверяются закрытие клиентского соединения и `RuntimeError: Model request cancelled` у worker.
4. Тот же живой worker с `--concurrency 1` завершает новый трёхэтапный RAG-процесс. Удержанный старый ответ всё ещё не выпущен. Trace отменённого процесса не меняется.
5. Сохранённая Temporal history проходит native replay без HTTP, model calls и записей в trace. Coordinator и workers завершаются штатно.

В отменённом процессе остаются 2 primary requests, 4 embedding items, 1 shadow observation и 4 source references. Вместе с последующим успешным процессом — 5 primary requests и 7 embedding items. Его trace сохранён отдельно в `interruption.nextTrace`. PostgreSQL tenant видит только свой run/retrieval/chunks; чтение release registry по-прежнему запрещено.

Замеры включают polling fixture; четыре наблюдения не оценивают распределение задержек или SLO.

| База | Embedding transport | После cancellation до отказа renewal, мс | После замеченного отказа до disconnect/worker error, мс |
|---|---|---:|---:|
| sqlite | isolated | 33253.10 | 61.51 |
| sqlite | session | 33335.33 | 52.67 |
| postgres | isolated | 33222.60 | 25.67 |
| postgres | session | 33283.45 | 27.38 |

## Проверки и evidence

- [Worker regression](./evidence/2026-09-28/primary-http-cancellation/worker.log): **132 теста**, включая настоящий LangGraph **1.2.11**, вложенный specialist, параллельные аренды, pre-cancel, deadline, byte limit, HTTP errors, proxy/auth и сбор helper process. Регрессия embeddings включает TLS, redirects и уход родительского процесса.
- **16/16** живых SQLite Temporal сценариев и **6/6** PostgreSQL RAG сценариев прошли. Шесть дополнительных Node file entries без совпавшего test name не считаются RAG сценариями.
- [SQLite Temporal](./evidence/2026-09-28/primary-http-cancellation/sqlite.log) и [PostgreSQL RAG](./evidence/2026-09-28/primary-http-cancellation/postgres.log) проверяют все три варианта на обоих transports. [SQLite isolated](./evidence/2026-09-28/primary-http-cancellation/sqlite/isolated-interrupt.json), [SQLite session](./evidence/2026-09-28/primary-http-cancellation/sqlite/session-interrupt.json), [PostgreSQL isolated](./evidence/2026-09-28/primary-http-cancellation/postgres/isolated-interrupt.json), [PostgreSQL session](./evidence/2026-09-28/primary-http-cancellation/postgres/session-interrupt.json) содержат измерения задержки после отказа renewal и trace следующего процесса.
- [Coordinator](./evidence/2026-09-28/primary-http-cancellation/coordinator.log): **293 pass, 16 gated skip**; gated cases выполняются в живых suites. [Native replay](./evidence/2026-09-28/primary-http-cancellation/replay.log): **34 сохранённые history**, плюс 3 configuration tests. Workflow commands и schema **30** не менялись.
- [Workspace typecheck](./evidence/2026-09-28/primary-http-cancellation/typecheck.log), [strict fixture typecheck](./evidence/2026-09-28/primary-http-cancellation/test-typecheck.log), [docs checks](./evidence/2026-09-28/primary-http-cancellation/docs.log) и [manifest с SHA](./evidence/2026-09-28/primary-http-cancellation/checks.json) фиксируют проверенную версию.

```bash
python3 -m pip install -r workers/requirements.txt
npm run test:worker
npm run fleet:test-temporal-rag
npm run fleet:test-temporal-postgres-rag
npm run test:temporal
```

Это локальная synthetic проверка корректности, не latency SLO и не qualification моделей. У worker сохраняется период renewal 45 секунд; отмена HTTP начинается после подтверждённого отказа renewal, а не немедленно после Workflow cancellation. Закрытие соединения не гарантирует прекращения вычислений на model server. Начальный системный запуск subprocess также не даёт жёсткой гарантии прерывания по таймеру на всех ОС.

Следующий gate — передача cancellation в query embedding во время RAG retrieval до primary. Индексационные embedding leases уже имеют свой cancellation path; retrieval использует обычную process lease и требует отдельной проверки.

## Перепроверка измерительных probe

[Первый обязательный HA job](./evidence/2026-09-28/primary-http-cancellation/ci-ha-initial.log) обнаружил 12 ошибок isolated embedding assertions: probe продолжал искать локальную переменную `process` в wrapper `request_embedding_response`, хотя владелец процесса перенесён в общий `_request_response`. HTTP/state assertions до проверки transport records прошли, но измерение не могло подтвердить cleanup и правильно завершилось отказом.

[Probe worker](../../../../apps/coordinator/test/embedding-worker-probe.py) и [probe RAG](../../../../scripts/embedding-rag-worker-probe.py) обновлены на функцию, владеющую subprocess. Фильтр по embedding request и активному `embed` сохраняет смысл embedding counters; primary helpers проверяются отдельно и не добавляются в эти counters. [Два новых теста](./evidence/2026-09-28/primary-http-cancellation/probe-tracking.log) выполняют настоящий embedding и primary HTTP через каждый probe, требуют один embedding process record, его правильный PID, закрытые pipes и собранный exit status. [Повтор HA isolated scenarios](./evidence/2026-09-28/primary-http-cancellation/ha-probe-recheck.log) прошёл **12/12** сценариев с настоящим worker/coordinator/PostgreSQL. [Повтор docs gate](./evidence/2026-09-28/primary-http-cancellation/docs-probe-recheck.log) включает 318 Python tests и проверку 1883 локальных ссылок.

## Основания решения

[Python urllib](https://docs.python.org/3/library/urllib.request.html#urllib.request.urlopen) описывает timeout блокирующих операций; общий deadline обеспечивается владельцем запроса. [Python subprocess](https://docs.python.org/3/library/subprocess.html#subprocess.Popen.communicate) требует явно завершить и собрать subprocess после timeout `communicate`; initial process creation может быть непрерываемым. [Context variables](https://docs.python.org/3/library/contextvars.html) дают раздельный контекст выполнения. Передача через настоящий закреплённый LangGraph проверена тестами; в установленном 1.2.11 `BackgroundExecutor.submit` использует `copy_context` и `ctx.run`.
