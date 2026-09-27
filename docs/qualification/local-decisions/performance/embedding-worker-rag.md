# Session transport в полном RAG workload обычного worker

Дата: 2026-09-27. Статус: инженерный эксперимент завершён; предметная qualification не заявляется.

После [session opt-in](./embedding-worker-session-opt-in.md) проверен полный путь: индексация двух документов → embedding запроса → поиск с provenance → три этапа primary → сохранение результатов → drain worker. Оба режима используют обычный `worker_loop`, один runtime и одинаковые модели. Рабочие deployments и default `isolated` не переключены.

## Зафиксированные условия

- Измеряемый commit: `772127e43ddc2c76f3c9586980d1301d726cb04b`; SHA-256 всех **74 файлов** coordinator/worker, harness, fixture, profile и зависимостей находятся в [plan.json](./evidence/2026-09-27/embedding-worker-rag/plan.json). Независимый verifier читает именно этот Git snapshot.
- Apple M1 Max, 32 ГиБ unified memory; macOS arm64, Python 3.14.3, Node 24.14.0, Ollama 0.34.2.
- Qwen3 8B Q4_K_M: `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`; embeddinggemma BF16, 768 dimensions: `85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1`.
- Четыре блока ABBA: `isolated`, `session`, `session`, `isolated`; в каждом три одинаковых процесса с этапами extract/analyze/review. Worker и coordinator concurrency = 2. Model-visible metadata одинаковы во всех блоках.
- Два [учебных документа](./rag-workflow.fixture.json), по одному chunk; 2 source embeddings + 9 query embeddings на блок. Никакие бизнес-системы не подключены.
- Собственный временный Ollama на loopback, cloud выключен; максимум две загруженные модели, `OLLAMA_NUM_PARALLEL=1`. Primary: `think=false`, temperature 0,2, seed 0, context 8192, output budget 384; embeddings: context 2048, `truncate=false`. Наложение HTTP включает очередь Ollama, это не измерение параллельных GPU kernels.
- Shadow выключен во всех блоках, вызовов decision runtime — 0. Сохранённый профиль нужен только для нормализации существующего harness; его использование здесь не является повторной qualification MLX-модели.
- Прогрев: 1 primary-вызов и 1 embedding-вызов с двумя источниками, отдельно от измеряемых 36 + 44 запросов. Из пустого `/api/ps` прогрев занял **30,416 с**, в том числе primary 7,582 с. Helper процессов заранее не было: их lazy startup входит в измерения worker.

Порядок ABBA уменьшает зависимость сравнения от единственного первого блока, но не устраняет прогрев, системную нагрузку и изменение состояния GPU. Это один описательный прогон, не рандомизированная оценка причинного эффекта.

## Результаты

Все **12 процессов / 36 primary-этапов / 44 embedding-запроса** завершились. Независимая проверка сопоставляет source SHA, HTTP-входы, query vector SHA в retrieval, сохранённые output SHA, ссылки на источники и владельцев helper. Для пяти различных embedding-входов в прогреве и workload vector SHA совпали. Во всех блоках совпали полные мультимножества primary prompt SHA и output SHA, а также token counts: 7602 input / 1032 output на блок.

Все 36 этапов получили оба полных документа и сослались на них; неизвестных citation markers нет. Сохранённый учебный ответ содержит правильные 100 → 120 заявок (+20%), 4 → 3 ч/заявку (−25%), 8% → 5% отклонений (−3 п.п.). Один повторяемый учебный пример не оценивает обобщение или предметную точность.

| Блок | Полная фаза, с | Индексация, мс | Embedding worker p50 / max, мс | Primary HTTP p50, мс | Helpers |
|---|---:|---:|---:|---:|---:|
| 0 isolated | 34,123 | 1418,185 | 159,153 / 259,697 | 6858,699 | 11 |
| 1 session | 27,542 | 417,827 | 69,457 / 142,504 | 5571,640 | 2 |
| 2 session | 28,178 | 1026,755 | 69,680 / 624,895 | 5514,455 | 2 |
| 3 isolated | 27,276 | 416,974 | 159,561 / 201,546 | 5160,660 | 11 |

В каждом блоке n=11 embedding и n=9 primary; nearest-rank p95 равен max. Медиана embedding уменьшилась примерно на 90 мс, но **последний isolated-блок быстрее обоих session-блоков по полной фазе**. Утверждать ускорение всего RAG-процесса на основании этого прогона нельзя.

Хвост 624,895 мс во втором session-блоке пришёлся на начальную индексацию. Соответствующий proxy HTTP занял 398,051 мс, native Ollama — 393,661 мс (load 11,972 мс). Время worker также включает startup и transport; источник остальных задержек отдельно не инструментирован. Session не устраняет очередь и вариативность backend.

