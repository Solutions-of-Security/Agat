# Срок действия аренды при retrieval

Дата: **27.09.2026**. Перед [изоляцией HTTP-поиска](./retrieval-http-responsiveness.md) проверен допуск по stage lease. На исходном коде `981c17588b8a383e31478fd898e21a2e559551a0` воспроизведён дефект: `renewLease` уже возвращал `false` для истёкшей аренды, но `searchKnowledge` выполнял поиск, пока фоновое обслуживание не меняло `running`.

Первый SELECT поиска теперь требует `lease_expires_at > nowIso()` вместе с совпадением узла, lease ID и статусом `running`. Ошибка возникает до чтения embeddings и создания retrieval/event. Это проверка действительности **при входе в retrieval**; повторная проверка перед commit и отмена уже начатого поиска этим изменением не реализованы.

## Проверка

SQLite-тест до исправления завершился `Missing expected exception`. После исправления проверены отказ до maintenance, отсутствие источников и успешного события, повторный отказ старому lease после переназначения, успешный поиск по новому lease с первым маркером `K1`.

PostgreSQL 17.6 проверен на двух экземплярах `AgatStore`: одна реплика истекает lease в БД, другая отклоняет поиск до maintenance; после переназначения старый lease отклонён, новый сохраняет ровно один retrieval и событие. Первый запуск нового теста остановился при подготовке embeddings: общий последовательный scheduler был занят предыдущим сценарием. Тест явно включает параллельный scheduler, затем весь набор повторён успешно. Продуктовое исправление из-за ошибки fixture не менялось.

- Node.js 24.14.0, coordinator: **235/235**.
- PostgreSQL Fleet/HA: **12/12**, включая RLS, cursor snapshots, rollback и широкий корпус.
- Типизация и сборка всех приложений: pass.

[Логи и SHA исходников](./evidence/2026-09-27/retrieval-lease-validity/checks.json) сохраняют исходный отказ, ошибку fixture и успешный повтор. Это функциональная проверка; она не измеряет отзывчивость и не заменяет будущие проверки истечения аренды в очереди, отзыва credentials и фоновых записей при изолированном поиске.

## Воспроизведение

```sh
node --import tsx --test --test-name-pattern='expired retrieval leases' apps/coordinator/test/knowledge-retrieval-validation.test.ts
npm run test:coordinator
npm run fleet:test-ha-postgres
npm run typecheck
npm run build
```

PostgreSQL-скрипт создаёт отдельный контейнер с loopback-портом и удаляет его при выходе. Рабочая база и production deployment не используются.
