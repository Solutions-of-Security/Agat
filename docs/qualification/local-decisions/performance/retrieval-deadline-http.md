# Повтор main HTTP-профиля после SQL deadline

Дата: **27.09.2026**. План закрепляется до измерения. Продолжение [SQL deadline](./retrieval-control-lock-deadline.md) и [потери подтверждения COMMIT](./retrieval-commit-acknowledgement.md).

Повторяется протокол [обычного main](./retrieval-main-http.md): одна неизменная версия исходников и собранных JS artifacts, Node 24, отдельный одноразовый PostgreSQL для каждого режима, 9716 кандидатов × 768 измерений, старый точный документ, cap=10000, poolMax=4 на роль. Режимы идут последовательно: sync, затем isolated. Каждый — один warmup, idle 2 с и три всплеска конкурентности 1/2/4. В паре 42 измеряемых поиска + 2 warmup. Health планируется внешним Python-клиентом каждые 100 мс с максимумом четырёх ожидающих probes; пропуски сохраняются отдельно.

Основная неопределённость — дополнительные SQL-команды перед statements и FETCH. Измеряются прежние latency, event-loop delay, фактический maintenance и lifetime RSS main. Принимается только полный replay с точными winner/SHA/score/K1 и durable trace для каждого поиска, правильным режимом executor, одинаковыми исходниками и build artifacts, штатным exit main и удалением собственного контейнера. Failure не заменяется успешным результатом и не скрывается повторным запуском в тот же каталог.

```sh
python3 scripts/run-rag-http-probe.py --coordinator-entry main --execution-mode sync --maintenance-interval-ms 1000 --evidence-dir docs/qualification/local-decisions/performance/evidence/2026-09-27/retrieval-deadline-main-sync
python3 scripts/run-rag-http-probe.py --coordinator-entry main --execution-mode isolated --maintenance-interval-ms 1000 --evidence-dir docs/qualification/local-decisions/performance/evidence/2026-09-27/retrieval-deadline-main-isolated
python3 scripts/verify-rag-http-probe.py --evidence-dir docs/qualification/local-decisions/performance/evidence/2026-09-27/retrieval-deadline-main-isolated --compare-control docs/qualification/local-decisions/performance/evidence/2026-09-27/retrieval-deadline-main-sync
```

Сравнение внутри пары описательное: общий хост, нерандомизированный порядок и короткие closed-loop bursts. Разницу с прошлым commit нельзя приписать только SQL deadline: между опытами менялись код обработки ошибок и нагрузка хоста. Ни основной SLA, ни качество модельных ответов этот синтетический ranking не оценивает.

## Результат

Оба запуска выполнены на **`dd3aab65ba545ef536bec65c4d7b3643d47b6cd0`**, Node **24.14.0**, с одинаковыми source/build hashes и PostgreSQL image IDs. [Полный независимый replay](./evidence/2026-09-27/retrieval-deadline-main-comparison.json) подтвердил **42/42 поиска + 2/2 warmup**, включая winner, SHA, score=1, `K1` и durable trace. Фактический main завершился с кодом 0 в обоих режимах; оба fixture-процесса также завершились, четыре PID отсутствовали при проверке после опытов. Собственные PostgreSQL-контейнеры удалены без cleanup errors.

| Конкурентность | Health max, sync → isolated, мс | Невыпущенные health probes, sync → isolated | Search p50, sync → isolated, мс | Search max, sync → isolated, мс |
|---:|---:|---:|---:|---:|
| 1 | 1889,130 → 80,873 | 35 → 0 | 1467,888 → 1879,650 | 1982,472 → 1940,781 |
| 2 | 3155,139 → 16,956 | 74 → 0 | 2115,126 → 2558,541 | 3233,407 → 3555,977 |
| 4 | 6755,236 → 33,989 | 172 → 0 | 4129,533 → 4647,009 | 6843,447 → 7966,216 |

Isolated сохраняет отзывчивость health в этом опыте, но **медиана поиска выше на всех трёх уровнях**. При конкурентности 4 максимальный поиск также дольше: 6,843 → 7,966 с. Это не ускорение поиска и не основание менять default `sync` без операторского решения.

Максимальный event-loop delay main: **2073,035 → 65,405 мс**. Наибольшая длительность maintenance во всех фазах: **26,561 → 49,550 мс**. В isolated во время нагрузки зарегистрированы 5/10/20 реальных maintenance calls для уровней 1/2/4; после каждой фазы очередь пуста и admission открыт. Lifetime peak RSS main, включая его worker threads: **305152000 → 353763328 bytes**.

Начальная load average за минуту составила **12,73 → 15,07**, конечная — **16,21 → 15,31**. Результаты относятся только к закреплённой паре. Они не выделяют собственную стоимость SQL deadline и не сравнивают managed PostgreSQL, реальный model inference или эксплуатационные SLO.

Новая пара добавлена в существующие replay-тесты CI вместе с прежней main-парой и handler-only архивом; отрицательные сценарии сохранены. [Проверки и хэши](./evidence/2026-09-27/retrieval-deadline-main-checks/checks.json). После измерений изменялся только отдельный COMMIT fault fixture: его `setNoDelay(true)` не входит в runtime coordinator или этот HTTP probe и не меняет исходники измеренной пары.

Следующий инженерный gate — срок lease во время записи retrieval/trace после ranking: уже проверенные row locks и request deadline не доказывают, что lease остаётся действующим после ожидания на поздней SQL-команде. Сначала требуется отдельное воспроизведение; при подтверждении отказ должен откатить весь retrieval без частичных markers/events.
