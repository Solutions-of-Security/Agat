# PostgreSQL state-store: реализация и дальнейший переход

## Статус

PostgreSQL adapter активирован в релизе 1.7. `AGAT_STATE_STORE_DRIVER=postgresql` больше не является заглушкой: coordinator создаёт bounded `pg` pools, сериализует schema migration advisory lock, поддерживает несколько replicas и использует PostgreSQL как единственный source of truth.

Реализация закрывает release contract Fleet/HA, но не все production gates прежнего проекта. Полная модель, риски и acceptance evidence: [Fleet и HA 1.7](./fleet-ha-1.7.md); решение по cell/data authority: [ADR-017](./adr-017-fleet-ha-cell.md).

## Реализованный plateau

- `SyncDatabase` отделяет существующий domain store от конкретного SQLite API.
- `PostgresDatabaseSync` использует отдельный worker thread и `pg` 8.23.0, чтобы сохранить совместимость синхронного `AgatStore`.
- System и tenant connection URLs обязательны, имеют разные roles и указывают на одну database cell.
- Каждая replica имеет bounded pools, connect/idle/statement timeouts и transaction client pinning.
- SQLite placeholders/небольшой dialect subset нормализуются в bridge; migration 20 устанавливает fleet schema, indexes, RLS и audit trigger.
- Stage, embedding и audit claims используют `FOR UPDATE SKIP LOCKED`.
- Project row lock сериализует quota check и run creation; optimistic revisions защищают policy/rollout updates.
- Artifact metadata и bytes находятся в PostgreSQL; local filesystem является только проверяемым download cache.
- SQLite остаётся поддерживаемым one-replica developer backend. Dual-write отсутствует.

## Транзакционные правила

### Выдача stage lease

Одна transaction блокирует project/run/stage candidates, проверяет queue, residency, project running quota, node capabilities и rollout eligibility, затем создаёт lease. `SKIP LOCKED` позволяет другой replica взять следующий candidate, не выдавая тот же stage.

### Queue quota

Создание run сначала выполняет `SELECT project ... FOR UPDATE`, затем считает non-terminal runs и вставляет новый run в той же transaction. Это делает `maxQueuedTasks` точным между replicas, а не eventually consistent counter.

### Recovery

Expired lease меняется только при совпадающем lease ID/generation/state. Повторный completion старого lease отклоняется. Maintenance loops могут выполняться на каждой replica, потому что mutations условные и транзакционные.

### RLS

Tenant query выполняется на отдельной non-BYPASSRLS role. Transaction-local `agat.current_project_id` используется direct и parent-derived policies; таблицы имеют `FORCE ROW LEVEL SECURITY`. System role выполняет scheduler/worker/admin paths явно, а не наследуется от предыдущего HTTP request.

### Audit outbox

Trigger создаёт outbox row вместе с event commit. Exporter claim/complete используют lease owner и idempotency key; timeout возвращает row в pending с backoff.

## Что изменилось относительно проекта 1.0

Первоначальный проект предполагал до активации driver полностью выделить async repository interfaces и вынести artifact bytes в object store. Для 1.7 выбран более узкий совместимый plateau:

- repository boundary представлен общим sync adapter, а не набором async repositories;
- artifact bytes временно перенесены в PostgreSQL, чтобы реально обеспечить cross-replica availability;
- migrations пока выполняются coordinator startup под advisory lock, а не отдельной Job;
- Temporal notification outbox не добавлен: существующие idempotent Updates/Signals остаются отдельным durable boundary.

Эти расхождения не скрываются: они записаны как `RISK-1702/1703/1704` и являются production scale gates.

## Оставшиеся этапы

### Offline migration

Нужен canonical SQLite exporter/importer, который:

1. работает только при остановленных writes;
2. сохраняет исходный SQLite backup;
3. загружает tables в dependency order;
4. сверяет counts, stable IDs, immutable JSON hashes и foreign keys;
5. не переносит expired leases как активные;
6. формирует подписываемый reconciliation report.

Dual-write не планируется.

### Разделение migration/runtime roles

Отдельная migration Job должна владеть DDL и advisory lock. Runtime system role должна сохранить DML/BYPASSRLS для trusted scheduler/admin paths, но лишиться schema alteration. Tenant role остаётся RLS-only.

### Async repositories и capacity

Worker-thread bridge блокирует event loop одной replica на время sync DB call. До высокой нагрузки нужны async repositories, pool acquisition metrics, query budgets и load test по целевому project/run mix.

### Artifact object store

PostgreSQL `BYTEA` обеспечивает correctness plateau, но крупные artifacts увеличивают DB/WAL/backup. Следующий этап вводит S3-compatible bytes authority с transactionally consistent metadata/outbox и lifecycle policy.

### Production database

Local PostgreSQL Deployment заменяется managed/multi-AZ endpoint. Обязательны TLS `verify-full`, connection admission, replication/WAL/backup monitoring, PITR и фактические restore/failover exercises с утверждёнными RPO/RTO.

## Acceptance matrix

| Проверка | 1.7 | Production gate |
|---|---|---|
| Две coordinator replicas, shared committed state | выполнено integration test | soak/failure injection |
| Single lease и exact project quotas | выполнено | target-load test |
| RLS/cross-project/global mutation deny | выполнено | property/fuzz test и security review |
| Cross-replica artifact download | выполнено | object-store lifecycle/DR |
| SIEM disjoint claim/retry/redaction | выполнено | sink conformance, retention/DLQ |
| SQLite→PostgreSQL reconciliation | manual procedure only | canonical migrator required |
| Backup restore | local DB не является evidence | timed clean-environment restore required |
| Multi-AZ failover | не входит | required |
| DDL-free runtime role | не выполнено | required |

## Наблюдаемость

Release snapshot уже показывает driver, cell, replicas, queue counts и audit outbox lag. Production telemetry должна дополнительно включать pool saturation/acquisition latency, statement/transaction duration, deadlocks, DB/WAL size, replication lag, backup age, restore verification age и RLS denials. SQL parameters, credentials URLs и encrypted payloads в logs запрещены.
