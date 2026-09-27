# Повтор main HTTP-профиля после SQL deadline

Дата: **27.09.2026**. План закрепляется до измерения. Продолжение [SQL deadline](./retrieval-control-lock-deadline.md) и [потери подтверждения COMMIT](./retrieval-commit-acknowledgement.md).

Повторяется протокол [обычного main](./retrieval-main-http.md): одна неизменная версия исходников и собранных JS artifacts, Node 24, отдельный одноразовый PostgreSQL для каждого режима, 9716 кандидатов × 768 измерений, старый точный документ, cap=10000, poolMax=4 на роль. Режимы идут последовательно: sync, затем isolated. Каждый — один warmup, idle 2 с и три всплеска конкурентности 1/2/4. В паре 42 измеряемых поиска + 2 warmup. Health планируется внешним Python-клиентом каждые 100 мс с максимумом четырёх ожидающих probes; пропуски сохраняются отдельно.

Основная неопределённость — дополнительные SQL-команды перед statements и FETCH. Измеряются прежние latency, event-loop delay, фактический maintenance и lifetime RSS main. Принимается только полный replay с точными winner/SHA/score/K1 и durable trace для каждого поиска, правильным режимом executor, одинаковыми исходниками и build artifacts, штатным exit main и удалением собственного контейнера. Failure не заменяется успешным результатом и не скрывается повторным запуском в тот же каталог.

```sh
python3 scripts/run-rag-http-probe.py --coordinator-entry main --execution-mode sync --maintenance-interval-ms 1000 --evidence-dir docs/qualification/local-decisions/performance/evidence/2026-09-27/retrieval-deadline-main-sync
python3 scripts/run-rag-http-probe.py --coordinator-entry main --execution-mode isolated --maintenance-interval-ms 1000 --evidence-dir docs/qualification/local-decisions/performance/evidence/2026-09-27/retrieval-deadline-main-isolated
python3 scripts/verify-rag-http-probe.py --evidence-dir docs/qualification/local-decisions/performance/evidence/2026-09-27/retrieval-deadline-main-isolated --compare-control docs/qualification/local-decisions/performance/evidence/2026-09-27/retrieval-deadline-main-sync
```

Сравнение внутри пары описательное: общий хост, нерандомизированный порядок и короткие closed-loop bursts. Разницу с прошлым commit нельзя приписать только SQL deadline: между опытами менялись код обработки ошибок и нагрузка хоста. Ни основной SLA, ни качество модельных ответов этот синтетический ranking не оценивает. До окончания запусков результатов нет.