Полный Node workload с прогревом — 148,383 с; launcher с запуском и cleanup — 150,308 с. Подробные времена и сохранённые ответы находятся в [workflow.json](./evidence/2026-09-27/embedding-worker-rag/workflow.json); сводки независимо восстановлены в [replay.json](./evidence/2026-09-27/embedding-worker-rag/replay.json).

## Ресурсы и завершение

Наблюдатель `sys.setprofile`/`threading.setprofile` фиксирует реальные `LocalModelClient.embed`, `Popen`, exchange и retire. Он не заменяет model transport и не отвечает вместо модели. Отдельный поток запрашивает `ps` с периодом ожидания 100 мс; накладные расходы наблюдателя включены в обе стороны сравнения. Python описывает profiler как механизм вызовов/возвратов и отдельно оговаривает его привязку к потоку: [sys.setprofile](https://docs.python.org/3/library/sys.html#sys.setprofile).

| Блок | Worker RSS p50 / max, МиБ | Сумма helper RSS p50 / max, МиБ | Снимки |
|---|---:|---:|---:|
| 0 isolated | 24,641 / 33,859 | 0 / 54,719 | 285 |
| 1 session | 24,453 / 34,094 | 30,375 / 58,000 | 227 |
| 2 session | 29,031 / 33,953 | 47,797 / 58,422 | 231 |
| 3 isolated | 27,844 / 33,828 | 0 / 54,922 | 236 |

Это **выборочные RSS**, не точные пики и не общий объём уникальных физических страниц. Короткоживущие isolated helpers могут полностью попасть между снимками; нулевая медиана не означает отсутствия памяти во время запроса. Для session сохраняются два процесса между запросами. Один снимок при завершении содержит RSS=0 уже выходящего helper; он сохранён без исправления исходных данных. Verifier допускает ограниченное окно гонки `ps` с завершением процесса и проверяет PID по фактическому inventory.

Все **26 helpers** завершились с кодом 0 и закрытыми stdin/stdout. У каждого запроса ровно один наблюдаемый владелец; session PID не используется одновременно двумя запросами, pool не превышает два helper. Все четыре worker закрылись: FD **4 → 4**, активных embed-вызовов и фоновых потоков не осталось. Журналы [isolated-0](./evidence/2026-09-27/embedding-worker-rag/0_isolated.worker.json), [session-1](./evidence/2026-09-27/embedding-worker-rag/1_session.worker.json), [session-2](./evidence/2026-09-27/embedding-worker-rag/2_session.worker.json), [isolated-3](./evidence/2026-09-27/embedding-worker-rag/3_isolated.worker.json) содержат все вызовы и снимки.

Собственные модели выгружены, `/api/ps` пуст, Node и Ollama завершились с кодом 0; наблюдавшиеся собственные PID отсутствуют после cleanup. [Launcher evidence](./evidence/2026-09-27/embedding-worker-rag/launcher.json) содержит настройки и 15 снимков модели. Последний снимок Ollama сообщал 6 177 989 590 + 673 028 505 байт `size_vram`; это заявленные allocations моделей, не измерение общего пика RAM/VRAM. Значение настроек concurrency и residency сверено с [официальным FAQ Ollama](https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests).

## Проверка и воспроизведение

```bash
# Нужны заранее установленные модели с указанными digest, Node 24 и чистые
# измеряемые исходники, сохранённые в commit. Output должен быть новым.
python3 scripts/profile-embedding-rag.py \
  --evidence-dir docs/qualification/local-decisions/performance/evidence/local/embedding-worker-rag
python3 scripts/verify-embedding-rag.py \
  docs/qualification/local-decisions/performance/evidence/local/embedding-worker-rag
python3 -m unittest discover -s scripts/test -p 'test_embedding_rag_verifier.py' -v
```

13 mutation/replay tests проверяют подмену snapshot, fixture/weights/order, неполную фазу, shadow, HTTP-повторы, изменённые prompts/outputs, query/source/citation binding, drift векторов, перепутанного владельца, ранний retire, потерянный helper, FD/thread leaks, посторонний RSS PID и неполный cleanup сервера. Результаты дополнительных проверок и SHA исходников/логов находятся в [checks.json](./evidence/2026-09-27/embedding-worker-rag/checks.json).

Решение: сохранить `session` как явный opt-in для нагрузок, где измеренная цена запуска helper существенна и есть бюджет постоянной памяти. Default остаётся `isolated`. Следующий инженерный gate — остановка обычного worker во время активного embedding: drain по SIGTERM, окончательное закрытие helper и сохранение lease/result при возобновлении работы. Отдельно остаются предметная qualification, Windows и production SLO.
