# Эксплуатация

## Health

```bash
curl -fsS http://127.0.0.1:8787/api/v1/health
npm run k8s:status
```

В Kubernetes health должен содержать `processRuntime.mode=temporal` и `connected=true`. Ответ через localhost обязан иметь `Server: kong/...`; `k8s:status` считает прямой stale coordinator на том же порту ошибкой, а не здоровым Gateway.

Узел считается `sleeping` после 90 секунд без heartbeat и `offline` после 300 секунд. Активный lease продлевается worker каждые 45 секунд; стандартный TTL — 180 секунд.

Health содержит `stateStore.driver`, cell/replica readiness, а для PostgreSQL — текущий schema v25 contract, admission status/report hash/checked-at. `haReady=true` подтверждает PostgreSQL и минимум две живые coordinator replicas, но не HA самой database: managed topology/PITR подтверждает отдельный resilience report. Нечувствительные policy-поля показывают, включены ли release/provenance/runtime-attestation и SIEM ack/DLQ gates; public keys, broker/sink tokens и evidence в health не возвращаются. Поля `edge.enabled`, `edge.attestationAvailable`, `edge.attestationMode` по-прежнему показывают отдельную native edge boundary без broker token/URL.

## Rollout 1.7 Fleet и HA

Исходная schema `19 → 20` добавила regional project policy, queue/quota state, coordinator heartbeats, signed worker release/rollout registry, PostgreSQL artifact bytes, SIEM outbox и RLS policies. Schema v23 вынесла новые artifact payloads в versioned S3-compatible authority, v24 добавила immutable region-loss activation ledger и monotonic cell write epoch, а текущая v25 — OCI provenance/runtime-attestation evidence и SIEM DLQ/retention state. SQLite остаётся совместимым single-coordinator developer backend; PostgreSQL является единственным metadata authority в Fleet/HA mode. Dual-write отсутствует.

1. Снимите согласованный backup текущего state store, Artifact Store, Temporal/Keycloak state и application secrets. Проверьте restore до cutover.
2. Для существующего SQLite-контура остановите writes и выполните canonical offline migration с reconciliation количества строк/hashes, foreign keys и artifact bytes по [migration runbook](./sqlite-postgresql-migration.md). В релизе 1.7 migrator отсутствовал; post-1.7 production-readiness этап закрыл этот gate.
3. Поднимите PostgreSQL с раздельными admin/migration/runtime/tenant credentials и TLS `verify-full`; примените текущую schema v25 отдельной Job при нуле replicas, получите успешный admission report. Runtime role не должна владеть objects или иметь DDL. Выполните cross-project RLS negative test tenant credential.
4. Для production endpoint выполните multi-AZ/PITR preflight, actual failover и isolated restore по [managed PostgreSQL runbook](./managed-postgresql-resilience.md). Сохраните HMAC reports и exact SLO policy approval; application `haReady` этот gate не заменяет.
5. Подготовьте versioned S3-compatible bucket, примените Agat lifecycle rule без current-version expiration, выполните BYTEA backfill/reconciliation и только затем включите `AGAT_ARTIFACT_STORE_DRIVER=s3`; подробный порядок — в [artifact store runbook](./s3-artifact-store-lifecycle.md).
6. Подготовьте dormant target в разрешённом residency domain, внешний source fencing и односторонние PostgreSQL/S3/Temporal recovery paths; выполните rehearsal по [region-loss runbook](./region-loss-dr.md). Не запускайте target runtime без exact activation ID/write epoch.
7. Запустите одну coordinator replica и проверьте `/health`, queues, artifact download, Temporal reconciliation и SIEM pending/delivery. Затем увеличьте до двух и выполните concurrent lease/quota test.
8. Для server workers проверьте exact OCI digest и SLSA v1 evidence закреплённым Sigstore pipeline, подпишите компактный provenance admission отдельным key, зарегистрируйте baseline release и настройте staged fallback/target rollout.
9. Подключите trusted local attestor с отдельным public root и worker-only scoped broker token. После negative tests включите три gates: `AGAT_REQUIRE_SIGNED_WORKER_RELEASES=true`, `AGAT_REQUIRE_WORKER_PROVENANCE=true`, `AGAT_REQUIRE_WORKER_RUNTIME_ATTESTATION=true`. Проверьте challenge replay, expiry и refresh без ротации node token. Hardware-attested mobile nodes используют отдельную app-attestation boundary.
10. Проверьте revoke: отозванный release, удалённый provenance/runtime root и истёкшая attestation не должны пройти authentication или получить новый lease. Уже выполняющийся внешний side effect требует отдельной incident/compensation процедуры.

