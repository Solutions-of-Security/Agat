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

## Native результат 08.10 MSK

Из commit `623612d` измерены все **49 scheduled / 49 admitted**; **46
computed** (31 ok, 15 abstain), **3 context_too_long**, decision drops **0**.
Все computed завершились за 5000 ms. Computed caller p50 **852.303 ms**,
p95 **2063.062 ms**, max **2234.359 ms**. Dispatch lag p95 **8.105 ms**,
max **9.737 ms**. Полный good denominator **46/49 = 0.9387755**; заранее
context-eligible denominator **46/46 = 1.0**. Последний не исключает
неудачные попытки по результату — admission определён до model calls.

Primary: **98 scheduled / 49 returned / 49 client_capacity drops**, каждый
returned response завершил 128 decode tokens, суммарно **6272**. Один
primary warmup отдельно. Caller wall p50 **3418.580 ms**, p95 **3955.621 ms**,
max **3989.909 ms**. Подтверждены **49 HTTP overlap pairs**; phase elapsed
**195963.806 ms** включает drain. Nominal window 196 s и scheduled rate
не выдаются за достигнутую primary throughput.

Decision admission loss исчез в этом отдельном run. При этом measured
caller p95 выше прежних 1126.021 ms при 0.5/s. Последовательные опыты с
неконтролируемым фоном, фиксированным порядком и phase alignment не
устанавливают причину изменения latency и не подтверждают устойчивую
production capacity. Исходный drop и оба результата сохранены.

Source-bound offline verifier: **pass / exact**, 64 measured sources и 58
verifier sources. Independent audit: **386 checks**; все **49** typed
signatures совпали со standalone baseline без duration, selected outcomes
и distributions не изменились. Это повторяемость, не classification accuracy.
Physical metrics: **51** handlers — 48 computed с двумя warmup и три
context rejection. Fresh census подтвердил отсутствие всех **5** owned
PIDs; resident сохранил прежние 4 процесса, 35 sources и counters 1/0/0.
Actual boot/login остаётся awaiting_event.

Full docs checks: **851 Python / 4 optional skips, 12 Node**, links/catalog
pass. [Allowlisted summary](./public-support-quarter-rate-summary.json)
сохраняет все denominators, предыдущий run и raw artifact SHA. Плановый
[Постоянный архив](./public-quarter-archive-summary.json) сверяет CRC и SHA
каждого файла после копирования и связывает предшествующие archives.
Плановый
0.5/s envelope/SLO не заменён; customer workflow, appointed owners и human
review/calibration/holdout остаются открытыми, routing false / not_assessed.
