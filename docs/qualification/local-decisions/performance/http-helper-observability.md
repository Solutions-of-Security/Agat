# Все owned HTTP helpers в реальном RAG

Дата: **28.09.2026**. Обычный worker завершил **12 workflows / 36 primary / 44 embedding / 36 knowledge requests**. Наблюдатель связал все **116 запросов с 98 процессами**, проверил их завершение и закрытие pipes. Independent replay прошёл; ответы и векторы сохранены. Предметная qualification и production SLO не оценивались.

Это следующий этап после [трёхмодельного RAG](./rag-http-isolation.md). В этом опыте shadow выключен: измеряется владение HTTP-запросами worker и память helpers при одинаковой двухмодельной нагрузке.

## Протокол и реализация

[План измерения](./evidence/2026-09-28/http-helper-observability/measurement-intent.json) записан до нагрузки. Измеряемый commit — `42256df2ff5d34ea2a1b6a807d57ed14bb06ba0e`; [frozen plan](./evidence/2026-09-28/http-helper-observability/run/plan.json) содержит SHA **79 файлов**. Четыре блока ABBA: isolated → session → session → isolated; три workflows по три single-agent stages в каждом, concurrency 2, одинаковые model-visible metadata `embedding_transport_rag`. Fixture, веса, prompts и outputs сопоставимы с [двухмодельным опытом 27 сентября](./embedding-worker-rag.md), но код и состояние машины изменились: причинное сравнение этих дат не выполняется.

Apple M1 Max, 32 ГиБ; Python 3.14.3, Node 24.14.0, Ollama 0.34.2. [Установленные worker dependencies](./evidence/2026-09-28/http-helper-observability/runtime.json) совпадают с requirements, включая LangGraph 1.2.11. Qwen3 8B и embeddinggemma сохранили manifest digests `500a1f…2b8b41` и `854626…679f1`. Собственный Ollama на loopback: cloud off, максимум две модели, `NUM_PARALLEL=1`; primary context 8192, temperature 0,2, seed 0, output budget 384, `think=false`; embedding 768D, context 2048, `truncate=false`. Прогрев отдельно: один primary и batch двух source embeddings; **6,993 с**.

