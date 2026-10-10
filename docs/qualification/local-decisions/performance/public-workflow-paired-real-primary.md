# Полный paired inventory с real primary и matched control

Дата: 10.10.2026 MSK. Native engineering diagnostic — `integration_pass`.
Actual coordinator и Python worker завершили 98 workflow на всех 49 original
development cases / 44 groups: 49 control и 49 shadow. Сохранены все 98 actual
primary outputs и все 49 known caller returns. Это следующий performance gate
после [двухслотового deadline/recovery](./public-workflow-two-slot-deadline.md).

## Prospective protocol и фактическое исполнение

Исходный context profile сохранён целиком: 46 eligible cases и три whole-input
overlong cases, максимум 9253 native decision tokens. Shortened inputs,
подмена inventory, новые labels и posthoc retry отсутствуют.

Каждая пара соседних original indices создаётся одновременно, с creation skew
не более 250 ms. У чётной пары порядок control→shadow, у нечётной shadow→control;
index 48 — последний singleton. Получены 50 bounded batches и 48 authenticated
pending-primary witnesses. В каждом witness две distinct stages занимают
один actual worker, сохраняют node/assignment/attempt и ещё не имеют output.
Native primary HTTP overlap подтверждён во всех 48 двухслотовых блоках:
min/p50/p95/max 2407.0/6038.0/
11148.0/31710.0 ms.

Worker concurrency 2, coordinator sequential/global 2. Primary — pinned
Ollama 0.35.1 / Qwen3:8b, `OLLAMA_NUM_PARALLEL=2`; фактический runner log
показывает `-np 2`, `n_seq_max=2`, total context 65536 и 32768 на каждый slot.
Explicit generation: temperature 0, seed 0, thinking false, decode 128,
keep-alive 5 minutes. На всех actual scoring requests primary prompt содержит
original run.input ровно один раз; начальный agent stage input — null.
Обе published versions используют тот же direct start→agent→end graph и
condition-blind process/system prompt; только v2 включает observational shadow.

