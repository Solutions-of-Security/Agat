# Отмена native shadow при двух занятых слотах worker

10.10.2026 MSK: actual coordinator и Python worker с concurrency 2 прошли
отмену одного workflow во время активного native HTTP-вызова. Соседний
workflow оставался назначен тому же worker, сохранил открытый primary
HTTP-запрос через retirement/recovery decider и затем завершился без retry.
Recovery suffix тоже завершился. Engineering gate — `integration_pass`;
qualification `not_assessed`, routing false, reference labels 0.

В focused gate использованы четыре полных original development inputs —
indices 24–27 из прежнего inventory 49 cases / 44 groups. Это проверка
изоляции отмены между двумя реальными слотами. Отдельный
[предыдущий real-primary gate](./public-workflow-real-primary.md) содержит
98 workflow на всём inventory; здесь primary явно является управляемой
HTTP fixture, позволяющей оставить соседний запрос в полёте до recovery.

## Протокол и фактическая граница

Measurement source `02bfc0e6a0c84cf18dbc6c539d149b6bd291e930`, 201 files,
закреплён до native origin/scoring. Original context source
`5ac6504db17e8407076bd2ad1f740c7715977776`, 40 files; full raw context SHA
`c1c45eca0e0d11180dc060d292ab14480708ada261a00c1b79c57649f04dcf8c`.
В plan сохранён весь sealed context; выбранные inputs передаются целиком.
Native token counts — 188, 1525, 681 и 930; все eligible.

[Launcher](../../../../scripts/run-two-slot-native-cancellation.py) создаёт
собственный runtime и relay на временных loopback ports.
[Driver](../../../../scripts/run-two-slot-native-cancellation.mts) использует
actual coordinator/worker, concurrency 2, sequential scheduler, global limit 2.
Граф `start → agent → end` передаёт original run input через existing
coordinator fallback при null initial stage input. Primary prompt содержит
original state ровно один раз; decider получает тот же state, frozen
question/options/profile. Production coordinator и worker не изменялись.

Runtime 0.12.3: profile
`776fba7fa1244e13292ac3605a53a6d6c6cf557901c8d863cab9489d6d2f47ce`,
input 2048 tokens, wired 4096 MiB, cache 128 MiB, inference 5000 ms,
caller 10000 ms. Два computed warmups на каждый из двух epochs.
Peer primary hold — максимум 30000 ms; driver — 180000 ms;
native retirement — 10000 ms, recovery — 90000 ms. Retry 0.

| Этап | Проверенное действие |
|---|---|
| Prefix, original index 24 | Полный primary и shadow завершены; physical counters закреплены до target |
| Target 25 и peer 26 | Два actual agent stages одновременно назначены одному worker; assignments и leases различаются |
| Peer primary | Прочитан полный HTTP request, TCP остаётся открытым; ответ и response bytes ещё не отправлены |
| Отмена target | Полные authenticated pending traces, fresh active-native snapshot, anonymous cancel 401 / authenticated 204 |
| EOF и retirement | Только target lease renewal получает 404; caller EOF закрывает native upstream без response bytes; runtime exit 75 / inference child -15 |
| Recovery | Все три старых native PIDs отсутствуют; новый PID/epoch, тот же profile, два warmups |
| Освобождение peer | Lease/worker/attempt 1 сохранены, HTTP всё ещё открыт; только теперь primary fixture возвращает ответ |
| Завершение | Peer и suffix 27 сохраняют primary output и computed shadow без retry |

Raw trace snapshots сохранены до cancel, после cancel и перед peer release.
Их stage ID, worker snapshot, original full input и assignment ID сверяются
с финальным trace. Lease UUID связывается с assignment через actual accepted
intent; эти два идентификатора не отождествляются.

