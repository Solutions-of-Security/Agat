# Полный Temporal/RAG с runtime 0.12.1

28.09.2026. **Оба transport прошли полный процесс с runtime 0.12.1**, включая
отказ/restart decider, восстановление Temporal и независимый native replay.
Продолжение [диагностики exit 75](./decision-exit-diagnostics.md).
Эксперимент проверяет новый execution profile отдельно от прежнего runtime 0.12.0.
Предметная qualification и маршрутизация модели остаются выключенными.

## Фиксация профиля

Прежний launcher закреплял профиль `4bd6…` из исторического RAG-плана и отклонял
runtime 0.12.1. Теперь `--shadow-profile` принимает отдельный, заранее сохранённый
и закоммиченный экспорт команды `decision_runtime profile`. Без этого аргумента
сохраняется прежняя привязка; прошлым результатам не приписывается новый runtime.

[Экспорт 0.12.1](./profiles/runtime-0.12.1.json) имеет canonical SHA-256
`aeda3e1818101bd91f920201bb2adfa1c10463894a456924a79d17d4c12a9c25` и implementation SHA
`b6096c513c13cd070b8c64e300ba9a385b6d1eb70fb7a9358a95ae6de5e2b944`.
Он получен настоящим MLX backend с прежними pinned decider-2b весами, policy,
максимумом 2048 токена, cache 128 MiB и inference deadline 5000 мс.
Отдельная повторная загрузка дала тот же fingerprint.

Launcher допускает изменение только runtime version и implementation fingerprint;
остальные настройки должны совпадать с исходным экспериментом. Экспорт входит в
`sourceSha256` frozen plan и проверяется против Git до запуска моделей. Живой
`/health` обязан точно совпасть с выбранным профилем, в том числе после restart.

[Независимый verifier](../../../../scripts/verify-temporal-real-rag.py) извлекает
исходники из измеренного commit, читает `VERSION` через AST без исполнения кода,
пересчитывает implementation SHA из всех корневых Python-модулей runtime и
сравнивает полный профиль. Повторное вычисление внешних SHA не позволяет подменить
deadline, policy, версию или реализацию. Исторические v1–v4 evidence продолжают
проверяться по прежнему контракту.

## Выполнение и диагностика

Первый [запуск](./evidence/2026-09-28/temporal-runtime-0.12.1/attempts.json)
завершился `Owned shadow runtime startup timeout` через 30,576 с. Health и warmup
ещё не были приняты; Ollama, PostgreSQL и Temporal не запускались. Три собственных
PID завершены, cleanup errors нет. Отдельный повтор экспорта профиля занял 8,43 с.
Причина первого timeout не установлена; лимиты и модель не менялись.

Во [втором запуске](./evidence/2026-09-28/temporal-runtime-0.12.1/attempts.json)
decider и оба отдельных model warmup прошли, но первый рабочий embedding request
завершился `TimeoutError` после 30 с. Документы не стали ready за 60 с, primary и
shadow workload не запускались. В сохранённых `/api/ps` samples видна только Qwen3;
причину таймаута по этим данным установить нельзя. Все собственные ресурсы закрыты.

Журналы Ollama и decider теперь сохраняются даже при раннем отказе в игнорируемом
`docs/private/temporal-real-rag/<путь evidence относительно docs>/`
с правами 0600 уже при создании файлов. Их SHA входят в launcher report. После
остановки дочерних процессов harness также сохраняет их ограниченные журналы
в той же приватной области. Пустой журнал первого startup
отказа не доказывает причину; SIGKILL родителя также не обязан оставить retirement
event. Сырые process samples и системные измерения хоста остаются приватными.

## Успешный полный прогон

Следующий диагностический запуск с `--shadow-resources` завершился успешно на
commit `5a9a464aaedc1ce97cce04e84a73c5f2622a0441`; frozen plan закрепляет 205 файлов.
Настройки моделей, policy, inference/startup deadlines и transport сохранены.
Полный протокол и resource counters находятся в `docs/private`; публичная
[сводка](./evidence/2026-09-28/temporal-runtime-0.12.1/result-summary.json)
содержит результаты и SHA файлов без resource samples и идентификаторов процессов.
Приватная копия сохранена также в основной рабочей директории проекта под
`docs/private/decision-profile-rollout-20260928/`.

