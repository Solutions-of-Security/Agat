# Scheduled-start после длительной недоступности coordinator

Дата: **28.09.2026**. Продолжение [cancellation intent](./temporal-scheduled-cancellation.md): transient ошибка создания не должна оставлять уже созданный instance без родительского workflow.

## Воспроизведение

Настоящая Activity отправляет create в coordinator, тот сохраняет instance и receipt. Proxy заменяет первый успешный ответ на HTTP 503, затем возвращает 503 ещё 125 секунд. Интервалы Temporal и production retry policy не ускоряются.

Прежняя policy завершила Activity после **8 попыток за 60,13 секунды**. Parent стал `FAILED`, единственный instance остался `waiting_external`, child отсутствовал. Это проверено [состоянием и history](./evidence/2026-09-28/temporal-scheduled-retry/baseline/retry.json); [baseline test](./evidence/2026-09-28/temporal-scheduled-retry/baseline/integration.log) завершился ожидаемой ошибкой.

## Решение

Новая ветка parent сохраняет creation Activity до подтверждения, явной отмены либо permanent request error. Убраны общий `scheduleToCloseTimeout` и `maximumAttempts` только для scheduled-start. Каждый attempt по-прежнему ограничен **15 секундами**, HTTP — **10 секундами**, backoff — от 1 до 15 секунд. Идемпотентность обеспечивает прежняя receipt под одним ключом namespace/Run/Activity. Явная отмена использует intent и cleanup предыдущего этапа, включая компенсации.

Это соответствует [модели Activity retry Temporal](https://docs.temporal.io/encyclopedia/retry-policies): временная недоступность сервиса сама по себе не подтверждает отсутствие side effect. [RetryPolicy](https://typescript.temporal.io/api/interfaces/common.RetryPolicy) позволяет не задавать максимальное число попыток; для установленного TypeScript SDK 1.24.0 поле опущено, поскольку его validator отвергает явный ноль. `ConfigurationError`, `CoordinatorRequestError` и явно non-retryable ошибки endpoint сохраняют конечный отказ. Изменение не включает автоматический повтор целого Workflow с новой occurrence identity.

Ветка защищена `patched("agat-scheduled-start-durable-retry-v1")`. Старые истории получают прежнюю bounded policy; две её старые proxy-ветки сохранены. Schema остаётся **30**, HTTP/receipt protocol не меняются. Уже запланированная Activity старого parent автоматически не переводится на новую policy.

## Проверки

После исправления **13-я попытка через 135,25 секунды** получила прежний instance, child запустился и завершился после внешнего сигнала. Parent и приложение — `COMPLETED`/`completed`, в базе **1 instance**, ключ всех запросов совпадает. [Итог и history](./evidence/2026-09-28/temporal-scheduled-retry/verified/retry.json) получены реальными coordinator/worker/server и production workflow bundle. Proxy моделирует HTTP outage после commit; длительный SQL outage отдельно здесь не проверяется.

- [Live integration](./evidence/2026-09-28/temporal-scheduled-retry/integration.log): **10 pass**, включая прежние retry/restart/cancellation/compensation сценарии на новой ветке.
- [Native replay](./evidence/2026-09-28/temporal-scheduled-retry/replay.log): **20 histories**, в том числе legacy retry exhaustion и новая успешная история; **3 configuration tests**.
- [Coordinator regression](./evidence/2026-09-28/temporal-scheduled-retry/coordinator.log): **292 pass, 10 gated skip**; gated Temporal сценарии выполнены отдельным live запуском.
- [Workspace typecheck](./evidence/2026-09-28/temporal-scheduled-retry/typecheck.log) и [strict проверка нового теста](./evidence/2026-09-28/temporal-scheduled-retry/new-test-typecheck.log) прошли. [Checks и SHA](./evidence/2026-09-28/temporal-scheduled-retry/checks.json) фиксируют проверенный код.

```bash
npm run fleet:test-temporal-rag
npm run test:temporal
npm run test:coordinator
npm run typecheck
```

CI исполняет новый test через существующий Temporal launcher glob. Полный verify, включая PostgreSQL, остаётся gate перед merge. Новый тест использует реальное время и занимает около 137 секунд; это проверка отказа за обеими прежними границами — 8 попыток и 2 минуты.

## Эксплуатация и границы

Обновляйте worker bundle штатным canary/ramp после coordinator schema 30. Новые parents сохраняются RUNNING во время transient outage; наблюдайте pending Activity и доступность coordinator. Для прекращения используйте Workflow cancellation: cleanup может ждать восстановления coordinator. Не заменяйте cancellation принудительным termination, если требуется application cleanup.

Перед rollout завершите старые scheduled parents либо примените отдельную согласованную процедуру recovery. Patch markers нельзя удалять, пока сохраняются использующие их histories. Termination, execution timeout/reset и permanent ошибки после неизвестного commit остаются отдельными границами; production SLO и предметное качество моделей не заявлены.

Следующий gate — аналогичный длительный outage обычного process tick: его конечная policy пока может завершить child при активном application state. Сначала требуется воспроизведение на реальном Temporal и проверка cancellation/replay при исправлении.
