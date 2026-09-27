# Размерность embedding-индекса при конкурирующих jobs

Дата: **27.09.2026**. После [исправления renewal](./lease-renewal-deadlines.md) проверен общий invariant коллекции: сохранённые векторы разных документов должны иметь одну размерность.

## Воспроизведение

На baseline `4a761c0125eb576ecbf9eb6b8d4cf69a3daa109e` два разных job одной пустой коллекции завершались через HTTP двух настоящих coordinator. Первый запрос уже обновил chunks, но ещё ожидал INSERT события; его данные не были committed. Второй SELECT размерности не видел этих данных и также считал коллекцию пустой. После освобождения event lock оба ответа были **200**, а `SELECT DISTINCT embedding_dimensions` вернул **[2, 3]**.

| Конкурирующие партии | Baseline | После |
|---|---|---|
| Одна коллекция, разные размерности | 200/200, индекс [2, 3] | 200/400, индекс [2]; второй job/document/индекс и события не изменены |
| Одна коллекция, одинаковые размерности | 200/200, индекс [2] | 200/200, оба документа ready |
| Разные коллекции, разные размерности | 200/200 | 200/200, коллекции сохраняют свои [2] и [3] |

В тесте первый запрос блокируется на реальном INSERT события. Второй до освобождения блокировки обязан дойти до event INSERT на baseline либо до collection lock после исправления. Контроль разных коллекций требует дойти именно до собственного event INSERT, пока первый всё ещё ждёт: глобальная сериализация не прошла бы эту проверку. Отклонённый несовместимый batch затем корректно завершается с совместимым вектором на том же lease; сохранение частичного индекса не требуется исправлять вручную.

## Изменение

В PostgreSQL completion сначала получает `FOR NO KEY UPDATE` строки коллекции и только затем прежний `FOR UPDATE` текущего job. После ожидания снова проверяется владелец и свежий deadline, а отдельный SELECT размерности видит committed результат предшествующей партии. Блокировка сохраняется до общего commit/rollback индекса, document, job и события. SQLite уже сериализует запись через существующий `BEGIN IMMEDIATE`; его контракт не меняется.

Порядок parent перед child совпадает с направлением каскадного удаления коллекции. Выбранный режим блокировки конфликтует с другим completion этой коллекции, но совместим с FK `KEY SHARE` для вставки дочерних данных. Это опирается на [матрицу row locks и рекомендации порядка захвата](https://www.postgresql.org/docs/17/explicit-locking.html) и [новый snapshot каждого statement в Read Committed](https://www.postgresql.org/docs/17/transaction-iso.html). Само по себе блокирование разных job не защищало общий размер коллекции; новый schema column или миграция для этой корректировки не нужны.

## Проверка

```sh
bash scripts/test-fleet-ha-postgres.sh \
  --test-name-pattern='serializes embedding dimensions across jobs|embedding completion rechecks|embedding complete |embedding maintenance stays'
node --import tsx --test apps/coordinator/test/embedding-lease-lifecycle.test.ts \
  apps/coordinator/test/knowledge.test.ts \
  apps/coordinator/test/knowledge-retrieval-validation.test.ts
npm run test:coordinator
bash scripts/test-fleet-ha-postgres.sh
npm run typecheck
npm run docs:check
```

Baseline дал **1 ожидаемый FAIL и 2 проходящих контроля**; исправленная исходная тройка — **3/3 PASS**. Дополнительный сценарий держит строку коллекции до expiry: completion отвечает 400, не сохраняет ни вектора, ни события, а новая аренда успешно завершает индекс. Регрессия: целевые SQLite **23/23**, PostgreSQL **9/9**, полный coordinator **284/284**, полный PostgreSQL **62/62**; typecheck, docs-check и architecture audit — pass. Включены прежние смена владельца, timely renewal, поздний event INSERT и maintenance второй реплики. [Итоговые результаты, логи и SHA](./evidence/2026-09-27/embedding-dimension-concurrency/checks.json) фиксируют полный набор.

Это проверка протокола на синтетических векторах длины 2 и 3, без model inference, оценки recall и производственного SLO. Она предотвращает воспроизведённую гонку новых completion, но не исправляет уже существующий смешанный индекс и не доказывает отсутствие всех возможных deadlocks. Прежняя временная граница до запроса COMMIT сохраняется.

Следующий инженерный gate — измерить HTTP-профиль embedding completion и renewal после усиления SQL-проверок: конкурентность, задержки health и фактический maintenance, с одинаковыми и независимыми коллекциями. Корректность и ограниченность накопленных запросов должны проверяться вместе с временем ответа; синтетический профиль останется отдельным от qualification модели.
