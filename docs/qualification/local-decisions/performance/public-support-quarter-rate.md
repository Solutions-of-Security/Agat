# Prospective quarter-rate public inventory

Предыдущий [primary-active опыт](./public-support-primary-load.md) при 0.5
decision arrival/s сохранил один `client_capacity` drop: caller duration
2284.683 ms пересекла следующий двухсекундный arrival. Это ограничение
one-slot schedule, а не свидетельство правильности классификации.

[Grafana constant-arrival-rate](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/)
задаёт arrivals независимо от завершения requests; при нехватке свободных
исполнителей сохраняется
[dropped iteration](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/dropped-iterations/).
[Google SRE](https://sre.google/sre-book/addressing-cascading-failures/)
рекомендует проверять capacity и overload в фактическом resource envelope;
длинная очередь увеличивает latency. Эти источники проверены 08.10.2026.
Выбор 0.25/s — инженерная гипотеза для отдельного опыта, не рекомендация
источников и не установленная граница capacity.

Explicit `--decision-rate 0.25` требует paired `--primary-binaries` и
`--primary-models`. Новые plan/result/phase v3 сохраняют исходный context
receipt без перезаписи и отдельно связывают `scheduleAdjustment` с его SHA.
Default 0.5/s остаётся прежним v1/v2. Все 49 исходных development inputs,
порядок, split seed, profile, 2048-token limit, 4096-MiB wired limit,
5000-ms inference deadline и caller timeout 10000 ms сохраняются.

Decision offsets 0/4/…/192 s; nominal window 196 s. Primary имеет **98**
arrivals на offsets 0/2/…/194 s при прежних 0.5/s, pinned model/request,
128 decode tokens и timeout 30 s. Общая monotonic origin и два отдельных
one-slot исполнителя сохраняют все drops, без queue/retry/catch-up. Все три
длинных input отправляются полностью. Два decision warmup и один primary
warmup отделены от scheduled denominators.

Новый driver ограничен 240 s и вызывает прежний 120-s driver двумя
последовательными сегментами с абсолютной origin и глобальными offsets.
Semaphore не сбрасывается на границе сегментов. Legacy bounds не расширены.
Offline verifier проверяет новый schema/schedule SHA, полные **49/98**
journals, actual HTTP overlap и counters; уменьшение primary denominator
до 49 либо переход обратно на 2-s decision offsets отвергается даже после
пересчёта seals. Teardown и resident preservation проверяются отдельно.

Девять новых tests покрывают 120-s boundary с deterministic
clock, удержание занятого слота через boundary, lag без rebasing,
cancellation второго сегмента, bounds/boolean
aliases, ранний CLI guard и полный temporary Git replay v3 с rehashed
corruptions. Это fixtures, не измерения модели.

Customer workflow/owners, human reference labels/calibration/holdout и
actual boot/login остаются внешними gates. Routing false, qualification
not_assessed; меньшая частота отдельного diagnostic опыта не меняет draft
SLO или прежний capacity result.
