# RAG на корпусе документации: память PostgreSQL-моста

27.09.2026 (MSK). Поиск по **66 документам, 2429 фрагментам и реальным
768-мерным embeddings** выявил избыточные аллокации в PostgreSQL-мосте.
После повторного использования response buffer наблюдаемый пик RSS процесса
снизился с **3,21 ГиБ до 540 МиБ**. Во всех поисках сохранены topK и provenance.

## Корпус и метод

Источник — все верхнеуровневые `docs/*.md` публичного Agat на commit
`44dbb436f86d8406b8b5713e3e1b66faa7f8a28a`. Вложенные evidence, review и holdout
в корпус не входят. Тексты нормализованы штатным ingestion; chunk size 400,
overlap 40. Синтетические копии для увеличения объёма не добавлялись.

Использован установленный `embeddinggemma:latest`, digest
`85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1`, Ollama 0.34.2.
Собственный сервер на loopback работает с `OLLAMA_NO_CLOUD=1`; новые веса не
скачиваются. Вызовы [Ollama embed](https://docs.ollama.com/api/embed) используют
`truncate: false`, context 2048 и batch 16. Входы — обычный текст по текущему
контракту Agat, без дополнительного retrieval prompt.

План и SHA исходников записаны **до** embeddings. Индексация каждого backend
проходит через `ingestKnowledgeDocument`, embedding leases и completion.
Сервер модели выгружен и остановлен до измерения. Для SQLite и PostgreSQL
создаётся отдельный свежий Node-процесс поиска: он не читает корпус или полный
vector cache. Измеряются вызовы `AgatStore.searchKnowledge`, включая SQL,
передачу, cosine/topK и запись retrieval event. HTTP, worker scheduling,
embedding inference и генерация ответа в задержку не входят.

Для каждого backend зафиксированы восемь запросов, один первый проход и четыре
повтора: 40 поисков, из них 32 warm samples. Порядок — SQLite, затем PostgreSQL.
Первый проход не является cold filesystem cache. Использованы Apple M1 Max,
32 ГиБ, локальный PostgreSQL 17.6 в Docker и один вызывающий поток.

## Проверка результата

Независимый [Python oracle](../../../../scripts/lib/rag_corpus.py) считает cosine
через `math.fsum` и полную сортировку всех кандидатов. Он проверяет topK=8,
точность score до округления ответа, порядок, отсутствие дублей и SHA документа
и фрагмента. Равные score на границе с допуском `1e-12` взаимозаменяемы, но все
строго лучшие кандидаты обязательны. Дополнительно проверяются одно retrieval
event и новые маркеры K1–K8 на каждом отдельном run.

[Сравнение двух прогонов](./evidence/2026-09-27/retrieval-buffer-comparison.json)
подтвердило одинаковые SHA корпуса, весов и настроек, все **2429 source vectors**,
восемь query vectors и **80 списков hits**. Все исходные evidence SHA сверены,
код сопоставлен с commit до изменения `38acba58f82ea77d3398e3f432a84cd48b2c67fe`
и после `29c98262602065ea62effef5206b8a96514d3799`.

Общий hash описания `/api/show` между процессами отличается. Сырые описания не
сохранены, поэтому причина различия не установлена. Зафиксированные поля модели,
digest весов, параметры и **все фактически использованные векторы** совпадают;
различие hash не скрыто в сравнительном свидетельстве.

## Изменение и измерения

Раньше каждый SQL-вызов создавал новый `SharedArrayBuffer` на 32 МиБ.
Порционное чтение правильно ограничивало каждый ответ, но много SQL/FETCH
создавали значительную нагрузку на выделение и освобождение памяти.

[Response buffer](../../../../apps/coordinator/src/postgres-response-buffer.ts)
теперь принадлежит экземпляру `PostgresDatabaseSync` и используется повторно:
синхронный интерфейс допускает только один незавершённый вызов на этот экземпляр.
JSON копируется до следующего запроса; header/length сбрасываются. Ожидание
повторно проверяет флаг готовности: по
[контракту Atomics.wait](https://tc39.es/ecma262/multipage/structured-data.html#sec-atomics.wait)
уведомление само по себе не гарантирует изменение значения. После timeout
база и worker закрываются, поэтому поздний writer не получает следующий запрос.
Лимит размера ответа, ошибки SQL и RLS сохранены.

| Backend / состояние | Warm p50, мс | Warm p95, мс | Пик RSS процесса, МиБ | Проверено поисков |
|---|---:|---:|---:|---:|
| SQLite до | 104.800 | 107.617 | 199.6 | 40/40 |
| PostgreSQL до | 530.932 | 664.193 | 3285.4 | 40/40 |
| SQLite после | 104.268 | 108.487 | 200.8 | 40/40 |
| PostgreSQL после | 423.336 | 469.494 | 539.9 | 40/40 |

Исходные результаты:
[до](./evidence/2026-09-26/retrieval-corpus/verification.json),
[после](./evidence/2026-09-27/retrieval-corpus-buffer/verification.json).
RSS — накопленный максимум свежего Node-процесса, включая его worker threads
и startup; `process.resourceUsage().maxRSS` переводится из
[KiB по контракту Node](https://nodejs.org/api/process.html#processresourceusage).
Это не память PostgreSQL server, Docker VM, Python oracle или уже выгруженной
модели. 540 МиБ — наблюдение данного запуска, не новый гарантированный RSS-limit.

Три проверки межпоточного протокола покрывают 100 больших/малых Unicode-ответов
на одном buffer, сохранность прежних результатов, раннее уведомление без ready,
уже готовый ответ, timeout и повреждённую длину. Настоящий PostgreSQL дополнительно
проверяет oversized response, восстановление после SQL error, курсоры, rollback
и RLS. Fleet/HA — **8/8**, coordinator — **227/227**, typecheck/build — pass.
Документационные проверки: 12 Node и 140 Python — pass, включая пять тестов oracle.
[Логи проверок и их SHA](./evidence/2026-09-27/retrieval-buffer-tests/checks.json)
сохранены отдельно от измерительного опыта.

## Воспроизведение и ограничения

Из корня репозитория, с установленным закреплённым embeddinggemma и Docker:

```bash
python3 scripts/run-rag-corpus.py \
  --source-ref 44dbb436f86d8406b8b5713e3e1b66faa7f8a28a \
  --evidence-dir docs/qualification/local-decisions/performance/evidence/new-corpus-run
```

Launcher создаёт собственные временные SQLite, Ollama и PostgreSQL, ограничивает
каждую фазу 600 секундами, сохраняет evidence под `/docs` и удаляет собственный
контейнер и его данные. Отдельно подтверждено отсутствие всех 14 зафиксированных
child PID двух запусков и контейнеров. Полный vector cache временный; его SHA
и hash каждого вектора сохранены, воспроизведение требует пересчёта embeddings.

Это одна последовательная пара before/after на технической документации.
Randomized crossover, конкурентный поток, production SLO, независимое качество
ответов, бизнес-источники и throughput здесь не квалифицируются. Поддерживаемый
предел остаётся 5000 кандидатов; индекс и коллекции большего размера — следующий
отдельный этап.
