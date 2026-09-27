# Общий срок embedding HTTP и отмена потерянной аренды

Дата: **27.09.2026**. Продолжение [ограниченного чтения ответов](./embedding-http-response-limits.md).

## Контрпример и решение

Предыдущий byte budget ограничивал объём ответа, но `urlopen(timeout=900)` не задавал общий срок запроса. В baseline два тестовых метода дали **четыре timeout errors**: ожидание headers, медленный успешный body, медленный body HTTP 500 и удержание слота после отмены аренды. Endpoint отправлял байты каждые 30 мс либо удерживал headers; данные были синтетическими и оставались на loopback. После изменения тот же исходный reproducer прошёл оба метода и все четыре случая.

Официальная документация описывает [timeout urllib как ограничение блокирующих операций](https://docs.python.org/3/library/urllib.request.html#urllib.request.urlopen). Простое ожидание Future с timeout оставило бы работающий транспорт. Замена на прямой `http.client` потребовала бы заново реализовать поддерживаемые proxy, TLS и redirect semantics.

Выбран отдельный процесс для одного стандартного urllib-запроса. Parent задаёт монотонный deadline, проверяет его и событие отмены каждые не более 50 мс и после получения ответа. При deadline или отмене helper завершается через kill и дожидается выхода до освобождения lease slot; его приватные pipes закрываются. [Повторный communicate после TimeoutExpired](https://docs.python.org/3/library/subprocess.html#subprocess.Popen.communicate) сохраняет уже переданные данные. Общих очередей, файлов состояния, locks и повторных HTTP-попыток у helper нет.

URL, Bearer token, исходный текст и trace headers передаются через stdin, без аргументов командной строки и временных файлов. Helper сохраняет стандартные environment proxy/CA настройки, проверку TLS-сертификата, urllib redirects, byte limits 8 МиБ / 4096 и кодирование JSON. Валидация векторов и telemetry span остаются в основном worker. Dockerfile включает новый модуль.

`--embedding-timeout` / `AGAT_EMBEDDING_TIMEOUT` — конечное число **0 < seconds ≤ 900**, default **900**; некорректная конфигурация отклоняется. Предел применяется и к индексации, и к query embeddings. `execute_knowledge_lease` передаёт свой Event окончательной потери аренды в транспорт; серверные fences и отказ устаревших complete/fail сохраняются. Временный сбой renewal по-прежнему не отменяет запрос.

## Проверка настоящего worker

Восемь сценариев запускают реальный Python worker, собранный coordinator и одноразовый PostgreSQL 17.6:

- прежние HTTP faults: успешный ответ 8 МиБ+1 и удержанный хвост HTTP 500;
- deadline 0,75 с при удержанных headers, медленном успешном body и медленном error body; после одного fail worker получает новую аренду и сохраняет только результат второй попытки;
- реальный renewal 404 через неизменённый 45-секундный интервал: другая нода уже владеет новым lease; старый worker закрывает model connection, завершает subprocess и execute, **пока model response ещё удержан сервером**; устаревших complete/fail нет, snapshot нового владельца не изменён;
- оба прежних контроля потерянного COMMIT acknowledgement.

Probe наблюдает production-код через profiling, без подмены транспорта и renewal. Для каждого model subprocess записаны PID, известный returncode и закрытые stdin/stdout. Для deadline/cancellation returncode ненулевой, для успешной второй попытки — 0; активных запросов и оставшихся worker threads нет. Проверяются vectors, job failures, events, удаление credentials и нормальный выход процессов.

## Верификация

Локальный PostgreSQL — **8/8 PASS**. Новый deadline/compatibility набор содержит 11 методов: кроме негативных случаев он проверяет HTTPS с отказом недоверенному сертификату и разрешённым CA, HTTP proxy credentials, модельные headers, 303 redirect, единый deadline цепочки redirects, изоляцию двух конкурентных запросов, pre-cancellation, конфигурацию и reaping зависшего helper. Полная worker-регрессия — **70 PASS + 1 SKIP** (LangGraph не установлен на host), typecheck — **PASS**.

При первом полном запуске один старый RAG fake не принимал новый keyword `cancelled`; сигнатура fixture исправлена, неуспешный лог сохранён отдельно. Реальные HTTP/PostgreSQL проверки проходили до этого исправления fixture.

[Manifest с исходниками и SHA](./evidence/2026-09-27/embedding-http-deadline/checks.json), [baseline](./evidence/2026-09-27/embedding-http-deadline/before-unit.log) и [PostgreSQL](./evidence/2026-09-27/embedding-http-deadline/postgres-targeted.log) фиксируют воспроизводимые результаты. Целевой набор embedding — **26/26 PASS**. В собранном штатном Docker image на Linux/Python **3.13.15** все **71/71 PASS** с установленным LangGraph и `--network none`; SHA обоих runtime-модулей из `/opt/agat` совпали с host. Первый container harness не смонтировал необходимые старым тестам fixtures `decision_runtime`; после добавления read-only mount образ прошёл без изменения runtime. Проверка документации — **12 Node + 165 Python**, 1555 ссылок, 187 Markdown-файлов и 13 шаблонов; architecture audit — **PASS**, 0 errors / 0 warnings.

```sh
python3 -m unittest discover -s workers -p 'test_embedding*.py' -v
npm run test:worker
bash scripts/test-fleet-ha-postgres.sh --test-name-pattern='real Python embedding worker'
npm run typecheck
npm run docs:check
```

После CodeQL-проверки для тестового HTTPS-сервера явно задан `minimum_version = TLSv1_2`; настройки production urllib не менялись. Повторные полные suites: host **70 PASS + 1 SKIP**, Linux image **71/71 PASS**.

## Границы и следующий gate

Проверяется ожидание клиента, а не прекращение вычислений удалённой модели. Backend может продолжать inference после закрытия соединения. Это не hard real-time гарантия: создание процесса на уровне ОС и его reaping могут добавить задержку; уже поздний результат parent отвергает. JSON parsing и валидация bounded body выполняются после завершения HTTP. Жёсткое внешнее убийство самого worker и Windows здесь не квалифицированы; normal shutdown worker всё ещё дожидается активных работ в пределах их срока.

Новый процесс имеет стоимость запуска и дополнительную память. Следующий gate — измерить эту стоимость при batch 1/32 и конкурентности 1/4, затем проверить ресурсы при серии отмен; не увеличивать concurrency или объявлять throughput improvement без измерений.
