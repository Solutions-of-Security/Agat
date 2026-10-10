# Whole public inventory: primary-only A/A repeat control

Дата протокола: 2026-10-10. Статус: harness подготовлен; native measurement ещё
не выполнен. Это engineering control для следующей оценки overhead. В прежнем
[paired real-primary inventory](./public-workflow-paired-real-primary.md)
primary outputs различались в 4/49 matched пар, при одинаковых input и generation
settings. Причина этих различий не установлена.

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
