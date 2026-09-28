# Process tick при длительной недоступности coordinator

Дата: **28.09.2026**. Продолжение [scheduled-start retry](./temporal-scheduled-retry.md): устойчивость уже запущенного process workflow должна сохраняться после того же outage.

## Воспроизведение и решение

Первый tick дошёл до настоящего coordinator, но proxy заменил его ответ на 503. Последующие запросы также получали 503. Прежний child исчерпал **8 попыток за 60,13 секунды**: child и parent стали `FAILED`, а application instance остался `waiting_external`. [Baseline state и обе histories](./evidence/2026-09-28/temporal-process-retry/baseline/recover.json) и [упавшая проверка](./evidence/2026-09-28/temporal-process-retry/baseline/integration.log) фиксируют расхождение. Второй baseline сценарий подтверждает, что [отмена во время retry](./evidence/2026-09-28/temporal-process-retry/baseline/cancel.json) уже выполняла cleanup.

Новый process tick повторяет transient ошибки без общего лимита попыток и времени. Отдельный Activity attempt остаётся ограничен **15 секундами**, HTTP request — **10 секундами**, backoff — от 1 до 15 секунд. Permanent `ConfigurationError`/`CoordinatorRequestError` сохраняют non-retryable поведение. Обычный бизнес-отказ приходит как terminal application state и завершает driver loop. Отмена по-прежнему переходит в отдельный non-cancellable cleanup с ожиданием компенсаций.

Изменение следует [Activity retry model Temporal](https://docs.temporal.io/encyclopedia/retry-policies). [ActivityOptions](https://typescript.temporal.io/api/interfaces/common.ActivityOptions) различает общий срок и срок одной попытки; здесь ограничение одного сетевого обращения сохранено. Состояние процесса и переходы находятся в coordinator, поэтому продолжается та же Activity в том же workflow, без нового business run. Это не добавляет exactly-once гарантии для внешних side effects.

Patch `agat-process-durable-tick-retry-v1` выбирается при входе в process workflow. Старые history сохраняют прежнюю policy. Schema остаётся **30**, application database и HTTP protocol не изменены. Уже запланированный pre-patch tick автоматически не переводится на новый retry budget.

## Протокол проверки

[Integration test](../../../../apps/coordinator/test/temporal-process-retry.integration.test.ts) запускает настоящие coordinator, Temporal server и worker с production bundle. В recovery сценарии proxy удерживает outage 125 секунд без ускорения часов. После девятой попытки test проверяет RUNNING у parent/child и активное application state, убивает worker через SIGKILL и запускает replacement. После восстановления ответа внешний сигнал завершает тот же instance. Run ID child должен сохраняться.

Оба сценария отправляют acknowledged Update во время retry. В cancellation сценарии parent отменяется на третьей попытке, первый cleanup reply теряется после commit, повтор должен завершить parent/child/application согласованно. Обе history каждого сценария проходят native replay. Модель и business side effects для этих тестов не требуются.

## Результаты

- [Recovery](./evidence/2026-09-28/temporal-process-retry/verified/recover.json): первый успешный ответ пришёл на **13-й попытке через 135,31 секунды**. После SIGKILL выполнен один restart; child Run ID сохранился. В базе один completed instance, parent и child COMPLETED. Два последующих успешных tick обработали wake/terminal state, новых instances нет.
- [Cancellation](./evidence/2026-09-28/temporal-process-retry/verified/cancel.json): три неуспешных tick, два cleanup запроса, согласованные CANCELLED/cancelled состояния. Успешного tick до отмены не потребовалось.
- [Полный live набор](./evidence/2026-09-28/temporal-process-retry/integration.log): **12 pass**, включая RAG, scheduled-start, ownership, длительный create retry и компенсации.
- [Coordinator regression](./evidence/2026-09-28/temporal-process-retry/coordinator.log): **292 pass, 12 gated skip**. Gated Temporal сценарии выполнены live.
- [Native replay](./evidence/2026-09-28/temporal-process-retry/replay.log): **28 histories**, включая четыре legacy parent/child history и четыре новые; **3 configuration tests**.
- [Workspace typecheck](./evidence/2026-09-28/temporal-process-retry/typecheck.log), [strict новый тест](./evidence/2026-09-28/temporal-process-retry/new-test-typecheck.log) и [checks с SHA](./evidence/2026-09-28/temporal-process-retry/checks.json) фиксируют проверенную версию. Полный verify CI остаётся gate перед merge.

```bash
npm run fleet:test-temporal-rag
npm run test:temporal
npm run test:coordinator
npm run typecheck
```

## Эксплуатация и границы

При недоступности coordinator новый process workflow остаётся RUNNING с pending Activity. Используйте метрики/описание Activity для наблюдения retry, а Workflow cancellation — для управляемого прекращения; cleanup также может ожидать восстановления coordinator. Перезапуск worker не сбрасывает накопленное состояние сервера Temporal.

Обновляйте worker bundle через штатный canary/ramp, сохраняя patch markers и старые ветки до завершения использующих их histories. Termination, workflow timeout/reset и permanent configuration/auth errors не получают автоматической application cleanup этим изменением. Старые failed workflows требуют отдельного recovery решения, их автоматическое возобновление не заявлено.

Следующий gate исходного интеграционного плана — сквозной RAG через Temporal с PostgreSQL runtime roles и штатной migration/admission. Предыдущие Temporal/RAG прогоны используют SQLite; отдельные PostgreSQL тесты не заменяют эту совместную проверку. Предметная qualification моделей и production SLO остаются открытыми.
