# Idle budget постоянных embedding helpers

Дата: 27.09.2026. Дизайн фиксируется до измерения; продолжение [model guard profile](./embedding-parent-guard.md).

Сравниваются до/после parent guard при capacity 1, 4 и 32. Для каждого размера порядок before/after/after/before, отдельный pool на фазу и 10 секунд простоя после заполнения. Baseline `218ecb5f6b70289df0c89ebbc7275de57a202684`; common worker modules совпадают побайтно. Из production используются настоящий `EmbeddingSessionPool`, framing, HTTP и cleanup. Модель заменена локальным deterministic HTTP fixture: вход `round/actor`, ответ `[round + 1, actor + 1]`. Это измерение транспорта без модельного inference.

В первой и второй волне по capacity запросов. Barrier в HTTP endpoint не позволяет завершить ни один ответ до входа всех запросов; поэтому заполнены все слоты и затем проверено повторное использование всех тех же helpers после idle. Всего 12 фаз, 148 helpers и 296 HTTP-запросов. После каждой фазы обязательны exit code 0, закрытые pipes, восстановленные FD и отсутствие оставшихся потоков fixture/исполнения.

CPU учитывается отдельно от startup и HTTP: native `proc_pid_rusage(RUSAGE_INFO_V0)` до и после idle, с проверкой неизменности PID, UUID и process start time. Mach ticks конвертируются через фактический `mach_timebase_info`; преобразование до эксперимента сверяется с 200 мс независимого `time.process_time_ns` под CPU-нагрузкой. Деление на размер clock tick нельзя опускать на Apple Silicon. Суммарный процент CPU выражается относительно одного ядра, не всей машины.

RSS и physical footprint фиксируются отдельно. Сумма RSS процессов может повторно учитывать shared pages и не равна дополнительной физической памяти системы. Snapshot не доказывает peak. Измерение не оценивает энергию батареи, Windows/Linux CPU counters, стоимость модельного inference или производственный SLO.

Fixture использует буквальный loopback-host без `HTTPServer.getfqdn()`: первоначальная проверка показала постоянный Unix resolver socket при первом разрешении имени на macOS. После исключения ненужного DNS точное равенство FD проверяется от момента до создания server до завершения server/pool. Проверка не ослаблена допуском «ещё одного FD».

## Воспроизведение

После commit всех измеряемых исходников, на macOS arm64:

```sh
python3 scripts/profile-embedding-idle-budget.py --output docs/qualification/local-decisions/performance/evidence/2026-09-27/embedding-idle-budget
python3 scripts/verify-embedding-idle-budget.py docs/qualification/local-decisions/performance/evidence/2026-09-27/embedding-idle-budget
python3 -m unittest discover -s scripts/test -p test_embedding_idle_budget_measurement.py -v
```

Replay работает без macOS native API; новый experiment требует нового каталога. Все данные находятся в `/docs`. Production defaults и runtime не меняются.

## Основания измерения

[Apple resource.h](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/resource.h) определяет ABI `rusage_info_v0`. В [native tests Apple](https://github.com/apple-oss-distributions/xnu/blob/main/tests/recount/recount_perf_tests.c) `ri_user_time`/`ri_system_time` преобразуются из Mach time перед выражением в секундах. [Python time.process_time](https://docs.python.org/3/library/time.html#time.process_time) даёт CPU-time текущего процесса; здесь он используется как независимая проверка единиц, а не как оценка дочерних процессов.
