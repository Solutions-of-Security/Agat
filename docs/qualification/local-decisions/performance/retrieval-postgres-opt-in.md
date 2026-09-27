# Операторское включение изолированного PostgreSQL retrieval

Дата: **27.09.2026**. Продолжение [измерений исполнителя](./retrieval-isolated-executor.md) и [проверки lease перед записью](./retrieval-lease-locking.md). Теперь обычный coordinator поддерживает явный `AGAT_KNOWLEDGE_SEARCH_EXECUTION=isolated` для PostgreSQL; default остаётся `sync`. [Параметры, HTTP-отказы и восстановление](../../../local-rag-and-memory.md) описаны в эксплуатационной документации.

## Решение и границы

В main создаётся второй владелец AgatStore с теми же сериализуемыми security, region, artifact и PostgreSQL настройками. Telemetry-объект основного потока не передаётся; durable retrieval/event сохраняются обычной транзакцией. Worker не регистрирует отдельную coordinator replica и не запускает второй maintenance. До начала HTTP listen он должен подтвердить готовность. При штатной остановке закрываются исполнитель и его pools, затем основной store.

Новый pool не добавляется поверх уже допущенного бюджета: на каждую из двух ролей main получает `poolMax - 1`, executor — `1`; минимум для isolated — 2. Формула admission и полный configured poolMax сохраняются. Это следует принципу [node-postgres: считать все pools всех экземпляров и оставлять резерв](https://node-postgres.com/guides/pool-sizing). Из него не следует допустимость произвольного числа replica: rollout capacity по-прежнему должна входить в deployment budget.

Health возвращает 503 только после закрытия admission исполнителя, включая deadline активного запроса или его аварийный выход. Заполненная рабочая очередь не меняет accepting. В существующем Kubernetes manifest readiness и liveness используют этот endpoint; [Kubernetes различает снятие с обслуживания и restart](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/). Здесь остановленный исполнитель сам не восстанавливается, поэтому restart устраняет отказ; обычная занятость его не вызывает. Внешние supervisors и Compose требуют своего порядка восстановления. Автоматического повтора транзакций нет, исход активного запроса после timeout может быть неизвестен.

SQLite opt-in отклоняется из-за измеренной конкуренции single-writer с main maintenance. Полная квалификация production S3, непрерывная нагрузка всего main и внешние SLO остаются отдельными этапами. Этот этап не включает новый deployment, ANN, обучение или допуск автоматических решений.

## Проверки

Конфигурационные тесты проверяют default, PostgreSQL-only, минимум pool, строгие границы очереди/deadline и сохранение admission budget для всех poolMax 2–32. HTTP-тест проверяет health до и после закрытия исполнителя.

Интеграционный сценарий запускает настоящий собранный `dist/server.js` с его JS-worker на одноразовом PostgreSQL и с обычными main timers. Он проверяет:

- health 200 с фактическим режимом и свободной очередью;
- четыре наблюдаемых соединения: main и retrieval для system/tenant, в сумме по два на роль при poolMax=2;
- успешный HTTP-поиск и видимые другой реплике sources с `K1`;
- deadline при удержании run row lock, HTTP 504, затем health 503 и отказ нового поиска 503;
- штатный SIGTERM, exit 0 и отсутствие всех четырёх соединений в `pg_stat_activity`.

Проверка количества sessions использует отдельные application_name, а допустимый верхний budget дополнительно проверяет конфигурационный тест. Это не нагрузочный admission-probe production endpoint. Deadline-сценарий намеренно не требует отсутствия commit: такой гарантии у принудительного завершения нет.

Результаты и SHA исходников/логов: [checks.json](./evidence/2026-09-27/retrieval-postgres-opt-in/checks.json). Coordinator **246/246**, PostgreSQL **18/18**; отдельно сохранён первоначальный полный прогон через TypeScript entry point, финальный полный прогон проверяет собранные JS-файлы. Типизация всех приложений и probe, сборка, Compose и Kubernetes render прошли. Docs: **12 Node + 143 Python**, 1441 локальная ссылка; архитектурный аудит PASS без замечаний. Первый docs-запуск остановился на запрете loopback-сокета в sandbox, повтор с доступом к сокету прошёл без изменения теста. Следующий этап — перегрузка и восстановление настоящего coordinator с одновременным обслуживанием, включая проверку отсутствия скрытого replay после restart.

```sh
npm run test:coordinator
npm run fleet:test-ha-postgres
npm run typecheck
npm run build
npm run docs:check
```
