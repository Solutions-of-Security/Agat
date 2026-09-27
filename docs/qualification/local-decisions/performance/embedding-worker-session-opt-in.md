# Ограниченный session transport в обычном worker

27.09.2026. После [проверки прототипа](./embedding-session-prototype.md), [настоящей embeddinggemma](./embedding-session-model.md) и [двухминутной серии восстановления](./embedding-session-endurance.md) в worker добавлен явный операторский режим постоянных helper. По умолчанию сохраняется `isolated` — новый процесс для каждого HTTP embedding-вызова.

## Использование

```sh
AGAT_EMBEDDING_TRANSPORT=session \
AGAT_WORKER_CONCURRENCY=4 \
python3 workers/agat_worker.py
```

Эквивалент: `--embedding-transport session --concurrency 4`. Режим принимает только `isolated` / `session`; ошибочное CLI/env значение останавливает запуск. `AGAT_EMBEDDING_TIMEOUT` / `--embedding-timeout` остаётся общим сроком одного вызова (0 < timeout ≤ 900, default 900). Режим действует на embeddings документов и запросов. Запуск требует прежних credentials/model settings, которые пример не заменяет.

Session pool принадлежит `worker_loop` и ограничен `config.concurrency` (1–32). Пустые сессии не запускают processes; helper создаётся при первом использовании. [LIFO queue](https://docs.python.org/3/library/queue.html#queue.LifoQueue) сначала выдаёт недавно освобождённую сессию, поэтому последовательная работа использует один helper даже при capacity 4. Каждая выданная сессия принадлежит одному запросу до возврата/error, затем слот возвращается в pool. Привязки процессов к произвольным caller threads нет.

Ожидание свободной сессии уменьшает оставшийся HTTP deadline. Timeout/cancellation ожидающего запроса не завершает процесс другого владельца. Отмена активного запроса, deadline и повреждение framing удаляют только его helper, выполняют reap и закрывают pipes; следующий запрос может создать замену. Полностью прочитанная HTTP-ошибка сохраняет синхронизацию и допускает reuse. Внутреннего retry HTTP нет: повтор index job остаётся решением coordinator с новой lease.

Существующий worker executor продолжает ограничивать активные leases. Штатный SIGTERM сохраняет прежний drain: принятые задачи завершаются в своих пределах, новые не берутся. [ExitStack](https://docs.python.org/3/library/contextlib.html#contextlib.ExitStack) закрывает pool после выхода из worker loop, включая путь исключения, перед telemetry shutdown. Явный `pool.close()` отдельно сигнализирует всем active guards и закрывает все helper. Polling interval не является hard real-time гарантией: [под конкурентной нагрузкой](./embedding-session-endurance.md) возврат после cancellation занимал до 113 мс.

## Входы, упаковка и сохранение эксперимента

Рабочая реализация находится в [workers/embedding_transport.py](../../../../workers/embedding_transport.py) и копируется в Docker image. Алгоритм отдельной сессии перенесён из проверенного прототипа; добавлен ограниченный pool. Прежний `scripts/lib/embedding_session.py` сохранён как экспериментальный исходник для воспроизводимости опубликованных profiles. Он не импортируется обычным worker. Новый модуль загружается только при выборе `session` в worker loop; стандартный `LocalModelClient` без явно переданной transport-функции использует прежний disposable transport.

Frame request ограничен **2 МиБ**, response — **8 МиБ**, HTTP-error prefix — **4096 байт**. Coordinator выдаёт не более **32 chunks по 4000 UTF-16 code units**, worker ограничивает query **50000 Python-символами**. Проверка через настоящий HTTP переносит 32 × 4000 и один × 50000 NUL-символов: их JSON escape занимает по шесть байт, оба payload помещаются в frame budget. Ограничение относится ко всему envelope, включая URL/model/headers; необычно большие пользовательские headers могут получить явный отказ.

URL, payload и Authorization передаются заново для каждого запроса по private stdin; чужой ответ отклоняется request ID. Контекст окружения процесса (например proxy/CA variables) фиксируется при его запуске; изменение такого окружения требует перезапуска worker. TLS/HTTP выполняет прежний `embedding_http._fetch`. Close соединения не доказывает прекращение backend computation, а session reuse не означает connection pooling.

## Проверки и границы

**26 целевых Python-тестов** проверяют перенесённую сессию и pool: реальные зависания headers/body/error, oversized reply, partial frames/pipe backpressure, ложный readiness, reuse после HTTP error, idle crash, очередь из шести caller при capacity 2, очередь/deadline/cancel, закрытие active/waiting вызовов, cleanup worker при успехе/исключении, defaults/env/CLI и предельные input sizes. Общий host worker suite — **97 тестов: 96 pass, один skip отсутствующего LangGraph**; Собранный Linux image (Python 3.13.15) прошёл **97/97** с установленным LangGraph. Перед запуском проверены `/opt/agat` пути и точное совпадение байтов `agat_worker`, `embedding_http`, `embedding_transport` с исходниками.

PostgreSQL fixture запускает настоящий coordinator и `worker_loop` в обоих режимах. Пять model HTTP faults проверяют fail первой lease, успешную вторую lease, ровно два model calls и сохранение только нового вектора. В session-режиме bounded HTTP-errors сохраняют PID; deadlines заменяют PID, а drain закрывает финальный idle helper. Отдельный сценарий держит model response до настоящего **45-секундного renewal HTTP 404**: устаревший worker не отправляет `/complete` или `/fail`, соединение закрывается до освобождения ответа, новый владелец и его lease сохраняются. Probe читает lifecycle через Python profiler и не заменяет transport/lease функции. Первоначальные **14/14** целевых PostgreSQL-проверок прошли.

Дополнительно потеря COMMIT acknowledgement проверена в шести сочетаниях: disconnect/withhold × dry-run/isolated/session. **6/6** прошли. Independent connection подтверждает первые 32 сохранённых вектора до fault; worker получает completion HTTP 503, затем две новые lease завершаются HTTP 200. Все 34 вектора, timestamps первых 32 и три события сохраняются; model HTTP вызывается ровно для новых партий **32 / 1 / 1**, без replay. В session все три вызова используют один живой helper; ошибка coordinator completion не повреждает его HTTP-протокол. После drain этот helper завершается штатно, pipes закрыты. Это контролируемый model HTTP fixture с программно заданными векторами, не новый model-quality eval.

После расширения матрица содержит **18 разных worker/PostgreSQL сценариев** (первые два dry-run COMMIT controls присутствуют в обоих локальных запусках). Полный PostgreSQL suite теперь содержит 83 сценария и выполняется в CI.

```sh
npm run test:worker
npm run typecheck
bash scripts/test-fleet-ha-postgres.sh --test-name-pattern='real Python embedding worker'
docker build -f workers/Dockerfile -t agat-worker:embedding-session .
```

[Журнал проверок, исходники и SHA evidence](./evidence/2026-09-27/embedding-worker-session-opt-in/checks.json): worker/PG результаты выше, полный typecheck, 12 Node + 232 Python в docs suite; architecture audit — PASS, без ошибок и предупреждений.

Нагрузочные значения прототипа не являются SLO обычного worker. В lazy pool первый запрос платит за запуск процесса; ускорение ожидается при повторном использовании. Постоянная память требует бюджета: в предыдущих опытах четыре idle helper занимали суммарно до 142 МиБ, а concurrency может достигать 32. Windows этим набором не квалифицирован. Переключение обратно на `isolated` и перезапуск worker удаляет постоянный pool; схемы/индекс/leases совместимы.

Следующий gate — полный RAG workload с реальной моделью, проверкой downstream retrieval/primary и памятью обычного worker при выбранной concurrency. Опубликованные проверки качества модели, shadow и правила routing не меняются. Сам merge не переключает работающие deployments.
