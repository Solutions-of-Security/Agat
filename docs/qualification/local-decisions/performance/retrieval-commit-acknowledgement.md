# Потерянное подтверждение COMMIT retrieval

Дата: **27.09.2026**. Продолжение [проверки SQL deadline](./retrieval-control-lock-deadline.md). Цель — проверить отказ и восстановление, когда запись уже сохранена, а вызывающий процесс не получил подтверждение.

## Fault fixture и исходная ошибка

Одноразовый PostgreSQL 17.6 и собранный main соединены через test-only TCP proxy на `127.0.0.1`. Proxy разбирает границы PostgreSQL v3 messages, выбирает ровно заданный `application_name` и активируется только после `INSERT INTO knowledge_retrievals`. Он передаёт настоящий COMMIT серверу, но удерживает его ответ. Тест получает сигнал только после реального `CommandComplete(COMMIT)`; отдельное соединение читает сохранённый `K1`. Затем proxy либо разрывает соединение, либо продолжает удерживать ответ до deadline. Credentials и SQL bodies не сохраняются и не выводятся.

Так отделяются подтверждённый commit в БД и знание о нём у клиента. Протокол допускает отдельные `CommandComplete`, `ErrorResponse` и `ReadyForQuery`; потеря ответа не отменяет уже выполненную транзакцию. [PostgreSQL message flow](https://www.postgresql.org/docs/17/protocol-flow.html#PROTOCOL-FLOW-SIMPLE-QUERY).

Исходная реализация после разрыва не вернула оперативный отказ за окно 2500 мс, хотя `K1` уже был виден другому соединению. У выданного из pool клиента отсутствовал обработчик socket error; исключение могло остановить SQL bridge до публикации ответа. Кроме того, ошибка последующего ROLLBACK скрывала неопределённый исход COMMIT. [События pg.Client](https://node-postgres.com/apis/client#events) и [pool lifecycle](https://node-postgres.com/apis/pool#events) требуют учитывать ошибки живых соединений отдельно от обычных SQL-ответов.

## Изменение

- SQL bridge принимает событие ошибки каждого созданного клиента; ожидающий query Promise возвращает ошибку вызывающему коду.
- Ошибка при отправленном COMMIT помечается `AGAT_COMMIT_UNKNOWN`. Повреждённое соединение исключается из pool. Неудачный cleanup ROLLBACK не подменяет этот исход сообщением об отсутствующей транзакции.
- Isolated executor возвращает 503 и закрывает admission без повторного выполнения. Потеря ответа без разрыва продолжает давать 504 по общему deadline. Ожидающие запросы отклоняются; следующие получают 503.
- Обычный REST API в sync также возвращает 503 для неизвестного COMMIT вместо ошибки входных данных 400. Pool может создать новое соединение для отдельной операции; скрытого повтора текущей операции нет.

## Подтверждённые сценарии

| Режим и fault | Ответ поиска | Последующее состояние |
|---|---:|---|
| isolated, разрыв после COMMIT | 503 | health 503; очередь отклонена; после restart сохранён один `K1` |
| isolated, удержанный ответ | 504 | health 503; очередь отклонена; после restart сохранён один `K1` |
| sync, разрыв после COMMIT | 503 с явным неизвестным исходом | health 200; pool заменяет соединение; до нового запроса сохранён один `K1` |

Во всех трёх случаях только новый явный поиск создаёт `K2`. Первый сохранённый результат не удаляется и не повторяется. Все процессы штатно завершаются; proxy и одноразовый контейнер закрываются в cleanup. Это проверка управляемого разрыва на loopback, не полный managed-PostgreSQL failover или доказательство exactly-once для всех API.

Полные проверки: coordinator **247/247**, PostgreSQL Fleet/HA **26/26**, typecheck workspaces и строгая отдельная проверка TypeScript для proxy. Исходные failures, финальные логи и SHA: [checks.json](./evidence/2026-09-27/retrieval-commit-acknowledgement/checks.json).

Первая CI-проверка обнаружила отдельную ошибку fixture: сценарий `withhold` не успел достичь COMMIT за секундный deadline. Proxy отправлял каждый protocol message отдельным `write`, оставляя Nagle включённым на обоих sockets, тогда как `pg` вызывает `setNoDelay(true)`. Такой транспорт может добавлять задержку до целевого fault; [Node.js описывает этот обмен latency на throughput](https://github.com/nodejs/node/blob/main/doc/api/net.md#socketsetnodelaynodelay). Proxy теперь использует ту же настройку, что драйвер. Deadline 1000 мс и все проверки сохранены. Если поиск завершится раньше COMMIT, тест сразу сообщает HTTP status вместо ожидания сигнала fixture. Повторные три локальных сценария прошли; успешный COMMIT наблюдался через 75/63 мс в isolated. Исходный CI failure и новые логи сохранены отдельно, результат CI проверяется перед merge.

```sh
bash scripts/test-fleet-ha-postgres.sh --test-name-pattern='COMMIT acknowledgement|uncertain sync COMMIT'
npm run test:coordinator
bash scripts/test-fleet-ha-postgres.sh
npm run typecheck
npm run docs:check
```

Нельзя считать 503/504 доказательством rollback или поводом автоматически повторить операцию. Перед ручным повтором нужно прочитать сохранённые sources/trace; для закрытого isolated executor требуется restart. Следующий этап — повторить парный main HTTP-профиль на одном новом commit после изменений SQL deadline и обработки ошибок. Параметры нагрузки и ограничения интерпретации сохраняются из предыдущего протокола.
