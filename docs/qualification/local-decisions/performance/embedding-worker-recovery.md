# Восстановление настоящего embedding worker после потерянного ответа

Дата: **27.09.2026**. Продолжение [проверки embedding COMMIT](./embedding-commit-acknowledgement.md). Предыдущий этап проверял серверный контракт через HTTP; здесь тот же отказ проходит через обычный Python worker loop, клиент и пул исполнения.

## Исполнитель и наблюдение

Два сценария запускают собранный coordinator, PostgreSQL 17.6 и отдельный Python-процесс. Worker получает собственные credentials через stdin, сохраняет их в закрытый временный файл и использует стандартные `parse_args`, `CoordinatorClient`, `worker_loop`, `execute_knowledge_lease` и `knowledge_lease_renewer`. Concurrency — 1, poll interval — 0,2 с, `--once` выключен. Web, model discovery и telemetry выключены. Модельный endpoint не вызывается: штатный `--dry-run` строит 32-мерный вектор из SHA256 текста.

Test-only observer использует [sys.setprofile](https://docs.python.org/3/library/sys.html#sys.setprofile) и [threading.setprofile](https://docs.python.org/3/library/threading.html#threading.setprofile), чтобы фиксировать вход/выход настоящих функций. Он не заменяет worker-функции, HTTP-клиент или ThreadPoolExecutor. Наблюдаются пути запросов, HTTP statuses, lease/chunk IDs, thread IDs и порядок вызовов; токены, исходные тексты и тела SQL в свидетельство не попадают.

Полный worker loop важен для проверки освобождения слота: его [ThreadPoolExecutor](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Executor.shutdown) продолжает брать задачи только после завершения предыдущего Future. Дополнительно проверяются завершение каждого renewal thread до возврата соответствующего execution и отсутствие живых фоновых потоков после штатного SIGTERM.

## Fault и проверяемое продолжение

Исходный документ содержит 33 chunks. После COMMIT первых 32 proxy удерживает реальное подтверждение PostgreSQL, а независимое соединение читает 32 сохранённых вектора и одно batch-событие. Затем добавляется второй документ с одним chunk. В первом сценарии соединение разрывается; во втором ответ остаётся удержанным до существующего `pg.query_timeout=1000` мс. Политика timeout и production runtime не меняются.

Обычный worker получает **503** при первом complete и отправляет один fail со старым lease, который получает **400**. Затем тот же живой worker сам получает новую аренду на оставшийся chunk и ещё одну — на следующий документ. Их complete возвращают **200 / 200**. На каждый lease приходится ровно один complete; автоматического повторения первой партии нет.

Независимое чтение БД проверяет все **34** вектора по SHA256 фактического chunk content, размерность и модель. Первые 32 chunks, включая timestamps, остаются неизменными. Оба job завершаются с failures=0, пустыми owner/lease/expiry/last_error. Сохраняются ровно три события: batch, ready первого документа, ready второго; исходный event ID не меняется, retrying/failed-событий нет.

## Результаты и границы

На Python **3.14.3**, Node **24.14.0**, PostgreSQL **17.6** оба сценария прошли: по три lease, ответы completion **503/200/200**, **34 точных вектора**, три события и три завершённых renewal thread на сценарий. После SIGTERM Python worker и coordinator возвращают код 0; credentials удалены, незавершённых HTTP-запросов и живых worker threads нет. Runtime-исправление не потребовалось.

[Логи и SHA](./evidence/2026-09-27/embedding-worker-recovery/checks.json) фиксируют целевые **2/2** и полный PostgreSQL-набор **67/67**, успешные typecheck, docs-check (12 Node / 165 Python) и architecture audit. Worker unit suite: **44 PASS, 1 SKIP**; штатно пропущен LangGraph runtime test, поскольку зависимость LangGraph локально не установлена.

```sh
bash scripts/test-fleet-ha-postgres.sh --test-name-pattern='real Python embedding worker'
bash scripts/test-fleet-ha-postgres.sh
npm run test:worker
npm run typecheck
npm run docs:check
```

Это проверка реального worker control flow с синтетическим inference, а не работоспособности конкретной embedding-модели, её качества, длительного inference или поведения при потере renewal. Следующий gate — утрата владения embedding lease во время вычисления: окончательный отказ renewal должен прекращать отправку устаревшего результата, а временная ошибка связи не должна преждевременно отменять действующую аренду.
