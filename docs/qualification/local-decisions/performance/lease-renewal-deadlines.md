# Продление аренды после ожидания SQL

Дата: **27.09.2026**. Этап продолжает [проверку embedding lifecycle](./embedding-lease-lifecycle.md) и отдельно рассматривает время между началом renewal, получением ownership lock и окончанием UPDATE.

## Воспроизведённый дефект

На baseline `e92b5449f3e35a9a3d5631d84e73aadf2f59501c` stage renewal передавал cutoff и новый срок в один UPDATE до ожидания строки. Когда lock освобождался после прежнего expiry, параметр cutoff оставался старым и HTTP возвращал 204. Старый владелец получал продление уже истёкшей аренды.

Дополнительно реальный SHARE table lock разрешал SELECT FOR UPDATE, но задерживал последующий UPDATE. И stage, и embedding renewal отвечали 204 после истечения прежнего срока. Поэтому одной проверки после получения строки недостаточно для поздней SQL-записи.

| Реальный HTTP / PostgreSQL 17.6 | До | После |
|---|---|---|
| Stage: row lock освобождён после expiry | 204, аренда продлена | 404; новую попытку получает новый lease id |
| Stage: владелец заменён, пока запрос ждёт | 404 | 404; новый владелец завершает primary |
| Stage: другой запрос своевременно продлил срок | 204 | 204; завершение с текущим lease проходит |
| Stage: UPDATE ждёт table lock до expiry | 204 | 404; продление откатывается |
| Embedding: UPDATE ждёт table lock до expiry | 204 | 404; продление откатывается, новая попытка создаёт ready-индекс |

В каждом lock-сценарии тест наблюдает ожидаемый SQL в `pg_stat_activity` **до** expiry и освобождает блокировку после границы. Смена владельца моделируется отдельной SQL-транзакцией; HTTP обрабатывает обычный собранный coordinator. Восстановление выполняется через store API. Два положительных контроля не позволяют заменить решение безусловным отказом.

## Решение и источники

Оба метода renewal используют общую внутреннюю операцию, принимающую только имена двух таблиц из фиксированного union. Она открывает транзакцию, читает owner/status под FOR UPDATE, проверяет корректную дату и текущее время, затем вычисляет новый срок. После UPDATE прежний deadline проверяется ещё раз. Если он истёк, исключение инициирует rollback и только после него преобразуется в прежний результат `false` / HTTP 404. Ошибки хранилища продолжают выбрасываться; nested-вызов не перехватывает отказ до rollback внешней транзакции.

[Read Committed](https://www.postgresql.org/docs/17/transaction-iso.html) возвращает актуальную заблокированную строку и повторно проверяет WHERE после конкурентного обновления, но переданный приложением cutoff не становится свежим. [Таблица совместимости PostgreSQL locks](https://www.postgresql.org/docs/17/explicit-locking.html) объясняет второй интервал: SHARE совместим с ROW SHARE у SELECT FOR UPDATE и конфликтует с ROW EXCLUSIVE у UPDATE. [CURRENT_TIMESTAMP](https://www.postgresql.org/docs/17/functions-datetime.html#FUNCTIONS-DATETIME-CURRENT) закреплён за началом транзакции, поэтому замена параметра этой функцией не устраняет ожидание. Выбрана свежая проверка приложения с атомарным откатом SQL-only операции.

## Проверка

```sh
node --import tsx --test apps/coordinator/test/lease-renewal-deadlines.test.ts \
  apps/coordinator/test/stage-lease-admission.test.ts \
  apps/coordinator/test/embedding-lease-lifecycle.test.ts \
  apps/coordinator/test/knowledge-retrieval-validation.test.ts
bash scripts/test-fleet-ha-postgres.sh \
  --test-name-pattern='stage renewal checks|renewal rolls back when its UPDATE|embedding renew respects|maintenance preserves a timely renewal|embedding maintenance preserves a renewal'
npm run test:coordinator
bash scripts/test-fleet-ha-postgres.sh
npm run typecheck
npm run docs:check
```

До исправления новые SQLite-сценарии дали 4 ожидаемых FAIL и 2 проходящих контроля; PostgreSQL — 3 ожидаемых FAIL и 2 контроля. SQLite проверяет точное равенство now/expiry, повреждённую дату, реальный UPDATE перед переводом часов и отказ хранилища после записи. Последний отказ проверяет как rollback состояния, так и сохранение исходной ошибки. После изменения: целевые **33/33**, PostgreSQL targeted **10/10**, исправленные fixture-сценарии **5/5**, полный coordinator **284/284**, полный PostgreSQL **58/58**. Typecheck, docs-check и architecture audit — pass. [Логи, итог полного прогона и SHA](./evidence/2026-09-27/lease-renewal-deadlines/checks.json) фиксируют окончательную проверку.

Первый полный PostgreSQL-прогон дал 55 PASS и 3 FAIL в прежних fixtures: проверка renewal ожидала старый UPDATE вместо нового SELECT FOR UPDATE; shadow recovery делал единственный poll при работающем maintenance; незакрытый shadow-run затем попал в тест квот. SQL-barrier обновлён без ослабления deadline-проверки, replacement ожидается ограниченное время, собственный run и worker pool очищаются в finally. Неудачный лог сохранён отдельно; полный набор повторён после исправления fixtures.

Граница проверки — непосредственно после UPDATE перед запросом COMMIT. Изменение не доказывает отсутствие задержки физического commit/ответа, не устраняет скачки часов и не вводит новый timeout для произвольной table lock. Результаты синтетического fault injection не являются производственным SLO.

Следующий инженерный gate — выбор размерности embedding-индекса при одновременном завершении разных jobs одной коллекции. Блокировка отдельного job не сериализует этот общий invariant; сначала нужен воспроизводимый контрпример и положительный контроль совместимых векторов.
