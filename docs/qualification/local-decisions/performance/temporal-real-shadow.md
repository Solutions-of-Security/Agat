# Реальный shadow decider при восстановлении Temporal/PostgreSQL

Дата: 28.09.2026. Статус: **оба режима с явно прогретым decider прошли интеграционный gate**. После потерянного tick acknowledgement и SIGKILL Temporal worker сохранились primary outputs, первое принятое shadow-наблюдение, retrieval provenance и Workflow Run ID. Replay не добавил inference. Предметная qualification и маршрутизация decision-модели не включены.

Продолжение [реальных Qwen3/embeddinggemma с Temporal/PostgreSQL](./temporal-real-rag.md): добавлен настоящий MLX decider-2b в существующий opt-in harness. Ordinary Python worker выполняет primary, затем shadow, coordinator принимает оба результата; Temporal управляет процессом через Activity.

## Профиль и протокол

[Frozen plan](./evidence/2026-09-28/temporal-real-shadow/run-warm/plan.json) закрепляет 194 файла commit `04ff5f8d8be02f82ed8bd02d9c27b35d4051fa0f`. Qwen3, embeddinggemma, PostgreSQL, Temporal, transport/concurrency и двухдокументный синтетический fixture те же, что в предыдущем gate. Каждый transport получает собственные PostgreSQL и Temporal.

MLX runtime работает в отдельном Python 3.13.12 с 34 закреплёнными пакетами, включая MLX 0.32.2 и mlx-lm 0.31.3. Все шесть файлов локального model manifest проверяются по SHA до запуска. `Mapika/decider-2b`: revision `b37f7e1ba3fbc9238004cf531fabbee2619973fd`, artifact SHA `4ed0133fe678d5c60b78a59ad6c5e39ee24ee2c82e8fe1bc0699e5a0b2e7ee86`.

Runtime profile **`4bd6e0de2bfde982d8d5fbdfc4d5e7ef36ccd1d8bb69356cd558a33934cb7a2a`** совпадает с [предыдущим реальным RAG](./rag-http-isolation.md): runtime 0.12.0, isolated inference process, deadline 5000 мс, 2048 input tokens, allocator cache 128 MiB, без квантования и калибровки. Policy `agat.shadow.v1`: probability ≥0,8, margin ≥0,1. Профиль и здоровье сверяются до и после опыта; зависимости проверяются по requirements.

До загрузки Ollama-моделей выполняется один отдельный typed decision на исходном fixture input; warmup явно записан в plan/result и не включён в шесть workflow shadow calls. Затем запускается прежний сценарий: первый stage принят; второй реальный primary response удержан; Temporal worker остановлен и заменён; до выпуска ответа число shadow calls остаётся 1. После выпуска сохраняются остальные два stage/наблюдения.

## Наблюдения

| Проверка | isolated | session |
|---|---:|---:|
| Primary calls / embedding items | 3 / 5 | 3 / 5 |
| Shadow calls / accepted observations | 3 / 3 | 3 / 3 |
| Shadow input tokens по шагам | 139 / 155 / 154 | 139 / 155 / 154 |
| Shadow inference, мс | 1684,591 / 1877,151 / 1619,469 | 2355,016 / 1828,948 / 975,446 |
| Generated decision tokens | 0 | 0 |
| События history / tick requests | 63 / 6 | 63 / 6 |
| Полное время сценария, мс | 42 780,301 | 36 553,847 |

[Isolated](./evidence/2026-09-28/temporal-real-shadow/run-warm/isolated.json), [session](./evidence/2026-09-28/temporal-real-shadow/run-warm/session.json) и [offline verification](./evidence/2026-09-28/temporal-real-shadow/run-warm/verification.json) подтверждают точную привязку каждого request/result/observation к stage и входному состоянию. Softmax, margin, выбранный вариант и policy outcome пересчитаны по logits. Все outcomes — `ok/accepted`, fallback — `primary`. Первый accepted observation совпадает с байтовым снимком до SIGKILL; дубликатов нет.

