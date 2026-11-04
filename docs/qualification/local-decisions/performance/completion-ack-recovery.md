# Восстановление после потери acknowledgement принятого completion

Дата: **28.09.2026**. Продолжение [SIGKILL Python worker до completion](./python-worker-recovery.md). Этот gate проверяет противоположную границу: coordinator уже принял второй output и shadow, но Python worker ещё ждёт HTTP acknowledgement.

## Сценарий и контракт

[Temporal RAG fixture](../../../../apps/coordinator/test/temporal-rag.integration.test.ts) пропускает настоящий второй `/complete` через loopback proxy, читает успешный ответ coordinator и удерживает его перед worker. До SIGKILL проверяются два completed stages и два принятых shadow observations. Proxy не повторяет запрос. Модель уже вернула второй ответ; незавершённым остаётся ожидание completion HTTP.

После `SIGKILL` замещающий Python worker использует прежние credentials и продолжает следующий stage. Штатный lease TTL **180 секунд** сохранён; `lease.expired` не требуется. Первые два stage и их shadow observations сравниваются целиком с сохранёнными снимками. У всех трёх model stages остаётся `attempt=1`, у второго сохраняется прежний completed lease ID. Temporal Run ID не меняется.

Отдельный поздний POST по старой lease получает HTTP 400 с `Активная аренда не найдена`; ранее принятый output сохраняется. Такой отказ повторной отправки не означает, что исходный completion не был принят. Источником состояния остаётся coordinator. В соответствии с [HTTP Semantics §9.2.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.2), потеря ответа сама по себе не обосновывает автоматический повтор POST с неизвестным результатом. Новый retry в worker не добавлялся.

До конца опыта proxy продолжает удерживать старый ответ. Проверяется закрытие его соединения, завершение оставшихся Unix helpers, отсутствие повторного model call для принятого stage и продолжение процесса. Для isolated transport primary/embedding helpers к моменту completion уже собраны; session transport может сохранять idle embedding helper, который должен завершиться после потери родителя.

## Сохранность данных

Выполненный процесс содержит **3 primary requests**, **5 embedding items**, **3 shadow observations**, **3 retrieval records** и **6 source references**. Для каждого stage проверяются output, model-call count, query/source SHA, provenance и ссылки на оба документа. Повторный completion не добавляет output, shadow, retrieval или stage attempt.

Также проверяются потеря первого tick acknowledgement, restart Temporal worker, durable timer и native history replay без I/O или записей. PostgreSQL использует настоящие runtime/tenant roles: своему проекту видны `[1 run, 1 instance, 3 retrievals, 2 chunks]`, чужому — нули; чтение release registry запрещено. Schema **30**, Workflow commands и production worker не меняются.

## Проверки

Первый [SQLite isolated запуск](./evidence/2026-09-28/completion-ack-recovery/first.log) прошёл. Его [trace](./evidence/2026-09-28/completion-ack-recovery/first/isolated-completion-crash.json) сохраняет evidence принятого completion, два неизменённых stage/shadow и отсутствие повторной попытки. SQLite проверил все **4 crash-сценария**. PostgreSQL launcher объединяет переданный name pattern со своим `Temporal RAG` через OR, поэтому текущий прогон проверяет **всю PostgreSQL RAG матрицу**; все **14/14** сценариев прошли. Шесть дополнительных Node file entries не считаются RAG-сценариями. Сохранены девять crash traces (первый запуск и по четыре для каждой базы), включая перепроверку прежнего SIGKILL до completion.

- [SQLite](./evidence/2026-09-28/completion-ack-recovery/sqlite.log) и [PostgreSQL](./evidence/2026-09-28/completion-ack-recovery/postgres.log) проверяют `worker-crash` и `completion-crash` для isolated/session transports.
- [Coordinator regression](./evidence/2026-09-28/completion-ack-recovery/coordinator.log), [strict fixture types](./evidence/2026-09-28/completion-ack-recovery/test-typecheck.log), [documentation checks](./evidence/2026-09-28/completion-ack-recovery/docs.log) и [SHA manifest](./evidence/2026-09-28/completion-ack-recovery/checks.json).

```bash
npm run fleet:test-temporal-rag -- --test-name-pattern='(worker-crash|completion-crash)'
npm run fleet:test-temporal-postgres-rag
```

В первом [coordinator regression](./evidence/2026-09-28/completion-ack-recovery/coordinator-initial.log) один primary workflow fixture вернул пустой результат проверки. Исходный wrapper скрывал внутренний assertion; точная причина этого запуска не установлена. Повтор с дополнительной [диагностикой](./evidence/2026-09-28/completion-ack-recovery/primary-diagnostic.log) прошёл. В тесте выявлена зависимость гарантии параллельности от 100-мс задержки ответа; она заменена явным барьером первого запроса до прихода второго в каждой фазе. Повторный полный coordinator regression прошёл: **293 pass, 24 gated skip**. Диагностика status/failure/concurrency сохранена для будущих отказов.

Первый [documentation run](./evidence/2026-09-28/completion-ack-recovery/docs-initial.log) выполнил **12 Node + 318 Python tests**, затем обнаружил временно отсутствующий `coordinator.log`: исходный неуспешный лог был переименован для сохранения до готовности повторного прогона. После появления финального лога ссылки и process catalog повторно прошли: **1960 local link targets** в **217 Markdown files**; исходный отказ сохранён.

Обычные команды без фильтра и обязательный CI запускают всю Temporal RAG матрицу, включая предыдущие retry/cancellation gates. Новый completion-crash добавляет по два сценария для каждой базы.

Ответы модели синтетические; это проверка сохранности state и восстановления, без предметной qualification и latency SLO. Ручной restart worker в fixture не является установкой production supervisor. Следующий gate — SIGKILL/restart самого coordinator после commit completion, с сохранением результата, provenance и того же Temporal workflow.
