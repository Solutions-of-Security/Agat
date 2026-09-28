# Восстановление RAG после SIGKILL Python worker

Дата: **28.09.2026**. Продолжение [отмены coordinator search HTTP](./knowledge-http-cancellation.md). Предыдущие Temporal gates перезапускали orchestration worker. Этот gate проверяет потерю самого Python worker во время primary HTTP, после уже принятого результата первого stage.

## Что проверяется

[Сквозной fixture](../../../../apps/coordinator/test/temporal-rag.integration.test.ts) удерживает второй primary response, убивает Python worker через `SIGKILL` и наблюдает закрытие model connection без выпуска ответа. Перед завершением worker собираются только PID/PPID/state его дочерних процессов; после завершения проверяется, что ни один из этих helpers больше не выполняется. Завершённый Unix zombie считается exited: его exit status собирает системный init/subreaper, а погибший родитель сделать это уже не может.

Это соответствует границе ОС: [SIGKILL нельзя перехватить](https://docs.python.org/3/library/signal.html#signal.SIGKILL), а Unix [меняет parent PID после смерти родителя](https://docs.python.org/3/library/os.html#os.getppid). Существующий guard в [HTTP helper](../../../../workers/embedding_http.py) получает ожидаемый PID до spawn и завершает helper при смене владельца. Production worker менять не потребовалось. Проверка относится к Unix; Windows PPID не является таким индикатором.

Замещающий Python worker запускается с прежними credentials, пока старая lease ещё может быть активна. Для этого опыта coordinator настроен с **lease TTL 30 секунд**, вместо штатных **180 секунд**; ни часы, ни сроки в базе не подменяются. Fixture ждёт настоящий `lease.expired`, новую попытку primary и затем отправляет поздний completion старой lease. Coordinator отвечает HTTP 400 с `Активная аренда не найдена`.

Сохраняются тот же application instance, run и Temporal Run ID. Полный снимок первого принятого stage не меняется; второй stage получает новую lease и `attempt=2`, третий выполняется с `attempt=1`. Старый HTTP response остаётся удержанным до завершения восстановления. Повтор primary разрешён, поскольку незавершённая попытка не приняла output и не выполняла внешних MCP side effects.

## Provenance и повторная попытка

На процесс приходятся **4 primary requests**, **6 embedding items** (два документа и четыре query attempts), **3 принятых shadow observations**, **4 retrieval records** и **8 source references**. Ответ брошенного второго primary request не принимается. Её завершённый retrieval сохраняется наряду с retrieval повторной попытки.

[Проверка provenance](../../../../scripts/lib/decision-rag.ts) по умолчанию по-прежнему требует ровно один retrieval event на stage. Crash fixture явно ожидает два для повторённого stage и проверяет query model/dimensions/SHA, оба source chunks и citation aliases **у каждой попытки**. Неверное число событий и повреждение query/content любой попытки отклоняются [регрессионным тестом](../../../../apps/coordinator/test/decision-rag-workflow.test.ts).

В каждом сценарии также сохраняются потеря tick acknowledgement, restart Temporal worker, durable timer и native history replay без I/O или записей. PostgreSQL проверяет runtime/tenant roles, запрет чтения release registry и RLS: `[1 run, 1 instance, 4 retrievals, 2 chunks]` своему проекту, нули чужому. Schema **30** и Workflow commands не менялись.

## Результаты

Первый [SQLite isolated запуск](./evidence/2026-09-28/python-worker-recovery/first.log) прошёл; его [trace](./evidence/2026-09-28/python-worker-recovery/first/isolated-worker-crash.json) содержит проверенные PID counts, отказ старой lease, новую попытку и неизменённый первый stage. Проверка подтверждает существующий recovery без изменения production worker.

Полная матрица прошла: **22/22 SQLite Temporal сценария** и **12/12 PostgreSQL RAG сценариев**. Дополнительные шесть PostgreSQL Node file entries без совпавшего имени не считаются RAG-сценариями. В isolated наблюдался один owned helper, в session — два (primary и embedding session); все прекратили выполнение. Сохранены пять новых crash traces, включая первый диагностический запуск, и полные логи регрессии. Прежние сценарии имеют полный evidence в предыдущем gate.

Documentation checks: **12 Node + 318 Python tests**, **1945 local link targets** в **216 Markdown files**.

- [SQLite Temporal](./evidence/2026-09-28/python-worker-recovery/sqlite.log) и [PostgreSQL RAG](./evidence/2026-09-28/python-worker-recovery/postgres.log); crash traces находятся в каталогах [SQLite](./evidence/2026-09-28/python-worker-recovery/sqlite/) и [PostgreSQL](./evidence/2026-09-28/python-worker-recovery/postgres/).
- [Coordinator regression](./evidence/2026-09-28/python-worker-recovery/coordinator.log): **293 pass, 22 gated skip**. [Native replay](./evidence/2026-09-28/python-worker-recovery/replay.log): **34 histories** и **3 configuration tests**.
- [Workspace types](./evidence/2026-09-28/python-worker-recovery/typecheck.log), [strict fixture/verifier types](./evidence/2026-09-28/python-worker-recovery/test-typecheck.log), [documentation checks](./evidence/2026-09-28/python-worker-recovery/docs.log) и [SHA manifest](./evidence/2026-09-28/python-worker-recovery/checks.json).

```bash
npm run fleet:test-temporal-rag
npm run fleet:test-temporal-postgres-rag
npm run test:coordinator
npm run test:temporal
```

## Границы

Ответы модели синтетические. Disconnect не доказывает прекращение inference на model server; retry может повторить вычисление незавершённой попытки. Измеренные времена наблюдения helpers не являются latency SLO. Уменьшенный TTL предназначен только для теста и не означает рекомендацию менять production renewal/TTL. Предметная qualification остаётся открытой.

Следующий gate — неизвестный исход completion HTTP: coordinator принял output и shadow, но worker погиб до получения acknowledgement. Нужна проверка сохранности принятого stage, отсутствия его повторного model call и корректного продолжения после restart.
