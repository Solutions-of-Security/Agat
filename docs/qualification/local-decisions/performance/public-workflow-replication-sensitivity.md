# Descriptive sensitivity of counterbalanced replication

Статус: **полный native ledger проанализирован; source-bound replay и независимый audit PASS**.
Следующий шаг [плана](../../../local-decision-model-plan-2026-09-21.md) —
проверить, как aggregation unit и структура наблюдений меняют описательный
результат [завершённого native запуска](./public-workflow-counterbalanced-real-primary.md).
Новые model calls не требуются: анализ сначала выполняет source-bound replay
исходных 196 workflows, затем использует полный проверенный ledger.

## Метод

Метод зафиксирован в source-bound protocol **после измерения**. Это
descriptive analysis исходного development corpus. Все 49 inputs / 44 groups,
четыре периода, context-too-long cases и differing outputs сохраняются.

| Aggregation unit | Расчёт |
|---|---|
| Original input | Среднее двух shadow минус среднее двух control; затем равный вес каждого из 49 input |
| Original group | Среднее всех member case contrasts; затем равный вес каждой из 44 групп |
| Original adjacent pair block | Среднее его member cases; затем равный вес 25 blocks, включая singleton |

Исходное input-weighted mean сохраняет исходный denominator. Group и block
means показывают чувствительность к другой системе весов; они не являются
оценками production traffic. Groups пересекаются с paired blocks. Ни groups,
ни blocks не объявляются независимыми экспериментальными единицами.

Для каждой из 44 групп отдельно пересчитывается input-weighted mean оставшихся
cases — leave-one-group-out sensitivity. Проверяются **все** группы, исходный
full-corpus mean остаётся основным. Эти значения не используются для исключения
cases, подбора порядка или создания uncertainty interval.

Восемь period/condition cells сохраняют все 196 absolute observations и показывают
workflow/pre-primary/primary/post-primary/clock компоненты, reported primary
timings, prompt/decode token counts и done reasons. Reported model timings
не приравниваются к измеренному GPU time и не определяют cache hits.

Четыре outcome cells сохраняют все 98 matched replica contrasts. Их mean имеет
observation weight; сумма mean × cell count / 98 воспроизводит full-corpus
contrast. Outcome зависит от admission и хода исполнения: эти условные разрезы
не определяют causal эффект busy, abstain или accepted inference. Пустой cell
имеет явный count=0 и null statistics.

Дополнительные case partitions по ABBA/BAAB, output equality и context eligibility
показываются вместе с denominator. Negative contrasts сохраняются. Weighted
component means должны воспроизводить whole contrast с прежним допуском .005 ms;
medians и quantiles не складываются. P50/P95 используют nearest rank, median —
среднее двух центральных значений при чётном count.

## Основание выбора

