# Active caller deadline при двух занятых слотах worker

10.10.2026 MSK: actual coordinator и Python worker concurrency 2 завершили
четыре workflow и сохранили четыре primary outputs. Target пересёк
published caller deadline 250 ms во время активного native HTTP без response
bytes и записал `unavailable/timeout`. Соседний primary TCP-запрос оставался
открытым на той же lease через retirement/recovery decider и затем завершился.
Engineering gate `integration_pass`, qualification `not_assessed`, routing false.

Это продолжение [двухслотовой отмены](./public-workflow-two-slot-cancellation.md)
на тех же четырёх полных original inputs, indices 24–27: 188, 1525, 681 и
930 native tokens. Original inventory 49 cases / 44 groups целиком остаётся
в sealed plan. [Предыдущий real-primary inventory](./public-workflow-real-primary.md)
содержит все 98 matched workflows; здесь primary — управляемая HTTP fixture,
которая удерживает соседнее соединение до проверенного восстановления.

## Фиксация протокола

Measurement commit `59eb1c90f7f49c44b08b7b9df6ae2ba225b9db49`, 206 sources,
зафиксирован до native origin/scoring. Context commit
`5ac6504db17e8407076bd2ad1f740c7715977776`, 40 sources; raw context SHA
`c1c45eca0e0d11180dc060d292ab14480708ada261a00c1b79c57649f04dcf8c`.

[Launcher](../../../../scripts/run-two-slot-native-deadline.py) использует
общий owned lifecycle с [cancellation launcher](../../../../scripts/run-two-slot-native-cancellation.py).
[Driver](../../../../scripts/run-two-slot-native-deadline.mts) публикует две
версии одного прямого графа `start → agent → end`: version 1 имеет shadow
caller 10000 ms, version 2 — 250 ms. Они различаются только timeout.
Primary prompt получает original run input ровно один раз через существующий
fallback при null initial stage input. Coordinator/worker production code
не изменялся. State, question/options и serving profile сохранены.

Actual worker concurrency 2, sequential scheduler/global limit 2; retry 0.
Case versions `[1, 2, 1, 1]`, start order `[0, 2, 1, 3]`: после полного
prefix peer primary начинается раньше target. Pending raw traces подтверждают
два distinct assignments одному worker, target intent и ещё не возвращённый
peer primary. Это обеспечивает наблюдение соседней задачи в момент deadline.

Runtime 0.12.3 / profile
`776fba7fa1244e13292ac3605a53a6d6c6cf557901c8d863cab9489d6d2f47ce`:
input 2048 tokens, wired 4096 MiB, cache 128 MiB, native inference 5000 ms.
Два warmups на каждый из двух epochs. Peer hold 30000 ms, driver 180000 ms,
retirement 10000 ms, recovery 90000 ms — prospective budgets, без ослабления.

| Граница | Raw evidence |
|---|---|
| Prefix | Полные primary/shadow, idle physical counters до target |
| Target и peer | Одновременно running, один worker, разные original leases, attempt 1 |
| Worker deadline | Monotonic local HTTP timing, actual socket EOF, ноль target response bytes |
| Durable fallback | Accepted `unavailable/timeout`, затем complete с original primary output |
| Native retirement | Runtime exit 75 / inference child -15; все три старых native PIDs absent |
| Owned recovery | Новый PID/epoch, прежний profile, два computed warmups |
| Peer release | TCP открыт, response bytes 0, прежний assignment/worker; ответ отправлен после recovery |
| Peer/suffix | Original primary output и healthy typed shadow, без retry |

