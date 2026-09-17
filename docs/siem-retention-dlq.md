# SIEM delivery conformance, retention и DLQ

## Результат

Schema v25 заменяет неограниченный retry на конечный, наблюдаемый lifecycle:

```text
pending → delivering → delivered → operational retention purge
              │
              ├─ retryable failure + attempts left → pending
              └─ permanent failure / max attempts → dead + immutable redacted DLQ snapshot
                                                      ├─ admin replay → pending
                                                      └─ admin resolve → retained evidence → purge
```

`events` остаётся authoritative application audit ledger. Retention этого документа относится к operational SIEM delivery state и DLQ evidence; она не удаляет audit events. Срок хранения основного ledger определяется отдельной compliance/backup policy организации.

## Sink conformance

Coordinator отправляет bounded NDJSON batch по HTTPS:

- `Content-Type: application/x-ndjson`;
- `X-Agat-Audit-Batch: FIRST_ID-LAST_ID`;
- стабильный `idempotencyKey=agat-audit-EVENT_ID` в каждой строке;
- bearer credential только из `AGAT_SIEM_BEARER_TOKEN`;
- redirects запрещены, remote HTTP запрещён;
- raw message/data/reason не экспортируются: только allowlisted metadata и SHA-256.

При `AGAT_SIEM_REQUIRE_ACK=true` (default) одного HTTP 2xx недостаточно. Sink обязан вернуть точный header:

```http
X-Agat-Audit-Ack: FIRST_ID-LAST_ID
```

Missing/mismatched acknowledgement считается retryable failure. Это не создаёт exactly-once: network timeout после sink commit по-прежнему может привести к повтору. Sink обязан дедуплицировать по `idempotencyKey`.

Классификация:

| Результат | Действие |
|---|---|
| 2xx + exact ack | `delivered` |
| 2xx без/с неверным ack | retry, затем DLQ |
| 408, 409, 425, 429, 5xx, timeout/network | retry с exponential backoff |
| остальные 4xx | immediate DLQ как permanent contract/auth/payload failure |
| `AGAT_SIEM_MAX_ATTEMPTS` исчерпан | DLQ |

Coordinator не читает response body. В state сохраняется только SHA-256 комбинации status/ack, код failure и bounded technical error; sink payload/error body не становится новым каналом утечки.

## Durable state

### `audit_export_outbox`

Transactional trigger создаёт row в том же commit, что и `events`. Несколько coordinator replicas используют leased `FOR UPDATE SKIP LOCKED`; stale delivery lease возвращается в `pending`. Schema v25 добавляет:

- terminal status `dead`;
- `last_failure_code`;
- `last_response_sha256`;
- `dead_at`.

### `audit_export_dead_letters`

При terminal failure в той же database transaction создаётся/обновляется DLQ row и outbox становится `dead`. DLQ хранит:

- canonical redacted SIEM payload и его SHA-256;
- project/event ID, attempt count и reason code;
- только hashes error/response;
- first/last dead timestamps;
- replay/resolve evidence и retention deadline.

Raw event message, raw `data_json`, credentials, prompt/output и sink error body в DLQ не копируются. Повторное падение replayed event переоткрывает тот же `event_id`, сохраняя initial `first_dead_at` и replay count.

### `audit_export_retention_state`

Singleton фиксирует effective delivered/DLQ policy, последнее выполнение и число удалённых operational rows. Background maintenance ограничена batch и не создаёт audit event на каждый purge, иначе exporter рекурсивно порождал бы новые delivery rows.

## Retention invariants

- delivered outbox row удаляется после `AGAT_SIEM_DELIVERED_RETENTION_DAYS`, если с ним не связано retained DLQ evidence;
- `open` DLQ не удаляется никогда, даже после `retained_until`;
- `resolved` DLQ удаляется только после явного admin decision и DLQ retention;
- `replayed` DLQ удаляется только после успешной delivery и DLQ retention;
- DLQ/outbox удаляются согласованно; authoritative `events` не затрагивается;
- maintenance запускается каждые `AGAT_SIEM_RETENTION_INTERVAL_SECONDS` и обрабатывает bounded batch.

Defaults: delivered 30 дней, DLQ 90 дней, interval 900 секунд. Production policy должна выбрать значения по требованиям организации, но не подменять legal hold/PITR policy основного audit ledger.

## RBAC и операции DLQ

Прямой tenant PostgreSQL role не имеет privileges на `audit_export_dead_letters` и `audit_export_retention_state`. API читает их system role только после RBAC:

- `admin`: list всех cells/projects, replay и resolve;
- `auditor`: read-only list только своего selected project;
- остальные роли: deny.

### Просмотр

