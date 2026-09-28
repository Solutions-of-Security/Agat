# Restart coordinator после принятого completion

Дата: **28.09.2026**. Продолжение [потери completion acknowledgement у Python worker](./completion-ack-recovery.md). Этот gate проверяет потерю процесса coordinator после commit, пока тот же Python worker ещё ждёт подтверждение второго completion.

## Граница отказа

[Temporal RAG fixture](../../../../apps/coordinator/test/temporal-rag.integration.test.ts) удерживает успешный ответ второго `/complete` после чтения настоящего coordinator response. Сохраняются полные snapshots двух completed stages, двух shadow observations и уже записанных source references. Затем coordinator завершается через `SIGKILL`; Python worker продолжает ждать.

Пока coordinator отсутствует, настоящий `processChangedV1` Update принимается существующим Temporal workflow. Fixture ждёт как минимум один реальный отказ соединения tick Activity. Proxy преобразует только ожидаемый `fetch failed` к остановленному endpoint в HTTP 503; другие ошибки остаются test failures. После restart используется прежнее хранилище и прежние coordinator settings, а proxy направляет новые запросы на новый ephemeral port.

До выпуска acknowledgement новый coordinator обслуживает аутентифицированный GET run trace. Принятые stages/shadows сравниваются целиком с snapshots; source references остаются прежними. Старый completion отвергается с HTTP 400. Python PID не меняется, его процесс жив, количество primary calls всё ещё равно двум, клиентское completion-соединение открыто.

Только после этих проверок proxy возвращает удержанный успешный acknowledgement. Тот же Python worker выполняет третий stage. Сохраняются application instance/run, Temporal Run ID, completed lease второго stage и `attempt=1` у принятых stages. В отличие от [потери незавершённой lease](./python-worker-recovery.md), настоящий expiry не требуется; используется штатный TTL **180 секунд**.

## Данные и проверки

Итог: **3 primary requests**, **5 embedding items**, **3 shadow observations**, **3 retrieval records**, **6 source references**, без `lease.expired`. Проверяются query/source SHA, provenance и citations каждого stage. History replay не выполняет I/O и не меняет trace. Startup reconciliation coordinator работает с уже существующим workflow; нового Temporal Run ID не появляется.

PostgreSQL использует штатные runtime/tenant roles и isolated retrieval. Собственному проекту видны `[1 run, 1 instance, 3 retrievals, 2 chunks]`, чужому — нули; release registry остаётся недоступным tenant. Schema **30**, Workflow commands и production behavior не менялись.

Первый [SQLite isolated запуск](./evidence/2026-09-28/coordinator-completion-recovery/first.log) прошёл; [trace](./evidence/2026-09-28/coordinator-completion-recovery/first/isolated-coordinator-crash.json) фиксирует реальный tick outage, принятый Update и данные, прочитанные из нового coordinator. Прошли **6/6 SQLite** и **6/6 PostgreSQL crash-сценариев**. По шесть дополнительных Node file entries без подходящих тестов не считаются crash-сценариями. Сохранены пять новых coordinator-crash traces и полные логи матрицы, включая перепроверку двух Python crash-границ. Coordinator regression: **293 pass, 26 gated skip**; documentation checks: **12 Node + 318 Python tests**, **1973 local link targets** в **218 Markdown files**.

- [SQLite crash matrix](./evidence/2026-09-28/coordinator-completion-recovery/sqlite.log), [PostgreSQL crash matrix](./evidence/2026-09-28/coordinator-completion-recovery/postgres.log).
- [Coordinator regression](./evidence/2026-09-28/coordinator-completion-recovery/coordinator.log), [strict fixture types](./evidence/2026-09-28/coordinator-completion-recovery/test-typecheck.log), [documentation checks](./evidence/2026-09-28/coordinator-completion-recovery/docs.log), [SHA manifest](./evidence/2026-09-28/coordinator-completion-recovery/checks.json).

```bash
npm run fleet:test-temporal-rag -- --test-name-pattern='(worker-crash|completion-crash|coordinator-crash)'
npm run fleet:test-temporal-postgres-rag -- --test-skip-pattern='/(recover|cancel|interrupt|query-interrupt|search-interrupt):'
```

Для PostgreSQL используется отрицательный фильтр: launcher уже задаёт положительный `Temporal RAG`, а несколько name patterns объединяются через OR. Без фильтров обязательный CI проверяет всю матрицу, включая предыдущие cancellation/retry gates.

При CI-review CodeQL отметил lease ID, перенесённый из пути loopback-запроса в stale-completion URL. Host уже закреплён за запущенным coordinator; идентификаторы lease/run теперь дополнительно кодируются как отдельные path segments. Это соответствует [рекомендации CodeQL ограничивать входы в пути исходящего запроса](https://codeql.github.com/codeql-query-help/javascript/js-request-forgery/). После изменения повторно прошли оба PostgreSQL coordinator-crash сценария и strict TypeScript: [лог](./evidence/2026-09-28/coordinator-completion-recovery/url-review-postgres.log), [SHA и результаты перепроверки](./evidence/2026-09-28/coordinator-completion-recovery/url-review-checks.json). Исходный SHA manifest и результаты до review сохранены.

Ответы модели синтетические. Это crash/restart одного coordinator на том же хранилище; replica/region failover, потеря базы, production supervisor и предметная qualification этим опытом не проверяются. Следующий этап — повтор измерений реальной локальной RAG-цепочки после изменений HTTP isolation: зафиксировать model/source profiles и prompts, проверить результаты и ресурсы, затем выбирать оптимизации по измерениям.
