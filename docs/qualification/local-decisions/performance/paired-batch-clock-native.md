# Native проверка часов парного workflow на 49 входах

11.10.2026 MSK. После [добавления clock brackets](./paired-batch-clock.md)
повторён прежний bounded paired protocol на полном неизменном наборе
49 development inputs / 44 groups. Это свежий MLX-прогон; replay старого
архива и шесть synthetic cases предыдущего этапа остаются отдельными опытами.

## Закреплённый протокол

До inference сохранены private prospectus и launcher plan. Измеренные sources
принадлежат commit `61786134084498672fcc2eca5ac6f9e8b5f5a36b`: 225 файлов.
Исходный context profile сохранил опубликованный file SHA
`c1c45eca0e0d11180dc060d292ab14480708ada261a00c1b79c57649f04dcf8c`.
Модель Mapika/decider-2b, revision, 34 runtime dependencies, wired limit
4096 МиБ, cache limit 128 МиБ и профиль 0.12.3 не менялись.

Настоящие coordinator и Python worker обработали 25 последовательных пар
с concurrency 2 через fixture primary и отдельный temporary MLX runtime.
Последний batch содержит один исходный вход. Сохранились прежние 20 s pair
deadline, 10 s caller timeout, retry=0 и restart=false. Постоянные resident
LaunchAgents и их endpoints проверялись только чтением.

## Наблюдаемый результат

Все 49 instances, primary calls и bound shadow returns завершены. Физические
scheduled outcomes: 16 `ok`, 8 `abstain`, 24 `busy`, один `context_rejected`.
Ещё два context-ineligible inputs получили настоящий `busy` до проверки
token/context; их входы не сокращались. Два warmups и 49 scheduled HTTP calls
дают 51 физический запрос runtime, включая 26 model scores. Busy и context
rejection не считаются вычисленными модельными ответами.

Все 25 batches имеют schema `agat.decision.paired-workflow-batch.v2`.
Независимо пересчитаны monotonic elapsed, оба sampling intervals, общий
deadline и barrier между парами. Максимальная sampling width —
0.004625000000032742 мс; максимальный абсолютный wall/lower elapsed residual —
0.862 мс. Исходное правило допуска и deadlines не расширены.

Native active metrics witness связывает `busy` с ещё выполняющимся admitted
партнёром в первой паре. Три idle snapshots сохраняют один runtime epoch;
счётчики замыкают все 51 физический HTTP call. Все 39 recorded temporary PID
отсутствуют после завершения. Восемь protected resident fields, четыре PID,
20 installed runtime files и physical counters не изменились.

## Независимая перепроверка

Текущий [production verifier](../../../../scripts/verify-public-support-workflow.py)
прошёл source-bound replay с independently pinned plan/result bytes и exact
physical HTTP accounting. Отдельный stdlib audit без application imports
прошёл **2870 checks**, проверил 35933 JSON keys, 198 actual lease HTTP records,
исходные тексты запросов, saved returns, native metrics и clock math.
Три incomplete renewal request bodies сохранены явно; основной intent,
shadow return и completion каждого входа имеют полные проверенные bytes.

Все 25 типизированных результатов — 24 вычисления и один context rejection —
совпали с закреплённым healthy control для соответствующих исходных входов.
В девяти парах admitted участник отличается от предыдущего paired run.
Поэтому 16/8 вместо прежних 15/9 `ok/abstain` не является измерением изменения
качества. Descriptive caller p50/p95 и полные counts сохранены в
[сводке](./paired-batch-clock-native-summary.json); причинное ускорение
workflow или улучшение производительности не установлено.

[Архив](./paired-batch-clock-native-archive-summary.json) содержит 305 файлов,
273 source-snapshot files для двух commits и frozen healthy archive.
Оригинальная ZIP-копия под `/docs/private` исходного workspace проверена
по CRC и SHA/size каждого entry. После восстановления audit без Git и без
inference дал byte-identical report. Этот replay также проверяет живое
read-only resident state на том же host; переносимость этой проверки на
машину без установленного package не заявляется.

Свежий native gate подтверждает работу clock collector на закреплённом
полном development inventory. Реальные reference labels отсутствуют,
classification accuracy не измерена. Human reviews, calibration/holdout,
owners, реальная customer workload и SLO остаются открытыми.
Routing=false, qualification=`not_assessed`.
