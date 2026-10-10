# Native execution of the frozen ABBA/BAAB design

Статус: **первый native запуск завершён; source-bound replay и независимый аудит PASS**.
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

## Наблюдение 2026-10-10

Один native attempt, без failed native attempts: 196 completed workflows и
196 primary calls, по 98 control/shadow; 98 decision HTTP attempts, два native
warmup attempts и один primary warmup. Полный runtime — 693985.441 ms.
Все 49 исходных input / 44 groups прошли все четыре периода предварительно
зафиксированного порядка. Из 100 batches 96 двух-input batches имеют реальные
pending worker traces и пересекающиеся primary HTTP intervals.

Decision outcomes: ok=32, abstain=18, busy=46, context_rejected=2. Все 46 busy
имеют активного peer той же pair/period/replica; четыре context-too-long
наблюдения получили busy до context check. Это 98 HTTP attempts, включая
admission refusals; число GPU inference из этого счётчика не выводится.
Native warmup также может включать cached return.

Все четыре primary request bodies каждого input совпадают. 95/98 matched
control/shadow output pairs совпали; все четыре outputs совпали у 46/49 input.
Индексы 19, 36, 43 с различными outputs сохранены в полном denominator.
Средние case contrasts mean-two-shadow minus mean-two-control:

| Компонента | Mean delta, ms |
|---|---:|
| Whole workflow | +412.921755 |
| Pre-primary wall | +5.540816 |
| Primary proxy monotonic | −376.989173 |
| Post-primary wall | +784.377551 |
| Clock closure residual | −0.007439 |

Компоненты складываются в whole contrast в установленных допусках. Это
описательное наблюдение данного запуска; +412.922 ms не является causal shadow
cost, production estimate или customer SLO. Caller latency смешивает accepted,
abstain, busy, context refusals и возможные cache hits. Неравное elapsed-time
spacing, nonlinear drift, carryover и зависимые input groups остаются открыты.

## Проверки и сохранность

Measurement source `f2e465227c015ec1145dc953f25543f876bd6850`: 224 files;
предварительный design — 219 files, context — 40. Context SHA
`c1c45eca0e0d11180dc060d292ab14480708ada261a00c1b79c57649f04dcf8c`,
prospective design file SHA
`f72ba21086e9be0627d2848636e4b0e5a6e5bfd9b02446a377cfafcf503aa03b`.
Native plan file SHA
`0e836585603d3cde7e3000f57e7f7ed00c585bc54ba09292499c85b015d753b7`,
result file SHA
`76569fa7470b33a32ef1070ecbb40f2c6826cfa0a59745b7ac00d8852ca33c61`.

18 новых / 100 related / 1172 Python tests прошли (4 optional skips),
три strict TypeScript checks и три model-free replay прежних serial/paired/A/A
native данных прошли. Независимый stdlib audit: 15763 checks / 427840 JSON keys,
без application imports или model calls. Все 231 owned PID независимо проверены
как отсутствующие. Protected resident: 27 checks PASS, восемь snapshot полей,
включая GET health/metrics/monitor и четыре PID, неизменны.

Первоначальный аудитор и оба failure logs сохранены. В отдельных версиях
исправлены сравнение cohort projection (exporter исключает только decision
context) и локальная коллизия имени набора BAAB-пар с admission samples.
Квитанции явно отличают pre-measurement auditor от revision 3 после измерения.
Native source, план, порядок, raw результат и accounting допуски не менялись;
дополнительных native запусков для исправления аудитора не было.

[Aggregate summary](./public-workflow-counterbalanced-real-primary-summary.json)
содержит receipts и описательные статистики. Raw inputs, outputs, HTTP journals
и 49 case ledgers сохраняются в private evidence. Human labels=0,
owners/customer/SLO/holdout gates открыты; routing=false / not_assessed.

## Восстановимый archive

[Archive summary](./public-workflow-counterbalanced-real-primary-archive-summary.json):
279 files / 42613126 bytes / 3168 selected Git objects, ZIP SHA
`fb58b3ad06a8a44e1a1f229152cfd8a83d8ca999cd0aa96ecf6ca734d302f790`.
CRC, every SHA и размеры проверены; verifier восстановлен из committed source
pack. Дополнительный replay прочитал фактическую ZIP-копию в исходном workspace
и воспроизвёл все 196 workflows без model calls. ZIP и отдельная proof-квитанция
сохранены в исходном `docs/private`; proof file SHA
`e0316ca8537bf9fbadc7d40c3a53a6d9bea69585e6b35bed66318894cc6bdbc8`. Архив содержит исходный auditor, обе
исправленные версии и failure logs; raw experiment сохраняется целиком.

## Инструменты

[Native CLI](../../../../scripts/run-public-support-counterbalanced-real-primary.py),
[driver](../../../../scripts/run-public-support-counterbalanced-real-primary.mts),
[source-bound replay](../../../../scripts/verify-public-support-counterbalanced-real-primary.py)
и [регрессии](../../../../scripts/test/test_decision_public_counterbalanced_real_primary.py).
CPU fixtures используют настоящие coordinator/worker requests и fake inference;
все четыре ответы каждого input намеренно различаются. Их proof не является
native measurement. Реальный запуск выполнен после проверки implementation
и merge предварительного design в main. Protected resident использовался только
для read-only comparison; experiment использовал собственные services.