```bash
curl -H "Authorization: Bearer $TOKEN" \
  'https://agat.example/api/v1/fleet/siem/dead-letters?limit=100'
```

Ответ содержит только redacted payload/hashes и lifecycle metadata.

### Replay

Сначала устраните root cause и подтвердите, что sink дедуплицирует event ID:

```bash
curl -X POST \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"reason":"parser mapping fixed under CHG-1042","confirm":"REPLAY_SIEM_DEAD_LETTER"}' \
  'https://agat.example/api/v1/fleet/siem/dead-letters/123/replay'
```

Replay сбрасывает attempts и возвращает outbox в `pending`; node/event idempotency key не меняется. Решение создаёт отдельное audit event.

### Resolve без повторной доставки

Resolve допустим только как документированное исключение, например подтверждённый duplicate в sink:

```bash
curl -X POST \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"reason":"sink independently confirms event 123 already committed","confirm":"RESOLVE_SIEM_DEAD_LETTER"}' \
  'https://agat.example/api/v1/fleet/siem/dead-letters/123/resolve'
```

Typed confirmation не заменяет change-management approval. Если local policy требует four-eyes для этого break-glass действия, API должен быть доступен только через существующий approved operator workflow.

## Конфигурация

```dotenv
AGAT_SIEM_ENABLED=true
AGAT_SIEM_URL=https://siem.example.internal/ingest/agat
AGAT_SIEM_BEARER_TOKEN=<sink-scoped-ingest-only-secret>
AGAT_SIEM_BATCH_SIZE=100
AGAT_SIEM_INTERVAL_SECONDS=5
AGAT_SIEM_TIMEOUT_SECONDS=10
AGAT_SIEM_REQUIRE_ACK=true
AGAT_SIEM_MAX_ATTEMPTS=8
AGAT_SIEM_DELIVERED_RETENTION_DAYS=30
AGAT_SIEM_DLQ_RETENTION_DAYS=90
AGAT_SIEM_RETENTION_INTERVAL_SECONDS=900
```

Remote SIEM требует отдельный bearer token. Не используйте admin/OIDC/node/enrollment credential. Secret должен давать sink только ingest scope; read/search/delete права ему не нужны.

## Мониторинг и alerting

Fleet snapshot показывает `pending/delivering/delivered/dead`, число open DLQ, oldest pending и effective retention. Рекомендуемые alerts:

- `deadLetters > 0`: page security/platform operator;
- oldest pending превышает согласованный delivery SLO;
- повторяющийся `ack_missing/ack_mismatch`: sink contract regression;
- 401/403 (`http_4xx`): немедленно проверить scoped credential/route;
- retention `lastRunAt` старше двух intervals;
- растущий retry rate при нулевой sink ingestion.

При outage не разрешайте ручное удаление rows и не отключайте ack. Восстановите sink, проверьте idempotency, затем replay open DLQ bounded batches.

## Region-loss и restore

Region-loss activation возвращает только `delivering` rows в `pending`; `delivered`, `dead` и DLQ evidence сохраняются из PostgreSQL snapshot. После target startup:

1. сверить counts/status и retention state;
2. проверить sink route/credential и exact ack на canary event;
3. убедиться, что sink дедуплицирует уже доставленные IDs;
4. отдельно обработать open DLQ — они не replay автоматически.

PITR/restore point должен включать `events`, outbox и DLQ согласованно. Восстановление только одной таблицы запрещено.

## Проверки

- unit suite доводит event до max attempts, проверяет redaction/hash, open-DLQ retention deny, controlled replay, повторный terminal failure, resolve и bounded purge;
- PostgreSQL suite проверяет disjoint claims replicas и deny tenant role для DLQ/retention tables;
- server exporter требует exact ack, различает permanent/retryable HTTP и hash-only response evidence;
- kustomize/config tests фиксируют production knobs и scoped secret boundary.

## Источники

| ID | Первичный источник | Claim |
|---|---|---|
| SRC-2511 | [Kubernetes Auditing](https://kubernetes.io/docs/tasks/debug/debug-cluster/audit/) | audit records являются security-relevant chronological records; backend persistence и retention задаются операционной policy |
| SRC-2512 | [PostgreSQL 17 `SELECT`](https://www.postgresql.org/docs/17/sql-select.html) | `FOR UPDATE ... SKIP LOCKED` подходит для конкурентного queue claim, но не создаёт exactly-once delivery |
| SRC-2513 | [PostgreSQL 17 transactions](https://www.postgresql.org/docs/17/tutorial-transactions.html) | outbox/DLQ transition атомарен только внутри одной transaction boundary |

Источники проверены 2026-08-31. Конкретные retention сроки и incident approvals определяет политика эксплуатирующей организации.
