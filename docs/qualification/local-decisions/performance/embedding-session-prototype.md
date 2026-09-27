# Прототип повторно используемого embedding helper

27.09.2026. Продолжение [измерения транспортных расходов на embeddinggemma](./embedding-model-transport.md). Экспериментальный [EmbeddingSession](../../../../scripts/lib/embedding_session.py) находится в `scripts/lib`; обычный worker и его Docker image по-прежнему используют одноразовый процесс.

## Владение и ограничения

Сессия явно создаётся и закрывается caller через context manager. Один процесс выполняет один запрос за раз; конкурентность 4 в опыте означает четыре независимые сессии. Допускается отдельный `warmup()` с проверяемым readiness reply. Каждый HTTP-запрос использует существующий `embedding_http._fetch`: urllib создаёт новый request с текущими URL, headers и payload. Повторное использование процесса не означает HTTP connection pooling.

Общий monotonic deadline включает сериализацию, ожидание mutex, запуск helper и обмен HTTP-ответом. Ожидающий запрос при отмене/timeout не трогает процесс текущего владельца. Для активного запроса guard запрашивает проверку отмены, закрытия сессии и deadline каждые 20 мс; фактическая задержка зависит от планировщика и GIL. Затем он завершает только закреплённый за ним процесс. [Длительный опыт](./embedding-session-endurance.md) измеряет возврат после отмены до 113 мс под конкурентной нагрузкой. Закрытие противоположных концов pipes разблокирует даже запись большого запроса. Guard обязательно завершается до передачи mutex следующему владельцу. Новый вызов после транспортного отказа получает новый процесс; скрытого retry нет.

У протокола есть длина frame, монотонный request ID и тип ответа. Неполная frame, неверные ID/type/length либо превышение лимита приводят к удалению процесса и обоих private pipes. Полностью прочитанный HTTP-error сохраняет синхронизацию и допускает следующий запрос на том же helper. Источник и token передаются по stdin, не через argv/файлы. Максимальный request frame — **2 МиБ**, HTTP response — **8 МиБ**, error prefix — **4096 байт**, diagnostic — до 1200 символов. Request limit — отдельное ограничение прототипа, его совместимость с обычными входами worker ещё предстоит проверить.

`close()` будит активный guard, ожидает освобождения владения и reap. Idle helper получает EOF; через 250 мс без завершения применяется kill/wait. OS process creation, планирование, сериализация и reap не являются hard real-time операциями. JSON/vector validation выполняет прежний `LocalModelClient` после транспорта. Отключение клиента не доказывает прекращение вычисления модели. API не ограничивает число caller threads, ожидающих mutex; пула и admission policy здесь нет.