Running futures сами по себе не прерываются при `cancel_futures`, согласно
[Python Executor documentation](https://docs.python.org/3.13/library/concurrent.futures.html#concurrent.futures.Executor.shutdown).
Поэтому проверяются EOF, retirement event и PID absence. Native relay сохраняет
свой single-handler contract: peer удерживается на primary границе до shadow.
[Node timers](https://nodejs.org/api/timers.html#timers_settimeout_callback_delay_args)
не гарантируют точную задержку; driver сверяет actual clock до запуска
instances. Controlled early wake проверяется в actual-worker fixture.

## Результаты

Все четыре workflow completed, четыре durable primary outputs и четыре
known caller returns. Target caller `unavailable/timeout` известен, хотя его
native terminal counter unknown. Три остальных caller returns — один `ok`
и два `abstain`; generated native tokens 0. Coordinator run cancellation
не выполнялась; все intent/observation/complete — 200, renew — 204, fail нет.
Четыре renewals имеют `requestBodyComplete=false`; required mutation bodies
полные, неполные renew bodies не выдаются за прочитанные.

Два actual authenticated census: version 1 содержит три workflow, version 2
один target; оба 401 без token и 200 с token, raw body SHA сохранены.
Все full traces сверяются с metadata census и assignment/lease journal;
raw peer snapshots до deadline, после durable timeout и перед release
подтверждают сохранение input, stage, worker snapshot, assignment и attempt 1.

Native: восемь starts, включая четыре warmups; семь known terminals.
Idle snapshots `0 → 2 → 3` / `0 → 2 → 4`, active snapshot in-progress 1
с тремя прежними terminals. Interrupted target не получил typed result.
Три healthy signatures совпали с historical healthy control после исключения
ID/duration; baseline ZIP SHA/CRC/every file SHA/size проверены независимо.

| Наблюдение | ms |
|---|---:|
| Worker local HTTP deadline | 250.736 |
| Target relay elapsed | 250.131 |
| EOF → old native PIDs absent | 369.000 |
| Retirement → fresh epoch с двумя warmups | 5438.000 |
| Peer primary hold | 6285.000 |
| Whole launcher | 17646.460 |

Все 11 recorded owned PIDs независимо absent, credentials удалены;
driver/worker exit 0, runtime exits 75/130. Protected resident unchanged по
27 before/after read-only checks: четыре PIDs, 20 runtime files, package,
config/plist seals, health/metrics и Prometheus readiness. Permanent
8766/9095 не получили scoring calls.

## Воспроизведение и ограничения

[Verifier](../../../../scripts/verify-two-slot-native-deadline.py) проверил
raw external context/plan/result pins, полный historical source closure,
профиль, implementation SHA, 34 native dependencies и все receipts без
model/network calls. Independent stdlib audit — 708 checks,
27672 JSON-key checks, ноль application imports/model calls.

14 новых regressions / 48 related tests, strict TypeScript и 12 Node docs
checks прошли. Полный Python suite — 1030 tests PASS за 409.982 s,
четыре optional skips; skips не считаются native gates. Actual-worker fixtures
используют явно synthetic native epochs/PIDs и не считаются native evidence.
Предыдущий cancellation raw replay повторён после refactor и прошёл.

Первая fixture suite прошла actor/replay проверки, но teardown был ошибочно
привязан к прежнему fixture class; cleanup исправлен и suite повторён.
Первый standalone typecheck пропустил repository import flag, второй strict
check прошёл с `allowImportingTsExtensions`. В первом independent audit
ссылка на target row была переиспользована циклом; исправленный аудит прошёл.
Failed logs сохранены, native attempt один, полностью успешен.

Public [summary](./public-workflow-two-slot-deadline-summary.json) содержит
только pins/accounting; raw inputs/HTTP/logs и restorable source snapshots
хранятся под `/docs/private`. Checked archive copy фиксируется отдельным
[archive receipt](./public-workflow-two-slot-deadline-archive-summary.json).

Этот focused fixture-primary gate не измеряет classification accuracy,
calibration, customer capacity или GPU kernel preemption. Полные 49 cases
здесь не повторялись. Следующий performance gate — полный paired inventory
с real primary и matched control при worker concurrency 2: ранее такое
admission измерено только с fixture primary. Owners/customer/SLO/human
calibration/holdout остаются открытыми; routing false / not_assessed.
