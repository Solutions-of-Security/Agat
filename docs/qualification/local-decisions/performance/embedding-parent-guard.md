# Parent guard: стоимость и независимые владельцы

Дата протокола: 27.09.2026. Независимый replay подтвердил 264 ответа с побайтно одинаковыми vectors и завершение всех 176 helpers. Исходники и дизайн закреплены до измерения в `b3ffcbaacdef0aab42d2d365dbe29e206bd8cf7a`. Этот этап продолжает [SIGKILL recovery](./embedding-parent-exit.md).

## Зафиксированный дизайн

Сравниваются штатные isolated и session transport до и после Unix parent guard. Baseline — `218ecb5f6b70289df0c89ebbc7275de57a202684`; два старых transport-модуля извлекаются из Git во временный каталог. Общие `agat_worker.py`, telemetry, web tools и local decisions должны побайтно совпадать. Нового флага отключения защиты в worker нет.

Модель `embeddinggemma:latest`, digest `85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1`, dimension 768; установленные локальные веса, без скачивания. Отдельный Ollama на случайном loopback-порту, cloud выключен, одна загруженная модель и один parallel request, context 2048. После эксперимента модель выгружается, собственная группа процессов останавливается и проверяется отсутствие оставшихся PID.

Матрица batch 1/32 × concurrency 1/4 × isolated/session. Для каждой ячейки порядок before/after/after/before, восемь вызовов в блоке; перед матрицей восемь прогревочных вызовов. Всего 264 запроса, из них 256 измеряемых, 176 helper и 44 отдельные readiness-операции. Четыре варианта текста на каждый batch совпадают с [прежним парным профилем](./embedding-session-model.md). Полные vectors сохраняются вне интервалов запросов и проверяются независимо: размерность, конечность, порядок, input hash, component delta ≤ 1e-6, cosine distance ≤ 1e-10. Startup disposable входит в вызов; session readiness измеряется отдельно.

Регистрируются фактические PID, аргументы guard, hash исполняемого helper, владелец каждого запроса, закрытие pipes и returncode. RSS/FD снимаются до readiness, после readiness, после вызовов и после close; это snapshots, а не peak memory. Измерение не оценивает idle CPU и энергопотребление polling thread. Малое число повторов и один host не позволяют объявлять production SLO или статистически значимую цену guard.

## Независимость владельцев

`workers/test_embedding_parent_exit.py` запускает два настоящих процесса с `LocalModelClient` и два удержанных chunked HTTP-ответа. Все четыре сочетания isolated/session проверяются отдельно: SIGKILL первого владельца закрывает только его запрос; второй parent/helper остаются живы и после разрешения ответа возвращают точный `[1, 0]`, завершаясь с кодом 0 и reaping собственного helper. Проверка проводится на host и в Linux worker image с побайтно проверенными runtime-модулями.

## Наблюдения

Apple M1 Max, arm64, 32 GiB, Python 3.14.3, Ollama 0.34.2. Весь эксперимент занял 70,961 с. Восемь прогревочных вызовов отделены от таблицы; каждая клетка before/after содержит по 16 измерений. Полные данные: [план](./evidence/2026-09-27/embedding-parent-guard/plan.json), [наблюдения](./evidence/2026-09-27/embedding-parent-guard/result.json), [replay](./evidence/2026-09-27/embedding-parent-guard/replay.json).

| Batch | Concurrency | Transport | p50 до, мс | p50 после, мс | Разница, мс | max до → после, мс |
|---:|---:|---|---:|---:|---:|---:|
| 1 | 1 | isolated | 100.150 | 99.796 | -0.354 | 121.802 → 132.345 |
| 1 | 1 | session | 16.232 | 16.335 | +0.103 | 30.546 → 33.168 |
| 1 | 4 | isolated | 115.789 | 111.075 | -4.714 | 153.805 → 135.584 |
| 1 | 4 | session | 43.774 | 41.402 | -2.372 | 65.562 → 62.428 |
| 32 | 1 | isolated | 466.571 | 470.812 | +4.241 | 652.709 → 532.942 |
| 32 | 1 | session | 417.106 | 397.267 | -19.839 | 482.408 → 638.533 |
| 32 | 4 | isolated | 1481.655 | 1575.859 | +94.204 | 1955.430 → 1941.972 |
| 32 | 4 | session | 1513.298 | 1582.430 | +69.132 | 1851.717 → 1806.244 |

