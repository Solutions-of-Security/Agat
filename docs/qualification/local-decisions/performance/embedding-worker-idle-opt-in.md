# Idle timeout обычного embedding worker

Дата: 28.09.2026. [Модельный опыт](./embedding-idle-model.md) показал освобождение около 29 MiB helper RSS на слот и более медленные первые запросы после простоя. Теперь проверенный механизм доступен как явная настройка обычного worker. Default transport — `isolated`; для `session` освобождение по idle по умолчанию выключено.

## Настройка и границы

```sh
AGAT_EMBEDDING_TRANSPORT=session \
AGAT_EMBEDDING_IDLE_TIMEOUT=30 \
python3 workers/agat_worker.py
```

Эквивалентные параметры: `--embedding-transport session --embedding-idle-timeout 30`. Здесь 30 секунд — пример настройки, не измеренный оптимум или SLO. Прежние credentials, models и coordinator settings всё ещё необходимы. Значение — конечное число `0…3600`; `0` отключает retirement, положительное значение с `isolated` отклоняется при старте. CLI перекрывает env. Отрицательные, NaN/Infinity, строковые ошибки и некорректные programmatic values отклоняются до начала worker loop.

Оператор выбирает срок по частоте burst, допустимой задержке первого запроса и бюджету памяти. Откат — значение `0` для прежнего постоянного session pool либо возврат к `isolated` одновременно с отключением idle timeout. Настройки действуют и на индексирование документов, и на query embeddings; размер пула по-прежнему ограничен worker concurrency (1…32).

Pool создаёт один maintenance thread. Срок простоя отсчитывается после завершения последнего transport exchange, поэтому helper может закрыться, пока coordinator ещё обрабатывает COMMIT. Это не повторяет модельный вызов и не меняет lease. Освобождение начинается только после получения незанятого слота под общей блокировкой с новым caller. Активный HTTP пропускается. Если новый запрос застал retirement, ожидание и следующий startup входят в первоначальный request timeout, cancellation сохраняется. Внутренних повторов model HTTP нет.

Время sweep — четверть idle timeout, ограниченная 5 мс…1 с. Срок определяет пригодность к освобождению, не жёсткую гарантию планировщика. После EOF helper получает bounded wait с последующим kill при необходимости, процесс reaped и оба pipe закрыты. Model server/его веса этим механизмом не управляются.

При обычном SIGTERM закрывается приём заданий и сохраняется drain уже полученных lease; pool закрывается после завершения исполнителей. Maintenance error закрывает admission пула и сигнализирует активным helper. Worker проверяет состояние перед получением нового задания и после drain: ошибка выводится как `Embedding idle maintenance failed: <type>`, запуск завершается с ошибкой. Heartbeat получает stop в `finally`; обслуживающий поток присоединяется при context-manager close. Плановый recovery — исправление причины и новый запуск worker, без скрытого replay.

## Проверки поведения

[Общие lifecycle-сценарии](../../../../scripts/test/test_embedding_idle_pool.py) параметризованы типом pool: сохранённый qualification prototype и production реализация выполняют одни и те же 14 проверок настоящих helper/HTTP. В production worker добавлены ещё шесть проверок конфигурации, ресурсов и отказа обслуживания: всего **20/20** целевых тестов. Среди сценариев — active HTTP дольше idle timeout, queued cancellation, deadline при ожидании retirement, concurrent close, отдельный idle сосед и два burst по 32 helper с возвратом FD.

[Реальный worker/PostgreSQL](../../../../apps/coordinator/test/fleet-ha-postgres.integration.test.ts) прошёл **5/5** новых сценариев:

- При timeout 0 два документа используют один PID; после включения 0,15 с первый helper reaped до второго lease, второй документ получает новый PID. Оба вектора точны, lease различаются и дают по одному terminal success. Второй HTTP намеренно живёт дольше idle timeout; SIGTERM сохраняет drain. Restart видит готовые документы и не вызывает модель повторно.
- После настоящего HTTP 404 от production renewer (интервал 45 с) длительный active HTTP закрывается до ответа модели; нет stale complete/fail, переназначенный владелец сохраняется.
- Разрыв и удержание COMMIT acknowledgement: helper закрывается во время ожидания, worker продолжает только новые batch/lease. На каждый сценарий 34 точных вектора, три события и ровно три model-вызова; исходные 32 committed chunks сохранены. Все renewer/helper закрыты.

Эти PostgreSQL-сценарии используют контролируемый HTTP endpoint с детерминированными vectors, обычные процессы coordinator/worker и настоящий PostgreSQL; они доказывают lifecycle и durable state, а не качество модели. Предыдущий модельный опыт относится к qualification prototype. Сквозная проверка нового production opt-in с настоящей моделью остаётся следующим gate.

## Поставка и evidence

Реализация находится в существующем `workers/embedding_transport.py`, поэтому Dockerfile уже включает нужный runtime-файл; production не импортирует `scripts/lib`. [Worker tests](../../../../workers/test_embedding_idle_transport.py) проверяют выбранный pool и закрытие ресурсов при нормальном и аварийном выходе.

Полный worker suite: macOS/Python 3.14.3 — **121 pass + 1 skip** (32,289 с); Linux/Python 3.13.15 — **122/122** (34,660 с). Linux image `sha256:63c107345aca63eaaac8ac28b6f96ad68163aba335c9cffb9dbf62f037647343` запущен с `--init --network none`; фактические модули `/opt/agat` до тестов побайтно сверены с runtime-исходниками. Проверка типов всех трёх приложений — pass. `docs:check`: 12 Node + 303 Python tests, 1671 локальная ссылка в 201 Markdown-файле, 13 process templates. Architecture audit: 0 ошибок/предупреждений.

[Журналы, source hashes и образ](./evidence/2026-09-28/embedding-worker-idle-opt-in/checks.json) фиксируют проверки поставки. В первом запуске полного Linux suite ошибся harness: `python -` передал multiprocessing spawn несуществующий `/repo/<stdin>`. Сохранён исходный журнал; запуск исправлен на `python -c`, runtime не менялся и тест не отключался.

Решение сохраняет раздельные размер пула, idle retention и deadline, как в [HTTPX resource limits](https://www.python-httpx.org/advanced/resource-limits/). Владение основано на [Python Lock.acquire](https://docs.python.org/3/library/threading.html#threading.Lock.acquire), освобождение потока — на явном join. Для процессов дополнительно проверены reap, pipes, потеря lease и сохранность COMMIT. Автоматический выбор производственного timeout не выполнен.
