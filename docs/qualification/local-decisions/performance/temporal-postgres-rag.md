# Сквозной RAG: Temporal и PostgreSQL

Дата: **28.09.2026**. Продолжение [process tick recovery](./temporal-process-retry.md). Проверяется совместная работа production coordinator, Python worker, Temporal и PostgreSQL runtime roles; предыдущий [Temporal RAG gate](./temporal-rag-recovery.md) использовал SQLite.

## Найденный дефект и исправление

[Первый прогон](./evidence/2026-09-28/temporal-postgres-rag/initial.log) остановил оба embedding transport до создания instance: HTTP start вернул 400, `permission denied for table worker_releases`. Tenant-транзакция создания вызывает preflight, а preflight пытался читать глобальный registry через rollout join. Даже при разрешённых unsigned fixtures таблица участвовала в запросе. Отдельные store-тесты под system role этого не обнаруживали.

Сервер теперь получает небольшой fleet readiness snapshot **до tenant-транзакции** через system scope. Он содержит project ID, IDs подходящих workers и глобальное число активных задач. `scenarioFleetReadiness` применяет прежние release trust/attestation/rollout проверки. Дальнейший preflight читает проектные данные под tenant role, проверяет соответствие проекта snapshot, текущую доступность node, capacity, models и routing policy. Snapshot используется только для advisory readiness; выдача lease по-прежнему повторяет исходные проверки.

Снимок создаётся самим HTTP handler для process/template preflight, process pack и обычного start. Public body его не задаёт. Internal scheduled/webhook обработчики сохраняют прежний system path. Schema **30**, grants и RLS policies не меняются. Tenant по-прежнему не получает SELECT на глобальный registry. Это существенно: [PostgreSQL RLS](https://www.postgresql.org/docs/17/ddl-rowsecurity.html) не ограничивает superuser/BYPASSRLS, поэтому проверка выполняется отдельным tenant-соединением без этих прав, а не только фильтром application API.

## Воспроизводимый прогон

```bash
npm run fleet:test-temporal-postgres-rag
```

[Launcher](../../../../scripts/test-temporal-postgres-rag.sh) создаёт свой PostgreSQL **17.6** на случайном loopback port, использует штатный init ролей `agat_migrator`/`agat_system`/`agat_tenant`, выполняет production schema migration/admission и вызывает Temporal launcher. Migration credential не передаётся coordinator. Каждый launcher удаляет только свой временный container. PostgreSQL retrieval coordinator работает в `isolated` mode; SQLite default сохранён.

Один и тот же [RAG test](../../../../apps/coordinator/test/temporal-rag.integration.test.ts) запускает `isolated` и `session` embedding transports. Сначала Python worker индексирует два synthetic source documents. Затем реальный HTTP preflight и start запускают трёхшаговый процесс с durable wait. Proxy теряет первый tick response после commit, Temporal выполняет retry; worker убивается во время второго primary HTTP request и заменяется другим process. Новый worker восстанавливает history, после чего процесс завершается.

Проверяются ровно три primary ответа, пять embedded items (два документа и три запроса), три shadow-наблюдения, шесть source references и исходные fingerprints. Native replay не выполняет HTTP Activities, model calls или database writes. Primary/shadow ответы синтетические: качество модели и арифметика реального inference здесь не квалифицируются.

После завершения tenant того же проекта видит один run, один instance, три retrieval rows и два chunks. Tenant другого созданного проекта не видит ни одной из этих строк. Прямой SELECT `worker_releases` должен завершиться SQLSTATE **42501**. Успех HTTP readiness/start при этом подтверждает исправленный путь, без расширения SQL grants.

## Результаты

- Оба PostgreSQL сценария прошли: [isolated evidence](./evidence/2026-09-28/temporal-postgres-rag/final/isolated.json), [session evidence](./evidence/2026-09-28/temporal-postgres-rag/final/session.json), [лог](./evidence/2026-09-28/temporal-postgres-rag/integration-final.log). Node runner дополнительно отображает шесть файлов без подходящих имён тестов; они не считаются шестью новыми PostgreSQL сценариями.
- [16 preflight проверок](./evidence/2026-09-28/temporal-postgres-rag/preflight.log), включая HTTP, чужой project snapshot и изменение capacity после capture, прошли. Advisory snapshot не разрешает выдать второй lease занятому worker.
- [Coordinator regression](./evidence/2026-09-28/temporal-postgres-rag/coordinator.log): **293 pass, 12 gated skip**.
- [Полный прежний SQLite Temporal набор](./evidence/2026-09-28/temporal-postgres-rag/sqlite-temporal.log): **12 pass** после изменения HTTP readiness и общего RAG fixture.
- [Native replay](./evidence/2026-09-28/temporal-postgres-rag/replay.log): **30 histories**, включая обе PostgreSQL RAG histories, и **3 configuration tests**.
- [Workspace typecheck](./evidence/2026-09-28/temporal-postgres-rag/typecheck.log), [strict изменённые тесты](./evidence/2026-09-28/temporal-postgres-rag/tests-typecheck.log) и [checks с SHA](./evidence/2026-09-28/temporal-postgres-rag/checks.json) фиксируют проверенную версию. Shell launcher прошёл `bash -n`.

CI matrix дополнена обязательным `integration (temporal-postgres-rag)`. Его результат входит в общий `required` gate вместе с прежними suites; merge допускается после полного verify.

## Границы

Это один PostgreSQL server в disposable Docker, а не HA/failover или production SLO. Проверяются потеря HTTP tick acknowledgement и restart Temporal worker; рестарт PostgreSQL и coordinator здесь не добавлены. Role/admission и изоляция прочитанных run/retrieval/chunk rows проверены, но полный security audit не заявлен.

Следующий gate — отмена полного RAG workflow при активном primary HTTP request и проверка прекращения lease/позднего результата в том же PostgreSQL runtime. Предметная разметка, реальные business connectors и qualification исходного плана остаются открытыми.
