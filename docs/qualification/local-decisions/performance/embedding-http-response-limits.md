# Ограниченное чтение HTTP-ответов embedding endpoint

Дата: **27.09.2026**. Продолжение [обработки потерянной embedding-аренды](./embedding-worker-lease-cancellation.md).

## Контрпример и выбранный предел

`LocalModelClient.embed` раньше вызывал `response.read()` без ограничения до проверки количества и размерности векторов. Даже корректный JSON с огромным пробельным хвостом целиком попадал в память и принимался. Тело HTTPError тоже читалось целиком, хотя в сообщение попадали только первые 1000 символов: endpoint мог удерживать worker ожиданием ненужного хвоста уже известной ошибки.

Baseline на малых loopback-ответах дал **4 assertion failures** и **2 timeout errors в подслучаях**; корректный ответ на точной границе и полный batch 32×4096 прошли как положительные контроли. Тестовые byte budgets были уменьшены, чтобы не создавать опасный объём данных. Дополнительные реальные worker/PostgreSQL-сценарии используют production-пределы.

Успешный JSON теперь читается не более чем **8 МиБ + 1 байт**; лишний байт означает отказ до UTF-8 decoding и JSON parsing. Предел не зависит от Content-Length и применяется к chunked transfer. HTTPError читает не более **4096 байт** префикса с replacement для повреждённого UTF-8; прежнее ограничение диагностического сообщения 1000 символами сохраняется. Оба response закрываются через context manager.

Это использует явный размер [HTTPResponse.read(amt)](https://docs.python.org/3/library/http.client.html#http.client.HTTPResponse.read); [HTTPError](https://docs.python.org/3/library/urllib.error.html#urllib.error.HTTPError) также предоставляет file-like body. Ограничение проверяется в байтах, поэтому многобайтовый UTF-8 не увеличивает разрешённый сетевой объём.

Предел 8 МиБ оставляет запас для существующего контракта **1–32 вектора, 1–4096 конечных компонентов**. Положительный контроль сериализует 32×4096 значений с длинными представлениями конечных double (`-1.7976931348623157e308` / `-2.2250738585072014e-308`): **3 408 832 байта**. Все 131072 компонента проверены после реального HTTP и parsing. Размерность, индексы, ненулевой вектор и finite-number проверки сохраняются.

## Реальный worker и recovery

Два сценария запускают Python worker без dry-run, собранный coordinator и одноразовый PostgreSQL 17.6. Контролируемый локальный model endpoint сначала возвращает один из отказов, затем корректный новый вектор:

| Первый model response | Проверяемое поведение |
|---|---|
| HTTP 200, корректный JSON с пробельным хвостом, ровно 8 МиБ+1 | Отказ до complete, штатный fail первой аренды |
| HTTP 500, получены 4096 байт из заявленных 16384, хвост удержан | Worker закрывает ответ после префикса и продолжает без освобождения хвоста сервером |

Тот же worker получает новый lease ID и успешно отправляет второй результат. Проверяются ровно два model HTTP-вызова, один fail → 200, один complete → 200 только для нового lease, failures=1 и cleared owner/lease/error. В БД сохраняется только вектор второй попытки `[0,1]`; события — ровно retrying и ready. Оба renewal thread завершены, HTTP-запросы не остаются активными, credentials удалены, процессы выходят с кодом 0.

## Верификация и границы

Целевые HTTP-тесты — **7/7 PASS**, PostgreSQL с настоящим worker — **5/5 PASS**, включая оба новых сценария, потерю renewal и оба прежних COMMIT-контроля. Worker unit — **59 PASS + 1 SKIP** (LangGraph не установлен), TypeScript typecheck — **PASS**. Проверка документации — **12 Node + 165 Python**, 1549 локальных ссылок, 186 Markdown-файлов и 13 шаблонов; architecture audit — **PASS**, без ошибок и предупреждений. [Manifest с SHA](./evidence/2026-09-27/embedding-http-response-limits/checks.json) связывает исходники с [baseline](./evidence/2026-09-27/embedding-http-response-limits/before-unit.log), [целевыми HTTP-тестами](./evidence/2026-09-27/embedding-http-response-limits/unit-targeted.log) и [реальным worker/PostgreSQL](./evidence/2026-09-27/embedding-http-response-limits/postgres-targeted.log). Полный PostgreSQL-набор из 70 сценариев проверяется PR CI; локальный результат этого этапа относится к пяти целевым сценариям.

```sh
python3 -m unittest discover -s workers -p test_embedding_http_limits.py -v
npm run test:worker
bash scripts/test-fleet-ha-postgres.sh --test-name-pattern='real Python embedding worker'
npm run typecheck
npm run docs:check
```

Ограничен объём прочитанного тела, а не полная память Python при parsing. Предел не измеряет качество модели и не даёт wall-clock deadline: если endpoint ещё не прислал необходимое число байт или конец тела, сохраняется существующее ожидание транспорта. Слишком подробный provider JSON сверх 8 МиБ будет отклонён, даже если его векторы допустимы. Формат model URL, proxy/redirect behavior и timeout=900 не меняются. Следующий gate — общий срок ожидания model HTTP, включая медленное поступление тела и отмену аренды, с сохранением поддерживаемых настроек подключения.
