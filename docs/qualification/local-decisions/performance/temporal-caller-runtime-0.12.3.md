# Temporal/PostgreSQL/RAG 0.12.3 с caller accounting

07.10.2026. После [configured profile identity gate](../shadow/observability/caller-profile-identity.md)
повторена actual связка Python worker, PostgreSQL, Temporal, Qwen3:8b,
embeddinggemma и MLX decider. Новая версия gate v5 требует negotiated
caller timing, durable intent/return и их привязку к raw SQL, assignment,
request и configured profile bytes. Прежние версии v1–v4 сохраняют свои
протоколы проверки.

`run-temporal-real-rag.py --caller-accounting` требует shadow recovery и
resource diagnostics. Профиль 0.12.3 закреплён: wired 4096 МиБ, input
2048 tokens, cache 128 МиБ, isolated inference deadline 5000 ms. HTTP
caller deadline — 10000 ms; SLI threshold этого diagnostic gate — 5000 ms.
Постоянный resident используется только для readonly preservation checks.

В каждом transport gate теряет tick reply, перезапускает Temporal worker
при удержанном primary response, убивает временный shadow runtime,
сохраняет primary fallback и восстанавливает shadow до следующего call.
Accepted stage/observation и caller ledger сохраняются; replay не создаёт
новых primary, embedding, shadow calls или trace изменений.

| Финальный transport | Primary calls | Embedding items | Shadow calls / inference | Intent / return | History events |
|---|---:|---:|---:|---:|---:|
| isolated | 3 | 5 | 3 / 2 | 3 / 3 | 74 |
| session | 3 | 5 | 3 / 2 | 3 / 3 | 63 |

Всего четыре `ok/accepted` и два `unavailable/unreachable` с сохранённым
primary fallback; три отдельных warmups, два shadow runtime restarts,
12 source citations. Генерируемых decider tokens — ноль. SQL, assignment
inventory и caller inventory подтверждают шесть intents/returns и шесть
известных monotonic durations без profile mismatch или unknown return.

Pinned CLI с `coordinator_json_bytes` прошёл. Caller latency для n=6:
p50 114.469 ms, p95/max 1799.240 ms. Четыре успешных bounded/timely результата
из шести intents дают ratio 4/6; два unavailable не становятся success.
Это workload fixture, не customer SLO или model quality qualification.

## Перепроверка и доказательства

Первый native run завершился failed: новый probe ожидал ошибочный
`shadow-caller-inventory` namespace. Actual coordinator contract —
`agat.decision.caller-inventory.v1`. Ошибка проверяющего кода исправлена;
unit fixture теперь сверяется с реальным producer export. Его 51 PID и
два containers удалены, models unloaded. Raw failed evidence сохранён.

Полный исправленный повтор прошёл оба transports. Независимый verifier
проверил 214 committed source files, шесть raw SQL snapshots и восстановление.
Отдельный postflight SDK replay обеих histories прошёл 137 events с exact
measured workflow bundle. Seven-check audit подтвердил сохранение прежних
wired prompts, outputs, primary tokens, пяти embedding vectors и четырёх
MLX result signatures. Все 130 recorded processes и шесть containers всей
серии отсутствуют. Permanent resident сохранил четыре processes,
34 dependencies, 35 frozen sources, fresh scrape и counters 1/0/0.

734 Python docs tests (четыре opt-in skips), 12 Node tests, 2426 local links и process catalog
прошли; targeted suite — 63 tests, финальный caller contract repeat — семь.
Typecheck и compile обоих native transports прошли. Resource diagnostics:
75 samples, 71 sampled owned PID, dispatch levels 1/2 и max observed shadow
RSS 4 344 512 512 bytes. Это sampled observation; peak и causality не установлены.

[Публичная сводка](./evidence/2026-10-07/temporal-caller/result-summary.json)
содержит только counts, hashes и repository contributors. Private ZIP:
71 files, три Git states, 122 761 464 bytes; SHA-256
`26cef91d84c669d18eeba936119d0e1af7d36d0219477dd4d20466204737a357`.
CRC, SHA/size каждого файла, inventory и идентичная копия в исходном workspace
проверены. IDs, credentials, native paths, PID, prompts и state остаются private.

Следующие открытые gates: owner/SLO agreement и реальный поток, actual
boot/login и независимые human labels/calibration/holdout. Caller intents
не доказывают global HTTP attempt/customer coverage. Routing выключен.
