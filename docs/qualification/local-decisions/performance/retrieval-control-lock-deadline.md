# Deadline retrieval при блокировке служебного HTTP-запроса

Дата: **27.09.2026**. Продолжение [профиля настоящего main](./retrieval-main-http.md). Проверяется синтетическая конкуренция за строки PostgreSQL, не throughput или production SLO.

## Воспроизведение

Отдельное соединение удерживает `runs FOR UPDATE`. Поиск заканчивает ranking, блокирует stage и ожидает run. Одновременный HTTP `renew` этой stage блокирует основной поток coordinator через синхронный PostgreSQL bridge. Таймер executor в этом же потоке не может исполниться вовремя.

На исходной реализации с deadline **1000 мс** поиск, renew и health не завершились за дополнительное окно **2500 мс**; тест снял внешнюю блокировку только при cleanup. Это отдельный сценарий: прежний опыт с блокировкой чтения `knowledge_chunks` не удерживал stage и потому не воспроизводил цепочку. Пробный независимый supervisor с `Worker.terminate()` также не освободил SQL-ожидание в этом окне; он не включён в итоговую реализацию.

## Решение

Executor передаёт исходный монотонный deadline в retrieval worker и далее в PostgreSQL scope. Перед каждым SQL statement, включая cursor FETCH, bridge вычисляет остаток времени и задаёт transaction-local `statement_timeout`. Значение не превышает операторский `AGAT_POSTGRES_STATEMENT_TIMEOUT_MS`; ожидание в очереди уже вычтено. Это тот же срок запроса, а не отдельный полный budget на каждый statement.

PostgreSQL ограничивает statement вместе с ожиданием блокировок; только `lock_timeout` не ограничил бы прочую работу statement и применяется отдельно к каждой попытке захвата. Настройка локальна транзакции и не меняет глобальную конфигурацию сервера. [Statement и lock timeout](https://www.postgresql.org/docs/17/runtime-config-client.html#GUC-STATEMENT-TIMEOUT), [transaction-local set_config](https://www.postgresql.org/docs/17/functions-admin.html#FUNCTIONS-ADMIN-SET).

Операции вне явной транзакции, но с deadline, получают собственную короткую транзакцию. Перед COMMIT проверяется оставшееся время; ROLLBACK допускается после истечения. Ошибка истёкшего budget или SQL cancellation даёт 504 и закрывает admission executor без повторного выполнения. Порядок stage → run и обычное ожидание конкурентного поиска сохранены; NOWAIT не заменяет сериализацию K-маркеров. Дополнительных потоков, pools или параметров оператора нет. Для передачи времени используется общий процессный монотонный clock: [Node performance.now](https://nodejs.org/docs/latest-v24.x/api/perf_hooks.html#performancenow).

## Проверка

Три целевых сценария прошли на Node **24.14.0**, PostgreSQL **17.6-alpine**:

- Настоящий main, удерживаемый run, активный и ожидающий поиск, блокирующий renew и health. До снятия внешней блокировки оба поиска возвращают 504, renew — 204, health — 503; следующий поиск отклоняется 503. Оба retrieval-соединения исчезают. Нет retrieval/source/event-записей; после штатного restart явный запрос получает `K1`, скрытого replay нет.
- Изменение внутри транзакции и истечение срока перед COMMIT: commit отклоняется, rollback выполняется, исходное значение сохраняется. Последующее обычное SQL-чтение работает с прежним timeout.
- Cursor с несколькими FETCH и серверной задержкой: SQL cancellation происходит по остатку общего времени. После rollback cursor отсутствует, timeout восстановлен, соединение пригодно для следующего запроса. Отдельно проверены сохранение меньшего операторского timeout и сброс LOCAL после успешного COMMIT на том же физическом соединении.

Полные наборы coordinator **247/247** и PostgreSQL Fleet/HA **23/23** прошли; typecheck всех workspaces завершился успешно. Логи воспроизведения, проверок и хэши исходников: [checks.json](./evidence/2026-09-27/retrieval-control-lock-deadline/checks.json).

```sh
npm run test:coordinator
bash scripts/test-fleet-ha-postgres.sh
npm run typecheck
npm run docs:check
```

## Границы

HTTP main по-прежнему выполняет другие синхронные операции. SQL deadline устраняет воспроизведённое удержание stage исполнителем, но не делает HTTP real-time и не гарантирует мгновенную отмену при потере сети или зависшем сервере. Передача команды и подтверждения тоже занимают время. SQLite и default sync не получают этот PostgreSQL scope.

504 не доказывает отсутствие commit в общем случае: ответ может быть потерян после сохранения. Нулевые записи подтверждены только в описанных fault fixtures. Нужны проверка trace перед ручным повтором и restart для нового admission. Следующий этап — проверить неопределённый исход COMMIT и повторить ограниченный main HTTP-профиль с учётом добавленных SQL-команд; предыдущие числа относятся к своему закреплённому commit.
