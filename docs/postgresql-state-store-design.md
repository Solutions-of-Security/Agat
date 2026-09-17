# PostgreSQL state-store: реализация и дальнейший переход

## Статус

PostgreSQL adapter активирован в релизе 1.7. Post-1.7 production-readiness этапы добавили canonical offline migration, DDL-free Job boundary, managed-resilience contract и текущую schema v25 с S3 artifact outbox, region-loss marker, worker provenance/runtime attestation и SIEM DLQ/retention: coordinator создаёт bounded `pg` pools, но только валидирует schema/admission/activation contract; DDL выполняет отдельная Job под owner role.

Реализация закрывает release contract Fleet/HA, но не все production gates прежнего проекта. Полная модель, риски и acceptance evidence: [Fleet и HA 1.7](./fleet-ha-1.7.md); решение по cell/data authority: [ADR-017](./adr-017-fleet-ha-cell.md).

## Реализованный plateau

- `SyncDatabase` отделяет существующий domain store от конкретного SQLite API.
- `PostgresDatabaseSync` использует отдельный worker thread и `pg` 8.23.0, чтобы сохранить совместимость синхронного `AgatStore`.
- Migration, runtime system и tenant identities разделены. Deployment получает только runtime+tenant URLs; migration URL scoped отдельной Job.
- Каждая replica имеет bounded pools, connect/idle/statement timeouts и transaction client pinning.
- SQLite placeholders/небольшой dialect subset нормализуются в bridge; PostgreSQL schema v25 устанавливает fleet objects, RLS, audit/storage triggers, catalog manifest, admission/cell markers, tenant-inaccessible DR/activation ledger, attestation challenges и SIEM DLQ/retention state.
- Stage, embedding и audit claims используют `FOR UPDATE SKIP LOCKED`.
- Project row lock сериализует quota check и run creation; optimistic revisions защищают policy/rollout updates.
- Artifact metadata/retention/delete intent находятся в PostgreSQL; новые bytes — в versioned S3-compatible store, local filesystem является только проверяемым download cache. Legacy BYTEA поддержан только для bounded backfill.
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
- в historical 1.7 migrations выполнялись startup role; post-1.7 schema v21 вынесла их в Job и DDL-free runtime;
- Temporal notification outbox не добавлен: существующие idempotent Updates/Signals остаются отдельным durable boundary.

Оставшиеся расхождения записаны как `RISK-1703/1704` и являются production scale gates. `RISK-1702` закрыт [отдельной Job/runtime boundary](./postgresql-migration-job-runtime-role.md).

## Post-1.7 readiness и текущие gates

### Offline migration — готово

Canonical exporter/importer реализован и:

1. работает только при остановленных writes;
2. сохраняет исходный SQLite backup;
3. загружает tables в dependency order;
4. сверяет counts, stable IDs, immutable JSON hashes и foreign keys;
5. не переносит expired leases как активные;
6. формирует подписываемый reconciliation report.

Dual-write не используется. Contract и evidence: [Offline SQLite → PostgreSQL migration](./sqlite-postgresql-migration.md).

### Разделение migration/runtime roles — готово

Отдельная migration Job владеет DDL/advisory lock; runtime сохраняет только bounded DML/BYPASSRLS и не имеет ownership/schema CREATE. Catalog drift, active replicas и незавершённый connection admission блокируют startup. Tenant role остаётся RLS-only.

### Managed PostgreSQL resilience — готово

Provider-neutral gate проверяет multi-AZ, synchronous standby, automatic failover, private TLS endpoint, PITR retention/freshness и два distinct SLO approver. Текущая schema v25 сохраняет checkpoints и HMAC reports, доказывающие actual failover и exact PITR boundary; полный contract и per-cluster qualification описаны в [Managed PostgreSQL](./managed-postgresql-resilience.md).

### Async repositories и capacity

Worker-thread bridge блокирует event loop одной replica на время sync DB call. Connection budget и read-only p99 admission уже исполняются перед rollout; до высокой нагрузки всё ещё нужны async repositories, pool acquisition metrics и load test по целевому project/run mix.

### Artifact object store

Schema v23 ввела S3-compatible bytes authority с transactional PostgreSQL metadata/outbox, exact-version delete, retention/legal hold и bounded reconciliation; contract сохраняется в текущей v25. Полный contract: [S3 Artifact Store](./s3-artifact-store-lifecycle.md).

### Residency-aware region-loss — готово

Schema v24 связала isolated PostgreSQL restore, version-preserving S3 replica и Temporal recovery через sealed evidence, whole-cell policy и exact database snapshots; contract сохраняется в v25. Monotonic `write_epoch`/activation marker блокирует stale source runtime, а issued runtime challenges при активации инвалидируются; failback является новой обратной активацией. Полный contract: [Region-loss DR](./region-loss-dr.md).

### Worker supply-chain/runtime attestation — готово

Schema v25 хранит отдельно подписанный OCI/SLSA provenance admission, hash-only one-time runtime challenges и bounded workload-attestation claims. Release/provenance/runtime roots разделены; expiry, revoke, root removal, replay или binding mismatch закрывают authentication/lease. Полный contract: [Worker supply-chain attestation](./worker-supply-chain-attestation.md).

### SIEM conformance, retention и DLQ — готово

Schema v25 требует exact batch acknowledgement, ограничивает retry и атомарно переводит terminal event в redacted retained DLQ. Tenant role не видит operational global tables; auditor читает только свой project, replay/resolve доступны admin и фиксируются в audit. Operational retention не удаляет authoritative `events`. Полный contract: [SIEM retention/DLQ](./siem-retention-dlq.md).

### Production database qualification

Local PostgreSQL Deployment заменяется managed/multi-AZ endpoint. Исполняемый gate, physical rehearsal и objectives готовы; каждый deployed cluster обязан отдельно предъявить fresh provider evidence, повторный admission и passing failover/restore/SLO reports. Repository test не выдаётся за cloud qualification.

## Acceptance matrix

| Проверка | 1.7 | Production gate |
|---|---|---|
| Две coordinator replicas, shared committed state | выполнено integration test | soak/failure injection |
| Single lease и exact project quotas | выполнено | target-load test |
| RLS/cross-project/global mutation deny | выполнено | property/fuzz test и security review |
| Cross-replica artifact download | выполнено | object-store lifecycle/DR |
| SIEM disjoint claim/retry/redaction | exact ack, bounded retry, retained DLQ и retention выполнены | qualification конкретного sink/dedupe/retention policy |
| SQLite→PostgreSQL reconciliation | canonical apply/verify/rehearsal готов | production-sized rehearsal |
| Backup restore | physical named-point PITR rehearsal выполнен | timed isolated managed restore report per cell |
| Multi-AZ failover | provider gate + physical promotion rehearsal выполнены | managed operation report per cell |
| DDL-free runtime role | schema v25 Job, manifest/admission и negative E2E готовы | managed-IaC role provisioning |
| Region-loss activation | sealed whole-cell transition и runtime epoch gate готовы | provider fencing/replication rehearsal per cell |
| Worker provenance/runtime attestation | separate roots, challenge/replay/expiry/refresh gates готовы | pinned CI и local attestor qualification per cell |

## Наблюдаемость

Release snapshot уже показывает driver, cell, replicas, queue counts и audit outbox lag. Production telemetry должна дополнительно включать pool saturation/acquisition latency, statement/transaction duration, deadlocks, DB/WAL size, replication lag, backup age, restore verification age и RLS denials. SQL parameters, credentials URLs и encrypted payloads в logs запрещены.