[Официальная документация Ollama](https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests)
связывает model parallelism с увеличением выделяемого контекста и памяти.
Поэтому требуются actual runner/context evidence и overlap, а не одно значение
worker concurrency. Existing local native adapter сохраняет полный request и
реальный output; [OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
проверена отдельно от configured native generation options.

Budgets закреплены до native origin: primary HTTP 180 s, instance 210 s,
driver 3600 s; caller 10000 ms, decider inference 5000 ms, input 2048 tokens,
wired/cache 4096/128 MiB. Один fresh native attempt, retry 0, restart false.

## Все original outcomes и сохранение primary

| Наблюдение | Actual denominator |
| --- | ---: |
| Completed workflows / primary outputs | 98 / 98 |
| Control / shadow workflows | 49 / 49 |
| Matched complete primary requests | 49 / 49 |
| Known durable caller returns | 49 / 49 |
| Native ok / abstain | 16 / 7 |
| Whole-context rejection | 2 |
| Busy with actual active native peer | 24 |
| Typed signatures equal healthy baseline | 25 / 25 |
| Exact primary output pairs | 45 / 49 |

24 busy responses имеют raw HTTP 503, actual active-native gauge 1 и pending
peer той же original пары. Busy возвращён до typed inference и сохранён как
known `unavailable/busy` caller fallback; result ID/input SHA/distribution не
выдумываются. Один из трёх overlong inputs (index 6) получает busy до context
check; другие два сохраняют typed HTTP 422. Все original cases входят в census.

Все 25 typed native signatures совпали с [healthy serial baseline](./public-workflow-real-primary.md)
после исключения только request ID и duration. Independent audit пересчитал
softmax, selected option, margin и policy outcome для 23 computed results;
reference labels 0. Это runtime consistency, classification accuracy не измерена.

Primary prompt tokens — 100–9013, без truncation warnings; 83/98 responses
достигли pinned decode limit. Четыре primary output пары различаются (original
indices 4, 12, 36, 43), raw outputs/hashes сохранены. Accepted completion
неизменно содержит actual primary output, включая workflow с busy fallback.

Actual lease journal: 317 records, 98 completes,
49 intents, 49 returns; 121 renew replies
с неполным request body явно помечены `requestBodyComplete=false` и не считаются
required mutation evidence. Fail/revocation/reassignment/retry нет. Read-only
запрос временной SQLite store связывает каждый concurrent HTTP callback с
actual lease/run/stage/node, без общего изменяемого serial `current`.

## Наблюдаемая latency

| Boundary | Control p50 / p95 / max, ms | Shadow p50 / p95 / max, ms |
| --- | --- | --- |
| Primary HTTP | 5835.233 / 13563.086 / 32220.038 | 6683.353 / 9371.82 / 11249.752 |
| Whole workflow | 6069.95 / 13817.284 / 32489.527 | 7619.282 / 11527.79 / 13269.659 |

Shadow caller p50/p95/max — 141.389/
2222.527/3664.28 ms.
Matched whole-workflow delta median 1151.024 ms,
mean 42.223735 ms; range
-24870.245…7266.571 ms.
Total launch/measurement/cleanup — 438018.137 ms.
Shared primary cache/prefill, two runner slots и один counterbalanced pass
не устанавливают causal overhead, customer capacity или accepted SLO.

## Source, counters и independent replay

Measured source `f1b5ed39c88440805eded455137acff0056b4f2e` — 209 files;
original context source `5ac6504db17e8407076bd2ad1f740c7715977776` — 40 files.
Runtime implementation/profile/manifest и 34 pinned environment dependencies
сверены с исходным context. Cached primary release — 57 files, model — пять
blobs / 5225388164 bytes; full file SHA проверены до и после native inventory.

Native имеет один clean epoch: counters 0→2→51. Два decision warmups
отдельны от 49 scoring terminal calls. Итоговые 51 physical terminals:
16 ok, 9 abstain (включая два warmups), 24 busy, два context_rejected.
Один real-primary warmup отдельно от 98 scoring calls.
Reported remaining owned PIDs — []; driver/worker exit 0, runtime 130,
primary 0. Все 117 recorded temporary PIDs независимо rechecked absent,
credentials удалены. Resident seal, 20 installed runtime source hashes,
config/service files, четыре protected PID, raw health/metrics/readiness
побайтно совпали до и после опыта; обе captures прошли 27 checks.

Raw source-bound offline replay прошёл с нулём model calls; independent stdlib
audit — 7066 checks / 203542 JSON keys,
без application imports. 11 новых / 68 related regressions, strict TypeScript,
serial native backward replay, полный 1041-test Python suite (четыре optional
skips), 12 Node documentation tests, links/catalog прошли. Tests сохраняют
реальный coordinator/worker, а model/residency в них помечены fixtures.

Raw whole inputs, 98 full traces/primary outputs, admission/lease/census
journals и restorable committed source packs находятся под `/docs/private`.
Public [summary](./public-workflow-paired-real-primary-summary.json) содержит
denominators и pins. Private archive verification фиксирует CRC, SHA, source
roles, восстановленный offline replay и отдельную exclusive copy в original
workspace.

Plan raw SHA: `fc425628b72d20684875b5652db39b08637c534990809d1f5d8b1578645f7a10`. Result raw SHA:
`3e637d020ebe43adebe7975b78f9be8bf808c6e99470a0e5feef296f0da97106`. Original context raw SHA:
`c1c45eca0e0d11180dc060d292ab14480708ada261a00c1b79c57649f04dcf8c`.

Следующий runtime gate — cancellation во время active native caller при двух
worker slots с actual real-primary peer и owned recovery. Предыдущие focused
cancellation/deadline gates использовали held fixture primary; этот inventory
проверил complete real primary без interruption. Owners/customer/SLO/human
calibration/holdout открыты, routing false / qualification not_assessed.
