# Idle budget постоянных embedding helpers

Дата: 27.09.2026. Все 12 ABBA-фаз и независимый replay завершены; продолжение [model guard profile](./embedding-parent-guard.md).

Сравниваются до/после parent guard при capacity 1, 4 и 32. Для каждого размера порядок before/after/after/before, отдельный pool на фазу и 10 секунд простоя после заполнения. Baseline `218ecb5f6b70289df0c89ebbc7275de57a202684`; common worker modules совпадают побайтно. Из production используются настоящий `EmbeddingSessionPool`, framing, HTTP и cleanup. Модель заменена локальным deterministic HTTP fixture: вход `round/actor`, ответ `[round + 1, actor + 1]`. Это измерение транспорта без модельного inference.

В первой и второй волне по capacity запросов. Barrier в HTTP endpoint не позволяет завершить ни один ответ до входа всех запросов; поэтому заполнены все слоты и затем проверено повторное использование всех тех же helpers после idle. Всего 12 фаз, 148 helpers и 296 HTTP-запросов. После каждой фазы обязательны exit code 0, закрытые pipes, восстановленные FD и отсутствие оставшихся потоков fixture/исполнения.

CPU учитывается отдельно от startup и HTTP: native `proc_pid_rusage(RUSAGE_INFO_V0)` до и после idle, с проверкой неизменности PID, UUID и process start time. Mach ticks конвертируются через фактический `mach_timebase_info`; преобразование до эксперимента сверяется с 200 мс независимого `time.process_time_ns` под CPU-нагрузкой. Деление на размер clock tick нельзя опускать на Apple Silicon. Суммарный процент CPU выражается относительно одного ядра, не всей машины.

RSS и physical footprint фиксируются отдельно. Сумма RSS процессов может повторно учитывать shared pages и не равна дополнительной физической памяти системы. Snapshot не доказывает peak. Измерение не оценивает энергию батареи, Windows/Linux CPU counters, стоимость модельного inference или производственный SLO.

Fixture использует буквальный loopback-host без `HTTPServer.getfqdn()`: первоначальная проверка показала постоянный Unix resolver socket при первом разрешении имени на macOS. После исключения ненужного DNS точное равенство FD проверяется от момента до создания server до завершения server/pool. Проверка не ослаблена допуском «ещё одного FD».

## Результаты

Измеряемый commit `b2b2447c0ed736cecdb7ded830f2e6e063c291fd`, Apple M1 Max arm64, 32 GiB, Python 3.14.3. Полный эксперимент — 125,577 с. Native calibration 200,047375 мс согласуется с Python CPU-time 200,005 мс; фактический Mach timebase — 125/3. [План](./evidence/2026-09-27/embedding-idle-budget/plan.json), [сырой результат](./evidence/2026-09-27/embedding-idle-budget/result.json), [независимый replay](./evidence/2026-09-27/embedding-idle-budget/replay.json).

Каждая строка показывает диапазон двух ABBA-фаз. Простой в каждой фазе длился не менее 10 секунд; CPU excludes startup, HTTP и close.

| Slots | Guard | CPU за 10 с, мс | CPU, % одного ядра | Суммарный RSS, MiB | Суммарный footprint, MiB | Close, мс |
|---:|---|---:|---:|---:|---:|---:|
| 1 | до | 0.000–0.000 | 0.000–0.000 | 28.906–29.328 | 17.235–17.641 | 8.569–10.287 |
| 1 | после | 5.927–5.978 | 0.059–0.060 | 29.031–29.359 | 17.375–17.688 | 21.095–22.289 |
| 4 | до | 0.000–0.000 | 0.000–0.000 | 116.000–116.172 | 69.267–69.424 | 65.547–72.956 |
| 4 | после | 19.179–20.999 | 0.192–0.210 | 115.891–115.922 | 69.221–69.236 | 48.709–62.156 |
| 32 | до | 0.000–0.000 | 0.000–0.000 | 932.844–934.094 | 559.312–560.531 | 376.410–428.221 |
| 32 | после | 134.052–135.209 | 1.339–1.351 | 933.641–934.312 | 560.610–561.313 | 352.635–417.450 |

Guard имеет измеримую постоянную стоимость: до 1,351% одного CPU-ядра суммарно у 32 helpers на этом host. У baseline за выбранные idle-интервалы прирост CPU counters был 0; это не утверждение об универсальном нулевом энергопотреблении. Память преимущественно определяется количеством Python helpers: при 32 слотах около 934 MiB суммы RSS / 561 MiB суммы footprint остаются после завершения запросов. Guard не объясняет всю эту память; размер pool является отдельным ресурсным решением.

Все 296 HTTP-запросов вернули точные ответы. По два server/client набора интервалов подтверждают barrier concurrency и продолжение работы после idle без replacement. Все 148 helpers закрыты/reaped с кодом 0; родительские FD 4 → 7/13/69 → 4, оставшихся потоков нет. Close 32 слотов занимал 352,635–428,221 мс; это наблюдение на свободных сессиях, не гарантия времени принудительной остановки.

## Проверки и дальнейшее решение

Два native measurement tests проверяют независимое преобразование CPU units и фактическую capacity/reuse/cleanup матрицу 1/4/32. Одиннадцать portable replay/mutation tests отвергают подмену timebase, короткий idle, изменённый PID/start/UUID, reset counters, неверную привязку guard, отсутствие перекрытия HTTP, ошибочные векторы и утечки pipes/FD/threads. На системах без macOS native measurement tests явно пропускаются; сохранённые результаты проверяются обычным Python без вызова Darwin API. [Manifest проверок](./evidence/2026-09-27/embedding-idle-budget/checks.json).

Оставлять до 32 прогретых интерпретаторов после краткого всплеска означает заметный idle memory budget. Следующий gate — прототип освобождения простаивающих session helpers, с явным timeout, отсутствием вмешательства в активный запрос, проверками гонок request/close и повторного прогрева. Включение такого поведения в обычный worker требует отдельной проверки. Default isolated и текущий runtime не менялись.

Полный `docs:check` прошёл: 12 Node и 277 Python tests, 1647 локальных ссылок в 198 Markdown-файлах и 13 process templates. Architecture audit плана — 0 ошибок, 0 предупреждений.

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
