# Модельный burst → idle → burst

Измерение: 27.09.2026; replay и итоговая проверка: 28.09.2026. Дизайн зафиксирован до измерения на commit `577b11e9d80263120f196e6cfacdf21068f05182`. Следующий этап после [idle retirement prototype](./embedding-idle-prototype.md).

Сравниваются production `EmbeddingSessionPool` (`keep`) и `IdleEmbeddingSessionPool` из `scripts/lib` (`retire`), оба используют текущий guarded helper. Модель `embeddinggemma:latest`, digest `85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1`, dimensions 768. Отдельный owned Ollama, cloud выключен, parallel 1, context 2048, keep-alive 5 минут. Model server не выгружается между фазами: проверяется стоимость helper, а не повторная загрузка весов.

Матрица batch 1/32 × concurrency 1/4, порядок keep/retire/retire/keep. Каждая фаза: восемь embedding-вызовов, четыре секунды простоя, ещё восемь вызовов. Idle timeout прототипа — три секунды, только для qualification. Перед матрицей два отдельных прогревочных вызова batch 1/32. Всего 258 model-вызовов, из них 256 измеряемых; ожидается 62 helper, включая два одноразовых warmup helper.

Первые запросы каждого actor после idle анализируются отдельно от последующих: медиана смешанного burst могла бы скрыть startup. Startup входит в deadline и клиентскую latency. Дополнительно фиксируются реальные границы transport request, чтобы проверять отсутствие перекрытия владения одним helper. Общая `LocalModelClient.embed` latency также содержит декодирование/валидацию vectors после возврата слота, поэтому её интервалы не используются как границы владения transport.

Одинаковые восемь наборов входов, закреплённые source hashes, полные сжатые vectors, input hashes, dimensions/finite checks, component tolerance 1e-6 и cosine tolerance 1e-10. Hashing/compression и запись файлов выполняются вне всех timed calls. До/после idle и после close фиксируются PID, reap/pipes, FD и RSS. Ни одного живого helper после idle у retire; keep должен повторно использовать прежние PID. Каждый PID принадлежит одному запросу transport в момент времени.

Эксперимент не оценивает качество модели, производственный timeout или SLO. Короткий ABBA на одном Mac не устраняет все эффекты очереди, CPU/теплового режима и модельной нагрузки. Production worker defaults не меняются.

## Отказы после повторного запуска

Отдельные lifecycle-тесты используют контролируемый HTTP-body: после подтверждённого idle reap следующий helper получает deadline либо cancellation, закрывает все pipes до ответа endpoint, затем третья явная попытка успешно обрабатывается новым helper. Это реальный transport/process тест, но модельный ответ в этих двух fault-сценариях является fixture. Сам benchmark измеряет успешные ответы настоящей модели.

## Воспроизведение

После commit измеряемых исходников:

```sh
python3 scripts/profile-embedding-idle-model.py --output docs/qualification/local-decisions/performance/evidence/2026-09-27/embedding-idle-model
python3 scripts/verify-embedding-idle-model.py docs/qualification/local-decisions/performance/evidence/2026-09-27/embedding-idle-model
python3 -m unittest discover -s scripts/test -p test_embedding_idle_pool.py -v
```

Повторный experiment требует нового каталога. После измерения owned model явно выгружается, сервер/runner останавливаются, отсутствие оставшихся PID проверяется. Стандартный локальный порт Ollama пользователя не используется.

## Наблюдение и решение

На Apple M1 Max / 32 GiB, Python 3.14.3 и Ollama 0.34.2 прогон занял 125,397 с. [Закреплённый план](./evidence/2026-09-27/embedding-idle-model/plan.json), [сырые наблюдения](./evidence/2026-09-27/embedding-idle-model/result.json) и [независимый replay](./evidence/2026-09-27/embedding-idle-model/replay.json) сохранены вместе с восемью полными сжатыми vector blobs. Все **258/258** ответов совпали точно; максимальные component difference и cosine distance — 0. Созданы и закрыты 62 helper: 60 session и два isolated warmup. После unload/stop не осталось owned PID; модель выгружена из отдельного сервера.

В восьми фазах `retire` после idle сумма helper RSS равна нулю: все прежние PID reaped, pipes закрыты, FD возвращены к исходному числу. Второй burst создаёт новые PID. У `keep` после idle остаётся 29,141–29,969 MiB при одном слоте и 116,672–120,359 MiB при четырёх; второй burst использует те же PID. Это сумма RSS helper, а не уникальная физическая память системы и не память model server.