Running futures сами по себе не прекращаются при `cancel_futures`, как
описано в [официальной документации Python](https://docs.python.org/3.13/library/concurrent.futures.html#concurrent.futures.Executor.shutdown).
Поэтому gate проверяет фактические HTTP EOF, retirement event и PID absence.
В production worker cancellation использует отдельный
[threading.Event](https://docs.python.org/3.13/library/threading.html#event-objects)
для каждого assignment. Наблюдение двух distinct assignments подтверждает
его scope на настоящем worker. Native relay сохраняет существующий предел
одного handler: peer удерживается на primary границе до своего shadow-вызова.

## Результаты и accounting

Четыре actual workflows: три completed и один cancelled. Primary fixture
ответила на четыре requests; три outputs durable. Target primary response
был получен до shadow, но отменённый stage не сохранил output: поздние
observation, complete и cleanup fail получили 400.

Три durable caller returns известны: один `ok`, два `abstain`. Target имеет
accepted intent, `return_missing`, null durable return и ноль observations;
его terminal counter остаётся unknown. Неизвестный результат не реконструируется.
Пять renewals имеют `requestBodyComplete=false`; это явно неполные тела.
Все required intent/observation/complete/fail bodies прочитаны полностью.

Native accounting: восемь starts — четыре scheduled и четыре warmups.
Idle physical snapshots: первый epoch `0 → 2 → 3`, второй `0 → 2 → 4`.
Семь known physical terminals; один interrupted active native POST.
Fresh active snapshot имеет in-progress 1 и прежние три terminal calls.
Generated decision tokens 0; три healthy typed signatures совпали с
independently sealed historical healthy control после исключения ID/duration.

| Наблюдение | ms |
|---|---:|
| Cancel API | 2.637 |
| Cancel request → target EOF | 568.000 |
| Target local HTTP caller | 596.878 |
| EOF → old native PIDs absent | 511.000 |
| Retirement → recovered epoch с двумя warmups | 5582.000 |
| Peer primary hold | 6717.000 |
| Whole launcher | 19612.008 |

Все 10 recorded temporary PIDs независимо проверены как absent; driver/worker
exit 0, runtime exits 75/130, worker credentials удалены. Before/after
resident snapshots по 27 read-only checks совпали: четыре protected PIDs,
20 installed runtime files, package/config/plist seals, health, counters,
Prometheus readiness. Permanent listeners 8766/9095 не получили scoring calls.

## Повторяемость и границы

[Offline verifier](../../../../scripts/verify-two-slot-native-cancellation.py)
прошёл на raw plan/result/context pins и full Git source closure без network
или model calls. Independent stdlib audit без application imports выполнил
680 checks и 27176 JSON-key checks; отдельно проверил baseline ZIP outer
SHA/CRC/every file SHA/size, три signatures и live cleanup.

14 новых regressions и 77 related tests прошли: actual coordinator/worker
two-slot fixture, injected early timer wake, same-lease peer continuation,
source/raw rehash corruptions, неизвестный cleanup и startup failure receipt.
TypeScript strict check прошёл. В model-free fixture native epochs и PIDs
явно synthetic; она не принимается за native measurement.

Первый fixture запуск выявил CLI poll interval ниже допустимого минимума;
driver исправлен на 0.2 s до commit и native run. Другие fixture corrections
уточнили production privacy projection census и warmup timing shape.
Первый independent audit сравнивал options до нормализации default
`abstain:false`; это исправлено по actual wire/production contract.
Все failed test/audit logs сохранены; native attempt один и полностью успешен.

Public [summary](./public-workflow-two-slot-cancellation-summary.json) содержит
pins и счётчики; raw evidence и restorable source snapshots хранятся только
под `/docs/private`. [Archive receipt](./public-workflow-two-slot-cancellation-archive-summary.json)
фиксирует проверенный copy в исходном workspace.

Этот gate не измеряет customer capacity, classification accuracy, calibration
или GPU kernel preemption. Четыре исходных inputs не означают повтор всех 49.
Следующий runtime gate — actual worker caller deadline при concurrency 2
с сохранением соседнего in-flight primary и собственным recovery.
Owners/customer/SLO/human calibration/holdout остаются открытыми.