Docker Desktop 1.7 разворачивает PostgreSQL и две coordinator replicas для локальной проверки. Если namespace уже содержит SQLite coordinator, `npm run k8s:up` остановится до любых apply/build. `AGAT_K8S_ALLOW_POSTGRES_CUTOVER=true` только подтверждает осознанный запуск нового PostgreSQL authority и **не переносит данные**; старый `agat-data` PVC остаётся до удаления namespace. Не используйте флаг вместо миграции.

Минимальная проверка релиза:

```bash
node --import tsx --test apps/coordinator/test/fleet-ha.test.ts
npm run fleet:test-ha-postgres
kubectl kustomize deploy/k8s/docker-desktop >/dev/null
```

Production cutover остаётся заблокирован без per-cluster passing reports для timed backup/restore/failover, утверждённых RPO/RTO/SLO и load test. Исполняемый DB contract: [Managed PostgreSQL](./managed-postgresql-resilience.md); исходный Fleet contract: [Fleet и HA 1.7](./fleet-ha-1.7.md).

### Offline SQLite → PostgreSQL

Исполняемый migrator требует остановленных writers, новую target database и явное подтверждение `SOURCE_AND_WRITERS_STOPPED`. Сначала выполните rehearsal rollback, затем apply и независимый verify. Команды, report schema, failure semantics и запрет возврата на stale SQLite приведены в [отдельном runbook](./sqlite-postgresql-migration.md).

### PostgreSQL schema Job и runtime role

Coordinator startup не выполняет DDL. При rollout остановите все replicas, примените versioned role-bootstrap/schema Job, дождитесь successful capacity/load marker и только затем поднимайте runtime. Migration credential не передаётся Deployment; runtime имеет DML+BYPASSRLS, но не ownership/CREATE/TRUNCATE/TRIGGER/REFERENCES. Полный порядок, formulas и recovery: [PostgreSQL migration Job и DDL-free runtime](./postgresql-migration-job-runtime-role.md).

### Managed PostgreSQL resilience

Production Job валидирует fresh provider evidence и сам endpoint: ≥2 AZ, synchronous standby, automatic failover, PITR freshness/retention, private encryption, TLS/checksums/WAL, DDL-free schema v25 и distinct SLO approval. Planned failover и isolated PITR clone доказываются canaries «до/после» и HMAC reports; локальный physical drill не считается provider qualification. Команды, RPO/RTO/SLO и failure semantics: [Managed PostgreSQL](./managed-postgresql-resilience.md).

### S3-compatible Artifact Store

В Fleet/HA новые payloads записываются в versioned object store до commit PostgreSQL metadata. Coordinator проверяет SHA-256, размер, project hash и exact version ID; локальный файл служит только cache. Lifecycle worker удаляет только exact version через transactional outbox, а retention/legal hold блокируют enqueue. Bucket lifecycle управляет лишь incomplete multipart uploads и noncurrent versions: current versions удаляет только application state machine. Provisioning, backfill, reconciliation, typed confirmations и recovery описаны в [S3 Artifact Store и lifecycle](./s3-artifact-store-lifecycle.md).

### Residency-aware region-loss DR

Region-loss не является обычным rollout или database failover. Сначала внешний control plane полностью fence source database/object writes, ingress, enrollment и credentials. Затем изолированные PostgreSQL/S3/Temporal targets подтверждаются fresh HMAC evidence и двумя distinct approver. Suspended Job `agat-region-loss-dr-activation-v25` атомарно меняет whole-cell placement, revokes source workers, инвалидирует issued runtime challenges, requeues delivering outboxes и увеличивает write epoch. Target coordinator запускается только с exact `AGAT_REGION_LOSS_DR_ACTIVATION_ID`, `AGAT_REGION_LOSS_DR_WRITE_EPOCH` и target S3 bucket; workers получают новую cell-bound attestation. Failback проходит как новый reverse transition; полный runbook: [Residency-aware region-loss DR](./region-loss-dr.md).

### OCI provenance и runtime attestation workers

Release manifest, CI provenance admission и local runtime statement имеют три разные Ed25519 trust roots. Coordinator сохраняет только public roots и hash challenge, повторно проверяет stored provenance при worker authentication и отклоняет lease после expiry/revoke/root removal. Worker получает attestation через HTTPS либо loopback sidecar без redirects и обновляет её до expiry, не меняя node token. Production rollout, signing helper, broker contract, negative tests и incident response: [Worker supply-chain attestation](./worker-supply-chain-attestation.md).

## Rollout 1.6 native edge worker

