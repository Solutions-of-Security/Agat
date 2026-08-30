# PostgreSQL migration Job и DDL-free runtime

## Решение и граница

PostgreSQL schema v21 применяется только отдельной migration Job. Coordinator runtime больше не выполняет `CREATE`, `ALTER`, policy/trigger installation или grants при startup: он подключается DDL-free system role, проверяет immutable catalog manifest, privilege boundary и успешный connection admission marker, затем выполняет только application DML.

Этот этап не объявляет локальный PostgreSQL multi-AZ и не задаёт production RPO/RTO. Он закрывает deployment privilege и connection-capacity gates; topology, PITR и restore/failover относятся к следующему этапу.

## AS-IS

В 1.7 каждая coordinator replica на startup брала advisory lock и запускала idempotent DDL под `agat_system`. Роль одновременно владела schema, имела `CREATE`, `BYPASSRLS` и application DML. Lock сериализовал bootstrap, но compromise runtime credential давал ownership/DDL, а replica×pool demand не сверялся с database/role limits до rollout.

## Release TO-BE

| Роль / identity | Где доступна | Разрешено | Запрещено |
|---|---|---|---|
| database admin | versioned role-bootstrap Job | создать/изменить три роли, передать ownership | coordinator Deployment, workers |
| `agat_migrator` | schema Job и offline state migrator | schema ownership, `CREATE`, `BYPASSRLS`, transactional DDL/backfill/grants | runtime pods; `SUPERUSER`, `CREATEDB`, `CREATEROLE`, replication, database `CREATE` |
| `agat_system` | coordinator и admission probe | bounded DML/sequence use, `BYPASSRLS`, read-only schema marker | schema/database/temp `CREATE`, object ownership, `TRUNCATE`, `REFERENCES`, `TRIGGER`, marker mutation |
| `agat_tenant` | request-scoped coordinator pool и admission probe | exact RLS-governed project DML, three global read-only tables | `BYPASSRLS`, elevated membership, schema metadata и global mutation |

Owner rights в PostgreSQL нельзя отнять обычным `REVOKE`, поэтому schema и все objects принадлежат `agat_migrator`, а не runtime role. `agat_system` остаётся trusted cross-project system identity для scheduler/admin paths; HTTP project paths продолжают использовать отдельную FORCE RLS tenant role.

## Source register