[Сравнение с предыдущим запуском без shadow](./evidence/2026-09-28/temporal-real-shadow/baseline-comparison.json) подтверждает одинаковые prompts, primary outputs/token counts, embedding inputs/vectors/dimensions для каждого transport. Итоговая арифметика остаётся прежней: 100 → 120 заявок (+20%), 4 → 3 ч/заявку (−25%), 8% → 5% отклонений (−3 п.п.); учебный характер и неизвестность причин сохранены.

В обеих фазах подтверждены Activity attempt 2 после потерянного tick reply, две worker identity, тот же Workflow Run ID и сработавший durable timer. Tenant RLS даёт собственному проекту `[1 run, 3 retrieval, 2 chunks]`, чужому `[0, 0, 0]`; release registry закрыт для tenant. Native replay внутри live harness не меняет trace и не вызывает primary/embedding/decision/tick. После cleanup истории ещё раз прошли [независимый native replay](./evidence/2026-09-28/temporal-real-shadow/native-replay.json).

[Launcher](./evidence/2026-09-28/temporal-real-shadow/run-warm/launcher.json): всего 114 189,624 мс, включая warmup decision 702,527 мс, отдельные Ollama warmups, сборки, migration/admission и cleanup. Четыре контейнера удалены, Ollama-модели выгружены, все 77 наблюдавшихся собственных PID отсутствуют, cleanup errors нет. CLI decider штатно обрабатывает SIGTERM через KeyboardInterrupt и возвращает 130; его inference process собран. Счётчик PID является sampled inventory.

## Первый отказ и границы вывода

[Первый запуск без отдельного decision warmup](./evidence/2026-09-28/temporal-real-shadow/run/launcher.json) остановился после первого primary ответа: decider объявил backend недоступным и вышел с кодом 75. Все ресурсы закрыты. Тогда proxy проверял HTTP status до записи тела ошибки, поэтому точный typed reason **не сохранён**; приписывать отказ конкретно deadline, памяти или compilation нельзя. Первый harness также считал уже завершившийся backend ошибкой cleanup; это исправлено отдельно от контроля оставшихся PID.

Теперь ответ proxy сохраняется до проверки status, а typed warmup fail фиксируется до запуска основного workload. Отдельный прогрев согласуется с тем, что [MLX выполняет вычисления лениво](https://ml-explore.github.io/mlx/build/html/usage/lazy_evaluation.html) и [первая компиляция kernels может иметь дополнительную стоимость](https://ml-explore.github.io/mlx/build/html/install.html). Эти сведения объясняют смысл startup inference, но не устанавливают причину первоначального отказа.

Два повтора одного synthetic fixture не дают SLO, независимой оценки качества или доказательства надёжности cold start. Shadow здесь отвечает только на вопрос о наличии сопоставления июля и августа; `accepted` не означает, что decider проверил арифметику отчёта. Разница времени с предыдущим опытом не является причинным измерением overhead. Production runtime и default transport не менялись.

## Перепроверка и следующий шаг

[Verifier](../../../../scripts/verify-temporal-real-rag.py) поддерживает прежние v1 evidence и новый v2, проверяет pinned profile, runtime dependencies, typed request и распределения, snapshot наблюдения, primary fallback, replay evidence и cleanup. [22 mutation/replay tests](../../../../scripts/test/test_temporal_real_rag_verifier.py) включают прежние девять и 13 новых: подменённые вероятности и input fingerprints отклоняются даже после согласованной перезаписи proxy/persisted result и пересчёта внешних SHA. Проверки и SHA логов собраны в [checks.json](./evidence/2026-09-28/temporal-real-shadow/checks.json).

Следующий gate — настоящий отказ локального decider во время этого процесса: подтверждение primary fallback, сохранности ранее принятых наблюдений и восстановления shadow после restart runtime. Причина первого cold-start отказа остаётся отдельным открытым вопросом; повышение deadline без измерений не выполняется.
