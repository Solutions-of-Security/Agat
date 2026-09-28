# Отмена Temporal workflow и application state

Дата: **28.09.2026**. Продолжение [владения scheduled child](./temporal-scheduled-ownership.md). Проверяется отмена parent **после запуска child**. До изменения Temporal завершал оба workflow как `CANCELLED`, но application instance оставался `waiting_external` и сохранял активное ожидание сигнала: [baseline](./evidence/2026-09-28/temporal-cancellation/baseline/cancellation.json), [провал проверки](./evidence/2026-09-28/temporal-cancellation/baseline/integration.log).

## Изменение

`agatProcessWorkflow` перехватывает cancellation, вызывает новую Activity `cancelProcess` и только после application cleanup повторно выбрасывает исходную cancellation. Cleanup работает в `CancellationScope.nonCancellable`: запрос отмены не отменяет саму очистку. Это соответствует [контракту cancellation scopes Temporal](https://typescript.temporal.io/api/classes/workflow.CancellationScope). Parent продолжает использовать существующий child cancellation mode `WAIT_CANCELLATION_COMPLETED` и ожидает завершения child.

Внутренний `POST /api/v1/internal/processes/:instanceId/cancel` требует Temporal token, projectId и instance с runtime `temporal`. Он вызывает существующую application cancellation state machine в одной транзакции и возвращает актуальное durable state. Повтор завершённой отмены не меняет timestamps и не добавляет terminal events. PostgreSQL блокирует строку instance `FOR UPDATE OF pi`, сериализуя одновременно пришедшие cleanup attempts; чужой проект не получает строку. SQLite использует существующий `BEGIN IMMEDIATE`. Schema остаётся **29**.

Если отмена запускает компенсации, workflow продолжает принимать wake Updates/Signals и сверять state до `completed`, `failed` либо `cancelled`. Принудительной установки terminal status поверх компенсации нет. Выполнение самих компенсаций остаётся у обычных application workers; Temporal ожидает их результат. После завершения cleanup workflow сохраняет исходную семантику Temporal cancellation.

Transient cleanup failures повторяются без общего двухминутного лимита обычного tick: start-to-close каждой Activity — 15 секунд, HTTP deadline — 10 секунд, retry backoff — 1–30 секунд. Недоступность coordinator оставляет cancellation pending до восстановления; ошибочная конфигурация и окончательные HTTP 400/401/403/404 остаются non-retryable и требуют оператора. Завершение workflow при недоступной базе не выдаётся за успешную очистку.

Новые команды защищены marker `agat-process-cancellation-cleanup-v1` в ветке cancellation. [Temporal patching](https://docs.temporal.io/develop/typescript/workflows/versioning) сохраняет исходную последовательность команд при replay старой history. Две настоящие legacy cancellation histories добавлены к fixtures; replay не дописывает в них cleanup. Marker нельзя удалять без штатного patch retirement. Новый bundle сначала проходит replay и production rollout по Worker Versioning; старый binary не принимает новые history с marker.

## Свидетельства

- [HTTP и SQLite](../../../../apps/coordinator/test/temporal-cancellation.test.ts): неверный token, обязательный projectId, чужой проект, запрет database-runtime instance, повтор без новых событий/изменения ответа и сохранение уже завершённого процесса. [Лог](./evidence/2026-09-28/temporal-cancellation/http.log).
- [PostgreSQL](../../../../apps/coordinator/test/fleet-ha-postgres.integration.test.ts): два независимых процесса одновременно ожидают блокировку одного instance; оба возвращают одинаковый state, записан ровно один `process.instance.cancelled`. Повтор на исходной replica сохраняет ответ; tenant другого проекта не видит и не блокирует строку. [Целевой прогон](./evidence/2026-09-28/temporal-cancellation/postgres.log); полный Fleet/HA набор обязателен в CI.
- [Живой Temporal, отмена ожидания](./evidence/2026-09-28/temporal-cancellation/verified/cancellation.json): parent отменяется во время ожидания внешнего сигнала в child. Proxy теряет первый ответ cancel после commit; второй запрос получает тот же terminal state. Parent/child — `CANCELLED`, application — `cancelled`, активных signal waits — ноль.
- [Живой Temporal, компенсация и restart](./evidence/2026-09-28/temporal-cancellation/verified/compensation.json): тест завершает synthetic HTTP lease, затем отменяет parent. Application переходит в `compensating`; ответ cancel удерживается, Temporal worker получает SIGKILL. Замена восстанавливает Activity после timeout, повторяет idempotent cancel и остаётся активной вместе с parent до завершения единственной компенсации. Wake Update завершает cleanup; application и оба workflow отменены. Внешний HTTP side effect здесь моделируется завершением настоящего lease через store, без обращения к `example.test`.

[Полная coordinator regression](./evidence/2026-09-28/temporal-cancellation/coordinator.log): **289 pass, 6 gated skip**; gated сценарии отдельно выполнены на сервере. [Workspace и strict typecheck новых тестов](./evidence/2026-09-28/temporal-cancellation/typecheck.log) прошли.

Все **шесть** живых Temporal-сценариев, включая прежние RAG/retry/reconciliation, прошли: [лог](./evidence/2026-09-28/temporal-cancellation/integration.log). Native replay обеих новых пар выполняется сразу в интеграционном тесте. Вместе с двумя legacy cancellation fixtures обычная regression содержит **13 histories**: [лог](./evidence/2026-09-28/temporal-cancellation/replay.log).

Первый compensation fixture [не прошёл](./evidence/2026-09-28/temporal-cancellation/compensation-run.log): тест ошибочно читал граф из несуществующего поля DTO. Исправлен только тест — он создаёт типизированный граф до публикации; [финальный прогон](./evidence/2026-09-28/temporal-cancellation/integration.log) прошёл. Baseline и промежуточные ошибки сохранены. [Проверки и SHA исходных файлов](./evidence/2026-09-28/temporal-cancellation/checks.json) связывают evidence с кодом.

```bash
node --import tsx --test apps/coordinator/test/temporal-cancellation.test.ts
npm run fleet:test-temporal-rag
npm run fleet:test-ha-postgres -- --test-name-pattern='serializes Temporal cancellation'
npm run test:temporal
```

Полный [PostgreSQL CI](https://github.com/Solutions-of-Security/Agat/actions/runs/36365721214/job/108751614810) выявил конфликт имени `Foreign` с ранее выполненной fixture: 98 pass и один fail нового теста до вызова cancellation. [Ошибка](./evidence/2026-09-28/temporal-cancellation/ci-project-name-failure.log) сохранена. Новый тест теперь использует уникальные имена проектов, совпадающие с его случайными ID; production-код не менялся. [Совместный прогон трёх соседних сценариев](./evidence/2026-09-28/temporal-cancellation/postgres-shared-fixtures.log) прошёл.

## Обновление и оставшиеся границы

Сначала обновите coordinator с новым internal endpoint, затем Temporal workers с новым bundle по штатному canary/ramp. Workflow со старым worker сохраняет старое поведение отмены. Не откатывайте worker на bundle без patch после появления новых history. При длительном ожидании cleanup проверьте Activity failure/retry, доступность coordinator и прогресс compensation leases.

Этот этап не обрабатывает отмену scheduled parent **во время `startScheduledProcess` до создания child**: Activity могла уже записать instance, а parent ещё не получить ID. Следующий gate воспроизведёт эту границу и обеспечит cleanup при неизвестном результате создания. Принудительное termination, Workflow timeout/reset и исчерпание обычных tick retries также не эквивалентны обработанной cancellation. Ранее осиротевшие application rows автоматически не исправляются. Реальные модели, production compensation side effects и SLO этим тестом не квалифицированы.