| Источник | Версия / дата проверки | Claim scope |
|---|---|---|
| `apps/coordinator/src/postgres-schema-migrator.ts` | schema v21 | exclusive migration, runtime validation, admission finalize |
| `apps/coordinator/src/postgres-admission.ts` | report schema v1 | connection budget и bounded read-only load probe |
| `apps/coordinator/src/postgres-worker.ts` | pg 8.23 bridge | фактическая role/ownership validation до первого query |
| `deploy/k8s/docker-desktop/coordinator-postgres-migration.yaml` | Job v21 | admin bootstrap и schema/admission Jobs |
| [PostgreSQL 17 role attributes](https://www.postgresql.org/docs/17/role-attributes.html) | проверено 2026-08-30 | `BYPASSRLS`, elevated attributes и `CONNECTION LIMIT` |
| [PostgreSQL 17 privileges](https://www.postgresql.org/docs/17/ddl-priv.html) | проверено 2026-08-30 | ownership, `CREATE`, DML и default `PUBLIC` privileges |
| [PostgreSQL 17 connections](https://www.postgresql.org/docs/17/runtime-config-connection.html) | проверено 2026-08-30 | `max_connections`, reserved и superuser-reserved slots |
| [PostgreSQL 17 `REASSIGN OWNED`](https://www.postgresql.org/docs/17/sql-reassign-owned.html) | проверено 2026-08-30 | versioned ownership transfer в текущей database |

## Выбор

| Вариант | Security boundary | Rollout | Решение |
|---|---|---|---|
| оставить startup DDL с advisory lock | runtime credential остаётся owner | просто, но каждая replica может менять schema | отклонён |
| внешний generic migration framework | можно разделить роли | отдельный rewrite текущего SQLite-compatible DDL и backfills | допустим после v21 при росте migration chain |
| отдельная Job поверх того же versioned schema builder | migrator owner, runtime только validate/DML | минимальный переход, единый schema contract | выбран |

Решение пересматривается, если следующую migration нельзя безопасно сделать идемпотентной в одной transaction, если нужен online expand/contract между несовместимыми releases или synchronous bridge становится bottleneck migration window. Тогда DDL выделяется в standalone ordered migration package без возврата DDL в runtime.

## Transaction и schema contract

1. Role-bootstrap Job выполняет role changes, ownership transfer, database/schema revokes и grants в одной transaction.
2. Schema Job берёт отдельный session advisory gate на весь workflow и проверяет отсутствие `ready` coordinator с heartbeat моложе трёх минут.
3. `agat_migrator` берёт `pg_advisory_xact_lock(867530901)` и применяет весь DDL/backfill/grant contract в одной transaction.
4. `agat_schema_migrations` получает version `21`, contract ID и SHA-256 catalog manifest; admission становится `pending`.
5. Новый runtime connection доказывает отсутствие `CREATE`/ownership/elevated membership, exact DML boundary, validated constraints и совпадение catalog manifest.
6. Admission probe проверяет capacity и выполняет read-only load; только успешный report hash переводит marker в `passed`.
7. Финальная runtime validation перечитывает marker. Coordinator startup и Kubernetes init container fail-closed до `passed`.

Catalog manifest включает columns/defaults, constraints/validation, indexes, non-internal triggers, RLS flags/policies и две application functions. Out-of-band DDL меняет SHA-256 и блокирует startup. Marker недоступен tenant role и доступен runtime только на `SELECT`.

## Connection admission

Для двух pools на replica план вычисляется так:

```text
usable = max_connections - superuser_reserved_connections - reserved_connections
admitted = floor(usable × budgetPercent / 100)
available = admitted - currentClientBackends - externalReserve
planned = expectedReplicas × poolMax × 2
```

Gate требует `planned <= available`, а каждый role `CONNECTION LIMIT` должен вместить `expectedReplicas × poolMax`. Роли Agat не могут наследовать `pg_use_reserved_connections`: эти slots остаются emergency reserve.

После budget check probe одновременно открывает bounded runtime/tenant connections и выполняет `SELECT 1` до deadline. Успех требует zero connect/query errors, minimum operation count и p99 не выше configured threshold. Один локальный результат не является production SLO: test повторяют на managed endpoint с production-sized replica/pool parameters.

Основные параметры:

| Environment | Default | Contract |
|---|---:|---|
| `AGAT_POSTGRES_EXPECTED_REPLICAS` | `2` | planned coordinator replicas |
| `AGAT_POSTGRES_POOL_MAX` | `4` | max connections каждого из двух pools на replica |
| `AGAT_POSTGRES_CONNECTION_BUDGET_PERCENT` | `80` | доля non-reserved slots, допустимая для gate |
| `AGAT_POSTGRES_EXTERNAL_CONNECTION_RESERVE` | `5` | дополнительный operational headroom |
| `AGAT_POSTGRES_ADMISSION_CONCURRENCY` | planned, max 128 | фактический read-only probe |
| `AGAT_POSTGRES_ADMISSION_DURATION_MS` | `5000` | 0.5–300 seconds |
| `AGAT_POSTGRES_ADMISSION_P99_MS` | `250` | release-specific threshold, не SLO |
| `AGAT_POSTGRES_ADMISSION_MIN_OPERATIONS` | max(100, planned×10) | защита от пустого/слишком короткого probe |
| `AGAT_POSTGRES_ADMISSION_REPORT_PATH` | unset | optional atomic mode-0600 JSON evidence |

## Standalone runbook

Runtime replicas и writers должны быть остановлены. Три URLs указывают на одну database и используют разные password credentials; production endpoint требует TLS `verify-full`.

```bash
export AGAT_POSTGRES_MIGRATION_URL='postgresql://agat_migrator@db.example/agat'
export AGAT_POSTGRES_URL='postgresql://agat_system@db.example/agat'
export AGAT_POSTGRES_TENANT_URL='postgresql://agat_tenant@db.example/agat'
export AGAT_POSTGRES_SSL_MODE='verify-full'
export AGAT_POSTGRES_CA_CERT_PATH='/run/secrets/postgres-ca.pem'
export AGAT_POSTGRES_EXPECTED_REPLICAS='3'
export AGAT_POSTGRES_POOL_MAX='6'
export AGAT_POSTGRES_ADMISSION_REPORT_PATH='/secure/evidence/postgres-admission.json'

npm run fleet:migrate-schema
```

`fleet:migrate-schema` выполняет DDL, обе runtime validations, admission load и marker finalize как один release gate. `fleet:admit-postgres` повторяет read-only capacity/load probe, но сознательно не меняет schema marker; его используют для load rehearsal и новой replica topology.

## Kubernetes и Compose

`npm run k8s:up` для существующего контура сначала масштабирует coordinator до нуля, удаляет только versioned v21 Jobs, применяет manifests и ждёт:

1. `agat-postgres-role-bootstrap-v21` с admin Secret;
2. `agat-postgres-schema-v21` только с migration/runtime/tenant URLs;
3. schema v21 + `admission_status=passed` в coordinator init container;
4. затем обычный rollout coordinator replicas.

Migration/admin credentials не попадают в coordinator Deployment. PostgreSQL NetworkPolicy разрешает database traffic только coordinator и database-migration pods. Полный admission JSON находится в `/tmp/postgres-admission.json` Job pod и должен быть экспортирован в change evidence до следующего rerun; marker и Job log сохраняют его SHA-256.

Для Compose HA сначала остановите runtime и явно завершите one-shot services:

```bash
docker compose stop coordinator
docker compose --profile ha run --rm postgres-role-bootstrap
docker compose --profile ha run --rm postgres-migrate
AGAT_STATE_STORE_DRIVER=postgresql docker compose --profile ha up -d coordinator
```

## Failure semantics и recovery

| Failure | Состояние | Recovery |
|---|---|---|
| active coordinator heartbeat | DDL не начинается | scale runtime to zero, дождаться/зафиксировать stop, повторить Job |
| role-bootstrap statement | transaction rollback | исправить admin/ownership condition и rerun versioned Job |
| DDL/backfill/grant | schema transaction rollback | устранить ошибку; idempotent rerun под тем же advisory lock |
| catalog/grant validation | runtime не стартует | сравнить catalog с release, исправлять только migration role |
| connection budget | load не начинается, marker `pending` | уменьшить replicas/pools или увеличить подтверждённую capacity; rerun |
| load errors/p99/min operations | marker `pending` | сохранить report, диагностировать endpoint/pool/latency, rerun |
| process lost после DDL commit до marker pass | schema v21, admission `pending` | безопасно rerun всей Job; coordinator остаётся заблокирован |
| out-of-band DDL после pass | manifest mismatch | incident, diff catalog, утверждённая migration; не обновлять hash вручную |

## Операции и NFR

- **Availability:** schema Job является maintenance plateau и требует zero active coordinators; это не online expand/contract.
- **Consistency:** DDL/backfills/grants и marker reset атомарны; admission pass фиксируется под тем же advisory lock отдельной короткой transaction.
- **Security:** URL/пароли не выводятся; full report не содержит URLs; runtime startup проверяет actual roles/ownership и отсутствие database `TEMPORARY`, а не usernames configuration.
- **Capacity:** gate учитывает system и tenant pool каждого replica, оба PostgreSQL reserved classes, live client backends и explicit external reserve.
- **Observability:** health возвращает schema version/contract, admission status, report hash и checked-at; Job stdout содержит bounded summary.
- **Ownership:** Database/SRE владеет admin bootstrap/capacity; release owner — Job/version; security owner — role/grant review; incident commander — drift/unknown rollout outcome.

## Риски и открытые вопросы

| ID | Риск | Контроль | Exit / owner |
|---|---|---|---|
| DBR-01 | migration credential позволяет менять все cell data/schema | scoped Job Secret, scale-zero, audit/change record | external secret manager + short-lived credential / Security+DBA |
| DBR-02 | p99 smoke не моделирует business workload | conservative pool budget, separate production load test | approved workload profile / SRE+Application |
| DBR-03 | one-transaction DDL может держать locks | zero runtime, statement deadline, rollback | expand/contract package при online migration / Release owner |
| DBR-04 | Job pod report ephemeral | hash в marker/log, обязательный evidence export | centralized evidence sink / SRE |
| DBR-05 | local role bootstrap использует admin | admin только versioned Job, не Deployment | managed provider/IaC role provisioning / DBA |

## Acceptance evidence

```bash
npm run typecheck --workspace @agat/coordinator
node --import tsx --test apps/coordinator/test/postgres-admission.test.ts

AGAT_POSTGRES_MIGRATION_URL='postgresql://agat_migrator@127.0.0.1:55435/agat' \
AGAT_POSTGRES_URL='postgresql://agat_system@127.0.0.1:55435/agat' \
AGAT_TEST_POSTGRES_URL='postgresql://agat_system@127.0.0.1:55435/agat' \
AGAT_POSTGRES_TENANT_URL='postgresql://agat_tenant@127.0.0.1:55435/agat' \
AGAT_TEST_POSTGRES_TENANT_URL='postgresql://agat_tenant@127.0.0.1:55435/agat' \
node --import tsx --test apps/coordinator/test/fleet-ha-postgres.integration.test.ts

kubectl kustomize deploy/k8s/docker-desktop >/dev/null
```

Disposable PostgreSQL acceptance обязана подтвердить: successful schema/admission report, runtime `CREATE TABLE` denial, active-replica migration denial, catalog-drift startup denial, FORCE RLS cross-project negative test и multi-replica quota serialization.