Coordinator мигрирует SQLite schema `18 → 19`: расширяет `nodes` trust/attestation/credential/wipe полями и добавляет `edge_enrollment_challenges`. Перед rollout сделайте согласованный backup SQLite/WAL и Artifact Store. Старый binary после записи schema v19 не является поддерживаемым rollback-путём без восстановления backup.

1. Обновите coordinator/dashboard до `1.6.0`, оставив `AGAT_EDGE_ENABLED=false`; проверьте обычные workers и schema migration.
2. Разверните отдельный HTTPS attestation broker и выполните provider negative tests: неверный challenge hash, package/App ID, key ID, environment, expired verdict и отсутствующий required verdict должны отклоняться.
3. Сохраните broker bearer в secret manager. В Kubernetes ключ называется `edge-attestation-broker-token` внутри `agat-secrets`; URL/application IDs/verdict policy задаются ConfigMap.
4. Включите edge и перезапустите coordinator. `/api/v1/health` должен показать доступный broker mode.
5. Подпишите Android/iOS builds, зарегистрируйте по одному canary device и убедитесь, что карточка узла показывает `hardware_attested/active`, точного provider и production environment.
6. Запустите agent-only stage без MCP, затем negative stages с MCP/HTTP/embedding. Они не должны выдаваться mobile node.
7. На canary запросите remote wipe. Сразу после `202` work token должен получить `401`, stage — вернуться в очередь, а после device poll state стать `wiped` с `acknowledgedAt/revokedAt`.

Минимальные control-plane проверки:

```bash
node --import tsx --test apps/coordinator/test/edge-workers.test.ts
npm run typecheck
npm run build
kubectl kustomize deploy/k8s/docker-desktop >/dev/null
```

Android/iOS prerequisites, pinned runtime и полный broker contract: [Native edge worker 1.6](./native-edge-worker.md).

## Rollout 1.5 isolated MCP tools

Coordinator мигрирует SQLite schema `17 → 18`: `mcp_servers` получает transport и encrypted/redacted sandbox profile, `mcp_tool_calls` — transport и immutable profile hash. Перед rollout сделайте согласованный backup SQLite/WAL, Artifact Store и `AGAT_CREDENTIALS_KEY`; старый binary после записи schema v18 не является поддерживаемым rollback-путём без восстановления backup.

Для текущего bundle обновите coordinator/dashboard до `1.6.0`, примените namespace Role и соберите `agat-local/sandbox-wasi:1.6.0`. Docker Compose оставляет `AGAT_SANDBOX_ENABLED=false`. Kubernetes profile включает WASI, но намеренно держит `AGAT_SANDBOX_NETWORK_POLICY_ENFORCED=false`: native OCI tools не запускаются, пока оператор не установит enforcing CNI и не подтвердит negative egress test. Не выставляйте этот флаг только ради прохождения smoke test.

Первый smoke test выполняйте read-only WASI module без credential, затем с истекающим `kind=mcp` scope. Проверьте two-person destructive approval отдельным test profile, emergency deny во время long-running Job и cleanup:

```bash
kubectl get jobs,secrets,networkpolicies -n agat -l sandbox.agat.dev/managed=true
kubectl auth can-i create jobs --as=system:serviceaccount:agat:agat-coordinator -n agat
node --import tsx --test apps/coordinator/test/mcp.test.ts apps/coordinator/test/sandbox.test.ts
```

Production OCI profile требует review полного image digest и command, отдельного namespace/node pool по модели угроз и, при необходимости, установленного `AGAT_SANDBOX_RUNTIME_CLASS`. Подробный contract и incident procedure: [Изолированное выполнение MCP tools](./isolated-tool-execution.md).

## Rollout 1.4 A2A interoperability

Coordinator мигрирует SQLite schema `16 → 17`: добавляет endpoint capabilities/file limits и таблицы `a2a_push_configs`, `a2a_push_deliveries`, `a2a_remotes`, `a2a_outbound_tasks`. Перед rollout сделайте согласованный backup SQLite/WAL, Artifact Store и `AGAT_CREDENTIALS_KEY`.

Обновите coordinator и dashboard вместе. Existing endpoints сохраняются и получают fail-closed defaults для новых push/files capabilities; streaming у существующих записей не включается автоматически. Затем обновите workers до `1.4.0`, чтобы версия fleet/telemetry и optional base64 artifact encoding совпадали с control plane.

