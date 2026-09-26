# Изоляция полного retrieval

Дата протокола: **27.09.2026**. Статус: реализован исполнитель для квалификации; измерения с фоновым обслуживанием ещё не завершены. Обычный `main()` coordinator его не включает. Исходный [HTTP-опыт](./retrieval-http-responsiveness.md) показал задержки health при синхронном поиске, а [проверка lease](./retrieval-lease-validity.md) закрыла допуск уже истёкшей аренды.

## Реализация и границы

Один долгоживущий Node worker владеет отдельным `AgatStore` и выполняет всю синхронную транзакцию поиска. HTTP ожидает Promise; основной store не держит открытую транзакцию через `await`. Перед выполнением повторно проверяются token и идентичность узла, затем обычный `searchKnowledge` проверяет lease, разрешённые коллекции и индекс. Переданные политики доверия сохраняются; несериализуемые зависимости явно отклоняются. Worker присоединяется с `schemaOnly`, без seed/demo/регистрации второй coordinator-реплики.

Лимит квалификационного профиля — четыре незавершённых запроса, включая один активный. Остальные получают HTTP 429. Вход копируется при admission. Deadline включает очередь; истечение активного запроса закрывает исполнитель и не вызывает replay. Таймаут или аварийная остановка не доказывают отсутствие commit. Штатное закрытие отклоняет очередь и дожидается активной операции, принудительная остановка ограничена таймером. Отзыв credentials во время уже начатой транзакции этим механизмом не отменяет поиск.

Это следует рекомендациям [Node.js о пуле workers и AsyncResource](https://nodejs.org/docs/latest-v24.x/api/worker_threads.html). Однако [SQLite WAL допускает только одного писателя](https://sqlite.org/wal.html), а [PostgreSQL удерживает конфликтующие row locks до конца транзакции](https://www.postgresql.org/docs/17/explicit-locking.html). Поэтому перенос scorer/SQL в другой поток сам по себе не доказывает отзывчивость остальных операций с БД.

## Проверка перед включением

Функциональные сценарии используют настоящий worker и БД: overlapping HTTP-запросы, стабильные `K1/K2`, переполнение admission, отзыв token и истечение lease после постановки в очередь, неизменность принятого запроса, rollback второй несовместимой query, закрытие/deadline без replay и сохранение signed-release policy. PostgreSQL дополнительно запускает два исполнителя на одном run и проверяет сериализацию маркеров и отзыв credentials.

Нагрузочный протокол сохраняет прежние 9716 синтетических 768D-кандидатов, старый точный winner, bursts 1/2/4 и независимый Python-клиент. Добавлены режимы `sync` / `isolated` и настоящее `store.maintenanceTick()` каждые 1000 мс. Тики, их длительности и ошибки сохраняются по фазам. Idle-фаза обязана содержать maintenance. Два режима необходимо измерить на одном commit/runtime; общая нагрузка машины остаётся ограничением сравнения.

```sh
node --import tsx --test apps/coordinator/test/knowledge-search-executor.test.ts
npm run fleet:test-ha-postgres
python3 scripts/run-rag-http-probe.py --execution-mode sync --maintenance-interval-ms 1000 --evidence-dir docs/qualification/local-decisions/performance/evidence/new-sync
python3 scripts/run-rag-http-probe.py --execution-mode isolated --maintenance-interval-ms 1000 --evidence-dir docs/qualification/local-decisions/performance/evidence/new-isolated
python3 scripts/verify-rag-http-probe.py --evidence-dir docs/qualification/local-decisions/performance/evidence/new-isolated
```

Квалификационный путь пока передаётся только явно через `createCoordinatorServer`. Производственная конфигурация, rollout и автоматическое восстановление после аварии требуют отдельного решения по результатам измерений. Длительная нагрузка, реальные бизнес-коннекторы и независимая семантическая оценка остаются открытыми.