У одиночного input нет устойчивой прибавки p50; у batch 32 / concurrency 4 наблюдалось +94,204 мс для isolated и +69,132 мс для session. На этой нагрузке время включает очередь model server. Порядок ABBA уменьшает влияние дрейфа, но такой короткий прогон не разделяет стоимость polling thread и вариацию model/host. Поэтому результат **не доказывает нулевой overhead** и не обосновывает смену default.

Readiness session: p50 61,753 → 60,892 мс, max 85,469 → 84,433 мс (по 22 запуска). Суммарный RSS helpers после вызовов: один session 28,859–29,844 → 29,172–29,984 MiB; четыре session 116,219–118,531 → 116,250–119,359 MiB. Это фактические диапазоны snapshots, не чистая прибавка guard и не оценка peak. FD возвращаются к 5 после каждой фазы; во время session 1/4 слотов — 7/13.

Все 264 набора vectors совпали точно с первой записью соответствующего входа; восемь уникальных blobs, component/cosine difference 0. Нет оставшихся собственных PID: завершены 176 helpers, Ollama и его runner; модель явно выгружена. Для concurrent фаз replay подтвердил настоящее перекрытие четырёх запросов, правильного владельца и guard PID каждого helper.

## Проверки и следующий gate

Проверка двух соседних владельцев прошла на macOS и Linux во всех четырёх комбинациях transport. Linux worker image `sha256:927050befe221bf4b1b3c47ddffb494cc540b313acf3452dac24de5b0b7fc619` содержит неизменённый production runtime; все 102 worker-теста прошли. Пять targeted parent-binding тестов также прошли на host; полный host-набор — 101 pass + 1 skip за 25,217 с. Typecheck и docs:check прошли: 12 Node + 264 Python tests, 1640 ссылок, 197 Markdown-файлов и 13 process templates. Architecture audit — 0 ошибок, 0 предупреждений. Новая проверка профайлера использует настоящий HTTP и обе исторические/current реализации; она обнаружила на предварительном smoke различие `/var`/`/private/var` и позицию `-u`, затем прошла после исправления наблюдателя до замера.

16 replay/mutation-тестов подтверждают отказ при подмене модели, полных vectors, baseline/source hashes, ABBA, guard PID/argv, владельца, сроков, FD и списка оставшихся процессов. Они также отвергают последовательные интервалы вместо заявленной конкурентности. [Журналы и manifest](./evidence/2026-09-27/embedding-parent-guard/checks.json) сохраняют фактические результаты.

Следующий инженерный gate — CPU, память и завершение простаивающих session helpers при 1/4/32 слотах: в отличие от короткого model profile, эта проверка должна отдельно измерить постоянную стоимость guard. Windows lifecycle, backend cancellation, deployment policy и предметная qualification остаются открытыми. Default `isolated` сохранён.

## Воспроизведение

После commit всех измеряемых исходников:

```sh
python3 scripts/profile-embedding-parent-guard.py --output docs/qualification/local-decisions/performance/evidence/2026-09-27/embedding-parent-guard
python3 scripts/verify-embedding-parent-guard.py docs/qualification/local-decisions/performance/evidence/2026-09-27/embedding-parent-guard
python3 -m unittest discover -s workers -p test_embedding_parent_exit.py -v
```

Новый эксперимент требует нового каталога. Runtime defaults, pool capacity, deadlines и пределы HTTP-body не меняются.

## Источники

[Python os.getppid](https://docs.python.org/3/library/os.html#os.getppid) описывает смену родителя на Unix и сохранение старого значения на Windows; поэтому lifecycle-проверка ограничена Unix. [Ollama FAQ](https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests) описывает зависимость очереди и памяти от parallel requests; здесь настройка закреплена, очередь при concurrency 4 входит в HTTP latency. Это объяснение границ измерения, а не доказательство производительности.