До регистрации production peers задайте `AGAT_A2A_PUBLIC_BASE_URL`, `AGAT_A2A_OUTBOUND_ENABLED`, timeout/response limit и оставьте `AGAT_A2A_ALLOW_LOOPBACK_OUTBOUND=false`. Для Docker Compose обязательно передайте отдельный `AGAT_CREDENTIALS_KEY`; не полагайтесь на admin token как encryption fallback. Delegated OAuth peer регистрирует только `admin` после проверки показанного token endpoint origin. Smoke test выполняйте сначала на peer без side effects: discovery, `message:send`, polling/cancel, затем отдельно SSE и callback с уникальным Bearer. Rollback binary после записи schema v17 не поддерживается без восстановления backup.

## Rollout 1.3 specialist teams

Coordinator мигрирует SQLite schema `15 → 16` и добавляет к node capabilities `agent_runtime_profiles_json`. Сначала обновите coordinator, затем workers; legacy worker безопасно считается совместимым только с `tool_loop_v1` и не получает `specialist_team_v1`. Перед созданием production team убедитесь, что карточка узла показывает профиль и все модели supervisor/specialists.

Team нельзя распределить между несколькими узлами. При недостатке хотя бы одной pinned модели stage остаётся в очереди с обычным readiness/scheduler explanation. Rollback binary допустим только после проверенного backup базы: старый coordinator не знает manifest v3 и новое поле capability, поэтому смешанный control-plane rollout не поддерживается.

## Temporal rollout и replay

Перед каждым production worker build выполняются `npm run typecheck`, `npm test` и `npm run build`. `npm test` включает replay сохранённых Temporal histories. Новый immutable build сначала запускается как canary, затем получает ограниченный ramp и только после наблюдения становится current:

```bash
AGAT_TEMPORAL_BUILD_ID=git-abc123 npm run temporal:version
AGAT_TEMPORAL_BUILD_ID=git-abc123 AGAT_TEMPORAL_RAMP_PERCENTAGE=5 npm run temporal:ramp
npm run temporal:status
AGAT_TEMPORAL_BUILD_ID=git-abc123 npm run temporal:promote
```

Старый worker нельзя масштабировать в ноль, пока на нём остаются pinned executions. Полный runbook подключения, rollback и drain: [Production hardening durable runtime](./production-durable-runtime.md).

Разделы панели читают один snapshot `/api/v1/overview` и обновляют его по SSE либо раз в 15 секунд. Поэтому счётчики агентов, моделей и узлов отражают authoritative state store и свежесть heartbeat, а не локально сгенерированное UI-состояние.

При открытом запуске панель отдельно получает authenticated `/runs/:id/trace`. Вкладка показывает до 10 000 событий, а `trace.json` можно выгрузить из браузера. Если видны только краткие события overview, проверьте OIDC-сессию/роль (или legacy admin token) и ответ этого endpoint.

Вкладка **Replay / Eval** использует тот же trace endpoint: отдельный polling-сервис не нужен. OTLP export опционален и не является источником истины. Для его включения и диагностики см. [OpenTelemetry, execution manifest, replay и eval](./observability-replay-evals.md).

## Live и demo режимы

`AGAT_SEED_DEMO=false` — production-default. `true` допустим только для презентации на отдельной базе. При первом live-запуске после обновления coordinator удаляет известные demo-fixtures; пользовательские агенты, узлы и запуски сохраняются.

## Backup state store и артефактов

SQLite developer mode использует WAL. Для согласованного backup выполните SQLite online backup либо остановите единственный coordinator перед копированием `agat.db`, `-wal`, `-shm` и всего `AGAT_ARTIFACTS_DIR`. Простое копирование одного `agat.db` во время записи некорректно. Restore проверяется через `PRAGMA integrity_check` и выборочный authenticated artifact download со сверкой SHA-256.

В PostgreSQL Fleet/HA mode metadata, knowledge vectors, encrypted A2A/MCP state, queue/leases, audit outbox и artifact storage intents находятся в database authority HA-cell. Payload authority находится в versioned S3-compatible bucket той же residency cell; coordinator file cache не является источником истины и не входит в restore set. PostgreSQL PITR и bucket version/lifecycle retention должны иметь согласованный recovery window. Проверка restore должна включать:

1. PostgreSQL schema version `25`, catalog manifest, admission marker, cell activation/write-epoch marker, constraints, RLS policies и privileges migration/runtime/tenant roles;
2. counts/foreign keys и выборочные content hashes для runs, knowledge, A2A/MCP и artifacts, включая exact bucket/key/version references;
3. отсутствие cross-project read/write под tenant role;
4. reconciliation активных Temporal instances с application rows;
5. согласованность `events`/SIEM outbox/DLQ/retention state, возврат `delivering` в retry и дедупликацию уже delivered event IDs;
6. S3 reconciliation без missing active objects и untracked objects старше grace period;
7. запуск двух coordinator replicas и конкурентный queue/quota/download smoke test.

