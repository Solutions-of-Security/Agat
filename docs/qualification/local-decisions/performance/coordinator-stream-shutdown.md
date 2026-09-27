# Остановка coordinator с UI и A2A-подписками

Дата: **27.09.2026**. Продолжение [проверки retrieval queue при SIGTERM](./retrieval-shutdown-drain.md). Открытые SSE и A2A blocking-response удерживали `server.close`: очередь retrieval уже закрывалась, но полный процесс не завершался, пока клиенты не разрывали соединения.

## Изменение жизненного цикла

Main передаёт HTTP-серверу отдельный shutdown signal. При SIGTERM/SIGINT он закрывает UI/A2A event streams, прекращает новые запросы и останавливает ожидание A2A task. Уже сохранённые tasks и runs не отменяются. Обычные активные retrieval-запросы по-прежнему завершаются в соответствии с [контрактом предыдущего этапа](./retrieval-shutdown-drain.md).

A2A blocking `message:send` возвращает HTTP 503, `UNAVAILABLE`, reason `COORDINATOR_SHUTDOWN` и `metadata.taskId`, если task уже создана. После восстановления клиент может запросить её через GET. Новый отклонённый запрос не получает выдуманный taskId. SSE заканчивается без ложного terminal task event. UI flush timers снимаются по закрытию response; A2A poll повторно проверяет закрытие после каждого ожидания, прежде чем читать store.

Повторная проверка сигнала перед открытием stream нужна для запроса, чья аутентификация или чтение body завершились уже после начала остановки. Один listener на HTTP-сервер управляет набором stream responses и удаляется при закрытии. Новые A2A push-проходы после начала shutdown не запускаются; этот этап не меняет гарантии доставки уже начатых внешних запросов.

[Node.js server.close](https://nodejs.org/docs/latest-v24.x/api/http.html#serverclosecallback) ожидает активные ответы. [HTML SSE](https://html.spec.whatwg.org/dev/server-sent-events.html) предусматривает reconnect и Last-Event-ID; закрывать такой поток не означает отменять бизнес-операцию. [A2A specification](https://a2a-protocol.org/latest/specification/) допускает 503/UNAVAILABLE для временного системного отказа. Из этих контрактов выбран отказ HTTP-ожидания с сохранением durable task вместо подмены её состояния.

## Воспроизведение и проверка

Baseline `ba6e130db605e3e1c0afe35e8fe15befb4cb7bc3` не прошёл новый PostgreSQL-сценарий: `SSE/A2A requests prevented coordinator shutdown`. Тест запускает настоящий собранный coordinator с isolated retrieval, одновременно открывает UI events, A2A `message:stream` и A2A `message:send` с `returnImmediately=false`. Перед SIGTERM подтверждено создание двух task.

После исправления оба потока завершаются, blocking-response получает правильный 503 с ID, процесс выходит с кодом 0. Обе tasks остаются `SUBMITTED`, без добавленных terminal events и без дополнительных task после restart. Повторный GET возвращает прежнее состояние. Отдельное явное подключение к UI events с сохранённым Last-Event-ID получает полный ожидаемый набор последующих event IDs ровно по одному и в порядке журнала.

SQLite/HTTP-тест сохраняет процесс теста живым после закрытия store и пересекает интервалы A2A poll 250 мс и UI flush 1 с. Счётчики реальных store methods подтверждают отсутствие поздних чтений. Он также проверяет 503 для новых запросов после сигнала. Такой тест ловит утечку callbacks, которую завершение дочернего процесса могло бы скрыть.

[Логи и SHA](./evidence/2026-09-27/coordinator-stream-shutdown/checks.json) содержат первоначальный отказ, целевой повтор и полную регрессию: **247/247 coordinator**, **20/20 PostgreSQL**, типизация и сборка coordinator — pass; 1452 локальные ссылки и архитектурный аудит без замечаний. Время 5 с в assertion ограничивает ожидание зависшего теста; production shutdown SLO не измерялся. Следующий этап — повтор concurrent HTTP-профиля через обычный main с его реальной конфигурацией и фоновым обслуживанием; прежние latency-числа остаются привязаны к прототипу.

```sh
bash scripts/test-fleet-ha-postgres.sh '--test-name-pattern=closes UI and A2A streams'
node --import tsx --test apps/coordinator/test/a2a.test.ts
npm run fleet:test-ha-postgres
npm run test:coordinator
```
