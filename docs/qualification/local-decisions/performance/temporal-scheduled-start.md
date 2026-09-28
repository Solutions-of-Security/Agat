# Идемпотентный scheduled-start

Дата: **28.09.2026**. Продолжение [RAG через Temporal](./temporal-rag-recovery.md). Исправлен повторный create при потере подтверждения Activity. В [baseline](./evidence/2026-09-28/temporal-scheduled-start/baseline.json) два одинаковых запроса, включая одинаковый Idempotency-Key, создали **два instance**. После изменения повтор возвращает identity первого запуска.

## Контракт и реализация

`startScheduledProcess` формирует `Idempotency-Key: agat-scheduled-v1:<sha256>` из JSON-массива `[namespace, workflowRunId, activityId]`. Attempt, task token и payload не участвуют в ключе. Поэтому retry одной Activity сохраняет ключ, а отдельный parent Workflow Run получает другой. Это следует из [рекомендаций Temporal по идемпотентности Activities](https://docs.temporal.io/activity-definition#idempotency) и [полей Activity Info](https://typescript.temporal.io/api/interfaces/activity.Info). Workflow-код и последовательность его команд не менялись.

Внутренний endpoint по-прежнему требует Temporal token и projectId. Теперь отсутствие или неверный формат ключа возвращает **400** без создания процесса. Schema **28** добавляет `process_scheduled_start_receipts`: project, key, SHA канонического запроса, сохранённый ответ с instance/process/project ID и created_at. Исходный input в receipt не сохраняется. Primary key — `(project_id, idempotency_key)`; таблица включена в FORCE RLS и существующее разделение migration/runtime/tenant roles.

В одной транзакции coordinator проверяет receipt, создаёт процесс обычным `startProcess` и сохраняет ответ. SQLite использует `BEGIN IMMEDIATE`; PostgreSQL сериализует scheduled-start в пределах проекта блокировкой строки `projects FOR UPDATE`. [PostgreSQL удерживает такую блокировку до конца транзакции](https://www.postgresql.org/docs/17/explicit-locking.html#LOCKING-ROWS). Это осознанная сериализация запусков одного проекта; производительность массовых расписаний отдельно не измерялась.

При повторе с тем же запросом возвращается сохранённый ответ **до preflight**. Последующая публикация версии, заполненная очередь и удаление старого run не должны превращать retry в новое исполнение. Изменение processId, input, priority или других параметров с уже использованным ключом возвращает **409**. Worker считает окончательным только 409 с кодом `SCHEDULED_START_IDEMPOTENCY_CONFLICT`; временные preflight 409, transport failures и 503 с неизвестным исходом COMMIT сохраняют Activity retry. Ошибки первого запуска до commit не оставляют receipt и могут быть повторены после исправления причины.

Receipt сохраняется до удаления проекта. У него намеренно нет cascade от run/process: удаление истории не разрешает повторное исполнение старой Activity. Если соответствующий instance уже удалён, retry вернёт прежний ID, а последующий tick получит 404. Это явный отказ продолжения, без незаметного повторного создания. Отдельного TTL/cleanup receipts в этом этапе нет; их нельзя очищать независимо от принятой политики повторов и восстановления.

## Проверки

- [SQLite и HTTP](../../../../apps/coordinator/test/scheduled-start.test.ts): upgrade schema 27→28, reopen, повтор без дополнительных событий, version pin, конфликт payload, новая occurrence, receipt после retention, rollback всех writes при ошибке INSERT receipt, auth и разделение проектов. [Лог](./evidence/2026-09-28/temporal-scheduled-start/sqlite-http-final.log).
- [PostgreSQL](../../../../apps/coordinator/test/fleet-ha-postgres.integration.test.ts): два отдельных процесса одновременно достигают одной row lock; после её освобождения оба получают один instance и одну receipt. Tenant другого проекта не видит и не может вставить чужую receipt. Затем protocol proxy пропускает COMMIT, подтверждает его на сервере и теряет ответ: первый процесс получает `AGAT_COMMIT_UNKNOWN`, другой coordinator на retry использует уже сохранённую identity. [Итоговая целевая проверка](./evidence/2026-09-28/temporal-scheduled-start/postgres-verified.log).
- [Настоящий Temporal](../../../../apps/coordinator/test/temporal-scheduled-start.integration.test.ts): собранные coordinator и worker, loopback proxy теряет HTTP reply после commit; history подтверждает Activity attempt 2, один child Workflow ID и завершение parent. Проверяются точный ключ из Run/Activity ID, отдельный второй parent с новым ключом и native replay без HTTP/DB writes. [History и ответы](./evidence/2026-09-28/temporal-scheduled-start/final/recovery.json), [интеграционный лог](./evidence/2026-09-28/temporal-scheduled-start/integration.log).
- [Offline migration](../../../../apps/coordinator/test/sqlite-postgres-migrator.integration.test.ts) переносит непустую receipt из SQLite в PostgreSQL с reconciliation и проверкой identity/hash. [Лог](./evidence/2026-09-28/temporal-scheduled-start/migration.log).

Три локальных Temporal-сценария, включая оба прежних RAG transport, прошли. Дополнительный [финальный запуск](./evidence/2026-09-28/temporal-scheduled-start/conflict-integration.log) проверил retry временного preflight 409 и немедленный окончательный отказ при 409 с кодом конфликта ключа; эти два ответа задаёт HTTP fixture, а основной потерянный create reply проходит через настоящий coordinator. Новая parent history добавлена в обычные fixtures: [replay всех шести историй](./evidence/2026-09-28/temporal-scheduled-start/replay.log). [Coordinator regression](./evidence/2026-09-28/temporal-scheduled-start/coordinator-final.log): **287 pass, 3 gated skip**; эти три сценария отдельно исполняются с сервером. Клиентские identity экспортируемой history нормализованы; payload содержит только учебные данные.

Первые PostgreSQL проверки обнаружили ошибки самой fixture: [неполное application_name](./evidence/2026-09-28/temporal-scheduled-start/postgres-fixture-name.log), затем [закэшированный statistics snapshot](./evidence/2026-09-28/temporal-scheduled-start/postgres-fixture-snapshot.log). Исправленная проверка требует двух реально ожидающих транзакций и обновляет наблюдение через `pg_stat_clear_snapshot`, согласно [документации PostgreSQL](https://www.postgresql.org/docs/17/monitoring-stats.html). Первый [полный HA-прогон](./evidence/2026-09-28/temporal-scheduled-start/postgres.log) завершился с 96 pass и одним fail нового теста из-за неверного вызова существующего timeout helper. После исправления целевой тест, включая COMMIT fault, прошёл; полный набор повторяется обязательным CI перед merge. Production-код ради fixture не менялся.

Штатный [typecheck всех workspaces](./evidence/2026-09-28/temporal-scheduled-start/typecheck.log) и отдельный strict typecheck новых тестов прошли. Дополнительная проверка старого большого HA test-файла нашла [18 diagnostics](./evidence/2026-09-28/temporal-scheduled-start/test-types.log); все 18 воспроизведены на [исходном файле из baseline commit](./evidence/2026-09-28/temporal-scheduled-start/test-types-baseline.log), новых diagnostics нет. Этот файл штатный source typecheck не включает.

```bash
node --import tsx --test apps/coordinator/test/scheduled-start.test.ts
npm run fleet:test-temporal-rag
npm run fleet:test-ha-postgres -- --test-name-pattern='deduplicates scheduled-start'
npm run fleet:test-state-migration
npm run test:temporal
```

[Итоговые проверки и SHA исходных файлов](./evidence/2026-09-28/temporal-scheduled-start/checks.json) связывают evidence с проверенным кодом.

## Rollout и границы

Обновление меняет контракт внутреннего endpoint: старый worker без ключа получает 400. Перед rollout приостановите расписания и дождитесь завершения старых scheduled parent workflows и их Activities. Не переносите на новый worker Activity с неизвестным исходом запроса к старому coordinator: для такого запроса receipt ещё не существовала, требуется сверка application state и history. Это исправление не удаляет уже созданные дубликаты.

После согласованного backup и остановки writers примените schema **28** штатной [migration Job](../../../postgresql-migration-job-runtime-role.md) с passing admission; SQLite создаёт таблицу при следующем открытии store. Затем обновите все coordinator и Temporal workers и возобновите расписания. Новый worker со старым coordinator тоже не даёт гарантии дедупликации: старый endpoint игнорирует ключ. Смешанная пара версий для новых scheduled starts не поддерживается. Rollback требует согласованного возврата компонентов/state; сохранение новых receipts обязательно для продолжающихся Activity retries.

Проверен отдельный запуск `agatScheduledProcessWorkflow`, а не календарная точность Temporal Schedule service. Гарантия относится к созданию instance одной Activity; новый Workflow Run, включая ручной повтор или reset, имеет самостоятельный ключ. Она не утверждает exactly-once для внешних side effects. Реальные модели, PostgreSQL вместе с живым Temporal и production SLO здесь не квалифицировались.

Следующий gate — рестарт coordinator между созданием scheduled instance и `executeChild`: startup reconciliation может конкурировать с parent за тот же Workflow ID. Идемпотентность создания и восстановление владения child workflow — отдельные контракты; следующий этап проверит эту границу.