`AGAT_CREDENTIALS_KEY`, database credentials, release/provenance/runtime public-root configuration, private signing keys вне cluster и scoped broker/sink tokens backup-ятся раздельным защищённым secret-management процессом. Без прежнего `AGAT_CREDENTIALS_KEY` encrypted protocol/credential history не расшифровывается; one-way endpoint/node token/challenge hashes восстановить в raw secret невозможно, поэтому после потери выполняйте rotation/re-enrollment.

Локальный Kubernetes DR-набор включает:

- `agat-coordinator-postgres` PVC — основной Fleet metadata state store;
- `agat-artifact-store` PVC — локальный versioned MinIO payload store; production заменяет его provider-managed S3-compatible storage;
- сохранённый legacy `agat-data` PVC, только если ещё не завершён SQLite cutover;
- `agat-temporal-data` — history/timers локального Temporal dev server;
- `agat-keycloak-postgres` — users, roles, sessions и realm state;
- Secrets `agat-secrets` и `agat-postgres-secrets` в защищённом secret manager;
- definitions управляемых worker-пулов в Kubernetes API.

Не восстанавливайте PostgreSQL, object store, Temporal и identity state в произвольные разные моменты: artifact references, execution tokens/history и авторизация могут разойтись. После region-loss не подключайте snapshot/bucket к target coordinator до полного source fencing, same-residency policy decision и atomic activation по [runbook](./region-loss-dr.md). Repository capabilities закрыты; production остаётся заблокирован до qualification конкретных managed PostgreSQL/S3/CI/attestor/SIEM контуров и cross-system game day из [roadmap](./roadmap.md).

## Восстановление durable process

После рестарта coordinator перечитывает активные `runtime=temporal` instances и идемпотентно получает/запускает соответствующие Workflow IDs. Temporal worker replay-ит историю, а authoritative state store восстанавливает execution tokens, join arrivals, external signal waits, embedded subprocess links и compensation stack. Durable `wait` не превращается в polling-таймер coordinator. Завершение lease, approval, внешний signal и cancel отправляют подтверждаемый Update с Signal fallback; периодическая сверка остаётся fallback на случай краткого сбоя transport.

Проверка runtime:

```bash
kubectl logs -n agat deployment/agat-temporal-worker --tail=100
curl -fsS http://127.0.0.1:8787/api/v1/health
```

Workflow конкретного instance открывается по ссылке **Temporal** в таблице запусков процесса либо в `http://127.0.0.1:8233`.

## Потеря worker

Ничего вручную возвращать в очередь не нужно. После TTL coordinator переводит `running → queued`. Non-GET process HTTP и compensation автоматически получают стабильный key на весь retry одного stage, но at-least-once риск исчезает только если upstream действительно дедуплицирует этот header. MCP gateway дополнительно блокирует автоматический retry stage, если non-read call достиг `executing/completed/failed`: это предотвращает наиболее опасный повтор, но не заменяет идемпотентность upstream системы.

Если start webhook вернул `503` после создания receipt, повторите тот же запрос с тем же Bearer token и `Idempotency-Key`: coordinator повторно использует тот же instance и идемпотентно запускает его в Temporal. Не генерируйте новый key при transport retry одного source event.

A2A task является обычным run, поэтому потеря worker обрабатывается тем же lease TTL. Внешний клиент продолжает polling/subscription той же task ID; повторный `message:send` с тем же `messageId` и payload не создаёт второй run. Push outbox остаётся durable и может повторить delivery после рестарта, поэтому receiver обязан дедуплицировать по task/state.

Для LangGraph повторяется весь внутренний graph attempt: per-node checkpoints намеренно не сохраняются отдельно от stage. Для team это означает повтор supervisor и всех завершённых handoffs. Web-tools read-only; MCP side effects проходят существующие policy/approval/idempotency checks, а неизвестный исход блокирует автоматический retry stage.

Embedding job использует такой же pull lease и максимум три попытки. После потери worker просроченная job возвращается в `pending`; уже принятый batch не пересчитывается. Если документ перешёл в `failed`, удалите его и загрузите заново после исправления модели/endpoint. Изменение размерности одной embedding-модели внутри существующей collection отклоняется — создайте новую collection и переиндексируйте документы.

## Ротация worker token

Повторная регистрация узла с тем же `name` и действующим enrollment token выдаёт новый node token и инвалидирует старый. Удалите локальный credentials file и перезапустите worker.

