# Offline SQLite → PostgreSQL migration

## Решение и граница

`fleet:migrate-state` переносит одну остановленную Agat HA-cell с текущей schema v24 из SQLite в новую PostgreSQL database. Это canonical offline cutover: dual-write, online catch-up и автоматическое переключение трафика не входят в контракт.

Инструмент поддерживает три режима:

- `rehearse` создаёт schema и выполняет полный import/reconciliation в одной PostgreSQL transaction, затем делает `ROLLBACK` и доказывает возврат к baseline;
- `apply` импортирует и фиксирует данные только после полного reconciliation;
- `verify` ничего не меняет и сверяет остановленный SQLite source с уже импортированным PostgreSQL target в `REPEATABLE READ READ ONLY` transaction.

## AS-IS

До этого этапа 1.7 умел создать PostgreSQL schema v20 и работать с ней, но существующий SQLite state переносился только вручную. Документация требовала counts/hash/FK reconciliation и rehearsal, однако не было единого исполняемого формата, failure semantics и доказательства rollback. SQLite оставался authority до ручного cutover.

## Release TO-BE

После этапа оператор получает один versioned offline workflow для rehearsal, apply и read-only verify. SQLite остаётся единственным authority до успешного apply и явного переключения трафика; после первой PostgreSQL production write authority необратимо переходит в PostgreSQL в рамках этого workflow. Следующий этап вынес schema/admission в отдельную migration Job/role, а runtime сделал DDL-free; текущая schema v24 дополнительно содержит DR canaries, S3 lifecycle metadata/outbox и region-loss activation marker: [contract](./postgresql-migration-job-runtime-role.md).

## Source register

