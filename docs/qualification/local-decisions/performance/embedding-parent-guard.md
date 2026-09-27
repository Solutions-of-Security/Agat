# Parent guard: стоимость и независимые владельцы

Дата протокола: 27.09.2026. Дизайн фиксируется до запуска настоящей модели; результаты будут добавлены после независимого replay. Этот этап продолжает [SIGKILL recovery](./embedding-parent-exit.md).

## Зафиксированный дизайн

Сравниваются штатные isolated и session transport до и после Unix parent guard. Baseline — `218ecb5f6b70289df0c89ebbc7275de57a202684`; два старых transport-модуля извлекаются из Git во временный каталог. Общие `agat_worker.py`, telemetry, web tools и local decisions должны побайтно совпадать. Нового флага отключения защиты в worker нет.

Модель `embeddinggemma:latest`, digest `85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1`, dimension 768; установленные локальные веса, без скачивания. Отдельный Ollama на случайном loopback-порту, cloud выключен, одна загруженная модель и один parallel request, context 2048. После эксперимента модель выгружается, собственная группа процессов останавливается и проверяется отсутствие оставшихся PID.

Матрица batch 1/32 × concurrency 1/4 × isolated/session. Для каждой ячейки порядок before/after/after/before, восемь вызовов в блоке; перед матрицей восемь прогревочных вызовов. Всего 264 запроса, из них 256 измеряемых, 176 helper и 44 отдельные readiness-операции. Четыре варианта текста на каждый batch совпадают с [прежним парным профилем](./embedding-session-model.md). Полные vectors сохраняются вне интервалов запросов и проверяются независимо: размерность, конечность, порядок, input hash, component delta ≤ 1e-6, cosine distance ≤ 1e-10. Startup disposable входит в вызов; session readiness измеряется отдельно.

Регистрируются фактические PID, аргументы guard, hash исполняемого helper, владелец каждого запроса, закрытие pipes и returncode. RSS/FD снимаются до readiness, после readiness, после вызовов и после close; это snapshots, а не peak memory. Измерение не оценивает idle CPU и энергопотребление polling thread. Малое число повторов и один host не позволяют объявлять production SLO или статистически значимую цену guard.

## Независимость владельцев

`workers/test_embedding_parent_exit.py` запускает два настоящих процесса с `LocalModelClient` и два удержанных chunked HTTP-ответа. Все четыре сочетания isolated/session проверяются отдельно: SIGKILL первого владельца закрывает только его запрос; второй parent/helper остаются живы и после разрешения ответа возвращают точный `[1, 0]`, завершаясь с кодом 0 и reaping собственного helper. Проверка проводится на host и в Linux worker image с побайтно проверенными runtime-модулями.

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
