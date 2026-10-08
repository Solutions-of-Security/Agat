# Получение cohort для offline сверки

08.10.2026 MSK. [Collector](../../../../../scripts/collect-decision-shadow-cohort.py)
читает [authenticated cohort API](./shadow-pilot-cohort.md) одним GET и
сохраняет точные bytes ответа. Scope берётся только из независимо закреплённого
[prospective v2 plan](./shadow-pilot-plan.md): project/process/numeric version
и завершённое UTC window. Неполный план или незавершённое окно отклоняются до
сети. Routing, workload eligibility и SLO agreement этим не утверждаются.

## Запуск

Admin token должен быть уже настроен в environment как `AGAT_ADMIN_TOKEN`.
Другую переменную можно выбрать `--admin-token-env`; значение не передаётся
в CLI arguments и не сохраняется в receipt. Нельзя включать token в URL.

```bash
python3 scripts/collect-decision-shadow-cohort.py \
  --plan docs/private/pilot/plan.json \
  --plan-file-sha256 '<independent-plan-file-sha256>' \
  --coordinator-url https://coordinator.example \
  --traffic-kind observed_workflow \
  --output-dir docs/private/pilot-capture
```

URL — только origin, без path/query/fragment/userinfo. Remote origin требует
HTTPS с проверкой сертификата и hostname; plain HTTP допускается только
для literal loopback IP, например `http://127.0.0.1:8080`. Это direct connection;
ambient proxy не используется. Redirects, retries и compressed response
отклоняются. Header `x-agat-project-id` и query берутся из plan, а не из
отдельных ручных фильтров.

Transport — отдельный собственный process; credential передаётся через stdin.
Parent ограничивает весь network child timeout, включая DNS/TLS/header/body
waiting; default 30 s, допустимо 0.1–30 s. При timeout `subprocess.run` завершает
и ждёт child. Это network deadline, не предел времени локальной source/SHA
проверки. `SSLKEYLOGFILE` удаляется из child environment, чтобы стандартный
TLS context не создавал ambient файл с TLS secrets. Source files сверяются с
HEAD до и после capture; server deployment source этим не аттестуется.

## Файлы и отказ

Нужен новый каталог внутри `docs/private`: mode 0700, файлы 0600, exclusive
creation без overwrite. `cohort.http.json` сохраняется без JSON reserialization
только после проверки status 200, JSON media type, response framing, byte bound,
plan scope и ordered census. Максимум 16 MiB; incomplete length, chunk errors,
malformed JSON, partial/aliased snapshot и omitted ledgers не дают usable raw
output. Пустой complete cohort допустим как census; evaluator не объявит его
успешным пилотом.

Sealed `acquisition.json` содержит plan file SHA, plan seal, source identities,
cohort file SHA/bytes, scope/counts/run digest, snapshot и request metadata.
HTTP status отказа сохраняется без response body, headers или network exception
text. Token не включается в receipt. Exit 0 — captured, включая пустой census;
exit 1 — failed receipt. Existing/public output отклоняется до работы.
Для fixtures обязателен `--traffic-kind diagnostic_fixture`, receipt тогда
`diagnostic_only`. Success capture не равен success SLO.

`scopeAndCensusBindingVerified=true` означает проверенные декларации snapshot
и привязку к plan; это не цифровая подпись сервера и не подтверждение его build.
`serverDeploymentSourceVerified`, `eligibleWorkloadVerified`,
`populationCoverageVerified`, `httpAttemptInventoryVerified`, `agreementVerified`,
`sloAccepted`, `routingEnabled` остаются false; qualification — not_assessed.
Далее exact file SHA из receipt нужно сверить с сохранёнными bytes и передать
в [offline evaluator](./shadow-pilot-evaluation.md) вместе с отдельными plan и
profile pins. Profile/value semantics и caller targets проверяются там.

## Проверки и основания

Семь новых tests используют настоящий local HTTP server и disposable transport:
exact bytes/headers, chunked body, ignored proxy, redirects/401, incomplete body,
encoding/media/size/framing, wrong scope, missing ledgers и network deadline с
подтверждением child reap. CLI checks проверяют private/exclusive receipts,
token omission, future window, independent input SHA и source drift.

Выбор прямого client основан на официальных
[http.client](https://docs.python.org/3/library/http.client.html) и
[urllib proxy/redirect defaults](https://docs.python.org/3/library/urllib.request.html).
[TLS context](https://docs.python.org/3/library/ssl.html) документирует certificate
verification и `SSLKEYLOGFILE`; [subprocess.run](https://docs.python.org/3/library/subprocess.html)
документирует timeout с kill/wait. Источники проверены 08.10 MSK; сеть и
deadline дополнительно проверены executable tests.

## Нативный итог

Committed implementation `1dc769d`, 08.10 MSK: живой SQLite coordinator
отдал census четырёх явно синтетических instances. Collector CLI получил
его одним authenticated GET; raw bytes, run IDs и все ledgers сохранились.
Затем evaluator включил ok/timeout/missing/pending intents в denominator,
получил прежние `[0.25, 0.75]`, diagnostic_only / insufficient_data, exit 2.
80 отдельных audit checks сверили обе receipt seals, exact file/source SHA,
profile bytes, inventories, outcomes, ratios и private/token-free outputs.

Owners, timestamps и durations в этом опыте — test fixtures; actual model
calls — 0. Отдельный настоящий self-signed TLS server был отклонён как
`SSLCertVerificationError`; collector exit 1, HTTP requests 0, usable raw
output отсутствует. `SSLKEYLOGFILE` не создал файл. Все собственные server
threads/connections и network children завершены; resident не менялся.
[Машинный итог](./pilot-collector-native-summary.json) содержит hashes и границы.

16 targeted Python checks прошли (семь collector и девять evaluator).
Полный `docs:check`: 793 Python, 4 ожидаемых skips, 12 Node;
2483 local targets / 280 Markdown и catalog 7 categories / 13 templates.
Последующее добавление ссылки на machine summary проверяется отдельно.