Клиентская latency второго burst, миллисекунды. `n` — число наблюдений **на каждый режим**, p50 — nearest rank; первые запросы actor вынесены отдельно от последующих.

| Batch | Concurrency | Позиция после idle | n | Keep p50 | Keep max | Retire p50 | Retire max |
|---:|---:|---|---:|---:|---:|---:|---:|
| 1 | 1 | Первый запрос | 2 | 27,629 | 28,425 | 111,083 | 119,649 |
| 1 | 1 | Последующие | 14 | 15,564 | 22,324 | 14,734 | 32,058 |
| 1 | 4 | Первый запрос | 8 | 52,203 | 91,875 | 119,883 | 150,381 |
| 1 | 4 | Последующие | 8 | 46,826 | 72,354 | 47,750 | 124,855 |
| 32 | 1 | Первый запрос | 2 | 432,091 | 544,561 | 485,944 | 522,249 |
| 32 | 1 | Последующие | 14 | 395,146 | 569,990 | 391,968 | 447,744 |
| 32 | 4 | Первый запрос | 8 | 876,114 | 1658,143 | 943,162 | 1729,235 |
| 32 | 4 | Последующие | 8 | 1632,410 | 1899,336 | 1652,182 | 2004,133 |

Наблюдаемая разница p50 первого запроса — 53,853–83,454 мс. При `n=2` nearest-rank p50 является меньшим из двух значений; это описание опыта, не устойчивая оценка популяции. В latency входят очередь model server и колебания нагрузки, поэтому разница не объявляется чистой причинной стоимостью startup. Последующие тёплые вызовы не показывают единого улучшения, часть максимумов ухудшилась.

Выбран следующий инженерный шаг: явный опциональный idle timeout для `session` обычного worker, выключенный по умолчанию. Постоянное удержание остаётся разумным для непрерывной нагрузки; освобождение подходит для редких burst при допустимой цене первого запроса. Автоматическое включение трёхсекундного timeout отклонено: экспериментальное значение не является производственным SLO. Возврат к прежнему поведению должен выполняться отключением настройки. Следующий gate — production config, обычный worker/PostgreSQL lease path, повторный helper после idle и отсутствие stale result/replay; текущий этап production transport не меняет.

## Независимая перепроверка

[Verifier](../../../../scripts/verify-embedding-idle-model.py) заново проверяет исходники из закреплённого commit, identity модели, ABBA/idle интервалы, все входы и полные vectors, создание PID внутри фактического вызова, отсутствие перекрытия transport на одном helper, reap/pipes/FD и cleanup owned server. Сводка latency пересчитывается из монотонных timestamps.

[11 mutation-тестов](../../../../scripts/test/test_embedding_idle_model_verifier.py) проверяют исходный replay и отказ при подмене модели, источников, input/vector data, интервала, владельца, cleanup и maintenance. Дополнительная мутация после измерения выявила пропущенную проверку конечности нормы: конечные компоненты `1e200` переполняли сумму квадратов. [Сохранён исходный отказ теста](./evidence/2026-09-27/embedding-idle-model/verifier-baseline.log), verifier усилен и все 11 тестов проходят. Измерительный producer, production transport, пороги и сырые данные не менялись; повторный replay исходных данных побайтно совпал. `plan.json` сохраняет исходный hash verifier, а итоговый hash проверенного verifier записан в [checks.json](./evidence/2026-09-27/embedding-idle-model/checks.json).

Instrumentation smoke прошёл четыре сочетания `keep/retire × concurrency 1/4` в одном тесте с реальным subprocess/HTTP. Lifecycle suite после добавления отмены/deadline после idle: macOS **14/14** (6,461 с), Linux **14/14** (6,860 с). Linux использует собранный image `sha256:927050befe221bf4b1b3c47ddffb494cc540b313acf3452dac24de5b0b7fc619`; runtime-модули `/opt/agat` побайтно совпали с измеряемыми workers. Модельный benchmark выполнен только на macOS; Linux-проверки покрывают процессный transport с HTTP fixture.

Обоснование разделения срока хранения и request timeout: [HTTPX resource limits](https://www.python-httpx.org/advanced/resource-limits/). Ограниченное и неблокирующее владение основано на [Python Lock.acquire](https://docs.python.org/3/library/threading.html#threading.Lock.acquire). Эти источники описывают механизм; выбор настройки подтверждается измерениями проекта, значения timeout из сторонней библиотеки не перенесены.

Полный `docs:check`: 12 Node + 303 Python tests, 1663 локальные ссылки в 200 Markdown-файлах, 13 process templates. Architecture audit плана: 0 ошибок, 0 предупреждений.
