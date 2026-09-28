# Реальный RAG после изоляции HTTP worker

Дата: **28.09.2026**. Повтор с Qwen3 8B, embeddinggemma и MLX decider завершил **12 workflows, 36 primary calls, 44 embedding items и 18 shadow calls**. Все shadow outcomes — `ok/accepted`; primary outputs сохранены. Независимый verifier подтвердил источники, embeddings, citations, профили, результаты и cleanup. Все 12 итогов содержат девять правильных контрольных величин одной учебной задачи.

Это продолжение [проверок восстановления](./coordinator-completion-recovery.md) и [фиксации transport в плане](./rag-transport-profile.md). Инженерный прогон не является предметной qualification, production SLO или оценкой причинного overhead.

## Протокол

До нагрузки сохранён [план измерения](./evidence/2026-09-28/rag-http-isolation/measurement-intent.json). Четыре блока ABBA: control → shadow → shadow → control; три процесса с тремя single-agent stages в каждом блоке, concurrency 2. Fixture и model-visible metadata совпадают с [опытом 26 сентября](./rag-paired.md). Transport явно задан как `isolated` и проверен в обоих frozen plans. Primary и coordinator search используют новые owned HTTP helpers. Harness закреплён в `1017433`; перед запуском HEAD `3512f72` добавил только исправление MinIO integration и его документацию.

Apple M1 Max, 32 ГиБ; Node 24.14.0, worker Python 3.14.3 с LangGraph 1.2.11, launcher Python 3.13.12, MLX 0.32.2, mlx-lm 0.31.3, Transformers 5.17.0, Ollama 0.34.2. Все 34 MLX requirements совпали с lockfile. Этапы используют `single/tool_loop_v1`, а не LangGraph execution.

Primary `qwen3:8b` и `embeddinggemma:latest` сохранили manifest digests `500a1f…2b8b41` и `854626…679f1`. Decider revision — `b37f7e1ba3fbc9238004cf531fabbee2619973fd`; полный model artifact и файлы проверены по прежним SHA. Профиль decision **`4bd6e0de2bfde982d8d5fbdfc4d5e7ef36ccd1d8bb69356cd558a33934cb7a2a`** совпал с историческим опытом и не менялся между блоками.

Собственные Ollama и MLX servers слушают loopback. Cloud выключен; Ollama держит максимум две модели, `NUM_PARALLEL=1`, context 8192. Primary: temperature 0,2, seed 0, output budget 384, `think=false`; embeddings: 768D, context 2048, `truncate=false`. Отдельный прогрев: один primary call, embedding batch из двух источников, один decision call. Их нет в измеряемых счётчиках 36/44/18.

## Время и ответы

| Блок | До cleanup, с | Индексация, с | Primary / embedding calls | Shadow / перекрытие primary HTTP | Max shadow HTTP, мс |
|---|---:|---:|---:|---:|---:|
| Control, первый | 33,164 | 1,164 | 9 / 11 | 0 / 0 | — |
| Shadow, первый | 36,094 | 1,031 | 9 / 11 | 9 / 6 | 1831,379 |
| Shadow, второй | 35,524 | 1,318 | 9 / 11 | 9 / 6 | 1926,226 |
| Control, последний | 28,126 | 0,986 | 9 / 11 | 0 / 0 | — |

`workflowPhase.elapsedMs` заканчивается после валидации результатов, **до** cleanup в `finally`. Он включает запуск worker и индексацию. Полный workload с прогревом, межфазовыми проверками и cleanup занял **164,092 с**; launcher с загрузкой/остановкой моделей — **174,139 с**. Прогрев — **30,726 с**, сумма четырёх фаз до cleanup — **132,908 с**.

