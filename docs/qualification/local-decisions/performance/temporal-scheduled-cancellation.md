# Отмена scheduled-start при неизвестном результате создания

Дата: **28.09.2026**. Продолжение [отмены уже запущенного child](./temporal-cancellation.md). Закрыта граница между отправкой scheduled-start Activity и появлением child workflow.

## Воспроизведение

Два сценария на настоящем Temporal подтвердили сбой предыдущего кода. При отмене до передачи запроса coordinator родитель уже завершался как `CANCELLED`, а поздний HTTP request создавал активный instance: [до commit](./evidence/2026-09-28/temporal-scheduled-cancellation/baseline/before-commit.json). При отмене после commit, но до ответа Activity, instance оставался `waiting_external`: [после commit](./evidence/2026-09-28/temporal-scheduled-cancellation/baseline/after-commit.json). В обоих случаях child ещё не существовал. [Обе проверки падали](./evidence/2026-09-28/temporal-scheduled-cancellation/baseline/integration.log).

## Контракт

Schema **30**, `agat-scheduled-cancellation-intent-v30`, добавляет nullable `cancel_requested_at` к `process_scheduled_start_receipts`. Обычная receipt сохраняет прежнюю identity и NULL. Отмена до создания сохраняет тот же request SHA и key, timestamp отмены и JSON `null` в `response_json`. Таким образом, отсутствие instance не означает отсутствие принятого запроса отмены.

