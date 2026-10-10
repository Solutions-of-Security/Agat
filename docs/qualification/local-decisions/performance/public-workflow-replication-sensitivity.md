# Descriptive sensitivity of counterbalanced replication

Статус: implementation и CPU tests; анализ native receipts ещё не выполнен.
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