В прежнем запуске workload составлял 112,036 с, прогрев — 6,237 с, сумма фаз — 105,375 с. [Сравнение](./evidence/2026-09-28/rag-http-isolation/historical-comparison.json) сохраняет оба результата. Наблюдаемое увеличение времени нельзя приписать HTTP helpers: прежний worker environment и transport не были явно закреплены, менялись код, cache, момент запуска и нагрузка машины. Первый и последний control нынешнего опыта сами различаются примерно на пять секунд. Четыре заранее упорядоченных блока не являются случайно назначенными независимыми повторениями; [NIST описывает это различие](https://www.itl.nist.gov/div898/handbook/pri/section3/pri331.htm). Причинная оценка и доверительный интервал не вычислялись.

Наборы SHA primary-prompts и outputs совпали не только между нынешними блоками, но и с сохранённым историческим запуском. Три уникальных текста повторяются по 12 раз. Во всех 36 stages присутствуют оба источника; неизвестных citation markers нет, прежние маркеры сохраняют свою привязку.

Итоговый текст одинаков во всех 12 повторах: заявки **100 → 120 (+20%)**, среднее **4 → 3 ч/заявку (−25%)**, отклонения **8% → 5% (−3 п.п.)**. Сохранены учебный характер данных и неизвестность причин. [Аудит](./evidence/2026-09-28/rag-http-isolation/output-audit.json) связывает каждую запись с точным арифметическим reference и проверяет фактическую формулировку. Это assistant review трёх известных текстов и literal checks, не универсальный текстовый grader и не независимая человеческая приёмка. Shadow-вопрос проверяет упоминание двух месяцев, а не арифметику.

## Доступность файлов и ресурсы

Первый запуск [завершился startup timeout](./evidence/2026-09-28/rag-http-isolation/isolated/rag-workflow-launcher-result.json) до model workload. Диагностический traceback показал ожидание чтения весов в `sha256_file`. У весов, tokenizer и части прежнего venv был macOS flag `SF_DATALESS`; [Apple объясняет восстановление содержимого таких файлов при доступе](https://developer.apple.com/documentation/technotes/tn3150-getting-ready-for-data-less-files). Это подтверждает недоступность файлов, но не измеряет inference latency.

В `/private/tmp` создан отдельный venv из прежнего lockfile и восстановлены те же публичные model files по закреплённой revision. Все SHA проверены до нового запуска; оригинальные файлы не перезаписывались. Частичный download продолжен HTTP Range с сохранением 1 667 506 176 байт. Startup и inference limits не повышались. [Диагностика](./evidence/2026-09-28/rag-http-isolation/startup-diagnostic.log), [исходные file flags](./evidence/2026-09-28/rag-http-isolation/original-model-file-status.json), [восстановление](./evidence/2026-09-28/rag-http-isolation/model-restore.json) и [cleanup неудачной попытки](./evidence/2026-09-28/rag-http-isolation/startup-failure-cleanup.json) сохранены. При ошибке constructor inference PID не попал в опубликованный inventory; cleanup-record первой попытки подтверждает только перечисленные PID.

В успешном запуске обе Ollama-модели наблюдались загруженными; после explicit unload список пуст. MLX остановлен с `stopReason=closed`. Проверено отсутствие **71 наблюдавшегося собственного PID**, ошибок cleanup/inventory нет. Это выборочные process inventories с циклом ожидания 0,5 с и 17 снимков loaded models с периодом около 10 с. Короткие helpers могут не попасть в inventory; непрерывный RSS/VRAM peak и общее число всех созданных helpers здесь не измерены. Перекрытие HTTP включает очередь Ollama и не доказывает параллельное выполнение GPU kernels.

## Evidence и воспроизведение

- [Workflow plan](./evidence/2026-09-28/rag-http-isolation/resident-isolated/rag-workflow-plan.json), [results и outputs](./evidence/2026-09-28/rag-http-isolation/resident-isolated/rag-workflow-result.json), [independent verification](./evidence/2026-09-28/rag-http-isolation/resident-isolated/verification.json).
- [Launcher plan](./evidence/2026-09-28/rag-http-isolation/resident-isolated/rag-workflow-launcher-plan.json), [launcher result и inventory](./evidence/2026-09-28/rag-http-isolation/resident-isolated/rag-workflow-launcher-result.json), [progress log](./evidence/2026-09-28/rag-http-isolation/resident-isolated-launch.log).
- [Resident environment](./evidence/2026-09-28/rag-http-isolation/resident-environment.json), [snapshot ignored model manifest](./evidence/2026-09-28/rag-http-isolation/model-manifest-snapshot.json), [Ollama blob residency](./evidence/2026-09-28/rag-http-isolation/ollama-file-status.json).
- [Проверки и SHA evidence](./evidence/2026-09-28/rag-http-isolation/measurement-checks.json); [проверки harness](./rag-transport-profile.md) относятся к отдельному этапу.

Используйте launcher с закреплёнными MLX dependencies, worker Python в `PATH`, полностью доступный локальный manifest и новый каталог evidence. Команда запуска приведена в [профиле transport](./rag-transport-profile.md). Для offline verification модели загружать не нужно; исходники должны соответствовать frozen SHA:

```bash
python3 scripts/verify-decision-rag-workflow.py \
  --evidence-dir docs/qualification/local-decisions/performance/evidence/2026-09-28/rag-http-isolation/resident-isolated \
  --source-snapshot docs/qualification/local-decisions/performance/evidence/2026-09-28/rag-http-isolation/model-manifest-snapshot.json \
  --output docs/qualification/local-decisions/performance/evidence/new-rag-reverification.json
```

Следующий инженерный gate — измерить время владения запросами и sampled RSS **всех трёх типов HTTP helpers** на реальном worker, отдельно от model/proxy latency. Нынешнего elapsed time недостаточно для выбора новой transport-оптимизации. Реальные бизнес-источники, независимый holdout и production SLO остаются открытыми.
