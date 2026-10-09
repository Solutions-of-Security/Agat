# Отзыв lease во время shadow HTTP-вызова

09.10.2026 MSK. Настоящий coordinator отзывал lease по запросу оператора,
но worker обнаруживал это только при обычном renewal через 45 секунд.
Соединение shadow caller с пределом 10 секунд оставалось открытым практически
весь deadline. Исправление в
[worker](../../../../workers/agat_worker.py) наблюдает владение lease во время
разрешённого shadow-вызова и передаёт подтверждённый отзыв существующему
cancellation Event.

## Контракт и границы

После свежего renewal и допуска caller intent запускается отдельный watcher:
интервал ожидания 500 мс, общий deadline каждого HTTP renewal 1000 мс,
предел success/error body 4096 bytes. Подтверждённые 401/403/404/409 выставляют
Event; сетевой сбой, deadline, 429/500/503 оставляют владение неизвестным.
Обычный primary сохраняет renewal через 45 секунд. Dry-run, отключённый
decision endpoint, отказ или сбой intent не создают watcher.

Watcher использует существующий
[изолированный HTTP transport](../../../../workers/embedding_http.py).
Credentials проходят через private pipe, не через argv или файл. По завершении
или исключению shadow-вызова stop Event завершает transport, helper очищается
до освобождения watcher. Один renewal может занять до своего общего deadline;
к нему добавляются poll и scheduling. Это не согласованный cancellation SLO.
Дополнительные renewal выполняются только в этой короткой фазе, примерно до
двух попыток в секунду при быстрых ответах coordinator. Decision POST не
повторяется.

Отмена процесса сохраняет серверный fence: поздние observation и primary
completion отклоняются. Локальный `unavailable / cancelled` не превращается
в принятый durable return. Для отозванного assignment сохранены intent,
`returned: null`, `return_missing` и `ended_without_observation`.

## Парные actual HTTP-опыты

Во всех трёх опытах coordinator, опубликованный процесс, авторизация и Python
worker настоящие. Primary и decision endpoints — fixtures. Первый decision
POST удерживает ответ без headers; второй возвращает явно скопированный
результат прежнего native case `support-au-1569930`, с новым id и duration 0.
Новых inference нет; model metadata в таком ответе не доказывает запуск MLX.

| Исходники | EOF после успешного cancel API | Тот же worker выполнил следующий процесс |
|---|---:|---|
| `76d2d4f`, baseline | 9991.511 мс | да |
| `be308766`, первый watcher | 501.889 мс | да |
| `b33a23a9`, bounded transport | 570.463 мс | да |

Каждый опыт содержит ровно два primary и два decision POST. Без credentials
cancel API вернул 401, с admin token — 204. Отменённый agent stage остался
`cancelled` без output; следующий завершился с `PRIMARY_OUTPUT` и одной
принятой fixture observation. Финальный transcript сохраняет renewal 404,
поздние shadow/complete 400 и локальную caller timing 584.309 мс. Прежний
baseline не сохранял outgoing shadow body: его локальный reason нельзя
восстановить из durable unknown return.

Fixture-профиль сериализован Node `JSON.stringify`; его literal byte SHA
`8a7e99cb…` совпадает между двумя процессами и закреплён в plan. Он семантически
соответствует retained native context, но не объявлен исходными Python JSON
bytes того измерения. Нельзя использовать этот опыт для сравнения model
latency, tokenization, калибровки или качества.

## Перепроверка и evidence

Первый watcher прошёл HTTP cancellation, но отдельная regression воспроизвела
оставшийся thread при медленно поступающих renewal headers. Это выявило
недостаточность socket timeout и привело к bounded transport. Оба red logs
сохранены. Девять новых
[regressions](../../../../workers/test_shadow_lease_cancellation.py) проверяют
реальное закрытие socket, same-executor recovery, worker auth и HTTP statuses,
общий deadline при trickling headers, cleanup helper/thread и scope watcher.
Полный worker suite: 169 tests, pass, три optional skips; targeted 53 и
финальные девять — pass.

Независимый stdlib audit выполнил 781 checks: три historical Git snapshots,
raw request SHA, intent/assignment binding, HTTP порядок, отказ поздней записи,
unknown return и healthy suffix. Все шесть записанных PID отсутствуют;
renewal helpers с их parent binding не найдены. Короткоживущие helper PID
не перечислены в actual fixture reports; их обязательная очистка проверена
реальными transport regressions. Свежий resident snapshot после финального
опыта подтверждает прежние четыре PID, 35 protected source hashes, bundle,
registration, профиль, зависимости и ready monitoring без новых inference.

[Summary](./lease-cancellation-summary.json) связывает private raw evidence.
[Archive summary](./lease-cancellation-archive-summary.json) закрепляет ZIP SHA,
66 entries и пять source snapshots; CRC/SHA/size проверены в обеих копиях.
Полные private traces, scripts, test logs и source archives сохраняются в
проверенном ZIP и скопированы в исходный workspace `/docs/private`;
публичный документ не публикует credentials или source text.

Следующий runtime gate — полный development inventory 49 cases через actual
coordinator cancellation и owned native runtime с независимым offline replay.
Owner/customer/SLO, human labels, calibration/holdout и actual boot/login
остаются открытыми; routing выключен, qualification `not_assessed`.

## Основание реализации

[Python `urlopen`](https://docs.python.org/3/library/urllib.request.html#urllib.request.urlopen)
описывает timeout отдельных blocking operations. Общий deadline здесь
проверяется monotonic clock вне disposable helper; socket progress не
продлевает его. [Python Event](https://docs.python.org/3/library/threading.html#event-objects)
даёт cooperative signal, а thread нельзя принудительно остановить штатным
API. Поэтому stop распространяется на owned transport с последующим reap.
[Temporal cancellation](https://docs.temporal.io/activity-execution#cancellation)
также требует доставки сигнала через heartbeat. Это архитектурная аналогия:
данный watcher работает через существующий REST lease coordinator, не через
Temporal Activity. Источники перепроверены 09.10.2026.

```bash
PYTHONPATH=workers:. python3 -m unittest workers.test_shadow_lease_cancellation
python3 -m unittest discover -s workers -p 'test_*.py'
```