| Источник | Что он определяет | Как используется |
|---|---|---|
| `apps/coordinator/src/sqlite-postgres-migrator.ts` | preflight, import, canonical digest и report schema v1 | исполняемый migration contract |
| `apps/coordinator/test/sqlite-postgres-migrator.test.ts` | канонизация и dependency order | обязательные unit checks |
| `apps/coordinator/test/sqlite-postgres-migrator.integration.test.ts` | apply/verify и rehearsal rollback | disposable PostgreSQL acceptance |
| [SQLite transactions](https://www.sqlite.org/lang_transaction.html) | `BEGIN IMMEDIATE` не допускает конкурирующего writer | источник блокируется до завершения операции |
| [PostgreSQL `TRUNCATE`](https://www.postgresql.org/docs/17/sql-truncate.html) | `TRUNCATE` можно безопасно откатить внутри transaction | target очищается атомарно перед import |
| [PostgreSQL explicit locking](https://www.postgresql.org/docs/17/explicit-locking.html) | transaction/advisory lock semantics | один migrator на target database |

## Неизменяемые условия

- оператор обязан остановить coordinator, workers и все другие SQLite writers и явно передать `SOURCE_AND_WRITERS_STOPPED`;
- source открывается через `BEGIN IMMEDIATE`, переводится в query-only и должен иметь schema v20, исправные foreign keys и ровно одну `region/residencyDomain` cell;
- активные stages, executing MCP calls, running embedding jobs и locked SIEM outbox запрещают cutover;
- `apply` и `rehearse` принимают только target без Agat schema; повторный запуск требует новой database;
- все durable tables импортируются в детерминированном foreign-key order; self-referencing rows идут parent-before-child, а cycle отклоняется; только `coordinator_replicas` сознательно сбрасывается как runtime liveness state;
- SQLite artifact без `content_blob` читается только внутри указанного artifacts root, затем size и SHA-256 проверяются до insert;
- audit-outbox trigger отключается и возвращается внутри той же transaction, поэтому historical events не порождают повторный export;
- PostgreSQL migration, runtime system и tenant roles должны различаться и указывать на одну database;
- connection URLs передаются только через environment и не попадают в report или stdout.

## Rehearsal

Сначала создайте пустую disposable database с отдельными migration owner, DDL-free runtime и tenant roles. Для production endpoint используйте TLS `verify-full`; пароли не помещайте в shell history или аргументы процесса.

```bash
export AGAT_POSTGRES_MIGRATION_URL='postgresql://MIGRATION_ROLE@db.example/agat_rehearsal'
export AGAT_POSTGRES_URL='postgresql://RUNTIME_ROLE@db.example/agat_rehearsal'
export AGAT_POSTGRES_TENANT_URL='postgresql://TENANT_ROLE@db.example/agat_rehearsal'
export AGAT_POSTGRES_SSL_MODE='verify-full'
export AGAT_POSTGRES_CA_CERT_PATH='/run/secrets/postgres-ca.pem'

npm run fleet:migrate-state -- \
  --mode rehearse \
  --source /srv/agat/data/agat.db \
  --artifacts-dir /srv/agat/data/artifacts \
  --report /secure/reconciliation/rehearsal.json \
  --confirm-offline SOURCE_AND_WRITERS_STOPPED
```

Успех требует `success=true`, совпадения каждой copied table и `rollback.verified=true`. Сохраните report и его `reportSha256` в change record. Database после rehearsal содержит только bootstrap schema/baseline; удалите её и создайте новый target для apply.

## Apply и независимая проверка

```bash
export AGAT_POSTGRES_MIGRATION_URL='postgresql://MIGRATION_ROLE@db.example/agat'
export AGAT_POSTGRES_URL='postgresql://RUNTIME_ROLE@db.example/agat'
export AGAT_POSTGRES_TENANT_URL='postgresql://TENANT_ROLE@db.example/agat'

npm run fleet:migrate-state -- \
  --mode apply \
  --source /srv/agat/data/agat.db \
  --artifacts-dir /srv/agat/data/artifacts \
  --report /secure/reconciliation/apply.json \
  --confirm-offline SOURCE_AND_WRITERS_STOPPED

npm run fleet:migrate-state -- \
  --mode verify \
  --source /srv/agat/data/agat.db \
  --artifacts-dir /srv/agat/data/artifacts \
  --report /secure/reconciliation/verify.json \
  --confirm-offline SOURCE_AND_WRITERS_STOPPED

# После успешного apply+verify завершить schema/admission release gate:
npm run fleet:migrate-schema
```

Report schema v1 содержит SHA-256 SQLite database/WAL snapshot, cell, foreign-key status, artifact counters, а для каждой таблицы — row count и canonical SHA-256 source/target. Integer-like значения кодируются десятичной строкой, binary — base64, columns и rows имеют стабильный order. `coordinator_replicas` единственная строка с policy `runtime_ephemeral_reset`; её target count обязан быть нулевым.

## Cutover runbook

1. Зафиксировать maintenance window, владельца решения, свежий согласованный backup SQLite/WAL/artifacts и проверенный restore.
2. Остановить coordinator, workers и внешние writers; убедиться, что lock файла получает только migrator.
3. Выполнить `rehearse` на disposable database, приложить report к change record и удалить rehearsal target.
4. Создать новый production target и выполнить `apply`; при любом несовпадении transaction откатывается.
5. Выполнить отдельный `verify`, выборочный artifact download и `fleet:migrate-schema`; только successful admission marker разрешает coordinator runtime role.
6. Направить весь трафик в PostgreSQL cell, проверить health/queues/SIEM и оставить SQLite source immutable на период rollback window.

После первой production write в PostgreSQL старый SQLite становится stale. Возврат на него запрещён: нужен остановленный контур и проверенный reverse export либо восстановление согласованного PostgreSQL backup.

## Ошибки и восстановление

| Сбой | Безопасное состояние | Действие оператора |
|---|---|---|
| SQLite занят writer | import не начинается | найти writer, остановить его, повторить с тем же immutable source |
| schema/FK/cell/active-work preflight не прошёл | target не меняется | устранить причину и заново снять source backup |
| target уже содержит Agat schema | import не начинается | использовать новую database, не очищать неизвестный target |
| artifact отсутствует или hash/size не совпал | PostgreSQL transaction откатывается | восстановить artifact snapshot и начать на новом target |
| batch insert, constraint или reconciliation не прошли | PostgreSQL transaction откатывается | сохранить ошибку, исправить source/tooling, пересоздать target |
| процесс завершился до `COMMIT` | PostgreSQL сам откатывает transaction | убедиться в отсутствии commit и повторить на новой database |
| исход `COMMIT` неизвестен | состояние нельзя угадывать | не повторять apply; подключиться read-only и выполнить `verify` |
| report не записался после успешного `COMMIT` | данные могли быть зафиксированы | не повторять apply; выполнить `verify` в новый report path |

## Операции и NFR

- **Consistency:** cutover serializes SQLite writes и использует одну PostgreSQL transaction; acceptance требует exact row/digest match и validated foreign keys.
- **Capacity:** default batch — 250 rows, hard ceiling — 60 000 PostgreSQL parameters на statement; maintenance window и disk/WAL headroom владелец database измеряет на production-sized rehearsal.
- **Security:** отдельные credentials, TLS `verify-full`, report mode `0600`, redacted connection identity hash и отсутствие URL/secret в output.
- **Availability:** workflow намеренно требует downtime; его длительность не является заявленным RTO. Повторный apply в частично известное состояние запрещён.
- **Observability:** stdout содержит только migration ID/mode/report hash/path; durable evidence находится в signed change record вокруг report schema v1.
- **Ownership:** Database/SRE owner создаёт target и backup/restore evidence; application owner останавливает writers и принимает reconciliation; incident commander решает unknown commit outcome; security owner управляет credentials/report retention.

## Риски и открытые вопросы

| ID | Открытый риск | Текущий контроль | Exit gate / владелец |
|---|---|---|---|
| MIG-01 | downtime превышает окно | production-sized rehearsal, bounded batches | measured duration и approved window / Application + SRE |
| MIG-02 | filesystem artifact snapshot расходится с SQLite | immutable snapshot, size/SHA-256 fail-closed | zero mismatches / Data owner |
| MIG-03 | неизвестен исход commit при потере соединения | apply не повторяется, verify read-only | verified report или восстановление target / Incident commander |
| MIG-04 | migration credential имеет DDL | credential scoped offline/Job, runtime не owner и без CREATE | закрыто schema v21 boundary; short-lived secret остаётся hardening / Security+DBA |
| MIG-05 | SQLite schema кроме v24 | exact-version fail-closed | отдельный source upgrade/rehearsal / Application owner |
| MIG-06 | multi-cell source нельзя разделить автоматически | ровно одна cell в preflight | отдельный утверждённый migration plan / Data + Residency owner |

## Acceptance evidence

```bash
npm run typecheck --workspace @agat/coordinator
node --import tsx --test apps/coordinator/test/sqlite-postgres-migrator.test.ts
npm run fleet:test-state-migration

AGAT_TEST_MIGRATION_POSTGRES_URL='postgresql://MIGRATION_ROLE@127.0.0.1:55433/agat' \
AGAT_TEST_MIGRATION_RUNTIME_URL='postgresql://RUNTIME_ROLE@127.0.0.1:55433/agat' \
AGAT_TEST_MIGRATION_POSTGRES_TENANT_URL='postgresql://TENANT_ROLE@127.0.0.1:55433/agat' \
node --import tsx --test apps/coordinator/test/sqlite-postgres-migrator.integration.test.ts
```

Integration test импортирует durable state и filesystem artifacts, затем выполняет verify-only. Отдельные `AGAT_TEST_REHEARSAL_POSTGRES_URL`, `AGAT_TEST_REHEARSAL_RUNTIME_URL` и `AGAT_TEST_REHEARSAL_POSTGRES_TENANT_URL` включают rollback scenario и доказывают, что добавленная source row отсутствует после rollback.

Schema migration Job/role, DDL-free runtime, managed PostgreSQL resilience, S3 artifact authority и residency-aware region-loss DR реализованы последующими этапами. Текущие незакрытые gates — OCI/runtime attestation и SIEM retention/DLQ.