Это правило относится к обычному shared-token worker. Hardware-attested edge node перерегистрируется только тем же активным attestation key; после `wipe_pending/wiped/revoked` этот key навсегда заблокирован. Для replacement device используйте новое уникальное имя либо контролируемую будущую decommission/replacement процедуру — обходить revoke ручным редактированием state store нельзя.

В Docker Desktop Kubernetes актуальный enrollment token выводится командой `npm run --silent k8s:enrollment-token`. Он отличается от legacy admin token для режима без OIDC.

При включённом OIDC браузер использует Keycloak, а legacy admin token не является login password. Пароль локального `agat-admin` выводится `npm run --silent k8s:keycloak-password` и меняется через Keycloak. Не заменяйте Secret вручную: импортированный пользователь от этого не обновится.

## Изменение производительности

- слабая машина: `AGAT_WORKER_CONCURRENCY=1`, global `sequential`;
- несколько независимых GPU: задайте лимит каждого worker и global `parallel`;
- ноутбуки: global `auto`, передавайте battery env metrics при наличии интеграции ОС;
- неоднородный fleet: оставьте модель агента пустой, накопите реальные samples и выберите policy в разделе **Модели**; coverage и причины решения сверяйте по `routing.selected`;
- длинный inference: увеличьте `AGAT_LEASE_TTL_SECONDS`, если heartbeat network нестабилен, но оставьте renew включённым.

Model Router использует benchmark не старше 30 дней. Если throughput выглядит устаревшим, выполните репрезентативные stages на каждом worker; synthetic warmup при старте намеренно отсутствует. Настройка capabilities, VRAM и energy описана в [руководстве Model Router](./model-router.md).

Локальные worker-пулы можно создавать и останавливать в разделе **Узлы**. Остановка масштабирует управляемые Deployments до нуля; `npm run k8s:stop` делает то же самое для всех объектов с label `agat.local/managed=true`. Повторный `npm run k8s:up` сохраняет определения пулов, после чего их можно возобновить кнопкой **Запустить**.

## Недоступен SIEM sink

Раздел **Fleet / HA** показывает `pending`, `delivering`, `delivered`, `dead`, open DLQ, oldest pending и retention state. Успехом считается только 2xx с exact `X-Agat-Audit-Ack`, равным отправленному `X-Agat-Audit-Batch`. Retryable failure возвращает batch в `pending` с exponential backoff; permanent 4xx или исчерпанный лимит атомарно переводит event в `dead` и сохраняет redacted DLQ snapshot. Несколько replicas не экспортируют одну строку одновременно благодаря leased `SKIP LOCKED` claim.

1. Проверьте HTTPS identity/route sink без вывода Bearer в shell/log. Redirect не поддерживается намеренно.
2. Сопоставьте oldest pending с началом инцидента и убедитесь, что PostgreSQL доступен; не меняйте `audit_export_outbox` вручную.
3. Проверьте exact ack и дедупликацию `idempotencyKey=agat-audit-<eventId>`: timeout после фактического приёма законно создаёт повтор. Не отключайте `AGAT_SIEM_REQUIRE_ACK`, чтобы скрыть несовместимость sink.
4. Если credential отозван, ротируйте scoped sink token во внешней системе и Kubernetes Secret согласованной процедурой, затем перезапустите coordinator replicas по очереди.
5. После устранения root cause admin просматривает open DLQ и bounded batches возвращает нужные events через typed-confirmed replay; auditor имеет только project-scoped read. Resolve без delivery допустим лишь с внешним доказательством и change record.
6. Дождитесь нулевого backlog/open DLQ и сверяйте диапазоны `X-Agat-Audit-Batch`/event IDs, а не количество HTTP requests. Убедитесь, что retention maintenance выполнялась не старше двух intervals.

Raw event message/reason, prompts, outputs, tool arguments, sink response body и secrets не отправляются: export/DLQ содержат safe constant, hashes и allowlisted metadata. Open DLQ не удаляется retention task; authoritative `events` не удаляются вместе с operational outbox. Полные failure classes, API replay/resolve и restore invariants: [SIEM retention и DLQ](./siem-retention-dlq.md).

## Инцидент worker release

При подозрении на compromised build сначала нажмите **Revoke** в **Fleet / HA** и укажите incident ID. Coordinator помечает связанные server-worker nodes offline и запрещает им новые leases независимо от rollout percentage. Затем отзовите artifact/OCI во внешнем registry, остановите уже выполняющиеся процессы по их side-effect runbook и зарегистрируйте новый release с заново проверенным provenance admission.

