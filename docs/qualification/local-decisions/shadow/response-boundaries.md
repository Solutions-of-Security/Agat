# Shadow HTTP: отмена и deadline при получении ответа

09.10.2026 MSK. Продолжение проверки active HTTP recovery: найден и исправлен
дефект [клиента](../../../../workers/local_decisions.py), который мог вернуть
ответ после уже наблюдаемой отмены, пока watchdog ещё не получил CPU.

Воспроизведение использовало настоящий временный loopback HTTP-сервер.
Сразу после чтения заголовков устанавливался cancellation Event. Прежний
клиент возвращал result для HTTP 200, `profile_mismatch` для 409, `busy`
для 503 и `invalid_response` для 302. Для успешного ответа проверялся только
флаг watchdog; быстрые status branches вообще обходили deadline.

Теперь клиент синхронно проверяет cancellation Event и monotonic deadline
после соединения, перед обработкой заголовков, после чтения тела и после
разбора JSON. Уже установленная отмена имеет приоритет и даёт
`unavailable/cancelled`; истёкший срок — `unavailable/timeout`, без result.
Отмена после connect предотвращает отправку POST. Наблюдаемая отмена
сохраняет свой reason и при socket timeout.

Watchdog по-прежнему закрывает транспорт во время заблокированного чтения;
новые проверки не заменяют этот механизм. Отмена, пришедшая после последней
проверки перед возвратом, не объявляется предсказуемой: эти границы не дают
атомарной отмены между потоками или мгновенного прерывания GPU. Лимиты,
профиль, отсутствие retry, callerTiming и primary fallback сохраняются.
Изменяется worker HTTP-путь, а не implementation fingerprint модели.

[Python HTTPConnection](https://docs.python.org/3/library/http.client.html#http.client.HTTPConnection)
описывает timeout блокирующих операций, а
[Event.is_set](https://docs.python.org/3/library/threading.html#threading.Event.is_set)
позволяет проверить флаг без ожидания другого потока. Поэтому проверка на
границе приёма ответа дополняет watchdog. Deadline использует
[time.monotonic](https://docs.python.org/3/library/time.html#time.monotonic).

Восемь новых [regression tests](../../../../workers/test_local_decisions.py)
проверяют настоящие socket exchanges с намеренно не запущенным watchdog,
чтобы воспроизвести задержку его планирования. Обработка сети остаётся
реальной; изменение monotonic time в одном тесте — явная модель поздних
заголовков, а не измерение задержки сервера. Проверены connect, 200/409/503,
неверные headers, чтение/JSON parsing, socket timeout и следующий здоровый
вызов. Дополнительный lease test сохраняет готовый fixture primary output
и одно cancellation observation без повторного HTTP.

Повтор четырёх исходных HTTP-сценариев с обычным watchdog теперь возвращает
`cancelled` во всех случаях. Контекст моделей, предметные labels,
calibration/holdout и полномочия маршрутизации этот опыт не оценивает.

Полная регрессия: **160 worker tests / 3 optional skips**, **905 Python
documentation tests / 4 optional skips**, **12 Node documentation tests**;
workspace typecheck, 2572 локальные ссылки и каталог процессов — pass.
Начальный red run семи boundary tests сохранён отдельно от green
и обычного watchdog-воспроизведения. Результаты закреплены в
[summary](./response-boundaries-summary.json).

Следующий gate — prospective caller timeout во всём public development
workflow с сохранением primary, полным denominator и здоровым suffix;
физически завершённый ответ модели нужно отличать от доставленного caller
ответа. Эти локальные проверки не закрывают такой полный native run.

Private ZIP сохранён в исходном workspace: **13 entries / 362544 bytes**,
SHA-256 `fa9565e7a77e3c785898d5fc1b08754328846f55622927d4169c0c3ce8626061`.
Обе копии проверены по CRC, каждому SHA/size и committed worker source
bindings. [Archive receipt](./response-boundaries-archive-summary.json)
содержит только metadata.