[NIST о blocking](https://www.itl.nist.gov/div898/handbook/pri/section3/pri332.htm)
объясняет роль nuisance factors при проектировании измерений.
[NIST о restricted randomization и nested variation](https://www.itl.nist.gov/div898/handbook/pri/section5/pri55.htm)
показывает, почему структура данных и размер experimental unit влияют на анализ.
Эти источники поддерживают необходимость сохранять структуру нашего corpus;
конкретные equal-group/block means выбраны здесь как прозрачные descriptive
sensitivity summaries, а не как предписанный NIST estimator.
[Google Benchmark о random interleaving](https://github.com/google/benchmark/blob/main/docs/random_interleaving.md)
описывает уменьшение run-to-run variability при чередовании repetitions.
Это основание исследовать period cells; оно не доказывает устранение carryover
или elapsed-wall-time drift в нашем ABBA/BAAB запуске. Источники проверены 10.10.2026.

## Наблюдение 2026-10-10

Один post-measurement analysis, new model calls=0. Все 196 workflows,
98 matched replica contrasts, 49 whole inputs / 44 groups и 25 paired blocks
остались в исходном denominator. Три системы весов дают:

| Равный вес | Whole delta ms | Pre-primary | Primary proxy | Post-primary | Clock residual |
|---|---:|---:|---:|---:|---:|
| 49 original inputs | +412.921755 | +5.540816 | -376.989173 | +784.377551 | -0.007439 |
| 44 original groups | +544.899623 | +5.039773 | -296.110756 | +835.971591 | -0.000985 |
| 25 original paired blocks | +414.190980 | +5.150000 | -378.945460 | +787.990000 | -0.003560 |

Это разные descriptive summaries одного corpus; ни один из них не выбирается
как production estimate. Leave-one-group-out выполнен для всех 44 групп:
remaining-case workflow mean range +343.288…+655.689 ms. Full-corpus mean
+412.921755 ms сохраняется. Этот range отражает изменение состава фиксированного
corpus, а не sampling uncertainty или confidence interval.

Period cells содержат absolute workflow means для разных наборов inputs:

| Ordinal period | Control n | Control mean ms | Shadow n | Shadow mean ms |
|---|---:|---:|---:|---:|
| 0 | 25 | 8782.388 | 24 | 8047.898 |
| 1 | 24 | 5333.541 | 25 | 6313.663 |
| 2 | 24 | 5383.718 | 25 | 5819.060 |
| 3 | 25 | 5191.798 | 24 | 6273.649 |

ABBA case contrast mean −920.731600 ms (25 cases), BAAB +1802.144000 ms
(24 cases). Группы ориентации содержат разные inputs. Эти разрезы и более высокие
first-period absolute means не устанавливают order effect, cache warming или
causal overhead. Unequal elapsed-time spacing, нелинейный drift и carryover
остаются неидентифицированными.

Outcome cells используют observation-weighted matched contrasts:

| Outcome | Replica observations | Distinct cases | Mean delta ms | Caller mean ms |
|---|---:|---:|---:|---:|
| abstain | 18 | 11 | +953.346 | 1476.379 |
| busy | 46 | 30 | -298.256 | 7.266 |
| context_rejected | 2 | 2 | -11434.955 | 31.534 |
| ok | 32 | 20 | +1871.744 | 1542.296 |

Сумма mean × count / 98 воспроизводит полный workflow contrast. Distinct-case
counts этих cells могут пересекаться: два shadow наблюдения одного input могут
иметь разные outcomes. Отрицательные busy/context-refusal contrasts не означают
ускорение primary вследствие отказа: эти strata зависят от состояния admission,
периода, input и истории shared runner. Все context-too-long и different-output
cases сохранены; outcome или output equality не используются для filtering.

## Проверки и source binding

Source `0dac42ca4ff9a7131fd0f49ef121aa51c41a3358`: 228 files; исходный
native source — 224 files, context — 40. Analysis file SHA
`c8ae0701bd7cdb3695947da46e360aab1bab5e6f50219f6cdd81e0a1c8a09a1e`.
Native plan/result и prospective design bytes сохранены; model calls=0.
18 новых / 71 related / 1190 Python tests прошли, 4 optional skips.
Independent stdlib audit: 20816 checks / 32193 JSON keys, без application imports.
Protected resident: 27 checks и восемь snapshot полей неизменны.

Первый auditor использовал неокруглённый maximum. Shared distribution contract
округляет P50/P95/max до трёх decimals; mean/median и weighted contrasts
сохраняют шесть decimals, min сохраняет исходную точность. Исправленный auditor
закреплён отдельной revision после analysis; original auditor, 30 выявленных
formatting differences и failure log сохранены. Analysis/source/method/weights
не менялись, analysis reruns=0. Independent audit seal
`3fbbbf5aca9113b2e6c26498237d74492b5f1e91d3009bc1254eb15428c281a4`.

[Aggregate summary](./public-workflow-replication-sensitivity-summary.json)
содержит все cell denominators и статистики. Group members, original blocks,
все 44 leave-one-group-out rows и raw native evidence сохраняются privately.

## Восстановимый archive

[Archive summary](./public-workflow-replication-sensitivity-archive-summary.json):
277 files / 43574956 bytes / 3186 selected Git objects. ZIP SHA
`4295bcb22a11a2e855cf7d08fc0e48e72d0cbff63f232318bb05b43e4b2ea458`.
CRC, every SHA и размеры проверены. Verifier восстановлен из committed source
pack; дополнительный replay прочитал фактическую ZIP-копию в исходном workspace,
воспроизвёл все статистики и 196 historical workflows без model calls.
ZIP и отдельная proof-квитанция сохранены в исходном `docs/private`; proof file SHA
`94af1262ebf53398da99394e7fdc7effe4af0d438f0d7fec10ac8ea52c7f3687`. Original auditor, revised auditor, диагностические различия,
failure logs и все raw native inputs входят в archive.

## Инструменты и границы

[Analyzer](../../../../scripts/analyze-public-support-replication-sensitivity.py),
[source-bound verifier](../../../../scripts/verify-public-support-replication-sensitivity.py),
[protocol и aggregation](../../../../scripts/lib/decision_public_replication_sensitivity.py),
[regressions](../../../../scripts/test/test_decision_public_replication_sensitivity.py).
Private analysis закрепляет file SHA context/native plan/native result, source
closure и полный embedded native replay. Повторная проверка восстанавливает все
расчёты из исходных receipts и отклоняет resealed подмену статистики или protocol.
Human labels=0; owners/customer/SLO/holdout gates открыты,
causal/population/uncertainty claims=false, routing=false / not_assessed.
