# Native execution of the frozen ABBA/BAAB design

Статус: **implementation + CPU fixtures; native measurement ещё не выполнен**.
Новый driver исполняет [prospective design](./public-workflow-counterbalanced-design.md),
а не выбирает порядок по наблюдаемым latency. Все 49 whole development inputs /
44 groups и четыре периода каждого input остаются в denominator.

## Связь с предварительным планом

Native CLI требует `--replication-design` и `--replication-design-file-sha256`.
До запуска owned native/primary services выполняется source-bound replay этого
плана с тем же context file SHA. Исходные bytes копируются как отдельный private
artifact; native plan, workflow recipe и replay закрепляют их file SHA. Date
предварительного design должна предшествовать native plan.

Условия публикуются один раз: control version 1, shadow version 2. Graph, worker
prompt, whole input и primary generation сохраняются; только shadow config
отличает условия. Order берётся из sealed 100 batches. Pair/period/index/condition
и replica связывают каждый run, primary request, native request, lease и trace.
Два shadow scores одного input имеют разные stage/run IDs; их нельзя свернуть
в один caller return. Native counters должны учитывать 98 HTTP attempts и два
warmups, primary receipts — 196 calls и один warmup.

96 двух-input batches требуют actual pending worker traces и положительного
пересечения native primary HTTP intervals. Runner log должен подтвердить np=2,
n_seq_max=2, total ctx=65536 и per-sequence ctx=32768. Это configured primary
slots и HTTP overlap; GPU kernel concurrency и customer capacity не объявляются.
Для busy нужен actual active native peer той же пары, периода и replica. GET
admission snapshots и durable intent/return сопоставляются с исходными bytes.

## Ledger и ограничения

Каждый input сохраняет обе control/shadow replica пары, включая different
primary outputs. Все четыре native primary request bodies должны быть одинаковы.
Ledger считает mean-two-shadow minus mean-two-control и pre-primary / primary /
post-primary / явный clock residual с прежними wall/monotonic допусками.
Средние компоненты воспроизводят whole contrast; median не складываются.

Баланс ordinal periods не устраняет elapsed-wall-time drift, nonlinear effects,
primary cache/prefill carryover или group dependence. Sealed design не даёт
customer SLO или causal estimate; actual experiment также не заменяет owners,
human labels, calibration или independent holdout. Routing=false / not_assessed.
Failed attempts и все raw receipts сохраняются; outcome-based изменение порядка
или исключение different-output cases не допускаются.

## Инструменты

[Native CLI](../../../../scripts/run-public-support-counterbalanced-real-primary.py),
[driver](../../../../scripts/run-public-support-counterbalanced-real-primary.mts),
[source-bound replay](../../../../scripts/verify-public-support-counterbalanced-real-primary.py)
и [регрессии](../../../../scripts/test/test_decision_public_counterbalanced_real_primary.py).
CPU fixtures используют настоящие coordinator/worker requests и fake inference;
все четыре ответы каждого input намеренно различаются. Их proof не является
native measurement. Реальный запуск разрешён только после проверки implementation
и merge предварительного design в main; protected resident используется только
для read-only comparison, для experiment создаются собственные services.
