# Владелец запуска scheduled child

Дата: **28.09.2026**. Продолжение [идемпотентного scheduled-start](./temporal-scheduled-start.md). Исправлен конфликт при рестарте coordinator между commit instance и получением ответа Activity: startup reconciliation создавал standalone workflow с ID будущего child, после чего parent завершался с `Workflow execution already started`.

## Воспроизведение и решение

[Baseline на предыдущем коде](./evidence/2026-09-28/temporal-scheduled-owner/baseline/recovery.json) подтверждает существующий workflow до повторного ответа scheduled-start, отсутствие `parentExecution` и ошибку parent. [Провал проверки](./evidence/2026-09-28/temporal-scheduled-owner/baseline/integration.log) сохранён. Это конфликт владельцев создания, а не повторная запись instance.

Schema **29**, contract `agat-temporal-start-ownership-v29`, добавляет `process_instances.workflow_start_owner` с допустимыми значениями `coordinator` и `temporal_parent`. Обычный запуск по умолчанию принадлежит coordinator. Scheduled-start записывает `temporal_parent` в той же транзакции, что instance и receipt. Startup reconciliation выбирает только строки владельца `coordinator`; parent повторяет свою Activity и запускает child после её успешного завершения.

Решение сохраняет настоящую связь parent/child и существующий `ParentClosePolicy.REQUEST_CANCEL`. [Temporal описывает child как workflow, запущенный другим workflow](https://docs.temporal.io/child-workflows); существующий standalone execution не становится child от совпадения Workflow ID. Поэтому получение handle на чужой standalone workflow не исправило бы семантику отмены. Workflow-код и последовательность команд не менялись.

При добавлении колонки migration восстанавливает scheduled ownership из receipts schema 28. Совпасть должны instance ID, проект receipt и проект run; runtime должен быть `temporal`. Default сохраняет ручные запуски, даже если receipt другого проекта указывает на их ID. Backfill выполняется один раз при добавлении колонки; повторное открытие store не пересчитывает владельца.

## Проверки

- [Настоящий Temporal](../../../../apps/coordinator/test/temporal-scheduled-reconciliation.integration.test.ts): собранные coordinator/worker, HTTP proxy удерживает ответ после commit, первый coordinator получает SIGKILL. После startup замены scheduled workflow ещё отсутствует, а отдельный обычный instance восстановлен coordinator. Потеря удержанного ответа вызывает Activity retry; parent создаёт child с правильным `parentExecution`. Второй SIGKILL/restart сохраняет оба Workflow Run ID. HTTP signals завершают оба instance и parent. [Evidence](./evidence/2026-09-28/temporal-scheduled-owner/verified/recovery.json), [все четыре Temporal-сценария](./evidence/2026-09-28/temporal-scheduled-owner/integration.log).
- [SQLite](../../../../apps/coordinator/test/scheduled-start.test.ts): upgrade предыдущей структуры, сохранённые receipts, ручные instances, отрицательная проверка чужого проекта, reopen и повтор Activity. **4 pass**: [лог](./evidence/2026-09-28/temporal-scheduled-owner/sqlite.log).
- [PostgreSQL](../../../../apps/coordinator/test/fleet-ha-postgres.integration.test.ts): migration role удаляет новую колонку для воспроизведения физической структуры schema 28, затем штатный migrator/admission восстанавливает её и выполняет backfill. Проверены обе группы владельцев и чужой проект. Это проверка предыдущей структуры с реальными данными, без имитации старого admission marker. Вместе с concurrency/RLS/COMMIT-loss scheduled-start — **2 pass**: [лог](./evidence/2026-09-28/temporal-scheduled-owner/postgres.log).
- [Offline SQLite → PostgreSQL](../../../../apps/coordinator/test/sqlite-postgres-migrator.integration.test.ts): импортирует receipt и `temporal_parent` вместе, выполняет reconciliation и rehearsal rollback. **2 pass**: [лог](./evidence/2026-09-28/temporal-scheduled-owner/migration.log).

Первый PostgreSQL запуск [не прошёл из-за fixture](./evidence/2026-09-28/temporal-scheduled-owner/postgres-fixture-limit.log): тест удерживал дополнительное migration connection во время старта Job и превышал существующий role limit. Fixture теперь закрывает своё DDL connection до Job; лимиты и production admission не ослаблялись. Исправленный целевой прогон прошёл; полный Fleet/HA набор остаётся обязательным CI gate перед merge.

[Coordinator regression](./evidence/2026-09-28/temporal-scheduled-owner/coordinator.log): **288 pass, 4 gated skip**; все четыре gated сценария отдельно исполнились на настоящем Temporal. [Replay](./evidence/2026-09-28/temporal-scheduled-owner/replay.log): **7 histories**, включая новую parent history, и **3 config tests**. [Typecheck всех workspaces и новых тестов](./evidence/2026-09-28/temporal-scheduled-owner/typecheck.log) прошёл. Первый запуск docs checks внутри sandbox не получил доступ к loopback для HTTP fixtures; [ошибка окружения](./evidence/2026-09-28/temporal-scheduled-owner/docs-sandbox-denied.log) сохранена, повтор с разрешённым loopback прошёл: 12 Node tests, 316 Python tests и 1755 локальных ссылок. SHA исходных файлов и результаты связывает [checks.json](./evidence/2026-09-28/temporal-scheduled-owner/checks.json).

```bash
node --import tsx --test apps/coordinator/test/scheduled-start.test.ts
npm run fleet:test-temporal-rag
npm run fleet:test-ha-postgres -- --test-name-pattern='backfills scheduled workflow ownership|deduplicates scheduled-start'
npm run fleet:test-state-migration
npm run test:temporal
```

Первый [CI Temporal job](https://github.com/Solutions-of-Security/Agat/actions/runs/36364573956/job/108748322142) выявил гонку в fixture: tick уже прошёл gate перед вторым SIGKILL, и proxy зафиксировал оборванный ответ. Все проверки ownership прошли, но строгая проверка отсутствия дополнительных proxy errors — нет. Перед SIGKILL fixture теперь дожидается завершения уже отправленных ticks; намеренно потерянный scheduled-start reply сохраняется. [Ошибка](./evidence/2026-09-28/temporal-scheduled-owner/ci-tick-drain-failure.log) сохранена, production-код не менялся. [Повтор четырёх сценариев](./evidence/2026-09-28/temporal-scheduled-owner/drained.log) и [финальная целевая проверка полного drain](./evidence/2026-09-28/temporal-scheduled-owner/drain-verified.log) прошли.

## Обновление и границы

Перед обновлением приостановите расписания, завершите старые scheduled parents/Activities и остановите writers. После согласованного backup примените schema **29** штатной PostgreSQL migration Job с passing admission либо откройте SQLite store текущим release. Обновите все coordinator до возобновления работы: старый coordinator не учитывает владельца запуска. Требования согласованного обновления worker/coordinator из этапа schema 28 сохраняются. Offline migrator требует уже обновлённый SQLite source: [runbook](../../../sqlite-postgresql-migration.md).

Backfill не восстанавливает связь для ранее созданного standalone workflow и не угадывает происхождение instances до schema 28 без receipts. Такие записи требуют сверки application state и Temporal history перед возобновлением расписаний. Этот этап не отменяет, не удаляет и не усыновляет существующие orphan executions. Rollback должен сохранять ownership/receipts и согласованное состояние компонентов.

Используются учебные процессы и SQLite с живым Temporal; PostgreSQL migration и concurrency проверены отдельно. Календарная точность Schedule service, реальные модели и production SLO не квалифицировались. Следующий gate — отмена parent/child и согласованное terminal state application database.