Выбор основан на документированных свойствах [raw I/O с частичными read/write](https://docs.python.org/3/library/io.html#io.RawIOBase), [subprocess pipes и kill/wait](https://docs.python.org/3/library/subprocess.html#subprocess.Popen.communicate) и [socket timeout urllib](https://docs.python.org/3/library/urllib.request.html#urllib.request.urlopen). Протокол использует циклы полного чтения/записи; процесс после прерванного обмена не переиспользуется.

## Проверка отказов

**16 lifecycle-тестов** выполняются на macOS/Python 3.14.3 и в Linux/Python 3.13.15. Реальные HTTP endpoints проверяют смену source/token/origin, успешную партию 32 × 4096, HTTP error и oversized response, зависание headers, trickling success/error body, отмену и deadline. Проверены ожидающий отменённый запрос, две независимые сессии, новый владелец после отмены прежнего, active close, idle crash, некорректные frames и fake helper, который не читает заполненный pipe либо игнорирует EOF. После серии deadlines нет новых FD и guard threads; helper reaped до освобождения сервером зависшего ответа. Windows этим этапом не квалифицируется.

## Закреплённое измерение

Harness/verifier/prototype закреплены commit **`d06692c`** до опыта; [plan](./evidence/2026-09-27/embedding-session-prototype/plan.json) сохраняет полный SHA и хеши зависимостей. Матрица: размерность **768/4096**, batch **1/32**, concurrency **1/4**, порядок **isolated → session → session → isolated**, по восемь вызовов в блоке. Используется настоящий `LocalModelClient` с заменой только transport function внутри harness. Ответы HTTP fixture содержат обратный порядок индексов; независимый verifier заново строит все ожидаемые векторы и request/response SHA.

Всего **256 вызовов** в 32 блоках, **128 одноразовых** и **40 постоянных helper**, все завершились с кодом 0 и закрытыми pipes. Каждая постоянная сессия обслуживает восемь запросов при concurrency 1 или два при concurrency 4; закрытие происходит после блока. **40 readiness-вызовов** измерены отдельно: p50 **63,451 мс**, max **152,601 мс**. Они не входят в hot latency. Все HTTP-вызовы входят в таблицу, первый запрос каждого helper не скрывается как warmup.

| Размерность | Batch | Concurrency | Isolated p50, мс | Session p50, мс |
|---:|---:|---:|---:|---:|
| 768 | 1 | 1 | 70.256 | 1.003 |
| 768 | 1 | 4 | 76.349 | 5.029 |
| 768 | 32 | 1 | 80.929 | 10.354 |
| 768 | 32 | 4 | 112.085 | 61.595 |
| 4096 | 1 | 1 | 76.267 | 3.391 |
| 4096 | 1 | 4 | 84.536 | 12.799 |
| 4096 | 32 | 1 | 131.374 | 54.743 |
| 4096 | 32 | 4 | 403.426 | 363.285 |

По 16 наблюдений на ячейку; nearest-rank p95 здесь равен максимуму. Фактическое перекрытие достигло 1/4 во всех блоках. Полные интервалы, отдельные readiness, server observations и RSS snapshots сохранены в [result](./evidence/2026-09-27/embedding-session-prototype/result.json), сводки — в [replay](./evidence/2026-09-27/embedding-session-prototype/replay.json).

Наибольшая сумма RSS **idle helper после запросов — 133,922 МиБ**, максимум RSS parent — **277,469 МиБ**. Снимки сделаны после readiness и после всех запросов, не во время inference; это **не peak RSS**. Parent включает HTTP fixture, сохранённые vectors/hashes и allocator, его нельзя считать памятью обычного worker. Каждый helper удерживает два родительских FD; после закрытия блока число FD возвращается к исходному. Долговременная стабильность RSS/FD этим коротким опытом не доказана.

**Девять mutation-тестов verifier** отклоняют пропуск блока, повтор HTTP, изменение плана/лимитов, неверные исходники/векторы, согласованную порчу client/server input SHA, чужой helper, запрос до readiness, незакрытые pipes, рост FD, чужие RSS PID и перекрывающиеся запросы одной сессии.

```sh
python3 -m unittest discover -s scripts/test -p 'test_embedding_session*.py' -v
python3 scripts/profile-embedding-session.py --output docs/qualification/local-decisions/performance/evidence/<date>/<new-directory>
python3 scripts/verify-embedding-session.py docs/qualification/local-decisions/performance/evidence/2026-09-27/embedding-session-prototype
npm run docs:check
```

[Журнал проверок и SHA evidence](./evidence/2026-09-27/embedding-session-prototype/checks.json): 25 целевых host-проверок, 16 Linux, 12 Node + 211 Python в общем docs suite; architecture audit — PASS, 0 ошибок и предупреждений.

Следующий gate — парный опыт на закреплённой настоящей embeddinggemma с полными vectors, затем длительная серия успехов/отмен и учёт постоянной памяти. До выбора ownership/admission для обычного worker, проверки request budget и его реального lease/recovery-пути прототип остаётся инструментом эксперимента. Замеры скорости не меняют модель, качество retrieval или предметные критерии допуска.
