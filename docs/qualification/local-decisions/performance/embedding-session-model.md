# Постоянный helper на настоящей embeddinggemma

27.09.2026. Продолжение [прототипа с request ownership](./embedding-session-prototype.md) и [исходного профиля на модели](./embedding-model-transport.md). Проверяется только замена транспорта внутри экспериментального harness; обычный worker и модель не меняются.

## План и воспроизводимость

[Harness](../../../../scripts/profile-embedding-session-model.py), [verifier](../../../../scripts/verify-embedding-session-model.py) и зависимости закреплены commit **`65cc36d`** до опыта. [Plan](./evidence/2026-09-27/embedding-session-model/plan.json) сохраняет полный commit, SHA исходников, восемь synthetic Russian input cases, порядок и численные пороги. Endpoint — только `127.0.0.1:11434/v1`, уже установленная **embeddinggemma:latest**, digest `85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1`, Ollama **0.34.2**, 768 компонентов. Веса не скачивались, версия и digest до/после совпали.

Используется прежний `LocalModelClient` и [OpenAI-compatible embedding API Ollama](https://docs.ollama.com/api/openai-compatibility). В session-режиме заменяется только transport function на сессию конкретного actor; обработка JSON, индексов и векторов остаётся обычной. Deadline обеих групп одинаков: 30 секунд. Все ответы получены задолго до него.

Batch **1/32**, concurrency **1/4**, **isolated → session → session → isolated**, восемь вызовов в блоке. Каждый из четырёх случаев batch повторяется дважды в блоке. Один actor последовательно выполняет свои запросы; у каждого session-actor собственный helper. Всего **128 измеряемых вызовов** и **4 отдельных model warmup**. Session readiness вынесена за интервалы model calls и сохранена отдельно — **22 запуска**, p50 **62,329 мс**, max **67,752 мс**. Каждая измеряемая сессия используется восемь раз при concurrency 1 или дважды при concurrency 4; она закрывается после блока.

Перед опытом модель не была загружена (`/api/ps` пуст). Первый model warmup занял **1419,153 мс**; остальные три — **30,542 / 491,158 / 413,531 мс**. Эти четыре значения исключены из таблицы. Полный опыт — **33,009 с**, macOS arm64 / Python 3.14.3.

## Результат

| Batch | Concurrency | Isolated p50, мс | Session p50, мс | Снижение p50 | Isolated max, мс | Session max, мс |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 105.637 | 17.603 | 83.3% | 126.161 | 42.407 |
| 1 | 4 | 116.414 | 46.551 | 60.0% | 156.452 | 89.571 |
| 32 | 1 | 467.156 | 389.170 | 16.7% | 500.438 | 625.156 |
| 32 | 4 | 1254.025 | 1213.330 | 3.2% | 1556.780 | 1505.298 |

Для каждого поля p50 — 16 наблюдений; nearest-rank p95 равен max. У session **batch 32 / concurrency 1 хвост хуже**, несмотря на меньшую медиану. ABBA уменьшает простое смешение порядка, но не устраняет влияние соседней нагрузки, allocator, model scheduling или прогрева. Этот опыт подтверждает уменьшение наблюдаемой медианы; универсальное ускорение и production SLO не установлены. Фактическое перекрытие HTTP достигло 1/4 во всех фазах. [Очередь Ollama](https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests) и GPU execution этим счётчиком не измеряются.

Все **132** ответа совпали с соответствующим reference по **SHA полного канонического JSON векторов**. Максимальные component difference и cosine distance — **0** при заранее заданных границах 1e-6 / 1e-10. Это восемь разных учебных input cases с повторами, а не 132 независимых проверки качества retrieval. Все векторы сохранены в **8 gzip blobs, 486086 байт**; хешируются сжатый файл и распакованные данные. Компрессия и запись выполняются после фазы, вне измеряемых вызовов.

Все **88 helper** (66 одноразовых + 22 сессии) завершились с кодом 0 и закрытыми pipes. Verifier подтверждает принадлежность PID каждому actor/request, readiness до первого вызова, отсутствие перекрытия запросов одного actor и возврат числа FD после закрытия. Максимальная сумма **idle RSS четырёх helper после запросов — 118,594 МиБ**; максимум parent — **68,375 МиБ**, включая сохранённые vectors и harness. Это снимки между фазами, не peak memory процесса или GPU и не длительная проверка утечек.

Полные [result](./evidence/2026-09-27/embedding-session-model/result.json), [replay](./evidence/2026-09-27/embedding-session-model/replay.json) и сохранённые vectors позволяют повторно проверить вычисления без запуска модели. **12 offline-тестов** отклоняют неполный опыт, смену endpoint/model/версии, ослабление tolerances, подмену входа/исходников, повреждение gzip/decoded SHA, пересчитанный корректный vector blob с изменённым компонентом, чужого владельца, поздний readiness, утечку FD/pipes и необъяснённый helper RSS.

```sh
python3 scripts/profile-embedding-session-model.py --output docs/qualification/local-decisions/performance/evidence/<date>/<new-directory>
python3 scripts/verify-embedding-session-model.py docs/qualification/local-decisions/performance/evidence/2026-09-27/embedding-session-model
python3 -m unittest discover -s scripts/test -p test_embedding_session_model_verifier.py -v
npm run docs:check
```

[Журнал проверок и SHA всех evidence-файлов](./evidence/2026-09-27/embedding-session-model/checks.json): 12/12 целевых verifier-тестов, 12 Node + 223 Python в общем docs suite, architecture audit — PASS без ошибок и предупреждений.

Следующий gate — длительная повторная работа тех же сессий с контролируемыми отменами/deadlines, заменой helper и сериями RSS/FD. Затем потребуется явное владение transport в worker, ограничение числа живых процессов, проверка его входного бюджета и настоящего lease/recovery-пути. До этого новый режим остаётся экспериментом; model confidence, shadow/routing и defaults не меняются.