| Проверка | isolated | session |
|---|---:|---:|
| Primary calls / embedding items | 3 / 5 | 3 / 5 |
| Настоящий shadow inference / unavailable fallback | 2 / 1 | 2 / 1 |
| Контролируемый SIGKILL и restart decider | pass | pass |
| Activity retry, durable timer, сохранение Workflow Run ID | pass | pass |
| Снимки принятых stage/shadow и primary fallback | pass | pass |
| Tenant RLS и запрет release registry | pass | pass |
| Native replay в harness и отдельно после cleanup | pass | pass |

Три startup warmup decider учитываются отдельно от шести workflow attempts.
Двенадцать source citations проверены; оба transport имеют одинаковые prompts,
outputs и векторы пяти входов. [Сравнение с runtime 0.12.0](./evidence/2026-09-28/temporal-runtime-0.12.1/baseline-comparison.json)
также подтвердило совпадение всех шести primary outputs, prompt fingerprints,
token counts и embedding evidence. Четыре собственных контейнера удалены,
модели выгружены, оставшихся наблюдавшихся PID и cleanup errors нет.

Один успешный прогон не отменяет два отказа и не устанавливает их причины.
Согласно [Ollama FAQ](https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests),
совместная загрузка зависит от доступной памяти, а при нехватке новые запросы
могут ждать в очереди. Это возможный механизм, не установленная причина именно
этого отказа. Успешный повтор не доказывает cold-start надёжность или SLO.

Семь новых тестов проверяют привязку явного профиля, изменение исходников/версии,
подмену policy/deadline, выход пути за docs и независимость исторического профиля.
Вместе с прежними verifier и process-control tests прошли 53 проверки.
[Полная регрессия](./evidence/2026-09-28/temporal-runtime-0.12.1/checks.json): 293 coordinator, 61 web, 119 decision, 34 исторических Temporal replay, 12 desktop/mobile browser — pass. Worker: 140 pass и три первоначальных skip из-за отсутствия LangGraph в MLX-окружении; эти три проверки отдельно прошли в установленном LangGraph-окружении. Docs: 12 Node + 413 Python tests, 2106 локальных ссылок и каталог процессов — pass; typecheck, строгая проверка integration harness и все сборки — pass. Предметное качество на независимых заявках не оценивалось.

Следующий инженерный этап — исправить воспроизведённую зависимость cleanup
контроллера от успешного получения process inventory: ошибка наблюдения должна
сохраняться в отчёте, но не мешать остановке уже принадлежащего контроллеру процесса.

## Воспроизведение

Нужны Node 24, Docker, установленные pinned Ollama-модели и резидентное MLX-окружение
с точными зависимостями `decision_runtime/requirements-mlx.txt`. `AGAT_MLX_PYTHON`
и `AGAT_DECIDER_MANIFEST` обозначают локальный Python и уже проверенный manifest.
Выберите новый каталог evidence; существующие измерения не перезаписываются.

```bash
"$AGAT_MLX_PYTHON" scripts/run-temporal-real-rag.py \
  --evidence-dir docs/qualification/local-decisions/performance/evidence/local-profile-repeat \
  --shadow-python "$AGAT_MLX_PYTHON" \
  --shadow-manifest "$AGAT_DECIDER_MANIFEST" \
  --shadow-profile docs/qualification/local-decisions/performance/profiles/runtime-0.12.1.json \
  --shadow-recovery
```

Используются отдельные собственные PostgreSQL/Temporal для каждого transport,
реальные primary и embedding вызовы и отдельные typed warmups decider. Workflow
не выполняет модельные вычисления: они остаются вне deterministic replay.
Проверка интеграции и независимый native replay соответствуют
[официальному руководству Temporal](https://docs.temporal.io/develop/typescript/best-practices/testing-suite).
Успешный прогрев не гарантирует последующий inference; MLX использует
[ленивое выполнение](https://ml-explore.github.io/mlx/build/html/usage/lazy_evaluation.html).
Эти источники не устанавливают причину наблюдаемого startup timeout.
