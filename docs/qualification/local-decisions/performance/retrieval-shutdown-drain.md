# Остановка retrieval при заполненной очереди

Дата: **27.09.2026**. Продолжение [операторского PostgreSQL opt-in](./retrieval-postgres-opt-in.md). Проверка собранного coordinator выявила, что SIGTERM не закрывал ожидающую очередь: вызов executor.close находился внутри callback HTTP server.close, который ждал завершения этих же запросов.

## Исправление

Shutdown сразу прекращает admission исполнителя и возвращает ожидающим запросам HTTP 503. Активная транзакция может завершиться до собственного deadline. Основной store закрывается после завершения HTTP-ответов и остановки executor. Повторный SIGTERM не запускает второй цикл закрытия.

Это соответствует [контракту Node.js server.close](https://nodejs.org/docs/latest-v24.x/api/http.html#serverclosecallback): активные ответы могут продолжаться после прекращения приёма соединений. Поэтому очередь приложения нужно закрыть до ожидания callback, а не внутри него. Записи не повторяются автоматически; deadline активной операции по-прежнему может оставить неизвестный результат commit.

## Реальный процесс и PostgreSQL

На baseline `edb42b473f3d2c8b977193166bbdfeb883f46ec9` новый сценарий завершился ошибкой `SIGTERM left queued retrieval waiting for the active database operation`. Это не mock: запускается `dist/server.js`, чтение настоящего индекса блокируется отдельной PostgreSQL-транзакцией, ожидание подтверждается через `pg_stat_activity`.

Последовательность проверяет:

1. Один активный и один ожидающий поиск занимают queue capacity=2; третий получает 429, health остаётся 200.
2. HTTP heartbeat и продление lease получают 204, а `coordinator_replicas.last_seen` обновляется настоящим maintenance во время заблокированного ranking.
3. SIGTERM возвращает ожидающему 503 **до освобождения SQL-lock**, без retrieval-записи.
4. Повторный SIGTERM не прерывает активную операцию. После снятия lock она возвращает 200 и `K1`; процесс завершается с кодом 0.
5. Новый процесс на той же базе остаётся с одной записью: отказавшие и ожидавшие запросы не повторяются. Только явный следующий поиск создаёт `K2` и вторую запись.

Таймер 2,5 с в assertion — предел ожидания отсутствующего события для теста, не production latency SLO. Fixture освобождает собственный SQL-lock и закрывает процессы даже при assertion failure. Перенос общих настроек main в helper проверяется также прежним сценарием pool/deadline/readiness.

[Логи до/после и SHA](./evidence/2026-09-27/retrieval-shutdown-drain/checks.json) фиксируют воспроизведение и регрессию: **246/246 coordinator**, **19/19 PostgreSQL**, типизация и сборка coordinator — pass; 1446 локальных ссылок и архитектурный аудит без замечаний. На этом этапе проверены обычные HTTP-запросы и retrieval queue. Долгоживущие SSE-подписки UI/A2A не входят в этот сценарий; их завершение при shutdown — следующая проверка жизненного цикла сервера. У Node.js такой ответ продолжает считаться активным, поэтому одного server.close недостаточно, если приложение его не завершает.

```sh
bash scripts/test-fleet-ha-postgres.sh '--test-name-pattern=rejects queued retrieval on SIGTERM'
npm run fleet:test-ha-postgres
npm run test:coordinator
npm run typecheck --workspace @agat/coordinator
```
