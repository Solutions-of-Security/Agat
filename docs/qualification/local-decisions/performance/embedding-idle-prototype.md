# Прототип освобождения idle embedding helpers

Дата: 27.09.2026. После [измерения idle budget](./embedding-idle-budget.md) добавлен ограниченный прототип: 32 свободных helper больше не обязаны оставаться в памяти до завершения владельца. Production worker и его defaults не изменены.

[IdleEmbeddingSessionPool](../../../../scripts/lib/embedding_idle_pool.py) находится в `scripts/lib`, использует настоящий production `EmbeddingSession` и тот же helper, framing, byte limits, parent guard и HTTP transport. Idle timeout задаётся явно: конечное положительное число до 3600 секунд, отдельно от срока запроса. Выбор production timeout этим этапом не выполнен.

## Владение и остановка

Один maintenance thread на pool периодически просматривает свободные сессии. Период — четверть idle timeout, ограниченная 5 мс…1 с. Время простоя отсчитывается после последнего завершившегося exchange. Сессия освобождается через штатный EOF/reap только при отсутствии владельца; helper не получает собственный idle timer, который мог бы сработать одновременно с входящим IPC.

Новая блокировка согласует начало exchange и retirement. Обслуживание использует неблокирующий захват и пропускает активные запросы. Если новый запрос пришёл во время уже начатого retirement, он ждёт освобождения с сохранением cancellation и исходного deadline; затем обычный transport создаёт новый helper. Внутреннего retry и повторной отправки запроса нет. Таймер простоя не продлевается запросом, отклонённым до admission.

Pool close сначала останавливает maintenance и включает штатные stopping-сигналы всех сессий. Поэтому активный HTTP прерывается, даже когда другая сессия ещё завершает retirement. Затем закрываются все helper и выполняется join maintenance. Повторный и конкурентный close не открывают admission заново. Ошибка обслуживания сохраняется как `maintenance_failure`, закрывает admission и сигнализирует сессиям; владелец обязан выполнить обычный context-manager close, освобождающий ресурсы.

Idle timeout определяет момент пригодности к освобождению, не hard bound при произвольной задержке планировщика. Новый холодный helper после простоя снова оплачивает startup; его стоимость на реальной модели — следующий независимый gate.

## Проверки

[13 тестов](../../../../scripts/test/test_embedding_idle_pool.py) используют настоящие Python helpers, private pipes и HTTP:

- Lazy pool, некорректный timeout/capacity, повторный close и отказ после закрытия.
- Полное idle retirement, новый PID после простоя и ровно два HTTP-запроса для двух явных вызовов.
- Активные headers/body живут дольше idle timeout; свободный соседний слот закрывается независимо.
- Новый caller во время retirement успешно продолжает после reap либо получает свой deadline/cancellation без начала лишнего HTTP.
- Queued cancellation не затрагивает активный запрос. Close отменяет active HTTP и присоединяет maintenance.
- Два конкурентных close во время retirement; close сигнализирует active slot, пока другой slot намеренно удержан в retirement.
- Инъекция maintenance error делает отказ видимым и закрывает admission.
- Два всплеска по 32 одновременных запроса, разделённые idle retirement: 64 точных ответа, 64 helper reaped, между всплесками helper count возвращается к нулю, FD восстанавливаются, после close нет maintenance thread.

Планировочные gates в race-тестах удерживают retirement под настоящей блокировкой; framing, subprocess, HTTP и последующий reap не заменены. Проверки ждут полного retirement, а не одного `poll()` процесса: exit может предшествовать закрытию Python pipe objects.

macOS/Python 3.14.3: **13/13**, 5,833 с. Linux/Python 3.13.15 в собранном worker image: **13/13**, 4,968 с. Image `sha256:927050befe221bf4b1b3c47ddffb494cc540b313acf3452dac24de5b0b7fc619`, `--init --network none`; реальные `/opt/agat` transport-модули предварительно сверены побайтно с текущими исходниками. [Журналы и hashes](./evidence/2026-09-27/embedding-idle-prototype/checks.json).

Полный `docs:check`: 12 Node + 290 Python tests; 1653 локальные ссылки в 199 Markdown-файлах, 13 process templates. Architecture audit: 0 ошибок, 0 предупреждений.

```sh
python3 -m unittest discover -s scripts/test -p test_embedding_idle_pool.py -v
```

Следующий gate — закреплённый model-профиль burst → idle → burst: точность полных vectors, фактическое освобождение ресурсов, цена повторного запуска helper, cancellation/deadline после возобновления. Прототип пока не импортируется production worker; возможная интеграция и значение timeout требуют результатов этой проверки.

## Источники решения

[HTTPX resource limits](https://www.python-httpx.org/advanced/resource-limits/) разделяет максимальный размер pool и срок хранения idle connections. Здесь применён тот же принцип разделения настроек, но helper является процессом, поэтому отдельно проверены reap и pipes; значения HTTPX не перенесены как SLO. [Python Lock.acquire](https://docs.python.org/3/library/threading.html#threading.Lock.acquire) определяет неблокирующий и ограниченный временем захват; это позволяет не занимать активный слот обслуживанием и включить ожидание retirement в срок caller.
