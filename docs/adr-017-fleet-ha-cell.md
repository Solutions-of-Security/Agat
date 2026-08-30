# ADR-017: PostgreSQL authority на residency-scoped HA-cell

## Статус

Принято для релиза 1.7 и local/staging deployment. Последующие production-readiness этапы реализовали отдельную migration Job, managed PostgreSQL evidence gate и S3-compatible artifact authority; актуальные operational gates находятся в [roadmap](./roadmap.md), [managed PostgreSQL runbook](./managed-postgresql-resilience.md) и [ADR-018](./adr-018-s3-artifact-authority.md).

ADR-017 фиксирует исторический Fleet 1.7 plateau из [проекта PostgreSQL adapter](./postgresql-state-store-design.md). Его временное хранение artifact bytes в `BYTEA` заменено schema v23 и [ADR-018](./adr-018-s3-artifact-authority.md); synchronous repository bridge пока остаётся.

## Контекст

SQLite обеспечивал простую локальную durability, но требовал одного coordinator writer. Fleet stage требует несколько API/scheduler replicas, точные project quotas, региональное размещение, hard tenant defense и внешний audit delivery без двойного claim.

Основные ограничения:

- Temporal владеет workflow history, но не каталогами, queues, leases, approvals и audit Agat;
- project payload не должен пересекать residency boundary;
- coordinator должен переживать потерю replica без dual-write и без singleton scheduler leader;
- существующий store API синхронный, а полный async repository refactor не помещается в один release plateau;
- local Docker Desktop не должен называться production HA.

## Решение

1. Одна HA-cell соответствует одной паре `region/residencyDomain` и одной PostgreSQL database authority.
2. PostgreSQL является единственным source of truth в HA mode; dual-write с SQLite запрещён.
3. Business queue claims используют row locks и `FOR UPDATE SKIP LOCKED`; advisory lock применяется только к migration bootstrap.
4. Все tenant-owned tables получают FORCE RLS. HTTP project paths используют отдельную non-BYPASSRLS role с transaction-local project setting; explicit scheduler/admin paths используют system role.
5. Project quota хранится на project aggregate и проверяется под row lock. Online cross-cell relocation отклоняется.
6. Worker release registry проверяет canonical Ed25519 manifest; deterministic rollout bucket допускает только target/fallback release нужного ring.
7. Audit event атомарно создаёт outbox row, а exporters используют leased `SKIP LOCKED` batches и at-least-once HTTPS delivery.
8. Artifact bytes в release plateau хранятся в PostgreSQL, чтобы replicas не зависели от shared RWO filesystem.
9. Синхронный `AgatStore` временно обращается к `pg` через worker-thread bridge с bounded pools/timeouts; async repositories остаются strategic TO-BE.

Kill criteria решения: если load test показывает неприемлемую event-loop latency/DB WAL amplification, до production scale-out обязательны async repository и object-store plateaus; увеличение replica count не считается исправлением этих причин.

## Альтернативы

### Вариант A: shared SQLite volume

Отклонён: filesystem locking/RWO mount не создают корректный multi-writer HA и сохраняют single failure domain. Может оставаться developer mode с одной replica.

### Вариант B: central global PostgreSQL для всех регионов

Отклонён: упрощает операции, но переносит payload через residency boundaries и увеличивает region-loss blast radius. Может быть допустим только для deployment без residency требований после отдельного data/legal decision.

### Вариант C: distributed SQL active-active

Отклонён для 1.7: добавляет conflict/latency/operations complexity до подтверждённой multi-region потребности. Может стать валидным после measured cross-region RTO, понятной conflict model и vendor exit plan.

### Вариант D: отдельная database/schema на project

Отклонён как default: сильнее blast-radius isolation, но резко увеличивает migration/pool/backup cardinality. Остаётся вариантом для высокорисковых tenants или dedicated cells.

### Вариант E: немедленный полный async repository refactor

Отложен: стратегически предпочтителен, но слишком велик для совместимого 1.7 release. Становится обязательным при достижении kill criteria synchronous bridge.

## Последствия

Положительные:

- coordinator replicas независимы от локального state file;
- queue/quota/outbox claims масштабируются без singleton leader;
- RLS ловит cross-project query даже после application authorization ошибки;
- residency и release admission становятся data-plane invariants;
- audit exporter восстанавливается после replica/sink outage.

Отрицательные:

- system credential имеет cell-wide blast radius;
- startup role пока владеет DDL;
- PostgreSQL database/WAL принимает artifact bytes;
- existing SQLite state требует offline migration;
- local PostgreSQL pod остаётся SPOF;
- synchronous bridge ограничивает throughput одной replica.

## Проверка и критерии приёмки

- PostgreSQL integration test запускает две stores и доказывает shared state, exact quotas, RLS/global privilege deny, disjoint SIEM claims и cross-replica artifact download.
- Unit tests доказывают Ed25519 verification, deterministic fallback rollout и immediate revoke.
- Full coordinator/worker/Temporal suites остаются зелёными на SQLite compatibility path.
- `kubectl kustomize` показывает две coordinator replicas, rolling update, PDB, отдельный PostgreSQL service/PVC/Secret refs и ingress NetworkPolicy.
- Production status не меняется с conditional на approved без restore/failover/load-test evidence и закрытия блокирующих рисков из [Fleet и HA 1.7](./fleet-ha-1.7.md#риски-и-открытые-вопросы).