Не удаляйте revoked manifest: он нужен audit и исключает повторное использование ID/digest. При компрометации provenance signer или local attestor удалите соответствующий public root, перезапустите coordinators, ротируйте private key/scoped broker token и re-enroll workers; увеличение attestation lifetime не является recovery. Rollback выполняется новым optimistic rollout update на проверенный fallback/target; private Ed25519 keys в coordinator не загружаются. Hardware-attested mobile release блокируется средствами Play/App Store/MDM и attestation policy, а потерянное устройство — отдельным remote wipe.

## Диагностика

```bash
npm run typecheck
npm test
npm run build
python3 workers/agat_worker.py --help
python3 -m py_compile workers/agat_worker.py workers/web_tools.py workers/telemetry.py
PYTHONPATH=workers python3 -m unittest discover -s workers -p 'test_*.py' -v
```

Для проверки реального LangGraph-пути сначала установите закреплённую зависимость. В разделе **Узлы** ожидаются runtime `single`, `langgraph` и profiles `tool_loop_v1`, `specialist_team_v1`; если показан только `single`, worker безопасно не получит LangGraph-stage:

```bash
python3 -m pip install --requirement workers/requirements.txt
PYTHONPATH=workers python3 -m unittest \
  workers.test_web_tools.ModelToolLoopTests.test_langgraph_runtime_executes_bounded_model_tool_graph -v
PYTHONPATH=workers python3 -m unittest \
  workers.test_web_tools.ModelToolLoopTests.test_specialist_team_runs_versioned_handoff_and_validated_state -v
```

Dry-run проверяет coordinator без model server:

```bash
python3 workers/agat_worker.py --once --dry-run
```

Для диагностики web-инструментов сначала проверьте `agat-search`, затем worker. Ошибка поиска возвращается модели как tool result и также появляется в журнале этапа без текста запроса:

```bash
kubectl get deployment,pod,service -n agat
kubectl logs -n agat deployment/agat-search --tail=100
kubectl logs -n agat deployment/agat-worker --tail=100
kubectl logs -n agat deployment/agat-gateway --tail=100
kubectl logs -n agat deployment/agat-keycloak --tail=100
kubectl logs -n agat deployment/agat-temporal-worker --tail=100
```

Для MCP сначала проверьте `lastError`, transport/profile hash и время catalog sync в разделе **MCP**, затем coordinator log. Worker никогда не соединяется с MCP endpoint или sandbox Pod напрямую. `waiting_approval` требует решения в карточке запуска; `expired` означает, что lease или approval TTL закончился. Для OCI ошибка про NetworkPolicy означает fail-closed operator gate, а не сбой image. Полные arguments/results в logs и overview намеренно отсутствуют — сверяйте `callId`, status, transport, profile/result SHA-256 и размер.

## Потерянный native edge device

1. `admin` открывает **Узлы**, выбирает hardware-attested device, указывает incident ID/причину и вводит имя узла для подтверждения remote wipe.
2. Сразу после ответа `202` проверьте `credentialState=wipe_pending`, `status=offline` и событие `edge.wipe.requested`. Это server-side revoke и не зависит от доставки команды устройству.
3. Убедитесь, что активный stage истёк и был возвращён в очередь. Его поздний completion от старого token должен получить `401`.
4. После следующего control poll ожидайте `credentialState=wiped`, `edge.wipe.acknowledged` и deletion flags. `localDataDeleted=false` требует MDM/device-response расследования, хотя node credential уже окончательно отозван.
5. Если устройство остаётся offline, не снимайте инцидент: примените MDM/platform erase, отзовите physical access и оцените данные модели/storage отдельно. AGAT не может доставить команду выключенному устройству.
6. Не переиспользуйте старый attestation key и не исправляйте state вручную в database. Replacement регистрируется как отдельное устройство.

## Аварийная блокировка MCP

Глобальный emergency deny предназначен для утечки credential, скомпрометированного MCP server, ошибочной policy или неконтролируемого side effect. Его включает только `admin`; согласование инцидента не должно задерживать первичную блокировку.

1. Откройте **MCP** и включите **Emergency deny**, указав номер инцидента и краткую причину. Эквивалентный API-вызов:

   ```http
   PUT /api/v1/mcp/emergency-deny
   Content-Type: application/json

   { "enabled": true, "reason": "INC-2026-041: suspected CRM token leak" }
   ```

