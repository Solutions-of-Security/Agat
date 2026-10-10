# Whole public inventory: primary-only A/A repeat control

Дата измерения: 2026-10-10. Статус: **observed, engineering A/A gate PASS**.
Все 98 workflow completed; все primary outputs сохранены. Native case calls,
caller intents и durable shadow returns — 0. Primary request bytes совпали
в 49/49 пар. Primary outputs совпали в **46/49** пар;
indices с различиями: `[12, 36, 43]`. Различия не требуют retry.

В прежнем [paired real-primary inventory](./public-workflow-paired-real-primary.md)
primary outputs различались в 4/49 matched пар. Этот A/A pass измеряет
наблюдаемую вариацию primary-only на том же host/profile; причина прежних
различий и causal shadow overhead остаются не установлены.

## Проспективный дизайн

Все 49 original development inputs / 44 groups сохраняются целиком, включая
три inputs сверх native 2048-token budget. Каждый input дважды поступает
реальному Qwen3:8B через production coordinator/worker: 98 workflow в одном
published process/version 1, без decisionShadow config. Labels repeatA/repeatB
записываются только в private journal; worker prompts и native request bytes
для одного input должны совпасть. Stage input остаётся null, весь input
передаётся один раз через run input.

Порядок: adjacent original pairs, по два workflow за batch. Для чётной пары
repeatA → repeatB, для нечётной repeatB → repeatA; input 48 остаётся singleton
в обоих повторениях. Итого 50 batch, 48 свидетельств двух занятых worker slots
и положительного пересечения actual primary HTTP requests. Одна published
версия исключает изменение graph между labels.

Профиль совпадает с прежним paired inventory: worker/global concurrency 2;
Ollama 0.35.1 / Qwen3:8B digest
`500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`;
OLLAMA_NUM_PARALLEL=2, actual n_seq_max=2, total context 65536,
context/request 32768; think=false, temperature=0, seed=0, decode limit 128,
keep_alive=5m. Worker adapter фиксирует собственный temperature=0.2 и
преобразует его в тот же native profile для обоих повторов. Timeout primary
180000 ms, workflow 210000 ms, общий budget 3600000 ms; retry/restart запрещены.

Отдельный временный native decider сохраняет фоновую резидентность после
двух warmup, как в прежнем paired setup. Case native calls должны быть нулём:
после каждого batch raw /metrics показывает ту же эпоху, ready/idle и ровно
два warmup outcomes. Адаптер запрещает forwarding case score; любая попытка
POST завершает measurement ошибкой. Protected resident 8766/9095 и primary
11434 не используются для scoring.

## Приёмка и границы

Обязательны все 98 completed workflow и durable primary outputs, одинаковые
native request bytes для 49 пар, отсутствие intent/return/shadow stage,
полный authenticated process/version census и неизменный protected resident.
Все owned PIDs должны быть остановлены и независимо перепроверены. Raw
receipts, source closure, параметры и design seal фиксируются до результата;
offline replay не вызывает модели или сеть.

Output equality не является pass gate. Любые корректные различия сохраняются
по case, вместе с latency, prompt/decode token counts и finish reason. Failed
attempt также сохраняется. Один законченный A/A pass оценивает наблюдаемую
повторяемость на этом host; он не доказывает причину прежних расхождений,
классификационное качество, customer capacity или причинный shadow overhead.

Никакие labels, owners, accepted SLO, calibration или holdout не добавляются.
Routing остаётся выключенным, qualification=not_assessed.

## Обоснование и инструменты

[NIST](https://www.itl.nist.gov/div898/handbook/pri/section3/pri332.htm)
рекомендует учитывать контролируемые nuisance factors блоками и описывает
run-to-run variation. Здесь сохранён прежний alternating block order; полной
рандомизации нет. [Официальный llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md?plain=1)
предупреждает о backend-dependent различиях logits при batch/cache changes.
Это общий мотив для A/A проверки, а не установленная причина поведения
прикреплённой версии Ollama/Metal.

Harness: [launcher](../../../../scripts/run-public-support-primary-repeat-control.py),
[driver](../../../../scripts/run-public-support-primary-repeat-control.mts),
[verifier](../../../../scripts/verify-public-support-primary-repeat-control.py),
[invariants and negative tests](../../../../scripts/test/test_decision_public_primary_repeat_control.py).

## Измеренный результат и воспроизведение

| Метрика | repeatA | repeatB |
|---|---:|---:|
| Whole workflow p50, ms | 5680.382 | 5944.685 |
| Whole workflow p95, ms | 12026.905 | 9600.748 |
| Whole workflow max, ms | 30488.193 | 10943.050 |
| Primary HTTP p50, ms | 5433.734 | 5659.171 |
| Primary HTTP p95, ms | 11675.325 | 9276.081 |
| Decode-limit returns | 41 | 42 |

Matched repeatB − repeatA workflow delta: median
-470.494 ms, mean
-900.174 ms, range
-23510.738…5190.623 ms.
Это вариация между двумя одинаковыми контролями, не оценка эффекта shadow.
Per-case output hashes, prompt/decode tokens, finish reasons и latency
сохранены в [public metadata summary](./public-workflow-primary-repeat-control-summary.json).
Output hashes сравнивают durable worker text после штатного trim; полные
native response bytes находятся только в private archive.

50 batch / 48 two-slot witnesses; minimum actual primary HTTP overlap
2355.000 ms. Все 50 raw native background
snapshots сохранили одну эпоху и counters двух warmup без case scores.
Protected resident: 27 checks, 20 runtime sources, 4 process identity,
health/metrics/monitor bytes до/после неизменны. Все
105 owned PIDs независимо отсутствуют после teardown.
Runtime/primary/driver exits: 130/0/0.

Один native attempt / один completed result. Измеренный source commit
`d08d9aa06b1aded3d07a36214aabb4b74d5d16df`, source closure 218 files,
context closure 40 files. Stdlib audit:
5644 checks / 152601 JSON keys;
все outcome/latency/per-case metadata пересчитаны независимо от приложения.
Offline replay PASS, model calls=0; прежние serial/paired native receipts
повторно проверены без моделей. 18 новых / 47 связанных / 1119 полных
Python tests (4 optional skips), strict TypeScript трёх primary
harness и 12 Node docs tests прошли.

Raw inputs/outputs не публикуются. Immutable evidence ZIP, source snapshots,
CRC/SHA проверки и actual original-copy restored replay описаны в
[archive receipt](./public-workflow-primary-repeat-control-archive-summary.json).
