# Отмена полного RAG при позднем primary response

Дата: **28.09.2026**. Продолжение [RAG/Temporal/PostgreSQL](./temporal-postgres-rag.md). Gate проверяет согласованную отмену и сохранность принятых данных, когда Python worker ещё ожидает ответ модели.

## Сценарий

[Общий RAG fixture](../../../../apps/coordinator/test/temporal-rag.integration.test.ts) теперь выполняет recovery и cancellation для обоих embedding transports. PostgreSQL запускается после штатной migration/admission с runtime/tenant roles и isolated retrieval; SQLite использует прежний путь. Модель и shadow runtime возвращают учебные ответы.

1. Worker индексирует два документа; первый primary stage и его shadow observation приняты coordinator.
2. Второй primary HTTP request удерживается model fixture. Потеря первого tick reply уже вызвала настоящий Activity retry, а SIGKILL/replacement Temporal worker восстановил history.
3. Test запрашивает Workflow cancellation. Первый `/cancel` reply теряется после application commit; повторный cleanup запрос возвращает cancelled state. Temporal и application завершают отмену до выпуска primary response.
4. Model fixture выпускает поздний ответ. Настоящий Python worker получает `ApiError: Активная аренда не найдена`. Последующая попытка записать failure также отклонена. Проверка ожидает конкретную причину отказа, а не произвольную сетевую ошибку.
5. Первый output сохранён, второй stage cancelled с NULL output, третий stage не появился. Сохраняется только первое shadow observation; поздний primary не запускает новый shadow inference.

Cancellation сценарий совершает **2 primary вызова** и обрабатывает **4 embedding items**: два документа и два поисковых запроса. В trace сохранены **4 source references** из двух retrieval records. Это включает provenance уже полученного контекста отменённого stage; его output не принят.

Производственный cancellation protocol не менялся: этот этап добавляет совместную проверку ранее реализованных механизмов. Сохраняются schema **30**, lease fencing и последовательность Workflow cleanup. Cancellation histories проходят native replay без HTTP Activities, model calls и изменения trace.

## Доказательства

- PostgreSQL: [isolated](./evidence/2026-09-28/temporal-rag-cancellation/postgres/isolated-cancel.json) и [session](./evidence/2026-09-28/temporal-rag-cancellation/postgres/session-cancel.json) сохраняют конкретный worker diagnostic о завершённой аренде. [Live suite](./evidence/2026-09-28/temporal-rag-cancellation/postgres.log) прошёл все **4 RAG сценария** — два recovery и две отмены. Шесть дополнительных file entries Node runner не считаются RAG сценариями.
- SQLite: [isolated](./evidence/2026-09-28/temporal-rag-cancellation/sqlite/isolated-cancel.json) и [session](./evidence/2026-09-28/temporal-rag-cancellation/sqlite/session-cancel.json) подтвердили тот же контракт.
- [Полный SQLite Temporal набор](./evidence/2026-09-28/temporal-rag-cancellation/sqlite.log): **14 pass**, включая обе новые отмены RAG.
- [Coordinator regression](./evidence/2026-09-28/temporal-rag-cancellation/coordinator.log): **293 pass, 14 gated skip**. Новые gated cases исполняются в live suites.
- [Replay](./evidence/2026-09-28/temporal-rag-cancellation/replay.log) включает **34 histories**, в том числе четыре новые cancellation history; **3 configuration tests**. [Workspace typecheck](./evidence/2026-09-28/temporal-rag-cancellation/typecheck.log) и [strict test typecheck](./evidence/2026-09-28/temporal-rag-cancellation/test-typecheck.log) прошли. [Checks с SHA](./evidence/2026-09-28/temporal-rag-cancellation/checks.json) фиксируют проверенную версию.

```bash
npm run fleet:test-temporal-postgres-rag
npm run fleet:test-temporal-rag
npm run test:temporal
```

Обе live suites уже входят в обязательный CI. Для ручного сохранения evidence задайте `AGAT_TEMPORAL_RAG_EVIDENCE_DIR` в отдельную пустую директорию внутри `/docs`; запись существующих файлов отклоняется.

## Изоляция и эксплуатационные границы

В PostgreSQL собственный tenant видит run, instance, два retrieval records и два chunks. Другой созданный project scope не видит эти строки; прямое чтение release registry остаётся запрещено. Принятый output предыдущего stage сохраняется после cancellation и replay.

Проверка намеренно выпускает поздний primary response, чтобы проверить защиту записи. Она **не доказывает остановку сетевого запроса или вычисления модели** сразу после Workflow cancellation. Текущий primary `_chat` использует синхронный HTTP с socket timeout до 900 секунд; передача lease cancellation event в этот transport пока отсутствует. Следующий gate — реальное закрытие ожидающего primary HTTP после отказа renewal, освобождение worker slot и ограничение ресурсов. Backend computation после disconnect требует отдельной гарантии самого model server.

HA/failover PostgreSQL, реальные business side effects и предметная qualification моделей здесь не заявлены.