`cancelScheduledProcess` и `startScheduledProcess` используют одну блокировку проекта: SQLite `BEGIN IMMEDIATE`, PostgreSQL `projects FOR UPDATE`. Проверка receipt, запись cancellation intent и отмена существующего instance входят в одну транзакцию. Если create прошёл первым, cancel находит его receipt и запускает обычную application cleanup. Если cancel прошёл первым, последующий create возвращает **409 `SCHEDULED_START_CANCELLED`** без нового run. Изменённый payload с тем же key по-прежнему возвращает отдельный idempotency conflict. Это опирается на [transaction lifetime row locks PostgreSQL](https://www.postgresql.org/docs/17/explicit-locking.html#LOCKING-ROWS).

Внутренний `/api/v1/internal/processes/:processId/scheduled-cancel` требует Temporal token, projectId и bounded Idempotency-Key. Возвращает state существующего instance либо JSON `null`, если instance ещё не создавался или удалён retention. Повтор сохраняет первый timestamp intent. Чужой project scope не меняет реальный instance; его отдельный intent не раскрывает наличие чужой receipt. FORCE RLS и разделение migration/runtime/tenant roles сохраняются. Receipts с intent, как и обычные receipts, хранятся до удаления проекта и не очищаются вместе с run.

Новый parent назначает creation Activity явный ID `scheduled-start-v1`. Cleanup вычисляет ключ из тех же namespace, Workflow Run ID и **ID creation Activity**, а не ID cleanup Activity. [Temporal поддерживает явный Activity ID](https://typescript.temporal.io/api/interfaces/common.ActivityOptions#activityid). Для новой ветки явно задан `TRY_CANCEL`: родитель может начать cleanup, даже если исходный HTTP request ещё не завершён. Сохранённый intent запрещает поздний create; остановка сетевого запроса не используется как гарантия отсутствия записи.

Parent различает успешный start child и отмену до этой точки. До child он выполняет cleanup в `CancellationScope.nonCancellable`, после child остаётся прежний протокол child cleanup. Если side effect успел завершиться до появления child и приложение запустило компенсацию, parent опрашивает состояние durable timer, обычно раз в 60 секунд, и ждёт terminal state. Затем повторно выбрасывает cancellation. Cleanup transient retries остаются без общего лимита, с ограничением каждого HTTP/Activity attempt из предыдущего этапа.

Ветка защищена patch `agat-scheduled-cancellation-intent-v1`; старые history сохраняют прежний Activity ID и `executeChild`. [Patching](https://docs.temporal.io/develop/typescript/workflows/versioning) проверяется native replay реальных старых и новых history. Уже выполняющиеся pre-patch родители при replay старой стартовой части сохраняют старую ветку; это изменение не исправляет их автоматически.

## Проверки

- [SQLite/HTTP](../../../../apps/coordinator/test/scheduled-cancellation.test.ts): upgrade 29→30 с существующей receipt, reopen, отмена до/после create, конфликт payload, атомарный rollback intent при ошибке cleanup, retention, отдельная occurrence, auth и проекты. **3 pass**: [лог](./evidence/2026-09-28/temporal-scheduled-cancellation/sqlite-http.log).
- [PostgreSQL](../../../../apps/coordinator/test/fleet-ha-postgres.integration.test.ts): migration из физической структуры без новой колонки, сохранение старой receipt, create и cancel из двух отдельных процессов на одной блокировке, отсутствие активного instance после обоих ответов. Protocol proxy подтверждает commit intent на сервере и теряет ответ: первый процесс получает `AGAT_COMMIT_UNKNOWN`, retry восстанавливает intent, поздний create отклоняется. Tenant другого проекта не видит и не изменяет receipt. Вместе с тремя соседними сценариями — **4 pass**: [лог](./evidence/2026-09-28/temporal-scheduled-cancellation/postgres-verified.log). Предыдущий [совместный запуск](./evidence/2026-09-28/temporal-scheduled-cancellation/postgres.log) воспроизвёл конфликт тестового имени `Foreign`; исправлены fixture names, ограничения базы сохранены.
- [Offline migration](../../../../apps/coordinator/test/sqlite-postgres-migrator.integration.test.ts): непустая receipt и intent с JSON `null`/timestamp импортированы вместе, reconciliation и rollback rehearsal прошли. **2 pass**: [лог](./evidence/2026-09-28/temporal-scheduled-cancellation/migration.log).
- [Живой Temporal до commit](./evidence/2026-09-28/temporal-scheduled-cancellation/verified/before-commit.json): parent отменён, затем proxy выпускает задержанный create. Создано **0 instances**, HTTP 409 с ожидаемым кодом.
- [Живой Temporal после commit](./evidence/2026-09-28/temporal-scheduled-cancellation/verified/after-commit.json): сохранён **1 cancelled instance**, child отсутствует. Первый cleanup reply потерян, retry возвращает согласованное состояние.
- [Компенсация до child](./evidence/2026-09-28/temporal-scheduled-cancellation/verified/after-commit-compensating.json): synthetic HTTP lease завершён до отмены; parent остаётся RUNNING во время компенсации и durable timer, затем завершается CANCELLED. Единственная compensation завершена, application cancelled, child не создавался. Внешний side effect моделируется завершением lease через store; запросов к `example.test` нет.

Все **9** живых сценариев прошли: [лог](./evidence/2026-09-28/temporal-scheduled-cancellation/integration.log). [Coordinator regression](./evidence/2026-09-28/temporal-scheduled-cancellation/coordinator.log): **292 pass, 9 gated skip**, gated сценарии исполнились отдельно. [Replay](./evidence/2026-09-28/temporal-scheduled-cancellation/replay.log) включает **18 histories**, в том числе обе legacy histories с воспроизведённым дефектом и три новые. [Typecheck](./evidence/2026-09-28/temporal-scheduled-cancellation/typecheck.log) и [checks.json с SHA исходных файлов](./evidence/2026-09-28/temporal-scheduled-cancellation/checks.json) фиксируют проверенную версию. Полный Fleet/HA набор остаётся обязательным CI gate перед merge.

```bash
node --import tsx --test apps/coordinator/test/scheduled-cancellation.test.ts
npm run fleet:test-temporal-rag
npm run fleet:test-ha-postgres -- --test-name-pattern='upgrades scheduled cancellation|serializes Temporal cancellation|deduplicates scheduled-start'
npm run fleet:test-state-migration
npm run test:temporal
```

## Обновление и границы

Приостановите расписания и завершите старые scheduled parents/Activities, затем остановите writers, сохраните согласованный backup и примените schema **30** штатной migration Job с admission. Для SQLite сначала примените штатный store upgrade; offline migrator требует source v30. Обновите coordinator, затем worker bundle по canary/ramp; возобновляйте расписания на новом parent коде. Старый worker не знает cleanup protocol, а новый worker со старым coordinator не получает гарантии intent. Receipt/intent и patch markers должны сохраняться при recovery; rollback старого binary поверх нового state не поддерживается.

Workflow termination, timeout/reset и окончательный отказ Activity без cancellation остаются отдельными границами. В частности, обычный scheduled-start всё ещё имеет конечный retry budget: потерянный результат после его исчерпания не равен обработанной отмене. Следующий gate — такая ошибка создания и согласование результата без оставленного активного instance. Предметное качество моделей, реальные compensation side effects и production SLO не квалифицированы.
