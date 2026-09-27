# Срок lease во время поздней записи retrieval

Дата: **27.09.2026**. Продолжение [проверки после ranking](./retrieval-lease-locking.md) и [повторного main HTTP-профиля](./retrieval-deadline-http.md). Проверяется право сохранить retrieval, если lease истёк во время ожидания записи источников или события.

## Воспроизведение

На baseline `77bb6eab807c2b0b4e2b207e03d2122357401f5f` два теста с настоящим PostgreSQL 17.6 подтвердили ошибку. Другая транзакция удерживает `SHARE` lock на `knowledge_retrievals` либо `events`. Такая блокировка пропускает чтение sources и задерживает соответствующий INSERT. Тест наблюдает именно ожидающий INSERT через `pg_stat_activity`, проверяет, что ожидание началось до expiry, и освобождает lock уже после истечения lease. В обоих случаях прежний код успешно сохранил **один retrieval и одно событие**.

Вариант с `events` проходит дальше: INSERT retrieval уже выполнен внутри открытой транзакции до ожидания записи события. Проверка только перед INSERT retrieval не закрывает этот сценарий. Отдельный SQLite-тест выполняет настоящий INSERT события и лишь затем переводит модельные часы за expiry; baseline также не отклонял результат.

Row locks защищают строки от конкурирующих изменений, но не останавливают время. PostgreSQL удерживает полученные блокировки до конца транзакции; `SHARE` конфликтует с записью, сохраняя возможность обычного SELECT. [Официальные режимы блокировок](https://www.postgresql.org/docs/17/explicit-locking.html). Кроме того, SQL `CURRENT_TIMESTAMP` означает начало транзакции, поэтому его нельзя считать свежей проверкой после ожидания. [Функции времени PostgreSQL](https://www.postgresql.org/docs/17/functions-datetime.html#FUNCTIONS-DATETIME-CURRENT).

## Исправление и границы

После записи retrieval и события выполняется ещё одна проверка текущего времени против срока уже заблокированной stage. Если lease истёк, исключение запускает существующий ROLLBACK обеих записей. Проверки при входе, после ranking и получения stage/run locks сохраняются. Дополнительных SQL-команд, timers, pools и настроек нет.

Граница гарантии — завершение тела retrieval-транзакции **перед запросом COMMIT**. Время фактического commit и доставки HTTP-ответа, а также скачки часов не получают нового обещания. Уже разобранный [неизвестный исход COMMIT](./retrieval-commit-acknowledgement.md) остаётся отдельным состоянием; 503/504 нельзя считать доказательством rollback.

## Проверка

В каждом PostgreSQL-сценарии должны отсутствовать retrieval, sources и событие; после maintenance новый lease через тот же executor должен получить `K1`. SQLite проверяет rollback обеих реальных записей и такое же восстановление. Исходные и финальные логи, commit и хэши хранятся в [checks.json](./evidence/2026-09-27/retrieval-lease-write-fence/checks.json).

```sh
bash scripts/test-fleet-ha-postgres.sh --test-name-pattern='lease expires while writing'
node --import tsx --test apps/coordinator/test/knowledge-retrieval-validation.test.ts
npm run test:coordinator
bash scripts/test-fleet-ha-postgres.sh
npm run typecheck
npm run docs:check
```

Целевые проверки: PostgreSQL **2/2**, SQLite **7/7**. Полная регрессия: coordinator **248/248**, PostgreSQL Fleet/HA **28/28**; typecheck всех workspaces и docs-проверки **12 Node + 152 Python**, включая replay исторических измерений, прошли. Следующий инженерный шаг — параметры shadow-проверки агентного шага в визуальном редакторе: текущая документация требует редактировать их через API. Независимые бизнес-данные, человеческое review и разрешение автоматической маршрутизации остаются открытыми gates исходного плана.
