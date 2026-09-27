# Изолированный транспорт на настоящей embeddinggemma

Дата: **27.09.2026**. Продолжение [loopback-профиля транспортных расходов](./embedding-transport-profile.md). Runtime и настройки модели не меняются.

## Закреплённый опыт

Harness и verifier сохранены commit `fc51ea7` до первого запроса. [Plan](./evidence/2026-09-27/embedding-model-transport/plan.json) фиксирует исходники, порядок ABBA, восемь наборов synthetic inputs и численные пороги до наблюдений. Вызывается стандартный [OpenAI-compatible `/v1/embeddings` Ollama](https://docs.ollama.com/api/openai-compatibility) через существующий `LocalModelClient`; direct client берётся из `f9a16ea`, isolated использует реализацию общего deadline.

Использована уже установленная локальная **embeddinggemma:latest**, digest `85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1`, Ollama **0.34.2**, 768 компонентов. Версия и digest совпали до/после. Endpoint жёстко ограничен `127.0.0.1:11434`, модели не скачивались, source text — короткие сгенерированные русские учебные документы. В начале `/api/ps` был пуст; после модель присутствовала с `size_vram=673028505` байт. Это снимок residency, не измерение GPU kernels или peak memory.

Для batch **1/32** и concurrency **1/4** заранее задано **direct → isolated → isolated → direct**, по четыре вызова в блоке. На каждую сравниваемую группу — восемь наблюдений. Четыре отдельных warmup-вызова исключены; первый direct прогрел незагруженную модель за **1360,894 мс**. Полный опыт — **68 успешных вызовов** (64 измеряемых + 4 warmup), **19,867 с**, macOS arm64 / Python 3.14.3.

Каждый вызов сохраняет request SHA, времена и полный результат. Векторы дедуплицируются по SHA канонического JSON и сохраняются gzip без времени в заголовке. Все восемь blobs занимают **486086 байт**; verifier проверяет SHA и сжатого файла, и распакованных данных. Запись/сжатие происходят после блока, вне всех измеряемых вызовов.

## Задержки

| Batch | Concurrency | Direct p50, мс | Isolated p50, мс | Разница p50, мс | Относительно direct |
|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 16.014 | 114.252 | 98.238 | 613.5% |
| 1 | 4 | 29.816 | 135.951 | 106.135 | 356.0% |
| 32 | 1 | 472.846 | 526.631 | 53.785 | 11.4% |
| 32 | 4 | 984.714 | 1135.491 | 150.777 | 15.3% |

На одиночном запросе добавленная задержка **98–106 мс** существенно превышает быстрый warm inference. На batch 32 разница в этом опыте — **54–151 мс**, **11,4–15,3%** относительно direct p50. Это разности наблюдаемых распределений, не точное разложение CPU/GPU/network overhead: очередь Ollama, прогрев и нагрузка машины тоже влияют. При concurrency 4 измеряется перекрытие HTTP, а не параллельность GPU. При n=8 nearest-rank p95 равен максимуму; данные не устанавливают production SLO.

[Независимый replay](./evidence/2026-09-27/embedding-model-transport/replay.json) сохраняет min/p50/p95/max, отдельные warmup и фактическую конкурентность. Полные [сырые результаты](./evidence/2026-09-27/embedding-model-transport/result.json) не заменены сводными средними.

## Результат и дальнейшее решение

Все **68** результатов имеют **точно одинаковый SHA канонического JSON с соответствующим reference case**. Максимальные покомпонентная разница и cosine distance — **0**; заранее заданные границы 1e-6 / 1e-10 не менялись. Все 34 transport subprocess завершились с кодом 0 и закрытыми pipes. Это подтверждает сохранение векторов транспортом на восьми учебных наборах; качество поиска, предметная точность и независимые заявки здесь не оценивались.

Десять offline-тестов verifier проверяют план и warmup, endpoint/model fingerprint, historical source SHA, полные vectors, пределы отклонений, исходы subprocess и фактическое перекрытие. В том числе новая корректная gzip-последовательность с пересчитанными SHA и изменённым компонентом только isolated-ответа отклоняется сравнением с reference.

```sh
python3 scripts/profile-embedding-model-transport.py --output docs/qualification/local-decisions/performance/evidence/<date>/<new-directory>
python3 scripts/verify-embedding-model-transport.py docs/qualification/local-decisions/performance/evidence/2026-09-27/embedding-model-transport
python3 -m unittest discover -s scripts/test -p test_embedding_model_transport_verifier.py -v
npm run docs:check
```

Проверки: **10/10 verifier-тестов**, **12 Node + 186 Python** в docs suite, **1567** локальных ссылок, **189** Markdown-файлов и **13** шаблонов; architecture audit — **PASS**, без ошибок и предупреждений. Фактическая конкурентность достигла заданных 1/4 во всех фазах.

Защита deadline/cancellation сохраняется. Измерения дают основание исследовать заранее запущенный helper для частых query embeddings: в этом профиле цена нового процесса больше самого одиночного inference. Следующий gate — ограниченный прототип с измерением latency и проверкой владения каждым запросом, отмены без воздействия на соседа, замены процесса после отказа и cleanup. Перенос в обычный worker и изменение defaults требуют этих проверок; текущий этап меняет только инструменты измерения и evidence.