Флаг `--http-helpers` включает frozen `ownedHttpProbe=agat.worker.owned-http.v1`. Harness задаёт его независимо от inherited environment. Старые поля probe по-прежнему относятся только к embeddings; дополнительный `ownedHttp` содержит Embedding, Model и Knowledge. Обычные worker transport и defaults не изменены. Перехватываются реальные call/return, Popen и session retire через [Python profiler](https://docs.python.org/3/library/sys.html#sys.setprofile) и [профилирование новых потоков](https://docs.python.org/3/library/threading.html#threading.setprofile), без подмены ответов. Неожиданный helper без request owner является ошибкой evidence; отдельный session ping не входит в поддерживаемый профиль этого ordinary-worker harness.

Для каждого вызова фиксируются тип, thread, PID, монотонные границы, объём успешного ответа и состояние pipes. При исключении return-event также возникает; `completed=false` не считается успешным запросом. Request URL, headers и bodies в новый набор наблюдений не записываются. Session переиспользует только embedding helpers, остальные два типа остаются isolated.

## Задержки

| Блок | Фаза до cleanup, с | Индексация, с | Embedding owner p50, мс | Primary owner p50, мс | Knowledge owner p50, мс |
|---|---:|---:|---:|---:|---:|
| 0 isolated | 36,840 | 2,491 | 130,547 | 7073,350 | 84,130 |
| 1 session | 30,667 | 2,084 | 64,310 | 5706,627 | 102,149 |
| 2 session | 28,731 | 1,759 | 58,403 | 5361,890 | 88,087 |
| 3 isolated | 31,303 | 2,382 | 162,805 | 5340,535 | 92,012 |

В блоке n=11 embedding, n=9 primary и n=9 knowledge; nearest-rank p95 равен max. Owner duration включает сериализацию, запуск/IPC, backend, доставку ответа и завершение disposable helper. Primary proxy p50 по блокам: **6987,849 / 5562,302 / 5231,740 / 5254,660 мс**. Разность сумм owner и proxy, делённая на число вызовов, составляет **86,342 / 103,580 / 91,797 / 93,480 мс**. Это включает планирование и IPC, не является чистым временем Popen. Для embedding такая средняя разность — **95,630 / 20,149 / 13,931 / 97,530 мс**; session сохраняет первоначальные lazy starts. Время coordinator search отдельно на сервере не измерено; всю Knowledge duration нельзя объявить transport overhead.

Фаза включает startup worker и индексацию, заканчивается до `finally` cleanup. Полный Node workload с прогревом и cleanup — **135,132 с**, launcher — **137,863 с**. Суммировать перекрывающиеся owner durations как wall-clock нельзя. Четыре блока ABBA дают описательное наблюдение; снижение времени между блоками не является причинной оценкой эффекта session.

## Память и завершение

| Блок | Helpers: embedding / primary / knowledge | Worker RSS p50 / max, МиБ | Все helpers RSS p50 / max, МиБ | RSS snapshots | Helpers без RSS sample |
|---|---:|---:|---:|---:|---:|
| 0 isolated | 11 / 9 / 9 | 74,109 / 85,125 | 48,859 / 55,734 | 301 | 2 knowledge |
| 1 session | 2 / 9 / 9 | 42,203 / 85,188 | 91,859 / 114,984 | 253 | 3 knowledge |
| 2 session | 2 / 9 / 9 | 69,156 / 85,078 | 84,875 / 114,094 | 240 | 4 knowledge |
| 3 isolated | 11 / 9 / 9 | 70,016 / 85,312 | 46,047 / 55,906 | 258 | 3 knowledge |

Sampler ждёт 100 мс между `ps`; фактический интервал включает выполнение `ps`. RSS переводится из [1024-байтовых единиц macOS](https://raw.githubusercontent.com/apple-oss-distributions/adv_cmds/main/ps/ps.1). Это выборочные значения, не точные пики, не GPU allocations и не сумма уникальных физических страниц. **12 из 36 knowledge helpers** не попали в RSS-снимки; их запуск, owner и закрытие всё равно записаны profiler. Для session дополнительные живые embedding helpers дают RSS p50 **44,188 / 43,438 МиБ** в двух блоках. Накладные расходы наблюдателя включены во все режимы.

Все 98 helpers завершились с кодом 0 и закрытыми stdin/stdout; один session PID не обслуживал два запроса одновременно. Все четыре worker закрылись с неизменным числом FD, без активных запросов или оставшихся фоновых потоков. После unload `/api/ps` пуст; **127 наблюдавшихся собственных PID** отсутствуют, cleanup errors — 0. В inventory вошли все helpers из profiler, а дополнительные короткие служебные процессы могли пройти между launcher snapshots. Сохранены 14 снимков residency моделей; они не измеряют общий пик памяти.

Все 36 primary stages сохранили ответы и ссылки на оба источника, неизвестных markers — 0; пять уникальных embedding inputs дали одинаковые векторы. Мультимножества prompts/outputs совпали между четырьмя блоками и с опытом 27 сентября. [Аудит ответов](./evidence/2026-09-28/http-helper-observability/output-audit.json) сверяет одну повторную учебную задачу с точным reference: 100 → 120 (+20%), 4 → 3 ч/заявку (−25%), 8% → 5% (−3 п.п.), учебные данные и неизвестные причины. Это assistant review трёх уникальных текстов и literal checks 12 итогов, не независимая человеческая приёмка.

## Проверка и следующий шаг

[Workflow](./evidence/2026-09-28/http-helper-observability/run/workflow.json), [launcher и cleanup](./evidence/2026-09-28/http-helper-observability/run/launcher.json), [independent replay](./evidence/2026-09-28/http-helper-observability/run/replay.json) и [проверки](./evidence/2026-09-28/http-helper-observability/checks.json) сохранены. Пять loopback regression tests проверяют реальные три типа helpers, session reuse, HTTP 500 и несовместимые подмены evidence. Ещё **31 replay/mutation test** проверяет старый и новый workload, frozen scope, ownership, lifetime, RSS и launcher inventory. Strict TypeScript — pass. [Итоговая регрессия](./evidence/2026-09-28/http-helper-observability/final-validation.json): 12 Node + 344 Python tests; documentation links и process catalog — pass.

```bash
python3 scripts/profile-embedding-rag.py --http-helpers \
  --evidence-dir docs/qualification/local-decisions/performance/evidence/local/all-http
python3 scripts/verify-embedding-rag.py \
  docs/qualification/local-decisions/performance/evidence/local/all-http
```

Решение: измерение не обосновывает дополнительный постоянный pool для primary/knowledge. Primary latency в этом workload главным образом находится внутри proxy/backend, а session embedding уже имеет измеримый компромисс между задержкой и памятью; default isolated сохранён. Следующий инженерный шаг — закрыть ранее оставшуюся [проверку Linux deployment](./embedding-deployment-settings.md), остановленную из-за Docker ENOSPC: повторить передачу transport/deadline/idle settings через настоящий worker image и проверить его lifecycle. Большие реальные коллекции, длительные нагрузки, независимые бизнес-источники и production SLO остаются отдельными задачами исходного плана.