2. Убедитесь, что ответ имеет `enabled: true`. `pendingCallsDenied` показывает немедленно отклонённые ожидающие calls; `executingCalls` — запросы, которые могли уже уйти во внешнюю систему.
3. Для каждого `executing` call найдите `callId`, server/tool, run/stage и policy hash в MCP audit/trace. Kill switch не отзывает уже отправленный HTTP request: при необходимости отключите MCP endpoint, отзовите upstream token и выполните vendor-specific cancel/compensation.
4. Не удаляйте server/call records до расследования. Экспортируйте связанный run trace и сопоставьте события `mcp.emergency_deny.enabled`, `mcp.call.emergency_denied` и `mcp.call.execution_blocked` с логами внешнего MCP server.
5. Исправьте policy или credential. Для credential задайте MCP scope, минимальные upstream права и новый expiry; скомпрометированный secret ротируйте во внешней системе до обновления АГАТ.
6. Выполните policy preview и проверьте `newlyAllowed`, `newlyDenied`, `approvalsIncreased/Decreased` и каждый effective change. Публикуйте только с актуальным `baseSha256`.
7. Снимите блокировку отдельным admin-действием и новой причиной:

   ```json
   { "enabled": false, "reason": "INC-2026-041 contained; token rotated; policy v4 reviewed" }
   ```

8. Выполните один безопасный read-only smoke call. Отклонённые calls автоматически не возобновляются: новый вызов должен получить новый `clientCallId` и пройти актуальную policy заново.

Switch хранится в authoritative state store и сохраняется после рестарта/смены coordinator replica. Если панель недоступна, используйте тот же authenticated API через Gateway; не редактируйте таблицу `settings` вручную, иначе будет потерян actor/reason audit.

Для inbound A2A сначала откройте раздел **A2A** и проверьте `enabled`, Agent Card URL, capabilities, MIME/file limits, token suffix/rotation time, active-task limit и связанный внутренний run. Затем проверьте protocol boundary:

```bash
curl -i 'http://127.0.0.1:8787/a2a/v1/endpoints/ENDPOINT_ID/agent-card.json'
curl -i \
  -H 'Authorization: Bearer ENDPOINT_TOKEN' \
  -H 'A2A-Version: 1.0' \
  'http://127.0.0.1:8787/a2a/v1/endpoints/ENDPOINT_ID/tasks?pageSize=1'
```

`401` означает wrong/rotated token; `VERSION_NOT_SUPPORTED` — отсутствующий или не `1.0` header/query; `CONTENT_TYPE_NOT_SUPPORTED` — неверный media type или MIME вне endpoint policy; `TASK_LIMIT_REACHED` — достигнут endpoint limit. `AUTH_REQUIRED` не является сбоем: оператор должен принять решение в обычной карточке run, после чего тот же task продолжит lifecycle.

Для push проверьте callback origin/auth suffix, endpoint capability и audit events `a2a.push.configured/delivered/failed`. `failed` после пяти попыток автоматически не возобновляется; исправьте receiver и создайте новый config. Для немедленной containment выключите endpoint/push capability или весь `AGAT_A2A_ENABLED`.

Для outbound peer проверьте exact interface/skill из Agent Card, auth mode/suffix, `tokenEndpointOrigin`, `AGAT_A2A_OUTBOUND_ENABLED` и task mirror. Ошибка private/link-local range, redirect или HTTP вне loopback является ожидаемым boundary deny. Delegated peer требует реальную OIDC session; legacy local admin не имеет subject token. Token endpoint получает dashboard bearer как `subject_token`, поэтому любое изменение этого trust выполняет только `admin` и оставляет origin в audit. При инциденте сначала выставьте `AGAT_A2A_OUTBOUND_ENABLED=false`, затем отзовите static/OAuth client credential у peer и перезапустите coordinator. Подробности: [A2A interoperability](./a2a-adapter.md).

Для Local RAG сначала проверьте карточку collection: точное имя `embeddingModel`, число `embeddingWorkers`, `pendingJobs` и ошибку документа. Затем проверьте установленную модель и endpoint:

```bash
ollama list
curl -fsS http://127.0.0.1:11434/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"embeddinggemma","input":["health check"]}'
kubectl logs -n agat deployment/agat-worker --tail=100
```

Если indexing готов, но ответ не использует знания, убедитесь, что collection отмечена именно в окне этого run/process, а в trace есть `knowledge.retrieved`. Пустой список hits при готовых chunks чаще всего означает другое имя/размерность embedding-модели или нерелевантный query. Подробности: [Local RAG и управляемая память](./local-rag-and-memory.md).

Если model endpoint отвечает ошибкой на поле `tools`, выбранная модель или server не поддерживает function calling. Отключите web через `AGAT_WEB_ENABLED=false` и запретите MCP tools для такого stage либо выберите модель/OpenAI-compatible server с function calling. Настройка встроенных web tools описана в [руководстве по web-доступу](./web-access.md), gateway — в [руководстве MCP](./mcp-gateway.md).
