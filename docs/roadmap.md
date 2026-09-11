# Roadmap и дополнительные фичи

Актуализировано **8 сентября 2026 года** по `main`, commit `de27ed0`, коду активных экранов, отчётам проверок и текущему локальному UI. Версия пакетов остаётся `1.7.0`; выполненные UX-инкременты сами по себе не означают выпуск 1.8 или 1.9.

## Текущее состояние и ближайшая очередь

| Трек | Подтверждённое состояние | Что осталось | Очередь |
|---|---|---|---|
| Operator UX 1.8 | Основной интерфейс `UX-101`–`UX-110` реализован; release gate полностью не закрыт | Реальные устройства, screen reader, usability, populated Quality, интеграционные и multi-user проверки | P0: закрыть gate и устранить найденные дефекты |
| Creator/admin 1.9 | Сентябрьская переработка частично закрыла `UX-202`, `UX-204`, `UX-210`; основной scope `UX-205` реализован | Полный workspace агента, Basic/Advanced, Quality/Knowledge/Integrations/Infrastructure, guard остальных форм | P1, после текущих gates |
| Marketplace / Solution packs | 13 шаблонов ролей и установка копии; `SP-101` и `SP-005` частично | Manifest/provenance, схемы, коннекторы, eval, lifecycle и пять runnable packs | P0: сначала foundation и проверяемый `SP-102` |
| Использование продукта | Проведён [обзор сценариев и барьеров](./product-usage-research-2026-09-08.md); production-метрик использования нет в проверенных материалах | Первый полезный результат, готовность источников, принятие результата, повторное использование | P0: `USE-001`, `USE-002`, `USE-004`, `USE-008`, `UX-306`, `UX-307` |
| Production Fleet / DR | Этапы 1–6 отмечены реализованными в репозитории | Qualification каждого реального контура; tasks `OPS-101`–`OPS-105` ниже | Обязательный gate перед production cutover |

Статусы: **готов основной scope** — реализовано описанное поведение с указанными ограничениями проверки; **частично** — есть и кодовый остаток; **запланировано** — нет подтверждения полного результата. «Проверено в локальном UI» не означает production qualification, прохождение usability или сквозного LLM/Temporal-сценария.

Ближайшие задания по ролям исполнителей:

1. **QA + UX:** завершить оставшиеся проверки 1.8 и `UX-307`; свежая проверка использует уже переработанные экраны. Повторно разрабатывать `UX-101`–`UX-110` как новые эпики не нужно.
2. **Backend + QA:** реализовать `UX-111` до многопользовательского пилота согласований; начать `SP-001` с backend provenance установленного шаблона, затем общий schema contract `SP-002`.
3. **Frontend + backend:** `USE-002` → `USE-001`, дополнить существующий `SP-005`; минимальный контракт `UX-306` определить до реализации этих событий.
4. **Agent/eval + владелец процесса:** собрать `SP-102` на существующем RAG и шести P0-ролях, добавить `USE-004`, провести `USE-008`. `CON-001` начинается как общий контракт; конкретный connector входит в этот пилот только при необходимости источника.
5. **DevOps + security:** `OPS-101`–`OPS-105` по мере выбора production-контура. Локальный Compose без Temporal не закрывает проверку расписаний и HA.

Это рекомендуемые роли, а не назначение конкретным людям или запуск новых задач агентов. Сроки не обещаны: команда, владельцы пилотов и capacity ещё не подтверждены. Существующие ID сохранены; новые задачи уточняют разрывы и не заменяют родительские эпики.

## История реализации

Разделы версий ниже сохраняют историю поставленных возможностей. Последующие релизы могут закрывать ограничения предыдущих; актуальный остаток определяется таблицами текущих треков и release gates.

## Уже в MVP

- resource-aware очередь с `sequential / parallel / auto`;
- распределённые outbound-only workers;
- OpenAI-compatible local models;
- approval gate;
- live event log;
- мобильный операторский экран;
- retry, lease TTL и at-least-once recovery;
- базовая token security;
- визуальный process builder с агентными шагами, условиями и bounded loops;
- неизменяемые версии схем, экземпляры процессов и история переходов;
- Keycloak OIDC/PKCE, роли и project isolation;
- Kong DB-less Gateway;
- Temporal durable runtime для новых process instances;
- capability-aware `single | langgraph` runtime внутри agent stage без дублирования durable state.
- W3C/OTLP agent traces, immutable execution manifest и безопасный agent-only replay;
- A/B comparison по completion, latency и token budget без ложного quality score.
- project-scoped Local RAG с локальными embeddings, provenance, TTL working memory и явно сохраняемой episodic memory.
- A2A 1.0 HTTP+JSON interoperability: inbound/outbound, delegated OAuth, SSE/push, bounded files и trace correlation.
- production Temporal transport, replay gate, Worker Deployment Versioning, interval Schedules, Updates и parent→child workflows.
- native Android/iOS edge workers с hardware attestation, device-scoped credentials и remote wipe.
- Fleet/HA-cell на PostgreSQL с несколькими coordinator replicas, residency, signed rollout, FORCE RLS и SIEM outbox.

## Реализовано в 0.3

### Визуальные процессы

- BPMN-подобный canvas с drag-and-drop шагами и именованными ветками;
- `start / agent / http / transform / wait / approval / artifact / condition / loop / end`;
- явная обратная связь `repeat` и обязательная ветка `exit`;
- лимит итераций, защита общим бюджетом переходов и проверка достижимости завершения;
- черновик, публикация версии, запуск, approval, live-статус и отмена;
- n8n-подобные picker/search/edge insertion, undo/redo, autosave, test node и run-from-node;
- encrypted credentials, безопасные выражения и управляемый public HTTP;
- Temporal timers/signals и ссылка из instance в Temporal UI.

### Архитектурный runtime агентов

- runtime и bounded versioned profile сохраняются в каталоге агента;
- workers публикуют `agentRuntimes`, scheduler сопоставляет модель и runtime;
- LangGraph `StateGraph` выполняется только внутри одного lease и не попадает в deterministic Temporal Workflow;
- Docker worker включает закреплённый LangGraph, переносимый worker сохраняет dependency-free режим `single`;
- graph state наблюдаем через существующий trace, но не создаёт отдельный durable checkpointer.

## Реализовано в 0.4

### OpenTelemetry и сквозной trace

- run получает стабильный trace ID даже при выключенном exporter;
- W3C Trace Context связывает coordinator dispatch, worker agent, model и tool spans;
- OTLP/HTTP export включается явно и не передаёт prompt/output content;
- GenAI spans используют актуальные `invoke_agent`, `chat` и `execute_tool` conventions;
- worker возвращает latency, model/tool call count и provider token usage.

### Manifest, replay и eval foundation

- agent prompt/model/runtime фиксируются immutable snapshot до выполнения;
- worker/version/tool-schema snapshot и SHA-256 hashes входят в execution manifest;
- UI и API создают один replay или два A/B-кандидата на точном исходном input;
- replay ограничен terminal agent-only runs и не повторяет process/HTTP side effects;
- comparison показывает completion, latency, tokens, tool/model calls и output hash;
- quality честно остаётся `N/A`: golden datasets, rubric и manual/judge evaluation ещё не реализованы.

Подробности: [OpenTelemetry, execution manifest, replay и eval](./observability-replay-evals.md).

## Реализовано в 0.5

### MCP gateway с policy engine

MCP остаётся главным стандартом подключения tools и context. Ревизия спецификации от 28 июля 2026 добавила stateless core, header-based routing, cache hints, формальные extensions и усиление authorization. Для АГАТ это означает gateway, который кэширует catalogs, маршрутизирует по `Mcp-Method/Mcp-Name`, применяет allowlist и запрашивает approval по уровню риска. Источник: [официальный анонс MCP 2026-07-28](https://blog.modelcontextprotocol.io/posts/2026-07-28/).

- официальный MCP TypeScript SDK 2.0 и закреплённая ревизия `2026-07-28` без silent downgrade;
- project-scoped Streamable HTTP servers, encrypted Bearer/header credentials и TTL catalogs;
- namespace-safe public tool names, deny-by-default server policy и per-tool overrides;
- недоверенные по умолчанию annotations и risk `read/write/destructive/unknown`;
- approval непосредственно перед upstream call, lease-scoped idempotency и блокировка retry после неопределённого side effect;
- зашифрованные arguments/results, redacted operator preview, hash/size audit и связанные OTel spans;
- отдельный responsive-раздел MCP и единая очередь stage/tool approvals.

Подробности: [MCP gateway и risk policy](./mcp-gateway.md).

## Реализовано в 0.6

### Model Router и профили железа

Worker публикует RAM/VRAM, context/capabilities/quantization и energy signals. Coordinator пассивно измеряет throughput по реальным model calls, хранит EWMA и выбирает пару node/model по `balanced / performance / efficiency`. Quality threshold принимает только доверенную внешнюю оценку, неизвестные профили управляются явной compatibility policy. Решение попадает в trace/manifest, а retry переключается на следующую альтернативу. Для провайдер-независимости inference остаётся OpenAI-compatible; Ollama discovery использует официальные native endpoints. Подробности: [Model Router и hardware benchmarks](./model-router.md).

## Реализовано в 0.7

### Local RAG и управляемая память

- project-scoped collections и выбор их snapshot для run/process;
- pull-based embeddings на локальном worker через `/v1/embeddings`;
- provenance каждого выбранного фрагмента в отдельном trace event;
- working memory с TTL и очисткой;
- episodic memory только после явного сохранения;
- каскадное удаление и project JSON export;
- отдельный responsive Knowledge UI и RAG-фильтр trace.
- воспроизводимый real-Ollama E2E для document/query embeddings, retrieval provenance и model answer.

Главный критерий — не «больше памяти», а проверяемый источник и контролируемый lifecycle.

Подробности: [Local RAG, provenance и управляемая память](./local-rag-and-memory.md).

## Реализовано в 0.8

### Golden eval и prompt registry

- immutable golden datasets и knowledge fingerprints;
- append-only human rubric и опциональный локальный model judge с audit trail;
- версионированный prompt registry поверх сохраняемых prompt hashes;
- matching quality gate перед promotion prompt/model;
- batch runs набора examples через production scheduler/worker path;
- отдельный responsive раздел **Golden eval**.

Подробности: [Golden eval и prompt registry](./golden-eval-prompt-registry.md).

## Реализовано в 0.9

### A2A adapter

Project-scoped adapter подключает внешние agent platforms без переноса durable orchestration из Temporal и без выдачи прямого доступа к локальному fleet.

- прямой Agent Card и A2A 1.0 HTTP+JSON interface;
- отдельный hashed bearer token на endpoint с one-time выдачей и rotation;
- `Send/Get/List/Cancel Task`, idempotency по `messageId` и cursor pagination;
- mapping task в обычный одноагентный run с Local RAG, approval и scheduler policy;
- наследование W3C `traceparent` и переход из task в полный внутренний run trace;
- inline `text/plain`/`application/json`, финальный `text/plain` artifact и запрет file/url/raw boundary;
- responsive A2A registry и task audit в панели;
- явный отказ от streaming, push и раскрытия внутренних prompts/tools/memory.

Подробности: [A2A adapter](./a2a-adapter.md).

## Реализовано в 1.0

### Production hardening durable runtime

Эксплуатационная устойчивость durable control plane закрыта без преждевременного включения HA.

- реальные replay fixtures Temporal включены в обычный CI gate;
- Cloud/self-hosted profiles fail-closed требуют TLS/authentication и immutable worker build;
- Worker Deployment Versioning, canary/ramp/promote/status и rollback описаны и автоматизированы;
- подтверждаемые Updates сохраняют Signal fallback для pre-1.0 histories;
- interval Schedules запускают отдельные parent→child workflows;
- PostgreSQL state-store спроектирован, но незавершённый driver намеренно заблокирован;
- локальный `start-dev` явно отделён от production deployment.

Подробности: [Production hardening durable runtime](./production-durable-runtime.md) и [PostgreSQL state-store design](./postgresql-state-store-design.md).

## Реализовано в 1.1

### Risk-tier approvals и emergency deny

Существующие MCP risk labels, server/tool approvals, OIDC RBAC и audit trace объединены в fail-closed enterprise policy boundary.

- project-scoped immutable policy-as-code с defaults/rules, SHA-256 и optimistic activation после preview;
- tiers `low/elevated/high/critical` и effective `allow/approval/deny` поверх legacy policy floor;
- redacted preview diff для каждого call и before/after diff влияния candidate policy на текущие catalogs;
- обязательные два distinct OIDC approver для `critical` и `destructive` side effects;
- credentials scopes по namespace/tool/risk/catalog/expiry без раскрытия secrets worker;
- persisted глобальный emergency deny с admin-only RBAC, немедленным отказом waiting/new calls и final upstream preflight;
- расширенные SQLite audit records, UI, worker metadata и OTel policy attributes.

Подробности: [Risk-tier approvals и emergency deny](./mcp-risk-tier-approvals.md) и [MCP gateway](./mcp-gateway.md).

## Реализовано в 1.2

### Расширение process builder

- deterministic parallel fork/join с durable execution tokens и стабильным merge output;
- interval/cron/calendar Temporal Schedules, idempotent start/signal webhooks и отдельный внешний signal node;
- version-pinned subprocess, bounded nesting и переиспользуемые process templates;
- structural version diff и safe/live replay terminal instance на точной исходной версии;
- безопасный BPMN 2.0 import/export с BPMN DI и AGAT extensions;
- deterministic idempotency keys и reverse-order compensation только после подтверждённого side effect;
- responsive release-панель, новые runtime statuses и SQLite migration v15.

Подробности: [Process Builder 1.2](./process-builder-1.2.md) и [Визуальные процессы](./processes.md).

## Реализовано в 1.3

### LangGraph specialist teams

Текущий `tool_loop_v1` расширен версионированным `specialist_team_v1` без переноса durable orchestration из Temporal.

- supervisor выбирает только project-scoped участников из immutable ordered snapshot;
- каждый specialist выполняется отдельным bounded `tool_loop_v1` subgraph со своим pinned prompt/model;
- supervisor принимает структурированные `delegate/finish` решения, а общий `maxHandoffs` ограничен `1..8`;
- `specialist_team_state_v1` проверяется на каждом переходе, произвольные Python graphs и state schemas не загружаются;
- worker публикует `agentRuntimeProfiles`, scheduler требует профиль и все модели команды на одном узле;
- handoff audit и OTel metadata не сохраняют raw assignment/output или скрытое reasoning;
- execution manifest v3 и replay сохраняют точные definition hashes участников;
- approvals, MCP policy, credentials и emergency deny продолжают исполняться coordinator gateway.

Подробности: [LangGraph specialist teams 1.3](./langgraph-specialist-teams.md) и [Runtime агентов](./agent-runtimes.md).

## Реализовано в 1.4

### Расширенная A2A interoperability

- project-scoped outbound peers с bounded Agent Card discovery и exact HTTP+JSON 1.0 negotiation;
- outbound `message:send`, task polling/cancel, encrypted request/response и redacted task mirrors;
- transport auth `none`, static Bearer и delegated RFC 8693 token exchange без persistence user/peer access tokens;
- inbound `message:stream` и task subscription через ordered SSE;
- persistent push configs, encrypted Bearer credentials, durable retry outbox и idempotent deletion;
- opt-in bounded inline raw file input и agent file output по явным MIME modes;
- SSRF-safe DNS/IP pinning без redirects, private/link-local/mapped range deny и production HTTPS;
- отдельные inbound/outbound kill switches, project RBAC/audit и responsive peer/invoke UI;
- SQLite migration v17 и A2A adapter 1.1.0.

Подробности: [A2A interoperability 1.4](./a2a-adapter.md). Источник контракта: [официальная спецификация A2A](https://a2a-protocol.org/latest/specification/).

## Реализовано в 1.5

### Изолированное выполнение tools

- один policy boundary для HTTP, WASI и digest-pinned OCI MCP tools;
- bounded WASI Preview 1 module под wasmtime-py 48.0.0 без preopened filesystem/network capabilities и с fuel metering;
- одноразовый Kubernetes Job с non-root UID, read-only root, RuntimeDefault seccomp, drop ALL, resource/deadline limits и `backoffLimit=0`;
- default-deny `NetworkPolicy` и не более 16 exact public-IP/TCP rules; OCI fail-closed без подтверждённого enforcing CNI;
- optional operator-managed gVisor/Kata RuntimeClass вместо ложного обещания встроенной microVM;
- ephemeral immutable Secret broker для arguments/module/scoped credential; при emergency deny egress policy снимается только после подтверждённой остановки Pod;
- admin-only executable profile, encrypted module/profile, redacted API summary и profile hash в SQLite audit/OTel;
- responsive WASI/OCI editor и SQLite migration v18.

Подробности: [Изолированное выполнение MCP tools](./isolated-tool-execution.md).

## Реализовано в 1.6

### Native edge worker

- Android foreground service с pinned llama.cpp, Vulkan/CPU, legacy NNAPI capability probe и managed GGUF import;
- iOS app с Core ML/Metal, foreground loop и opportunistic BackgroundTasks в пределах ограничений ОС;
- one-time challenge и обязательная server-side Play Integrity/App Attest verification через HTTPS broker;
- hardware-attested, work/control-scoped node credential в Android Keystore или ThisDeviceOnly Keychain;
- немедленный server-side revoke/requeue и централизованный remote wipe с generation/ack audit;
- scheduler deny для MCP/HTTP/embedding и runtime кроме bounded `single/tool_loop_v1`;
- operator UI с admin RBAC, причиной и typed confirmation.

Web/PWA остаётся control surface и не обещает надёжный background inference там, где ОС его запрещает.

Подробности: [Native edge worker 1.6](./native-edge-worker.md).

## Реализовано в 1.7

### Fleet и HA

- PostgreSQL backend для нескольких coordinator replicas и exact project-scoped task queues/quotas;
- одна authoritative HA-cell на `region/residencyDomain`, regional worker placement и запрет online cross-cell relocation;
- artifact bytes в PostgreSQL для независимого download с любой replica;
- canonical Ed25519 worker manifests, trust roots, expiry/revoke и deterministic target/fallback staged rollout;
- отдельные system/tenant DB roles, request-scoped tenant context, FORCE RLS и exact least-privilege grants;
- transactional audit outbox, leased `SKIP LOCKED` export, redacted NDJSON и at-least-once SIEM delivery;
- Fleet/HA UI, Docker Compose PostgreSQL profile и Docker Desktop Kubernetes с двумя coordinator replicas, rolling update и PDB;
- schema migration v20 и отдельный disposable PostgreSQL integration suite.

Подробности: [Fleet и HA 1.7](./fleet-ha-1.7.md) и [ADR-017](./adr-017-fleet-ha-cell.md).

## Следующий релиз

### Production Fleet readiness и DR

- **Готово, этап 1:** canonical offline SQLite→PostgreSQL migrator, artifact backfill, reconciliation/verify report и rehearsal rollback;
- **Готово, этап 2:** отдельная migration Job/role, catalog drift gate, DDL-free runtime role и connection admission/load testing;
- **Готово, этап 3:** managed multi-AZ/PITR evidence gate, DR canaries (введены в v22, текущая schema v25), фактический physical restore/failover rehearsal и HMAC-sealed RPO/RTO/SLO evaluation; каждый production cluster всё равно обязан пройти собственную provider qualification;
- **Готово, этап 4:** versioned S3-compatible artifact authority, conditional PUT/HEAD reconciliation, cross-replica verified cache, retention/legal hold, exact-version delete outbox, safe lifecycle merge и bounded BYTEA backfill;
- **Готово, этап 5:** residency-aware whole-cell region-loss DR без active-active writes: sealed PostgreSQL/S3/Temporal evidence, residency policy, four-eyes, snapshot-bound activation, monotonic write epoch и safe failback;
- **Готово, этап 6:** verified OCI/SLSA provenance admission с отдельным trust root, one-time SPIFFE-compatible runtime attestation и refresh; exact SIEM batch acknowledgement, bounded retry, redacted retained DLQ, RBAC replay/resolve и operational retention.

Подробности: [worker supply-chain attestation](./worker-supply-chain-attestation.md), [SIEM retention/DLQ](./siem-retention-dlq.md), [ADR-020](./adr-020-worker-supply-chain-attestation.md) и [ADR-021](./adr-021-siem-retention-dlq.md).

### Что осталось после этапов 1–6

Запланированные repository capabilities этого блока отмечены закрытыми в предыдущих этапах. Перед production cutover остаётся qualification каждого конкретного контура. На 8 сентября свежий комплект evidence такого контура в этой актуализации не проверен; задачи ниже **открыты**, а наличие кода не является их закрытием.

| ID | Задача и критерий приёмки | Зависимости | Роль исполнителя |
|---|---|---|---|
| OPS-101 | Подтвердить managed PostgreSQL cell: актуальный provider evidence, измеренные restore/failover/RPO/RTO/SLO и пройденный admission gate | Выбранный provider, region/residency и конкретный cluster | DevOps + DBA |
| OPS-102 | Выполнить migration и rollback rehearsal на копии целевого объёма данных; reconciliation без необъяснённых расхождений, документированное окно простоя | OPS-101, доступная согласованная копия данных | DBA + QA |
| OPS-103 | Проверить сохранение версий S3, lifecycle/legal hold и fencing в выбранных регионах; evidence привязан к exact bucket/version и DR-политике | Целевые S3/region, OPS-101 | DevOps + storage |
| OPS-104 | Подключить pinned Sigstore release pipeline, trusted local attestor и реальный SIEM sink; проверить reject недоверенной сборки, exact ack/dedupe и DLQ/replay | Реальные CI/trust roots/attestor/SIEM | DevOps + security |
| OPS-105 | Провести cross-system region-loss game day и review scoped secrets/RBAC/trust roots; сохранить решение об activation/failback и измеренные результаты | OPS-101–OPS-104 | DevOps + security + владелец сервиса |

Все пять — обязательные задачи перед соответствующим production cutover. Evidence и протоколы сохраняются в `/docs`; тестовый локальный контур не подменяет выбранный production deployment.

Следующий продуктовый релиз определён как **1.8 — Operator UX foundation**. Его scope сформирован по результатам [UX/UI-аудита всех страниц и пользовательских сценариев](./ux-ui-audit.md). Production qualification Fleet остаётся обязательным параллельным gate и не заменяется UI-работами.

По результатам [приоритизации агентов и процессов на сентябрь 2026 — август 2027](./agent-process-priorities-2026.md) также открыт параллельный продуктовый трек **Solution packs**. Он не расширяет release gate 1.8: календарные волны задают порядок разработки, а привязка эпиков к версиям выполняется только после capacity planning.

### 1.8 — Operator UX foundation: основной scope реализован, gate открыт

Цель релиза: сократить когнитивную нагрузку в ежедневной работе оператора, исправить мобильную навигацию и доступность, не удаляя экспертные возможности платформы.

#### P0-задачи релиза 1.8

ID ниже обозначают roadmap-эпики. Перед разработкой каждый эпик декомпозируется в design/spec, frontend, accessibility и test-задачи с владельцем и спринтом. Размер относительный: `S` — локальное изменение, `M` — несколько связанных компонентов, `L` — сквозное изменение shell или пользовательского процесса.

| ID | Задача | Результат | Зависимости | Размер |
|---|---|---|---|---:|
| UX-101 | Перестроить информационную архитектуру по задачам и ролям | Первый уровень: `Обзор`, `Запуски`, `Согласования`, `Создание`, `Администрирование`; недоступные разделы не создают тупиков | — | L |
| UX-102 | Заменить мобильную навигацию | Один ряд из `Обзор`, `Запуски`, `Согласования`, `Процессы`, `Ещё`; infrastructure-разделы доступны внутри `Ещё` с учётом роли | UX-101 | M |
| UX-103 | Упростить глобальный topbar | Убраны повторяющиеся queue/node/agent metrics и дубли CTA; добавлены рабочие уведомления и меню профиля | UX-101 | M |
| UX-104 | Обновить типографику и размеры controls | Основной текст ≥14 px, вторичный ≥12 px, touch targets ≥44×44 px; исключения задокументированы | — | L |
| UX-105 | Закрыть базовые accessibility-разрывы | Skip link, корректные tabs, `aria-selected`, focus trap/return, Escape, понятные disabled prerequisites | UX-104 | M |
| UX-106 | Пересобрать Runs как status-first workspace | Первый экран отвечает на вопросы `что происходит`, `что требуется`, `каков результат`; trace/policy/hashes перенесены в технические детали | UX-104 | L |
| UX-107 | Создать единый inbox согласований | Очередь решений с действием, целью, эффектом, риском, инициатором, сроком, комментарием и audit trail | UX-101, UX-106 | L |
| UX-108 | Упростить создание запуска | Flow `Задача → Цепочка → Проверка и запуск`, явный reorder агентов, понятные priority presets, advanced defaults свёрнуты | UX-106 | M–L |
| UX-109 | Устранить mobile layout defects | Нет horizontal overflow на 390 px; исправлены Fleet, bottom nav, Golden eval master-detail и перекрытие контента fixed-навигацией | UX-102, UX-104 | M |
| UX-110 | Унифицировать опасные и глобальные действия | Нет прямого logout по аватару, декоративного terminal control и системных `window.prompt/confirm` для критических операций | UX-103, UX-105 | M |

#### Прогресс Operator UX на 8 сентября 2026 года

Первые два инкремента зафиксированы в [Operator UX foundation](./design/ux-shell-baseline.md), status-first Runs — в [спецификации UX-106](./design/ux-106-runs-status-first.md), единый inbox — в [спецификации UX-107](./design/ux-107-approval-inbox.md), новый запуск — в [спецификации UX-108](./design/ux-108-new-run-wizard.md), единые значимые действия — в [спецификации UX-110](./design/ux-110-action-dialog.md). Дополнительно учтены [сентябрьское рабочее пространство и проверки 6 сентября](./design/2026-09-workspace/design.md) и [Docker-срез 8 сентября](./local-docker.md). Статусы ниже относятся к указанному scope, а не ко всему release gate 1.8.

| ID | Статус | Сделано | Остаётся |
|---|---|---|---|
| UX-101 | Основной scope готов | Ролевая матрица, task-oriented группы, русские labels, breadcrumbs, защита deep links и read-only actions; run/approval deep links реализованы | Shareable routes остальных сущностей остаются в UX-302 |
| UX-102 | Готово | Один mobile-ряд из пяти пунктов и ролевой sheet `Ещё` | Проверка на реальных iOS/Android устройствах входит в release gate |
| UX-103 | Готово | Убраны дубли metrics/CTA; кнопка открывает project-scoped панель с pending approvals, warn/error-событиями, seen/unseen, `Прочитать всё` и точными переходами; profile menu явное | Server-side синхронизация seen-state между браузерами не входит в scope 1.8 |
| UX-104 | Базовый gate готов | 14/12 px для стандартных страниц, controls ≥44 px, responsive Fleet; populated Process проверен на 1280/390 px в сентябрьском инкременте | Полная матрица viewport/zoom, системная visual regression fixture и отдельная проверка читаемости масштабированного canvas |
| UX-105 | Базовый gate готов | Рабочий skip link; WAI-ARIA tabs с roving focus; подписанные dialogs; focus trap/return, Escape и объяснение недоступного experiment; keyboard-only smoke пройден | Screen reader и реальные устройства входят в release gate |
| UX-106 | Основной scope готов | Master-detail list, status/current stage/duration/result/error/next action, state-valid Repeat/Cancel/Open, inline cancel confirmation, lazy technical disclosure и shareable run/approval routes | Реальные устройства, screen reader и operator usability-test входят в release gate |
| UX-107 | Основной scope готов | Единый project-scoped inbox, summary и фильтры, risk/effect/deadline context, точные actions, redacted MCP disclosure, комментарий, обязательная причина отказа, audit-only resolved state и shareable approval routes; desktop/mobile Browser QA пройден | Кодовые остатки выделены в UX-111/UX-112; реальные устройства, screen reader, multi-user race и usability ещё требуют подтверждения |
| UX-108 | Основной scope готов | Трёхшаговый wizard, один безопасный default-agent, явный reorder, optional knowledge, summary/readiness, priority presets, journal-default и advanced disclosure; desktop/mobile Browser QA пройден | Реальный coordinator/worker e2e, реальные устройства, screen reader и operator usability-test входят в release gate |
| UX-109 | Выявленные дефекты исправлены | Ранее 36 route/viewport checks без overflow; Fleet и fixed bottom nav исправлены; сентябрьский populated Process проверен на 1280/390 px | Повтор полной матрицы после редизайна, реальные устройства, populated Quality и сложные Process fixtures |
| UX-110 | Основной scope готов | Аватар открывает меню; logout явный; декоративный topbar control удалён; 15 системных `prompt/confirm` заменены единым product dialog с точным объектом, последствиями, восстановлением и optional/required reason; desktop/mobile Browser QA пройден | Реальные устройства, screen reader и operator usability-test входят в release gate |

#### Выделенные остатки согласований

Эти задачи конкретизируют `UX-107`. `UX-111` нужен до многопользовательского пилота; `UX-112` — до обещания полного audit archive. Исторический scope базового inbox не считается равным этим расширениям.

| ID | Приоритет / статус | Задача и критерий приёмки | Зависимости | Исполнитель / размер |
|---|---|---|---|---|
| UX-111 | P0 / запланировано | Сохранять deadline MCP approval в store/DTO при создании; смена TTL и coordinator replica не меняет срок существующего запроса. Миграция явно задаёт политику legacy-записей; проверены expiration/decision races и distinct approvers | UX-107, SQLite/PostgreSQL stores, policy engine | Backend + QA / M |
| UX-112 | P1 / запланировано | Добавить cursor API полного журнала решений и UI поиска по объекту/периоду/статусу; история за пределами overview event window доступна в рамках RBAC, без пропусков/дублей при пагинации | UX-107, audit store, project RBAC | Backend + frontend + QA / M–L |

#### Детализация задач 1.8

**UX-101 — ролевая информационная архитектура**

- определить primary navigation для `admin`, `designer`, `operator`, `viewer`, `auditor`;
- объединить `Узлы`, `Модели`, `Fleet / HA` в `Администрирование → Инфраструктура`;
- объединить `MCP` и `A2A` в `Администрирование → Интеграции`;
- переименовать `Knowledge` в `Знания`, `Golden eval` — в `Качество`, сохранив протокольные термины в справке;
- для read-only ролей показывать представление без editor chrome и недоступных primary actions;
- добавить breadcrumbs и сохранить прямой переход к текущим сущностям.

**UX-106 — status-first Runs**

- оставить в основном представлении статус, текущий этап, длительность, результат, ошибку и следующее действие;
- перенести resource policy, trace filters, manifest, hashes и Replay / Eval в `Технические детали`;
- убрать Node Rail со страницы запусков;
- сделать выбранный run отдельным shareable route или устойчивым detail drawer;
- добавить явные `Повторить`, `Отменить`, `Открыть результат` только для допустимых состояний;
- каждое disabled действие сопровождается объяснением prerequisite.

**UX-107 — inbox согласований**

- отдельный пункт навигации и счётчик непросмотренных решений;
- фильтры по проекту, риску, типу действия и сроку;
- конкретные action labels, например `Разрешить Редактору сформировать отчёт`;
- причина отклонения и необязательный комментарий при согласовании;
- для MCP — redacted arguments, preview diff, policy reason и число требуемых approvers;
- после решения сохраняется и показывается audit trail без повторного выполнения действия.

**UX-108 — новый запуск**

- на первом шаге только название и задача;
- на втором — агенты, порядок и optional knowledge;
- на третьем — summary, readiness, режим, назначение результата и подтверждение;
- до раскрытия advanced показывать не более семи пользовательских решений;
- не выбирать все встроенные агенты и Artifact Store без понятного объяснения;
- заменить числовой priority 0–100 на `Обычный / Высокий / Срочный`, сохранив число в advanced.

**UX-110 — единые значимые действия**

- один product dialog для destructive, warning и promotion actions;
- до side effect показывать точный объект, последствия и способ восстановления;
- использовать точный action label вместо generic `OK` / `Подтвердить`;
- запрашивать reason только когда он отправляется в API или audit, с inline-валидацией обязательности;
- Cancel, Close, Escape и размонтирование всегда дают безопасный результат и возвращают focus триггеру;
- запрещать возврат blocking `window.prompt/confirm` unit guard-ом.

#### Release gate 1.8

Релиз 1.8 считается готовым, когда одновременно выполнены условия:

- не более шести пунктов первого уровня на desktop и пяти на mobile;
- sidebar и bottom nav адаптируются к пяти ролям;
- при 390×844 нет horizontal overflow ни на одной из 11 страниц;
- mobile navigation занимает один ряд и не перекрывает конечные действия форм;
- основной текст не меньше 14 px, вторичный — 12 px, интерактивные цели не меньше 44×44 px;
- кнопка уведомлений открывает панель, avatar открывает profile menu;
- нет видимых controls без действия;
- новый запуск выполняется по трёхшаговому flow;
- согласование содержит достаточный контекст и допускает комментарий;
- tabs и модалки проходят keyboard-only smoke test;
- сценарии проверены на 390, 768, 1280 и 1440 px;
- operator usability-test: минимум 4 из 5 участников без подсказки находят требующее решения действие и корректно завершают его.

Оставшиеся проверяемые результаты на 8 сентября:

- [ ] Полная viewport-матрица 390/768/1280/1440 px на актуальном редизайне, с заполненным Quality, разветвлённым Process и длинными данными; зафиксировать исключения typography/touch/zoom.
- [ ] Реальные iOS/Android устройства, keyboard-only и screen-reader проходы; проверить все пять ролей, read-only состояния, возврат фокуса и deep links.
- [ ] Новый запуск через настоящий coordinator/worker/LLM: ввод → знания → генерация → approval → результат/download; отдельно проверить ошибки embeddings/model и исчезновение worker. Существующий `test:ollama-rag` использовать как часть evidence, а не замену проверки полного пользовательского пути.
- [ ] Multi-user approval races, отказ, просрочка и critical four-eyes; внедрение persisted deadline отслеживается в UX-111.
- [ ] Usability-test 4 из 5 с протоколом ошибок и повтором после исправлений (`UX-307`).

Сентябрьский E2E из design-отчёта выполнял системное преобразование без LLM; он не закрывает пункт о реальном агенте. Temporal schedule/webhook E2E остаётся отдельным gate автоматизации (`USE-006`); текущий локальный Compose его не подтверждает.

### Использование продукта — первый полезный результат и повторный сценарий

Основание: [исследование использования Агата от 8 сентября](./product-usage-research-2026-09-08.md). Исследование включает код, локальный UI и первичные внешние источники; интервью, production adoption и ROI пока не измерены. Поэтому приоритет ниже — проверяемая продуктовая гипотеза. Все новые `USE-*` задачи **запланированы**; уже имеющиеся части описаны в исследовании, их следует переиспользовать.

Это отдельный трек пилота, а не автоматическое расширение release gate 1.8. P0 означает ближайшую очередь пилота; зависимости соблюдаются до начала зависимого scope.

| ID | Приоритет | Задача и критерий приёмки | Зависимости | Исполнитель / размер |
|---|---|---|---|---|
| USE-001 | P0 | Провести пользователя от выбранного сценария до первого полезного результата: шаблон → данные → готовность → тест → результат. Есть безопасный пример, сохранение прогресса, передача шага администратору и повтор после ошибки. В usability-проверке минимум 4 из 5 проходят выбранный путь без подсказки; время и остановки измерены | USE-002, текущая установка SP-005, UX-108, UX-306 | Frontend + UX + QA / L |
| USE-002 | P0 | Добавить общий scenario preflight: runtime/model/team, выбранные знания/embeddings, required tools/scopes, версия и доступность trigger. Для каждого blocker есть причина и точный переход. «Сохранено», «можно в очередь», «можно сейчас» и «проверено на сценарии» различаются; online/policy повторно проверяются при выполнении | Текущие scheduler/knowledge/policy contracts; pack requirements подключаются из SP-001 по готовности | Backend + frontend + QA / L |
| USE-003 | P1; до документного пилота | Дать поддерживаемые PDF/DOCX и текстовые файлы на вход знаниям/сценарию: локальное извлечение, preview, provenance документа/страницы, статус индексации и безопасный retry. Установлены MIME/size/time limits, дедупликация и project isolation; сканы без OCR явно отмечены как неподдерживаемые | UX-206, CON-001, файловый adapter CON-103; SP-002 для structured input | Backend + frontend + QA / L |
| USE-004 | P0 | Добавить читаемый результат и решение «Принять / Нужны исправления»: безопасный Markdown, источники и фрагменты, отдельно промежуточный/финальный output, download. Решение и причина связаны с run/version; исправление создаёт связанную задачу, проверенный пример можно передать в Golden eval. Принятие не выдаёт tool approval | UX-106, RAG provenance, существующий Golden eval, UX-306; typed output интегрируется со SP-002 | Frontend + backend + QA / L |
| USE-005 | P1; до action-пилота | Собрать диагностику ожиданий и безопасное восстановление: нет worker/model, неполные знания, credentials/policy deny, timeout/unknown side effect. Оператор видит причину, владельца шага и разрешённые Cancel/Repeat/Reconcile; тест повтора не дублирует внешнюю запись, audit связывает исходный и восстановленный run | UX-106, UX-107, USE-002, существующие replay/idempotency/compensation | Backend + frontend + QA / L |
| USE-006 | P1; до scheduled-пилота | Довести эксплуатационный путь trigger → run → результат: timezone/next run, последний успешный результат, причина пропуска, корреляция webhook, тест доставки, pause/resume. Проверены Temporal и внешний webhook, дубли и сбой worker; при выключенном runtime показаны причина и инструкция. Уведомления только о необходимом действии/сбое/восстановлении, без шума на каждом успешном тике | UX-205, USE-002, Temporal test contour; CON-001 для внешнего канала | Backend + frontend + DevOps + QA / L |
| USE-007 | P1; после первого pack | Дать бизнес-пользователю форму запуска опубликованного решения внутри текущего приложения: поля по schema, ссылки/файлы допустимых типов, образец результата, доступный статус и handoff человеку. Пользователь не редактирует prompt/graph; обе границы UI/API соблюдают RBAC, URL не раскрывает недоступный объект | SP-001, SP-002, SP-005, USE-004; USE-003 для файлов | Frontend + backend + UX / L |
| USE-008 | P0 | Провести пилот SP-102 и проверить повторную пользу: подтвердить две команды и владельца сценария, снять ручной baseline, задать quality/effort gate до начала, наблюдать две недели. Отчёт содержит принятие, rework, возврат за 7 дней, latency, review/support cost и решение продолжать/исправить/остановить. Если команд или данных нет, задача остаётся открытой | Подготовка сразу; запуск после SP-102, USE-001, USE-004 и UX-306 | Product + аналитик + владелец процесса / M |

Два существующих эпика подняты из backlog после 1.9; новые ID для них не создаются:

| ID | Приоритет / статус | Уточнённый результат | Зависимости | Исполнитель / размер |
|---|---|---|---|---|
| UX-306 | P0 / запланировано | Минимальная воронка от начала сценария до принятия и повторной задачи; event IDs/dedupe, project/version/cohort, разделение test/demo, client/server events и missing data. Никаких prompt/output, имён документов, URL источников и raw ошибок в telemetry. Определения метрик — в исследовании; pack KPI использует тот же контракт | Контракт до USE-001/USE-004; интеграция с текущими run/OTel и затем SP-004 | Backend + frontend + аналитик / M |
| UX-307 | P0 gate 1.8 и пилота / запланировано | Протокол проверки пяти участников и пяти ролей, реальные устройства/screen reader, проблемы по шагам и повтор исправленных сценариев. Квартальные регрессии остаются дальнейшей регулярной работой | Реализованный UX 1.8; USE-001/USE-004 для расширенного пилота | UX + QA / M |

**Связь с существующими эпиками:** `SP-001` хранит provenance/version; `SP-002` владеет schema validation; `SP-005` — каталогом/установкой/upgrade; `UX-206` и `CON-103` — ingestion; `SP-004` — pack KPI; `UX-203`/`SP-003` — evaluation/promotion. `USE-*` задают сквозной пользовательский результат и не создают вторые реализации этих механизмов.

Начальный порядок: контракт `UX-306` и подготовка `USE-008` → `USE-002` → `USE-001` и `USE-004` → пилот `SP-102`. `USE-003` и `USE-006` подключаются к выбранному следующему сценарию; `USE-005` обязателен перед внешними write-действиями. `USE-007` начинается после стабилизации первого pack/schema. Успешный первый пилот не означает прохождение полного P0 gate пяти packs.

### Параллельный продуктовый трек — Solution packs

Цель трека: превратить уже реализованные runtime, RAG, specialist teams, MCP policy, approvals, eval, replay, artifacts, HA и SIEM в устанавливаемые бизнес-решения. Единица поставки — не отдельный prompt или process template, а versioned bundle из агентов, процесса, коннекторов, knowledge requirements, схем входа/выхода, policy, eval, KPI и human-escape/rollback правил.

Все горизонты отсчитываются от 1 сентября 2026 года. Это порядок начала и прохождения gate, а не обещание срока без подтверждённых команды и capacity.

#### Прогресс Solution packs на 8 сентября 2026 года

| ID | Статус | Уже реализовано | Остаётся до Definition of Done |
|---|---|---|---|
| SP-101 | Частично | Curated-каталог 13 ролей, включая все 6 P0; bounded prompts, requirements, runtime config, установка копии через существующий Agent API | Version-pinned backend provenance, output schemas, tool allowlists как данные, golden eval examples и immutable template lifecycle |
| SP-005 | Частично | Вкладка маркетплейса, поиск, P0/P1/P2-фильтры, readiness requirements на карточках и предзаполненная установка | Pack manifest/registry, clean-project readiness check, process/connector install, совместимый upgrade и preview diff |

Сентябрьский UI обновил каталог, поиск и фильтры состояния; в активном экране вкладки называются `Мои агенты` и `Шаблоны агентов`. Полный Agent workspace не появился. В установке по-прежнему не сохраняются backend `sourceTemplateId`/version: переименование установленной копии нарушает распознавание шаблона. `SP-001`–`SP-004`, `CON-*` и runnable packs остаются запланированными, если ниже не указан подтверждённый инкремент.

Уточнения по исследованию использования, внутри существующего scope:

- `SP-001`: сохранять stable source template/version/hash и историю установки на backend; существующие записи без provenance остаются `unknown`, не привязывать их автоматически по имени.
- `SP-005`: показывать фактическую доступность и требования, фильтровать по задаче/входу/источнику; внутренние P0/P1/P2 и горизонты разработки не должны быть основным способом выбора пользователя. Переиспользовать `USE-002` для readiness и `USE-001` для первого запуска.
- `SP-101`: golden examples выбранного пилота проверяются на pinned локальных model/runtime/knowledge; включить русскоязычные данные, отсутствующие/конфликтующие источники, ошибки tools, latency и guardrails. Не объявлять качество по наличию совместимого worker.
- `SP-004`: переиспользовать события `UX-306` и outcome `USE-004`; считать задачи, принятые результаты и полные затраты, а не только completed stages/tokens.
- `SP-102`: первый сквозной pilot candidate; остальные P0 packs сохраняются, но их одновременный запуск до подтверждения пользы не требуется.

Карточка агента и успешное создание project-scoped копии не означают готовность solution pack: обязательные pack-level gates ниже остаются без изменений.

#### P0 foundation — 0–3 месяца

| ID | Задача | Проверяемый результат | Зависимости | Размер |
|---|---|---|---|---:|
| SP-001 | Ввести solution-pack manifest и immutable registry | Manifest фиксирует версии agents/team, process, tools/connectors, knowledge, input/output schemas, policy, eval, KPI, compatibility и rollback; API хранит immutable versions и lifecycle status | Agent/team snapshots, process templates, prompt registry | L |
| SP-002 | Ввести общий structured business output contract | Agent/process stage публикует versioned JSON Schema; invalid output не проходит в condition/side effect; сохраняются typed validation error, provenance и artifact reference | SP-001, Artifact Store, Local RAG provenance | L |
| SP-003 | Реализовать lifecycle `draft → eval → policy review → approve → publish → observe → rollback` | Promotion привязан к exact pack version; golden gate и policy preview обязательны; high-risk publication требует four-eyes; rollback не меняет immutable history | SP-001, Golden eval, MCP policy, Process Builder replay | L |
| SP-004 | Добавить pack-level KPI/SLO и value dashboard | Для каждого pack измеряются cycle time, straight-through completion, escalation, field/factual error, approval reject, rollback и cost per successful outcome без prompt/output content в telemetry | SP-001, OTel, traces, artifacts | L |
| SP-005 | Создать каталог, установку и upgrade solution packs | Пользователь может просмотреть requirements, установить/клонировать pack в проект, пройти readiness check и выполнить совместимый upgrade с preview diff | SP-001, SP-003, UX-101, UX-105; последующая унификация с UX-201/UX-207 | L |
| CON-001 | Зафиксировать стандарт connector pack | Единый contract для install/health/capabilities/scopes/schema/idempotency/rate limits/sandbox/secrets; conformance suite блокирует несовместимый connector | MCP/HTTP gateway, isolated tools, scoped credentials | M |

#### P0 connector packs — 0–3 месяца

| ID | Задача | Проверяемый результат | Зависимости | Размер |
|---|---|---|---|---:|
| CON-101 | ITSM + SIEM connector pack | Read/search/create/update для service request и incident, получение alert/evidence, dry-run/preview и risk-tier действия с least privilege | CON-001, MCP policy, SIEM audit | L |
| CON-102 | ЭДО/DMS + 1C/ERP connector pack | Получение документа и metadata, поиск контрагента/объекта учёта, подготовка draft-заявки; posting/signing остаются approval-gated | CON-001, isolated tools, four-eyes | L |
| CON-103 | SQL/BI + файловые источники connector pack | Read-only запросы по allowlisted datasets, bounded result schema, ingestion поддерживаемых документов и export отчёта в Artifact Store | CON-001, SP-002, Local RAG | L |

#### P0 agents и процессы — 0–3 месяца

| ID | Задача | Агенты и процесс | Проверяемый результат | Зависимости | Размер |
|---|---|---|---|---|---:|
| SP-101 | Выпустить шесть базовых agent templates | Researcher, Document Operator, Data Analyst, ITSM Specialist, Supervisor, Reviewer/Guardian | У каждой роли есть bounded prompt, output schema, tool allowlist, eval examples, failure/handoff contract и immutable version | SP-001–SP-003 | L |
| SP-102 | Выпустить Research-to-Report pack | Researcher → Analyst → Reviewer → Artifact | Ответ содержит проверяемые citations/provenance; reviewer блокирует unsupported claims; итоговый отчёт сохраняется как artifact | SP-002, SP-004, SP-101, Local RAG | L |
| SP-103 | Выпустить Document-to-Approval pack | Intake → Extract → Validate → Human approval → Archive/API | Поддерживаемые документы преобразуются в schema-valid record; расхождения видимы; side effect невозможен без требуемого approval | SP-002, SP-101, CON-102 | L |
| SP-104 | Выпустить IT Incident / Service Request pack | Trigger → Triage → RAG/runbook → Approval → MCP action → Postmortem | Агент различает advice и action, показывает preview/risk, исполняет только allowlisted действие и формирует postmortem artifact | SP-003, SP-004, SP-101, CON-101 | L |
| SP-105 | Выпустить Data Monitoring-to-Report pack | Schedule/signal → Collect → Diagnose → Review → Report | Повторяемый scheduled run выявляет отклонение, указывает данные/допущения и выпускает reviewed management report | SP-002, SP-004, SP-101, CON-103 | L |
| SP-106 | Выпустить Agent Release Governance pack | Golden eval → Reviewer → Policy review → Four-eyes → Publish → Observe → Rollback | Один runnable process управляет выпуском exact pack version и сохраняет evidence для каждого gate и rollback decision | SP-003, SP-004, SP-101 | M–L |

#### P1 domain packs — 3–6 месяцев

P1 начинается только после прохождения P0 gate. Коннекторы Git/CI и CRM/support channels реализуются по `CON-001` как часть соответствующего эпика; повторно использовать generic raw HTTP без domain contract недостаточно.

| ID | Задача | Агенты и процесс | Проверяемый результат | Зависимости | Размер |
|---|---|---|---|---|---:|
| SP-201 | Software Delivery pack и Git/CI connector | Planner → Coder → Tester → Security Reviewer → release gate | Изменение связано с issue/commit/test evidence; write/release действия разделены и approval-gated | P0 gate, CON-001, sandbox policy | L |
| SP-202 | Customer Support pack и channel/CRM connector | Intake → Triage → RAG → Safe action → Human fallback → QA | Измеряются first-response, resolution, escalation и QA; есть явный human escape и запрет неподтверждённых account actions | P0 gate, CON-001, SP-004 | L |
| SP-203 | Finance Close / Reconciliation pack | Collect → Reconcile → Variance analysis → Control → Sign-off | Каждая цифра и корректировка прослеживается до источника; segregation of duties и sign-off обязательны | P0 gate, CON-102, SP-002 | L |
| SP-204 | Contract Review pack | Ingest → Clauses → Legal RAG → Risk → Redline → Human approval → Record | Clauses и риски schema-valid и имеют provenance; автономное подписание исключено | P0 gate, CON-102, SP-002 | L |
| SP-205 | Procurement Comparison pack | Proposal intake → Compare → Finance/risk → Request → Approval → ERP draft | Сравнение воспроизводимо по зафиксированным критериям; конфликтующие данные эскалируются; ERP posting approval-gated | P0 gate, CON-102, SP-004 | L |
| SP-206 | Security / Compliance Response pack | Alert → Evidence → Guardian → Four-eyes → Action/SIEM | Evidence immutable, severity explainable, destructive response требует two-person approval, rollback/circuit breaker проверен | P0 gate, CON-101, SP-003 | L |

#### P2 expansion — 6–12 месяцев

| ID | Задача | Агенты и процесс | Проверяемый результат | Зависимости | Размер |
|---|---|---|---|---|---:|
| SP-301 | Sales / RFP pack и CRM/CPQ connector | Account research → Proposal → Pricing/legal review → Approval → CRM update | Цены и условия берутся из authoritative systems; исходящая коммуникация и CRM write проходят review | P1 gate, CON-001, SP-204 | L |
| SP-302 | HR Onboarding / Offboarding pack и HRIS/IdP/email connector | Request → Documents → Knowledge → Access tasks → Approvals → Audit | PII policy и retention заданы; кадровые решения не автономны; выдача/отзыв доступа прослеживаемы и approval-gated | P1 gate, CON-001, identity/RBAC | L |

#### Watchlist и условия входа

| ID | Направление | До выполнения условия | Условие перевода в roadmap |
|---|---|---|---|
| EXP-401 | Voice front office | Не строить отдельную real-time channel platform | Не менее трёх целевых клиентов подтверждают один сценарий; есть latency/SLA, consent, recording/PII и human-transfer contract |
| EXP-402 | Browser / computer-use execution | Не использовать UI automation как замену доступному API/MCP | Есть deterministic evidence, selector/version strategy, approval/circuit breaker, rollback и успешный bounded pilot в legacy UI |
| EXP-403 | Marketing/content pack | Не делать основой позиционирования Agat | Подтверждён платный repeat demand после P0/P1 packs и измерима ценность сверх commodity generation |

#### Definition of Done для каждого solution pack

Pack не считается готовым по факту появления карточки или demo-run. Одновременно обязательны:

- immutable manifest и воспроизводимая установка в чистый project;
- version-pinned агенты/team, процесс, connector requirements и knowledge requirements;
- JSON Schema входа/выхода, provenance и явная обработка invalid/unknown данных;
- least-privilege tools/credentials, risk policy, approval и human-escape contract;
- golden dataset, offline promotion gate и негативные тесты side effects;
- pack-level KPI/SLO, trace, audit и runbook диагностики;
- upgrade/rollback rehearsal без изменения immutable history;
- документация в `/docs` и один воспроизводимый end-to-end example.

#### Exit criteria волн

- **P0 / 0–3 месяца:** пять runnable packs `SP-102`–`SP-106`, шесть базовых ролей, три connector packs; каждый проходит clean-project install, golden eval, policy, schema, KPI и human-escape gate.
- **P1 / 3–6 месяцев:** минимум два production pilot в разных domain packs с измеренными cycle time, error/rework, escalation, approval reject и cost per successful outcome; critical side effects проходят multi-user race и rollback rehearsal.
- **P2 / 6–12 месяцев:** подтверждён repeat demand и положительный ROI после стоимости connectors/support; PII и outbound-communication reviews закрыты до production.
- **Watchlist:** ни один эксперимент не получает release commitment до выполнения указанного entry condition.

#### Рекомендуемая последовательность Solution packs

1. Начать `SP-001`, общий контракт `CON-001`, события `UX-306` и подготовку пилота `USE-008`. Schema/KPI можно проектировать одновременно, но зависимая реализация начинается после manifest contract.
2. После manifest реализовать `SP-002` и `SP-004`; затем довести `SP-101`, `SP-003` и `SP-005`. Переиспользовать 13 существующих ролей и установку, не создавать каталог заново.
3. Первым собрать `SP-102` на разрешённых внутренних текстовых источниках с `USE-001`, `USE-002`, `USE-004`. Внешний источник подключать через `CON-001` только при подтверждённой необходимости. Провести первый пилот; это промежуточная веха внутри P0.
4. По результатам пилота и готовности данных собрать `CON-102`/`CON-103`, `USE-003`/`USE-006` и `SP-103`/`SP-105`. Для `SP-104` сначала завершить `CON-101`, action-policy rehearsal, `UX-111` и `USE-005`.
5. Завершить `SP-106` и полный P0 exit gate: все пять packs, шесть ролей и три connector packs с прежними Definition of Done. Пилот одного сценария не закрывает эту веху.
6. В P1 выбирать `SP-201`–`SP-206` по подтверждённым владельцам, данным и повторной потребности; исходный порядок Software/Support остаётся гипотезой до измерений.
7. `SP-301` и `SP-302` начинать после P1 gate; `EXP-401`–`EXP-403` сохраняют свои entry conditions.

### Предварительно запланировано в 1.9 — Creator and admin simplification

Цель релиза: распространить progressive disclosure на authoring и инфраструктурные сценарии после стабилизации общего shell и операторских flows.

| ID | Задача | Результат | Зависимости | Размер |
|---|---|---|---|---:|
| UX-201 | Ввести общий паттерн `Базовый / Расширенный` | Единые компоненты disclosure, presets и advanced settings для форм | UX-104, UX-105 | L |
| UX-202 | Создать Agent workspace | Вкладки `Настройка`, `Промпт`, `Тесты`, `Версии`, `Запуски`; candidate prompt создаётся без ручного поиска Golden eval | UX-201 | L |
| UX-203 | Пересобрать Quality flow | Явный lifecycle `Dataset → Experiment → Review → Promote`, один contextual CTA, mobile list → detail | UX-201, UX-202 | L |
| UX-204 | Упростить Process Builder | Категории шагов, базовый picker, отдельные режимы `Проектирование`, `Запуски`, `Публикация`; увеличенные mobile controls | UX-201 | L |
| UX-205 | Разделить publish, automations и BPMN | `Опубликовать`, `Triggers/автоматизация`, `Импорт/экспорт` становятся отдельными намерениями | UX-204 | M |
| UX-206 | Упростить Knowledge ingest | Flow `База знаний → Источник → Индексация`; chunking/Top K/embedding settings перенесены в advanced | UX-201 | M–L |
| UX-207 | Создать Integrations workspace | MCP и A2A объединены; подключение выполняется wizard-ами, raw policy/transport details скрыты в advanced | UX-101, UX-201 | L |
| UX-208 | Разделить Infrastructure workspace | Узлы, модели и Fleet получают task-oriented tabs, compact cards и incident-oriented summary | UX-101, UX-201 | L |
| UX-209 | Унифицировать язык и состояния | Общий glossary, русские пользовательские labels, единые empty/loading/error/success patterns | UX-201 | M |
| UX-210 | Добавить guard для autosave и несохранённых данных | Уход из процесса или формы не теряет dirty/in-flight изменения | UX-204 | S–M |

#### Прогресс Creator/admin на 8 сентября 2026 года

Основания: активные [AgentsDirectory](../apps/web/src/components/AgentsDirectory.tsx), [ProcessesPage](../apps/web/src/components/ProcessesPage.tsx), [ProcessNodePicker](../apps/web/src/components/ProcessNodePicker.tsx), [ProcessReleasePanel](../apps/web/src/components/ProcessReleasePanel.tsx) и [сентябрьский отчёт проверок](./design/2026-09-workspace/design.md).

| ID | Статус | Что реализовано | Что осталось |
|---|---|---|---|
| UX-201 | Частично | Локальное раскрытие инструкций/деталей и advanced в отдельных сценариях | Общий Basic/Advanced contract и его применение ко всем целевым формам |
| UX-202 | Частично | Новый каталог: поиск, взаимоисключающие фильтры состояния, модель/готовность, настройка и запуск | Workspace `Настройка / Промпт / Тесты / Версии / Запуски`, переход candidate → experiment → promote |
| UX-203 | Запланировано | Существующий Golden eval остаётся функциональной основой | Упрощённый lifecycle, contextual CTA и mobile list/detail |
| UX-204 | Частично | Категории и поиск шагов, инспектор, проверки схемы, вкладки, тест шага, responsive canvas | Picker с ≤6 базовыми типами до advanced, общая UX-201 схема, сложные графы/устройства и полный release gate |
| UX-205 | Основной scope готов | Публикация на схеме, отдельные Триггеры, Выполнения и Версии; BPMN import/export внутри вкладки версий, отдельно от публикации | Реальный trigger E2E и operational UX — USE-006; сокращение raw cron/calendar/expression в базовом режиме — UX-201 |
| UX-206 | Запланировано | Text ingestion, embeddings, collections и provenance уже существуют | Flow «база → источник → индексация», advanced; PDF/DOCX и входные файлы — USE-003 |
| UX-207 | Запланировано | Отдельные MCP/A2A экраны и административная навигация | Единый workspace и connection wizard с проверкой scopes/доступности |
| UX-208 | Запланировано | Существующие Nodes/Models/Fleet и исправления responsive | Task-oriented tabs, compact cards и incident summary |
| UX-209 | Частично | Русские labels и empty/error состояния обновлённых Agents/Processes | Общий glossary и согласованность оставшихся Quality/Knowledge/MCP/A2A/Fleet экранов |
| UX-210 | Частично; Process scope готов | Сериализация autosave, сохранение до смены раздела/процесса/проекта, блокирование ухода при ошибке, beforeunload; проверены быстрый уход и сбой сети | Guard остальных форм, включая Agent/NewRun; общая проверка close/Escape/ошибки/in-flight, реальные устройства |

Не переносить повторно выполненные части в разработку. Готовность каталогов и process UI не закрывает agent lifecycle или весь релиз 1.9.

#### Ключевые критерии 1.9

- raw JSON, hashes, manifests и transport details не появляются в базовом режиме;
- Agent workspace поддерживает путь `candidate → experiment → review → promote` без разрыва навигации;
- Process picker сначала показывает не более шести базовых типов шагов;
- A2A endpoint разбит минимум на три последовательных шага и автоматически заполняет skill metadata;
- MCP сначала предлагает подключить источник, а не редактировать policy JSON;
- TTL, priority, quota и retention имеют человеко-понятные presets;
- mobile master-detail заменён на последовательные list/detail экраны;
- все destructive actions используют единый product dialog с целью, последствием и способом восстановления.

### После 1.9 — Efficiency and personalization backlog

| ID | Задача | Результат | Приоритет |
|---|---|---|---:|
| UX-301 | Глобальный поиск и command palette | Быстрый переход к run, process, agent, node, approval и доступным действиям | P2 |
| UX-302 | Shareable deep links | URL сохраняет выбранную сущность, вкладку и безопасный фильтр | P2 |
| UX-303 | Saved views и bulk actions | Сохранённые фильтры для очередей, узлов, моделей и audit; безопасные массовые операции | P2 |
| UX-304 | Персонализируемый Обзор | Presets для operator, designer, admin и auditor | P2 |
| UX-305 | Контекстная справка и glossary | Объяснения protocol/infrastructure терминов без ухода из текущего процесса | P2 |

`UX-306` и первый цикл `UX-307` перенесены в ближайший трек использования продукта. Дальнейшие квартальные usability regressions выполняются после baseline; они не откладывают первичную проверку релиза.

### Рекомендуемая последовательность UX-работ

1. Завершить открытые проверки 1.8 на текущем UI (`UX-307`), исправлять обнаруженные дефекты; core `UX-101`–`UX-110` уже реализован в указанном объёме.
2. До многопользовательского пилота выполнить `UX-111`; `UX-112` планировать до полного audit archive.
3. Определить минимальные события `UX-306`; реализовать путь первого полезного результата через `USE-002`, `USE-001`, `USE-004`.
4. Зафиксировать общий Basic/Advanced паттерн `UX-201`, довести остатки `UX-204` и guard форм `UX-210`, сохранив готовые Process-вкладки/публикацию.
5. Довести `UX-202`/`UX-203` до сквозного candidate → eval → promote, а `UX-206` — вместе с выбранным документным сценарием `USE-003`.
6. Развивать `UX-207`/`UX-208` по препятствиям подключения источников и эксплуатации пилота; завершить согласованность `UX-209`.
7. `UX-301`–`UX-305` остаются после стабилизации основных workflows; измерение и первичные пользовательские проверки уже входят в ближайший scope.

## Рекомендуемый порядок

| Приоритет | Статус | Фича | Почему сейчас |
|---|---|---|---|
| P0 | Готово в 0.4; quality foundation в 0.8 | OTel traces + manifest/replay | Переиспользовать для USE-004, UX-306 и pack-level quality; golden eval уже реализован |
| P0 | Готово в 0.5 | MCP gateway + risk policy | Реальные tools без бесконтрольного shell-доступа |
| P1 | Готово в 0.6 | Model router + benchmarks | Максимально использует неоднородное локальное железо |
| P1 | Готово в 0.7 | Local RAG + provenance | Делает агентов полезными на внутренних данных |
| P1 | Готово в 0.8 | Golden eval + prompt registry | Даёт измеримый quality gate для prompt/model/RAG изменений |
| P2 | Готово в 0.9 | A2A adapter | Даёт безопасную границу для внешних agent platforms |
| P1 | Готово в 1.0 | Production hardening durable runtime | Снижает операционный риск перед расширением deployment и HA |
| P1 | Готово в 1.1 | Risk-tier approvals + emergency deny | Единая policy-as-code boundary, four-eyes, scoped secrets и kill switch |
| P1 | Готово в 1.2 | Расширение process builder | Fork/join, triggers, subprocess, diff/replay, BPMN и compensation поверх durable runtime |
| P1 | Готово в 1.3 | LangGraph specialist teams | Supervisor/handoff внутри bounded agent stage без переноса durable orchestration |
| P2 | Готово в 1.4 | Расширенная A2A interoperability | Outbound, delegated auth, streaming/push и bounded files поверх существующей очереди |
| P1 | Готово в 1.5 | Изолированное выполнение tools | WASI/OCI Jobs, read-only root, exact-IP egress, ephemeral scoped Secrets и optional sandboxed runtime |
| P2 | Готово в 1.6 | Native mobile worker | Attested Android/iOS inference, scoped credential и remote wipe без ложного PWA background SLA |
| P3 | Готово в 1.7 | Fleet и HA | PostgreSQL replicas, regional queues, signed rollout, hard tenant isolation и SIEM export |
| Gate перед production | Этапы 1–6 готовы; OPS-101–OPS-105 открыты | Production Fleet readiness и DR | Требуются evidence конкретных provider/CI/attestor/SIEM deployment, migration rehearsal и production game day |
| P0 | Foundation готова в 1.8 | Role-based IA, mobile navigation, notifications и accessibility foundation | Shell, базовые уведомления и keyboard gate реализованы; остаются device/screen-reader release checks |
| P0 | UX-106–UX-110: основной scope готов, gate открыт | Единый create→monitor→decide workflow и безопасные значимые действия | Реальные LLM/worker/approval E2E, пять ролей, реальные устройства, screen reader и usability; deadline вынесен в UX-111 |
| P0 | Запланировано | Первый полезный результат и доказательство использования | USE-001/USE-002/USE-004/USE-008, UX-306/UX-307: закончить один сценарий, измерить принятие и повтор |
| P0 | Начато; `SP-005` частично | Solution-pack foundation и connector contract (`SP-001`–`SP-005`, `CON-001`) | Каталог и установка agent-копии готовы; manifest, structured outputs, release lifecycle, KPI, readiness и upgrade остаются в scope |
| P0 | `SP-101` частично; packs запланированы | Пять первых packs и шесть базовых ролей (`SP-101`–`SP-106`) | Первым SP-102 с проверкой пользы; 13 ролей не закрывают pack/eval/schema/immutable gates |
| P1 | После P0 gate, 3–6 месяцев | Software, Support, Finance, Legal, Procurement, Security packs (`SP-201`–`SP-206`) | Закрывает наиболее востребованные domain workflows после готовности connector packs |
| P2 | После P1 gate, 6–12 месяцев | Sales/RFP и HR lifecycle packs (`SP-301`, `SP-302`) | Расширяет front/middle office после доказанного repeat demand и ROI |
| Watch | Только по entry criteria | Voice, browser/computer use и generic marketing (`EXP-401`–`EXP-403`) | Не позволяет популярным, но менее подходящим сценариям перехватить roadmap |
| P1 | 1.9 частично реализован | Basic / Advanced и creator workflows | Сохранить готовые Process-вкладки/публикацию/autosave; завершить Agent workspace, picker, Quality, Knowledge и guard других форм |
| P1 | Предварительно в 1.9 | Integrations и Infrastructure workspaces | Убирает MCP/A2A/Fleet из общего пользовательского потока |
| P1 | Запланировано по gates сценариев | Документы, recovery, регулярные запуски, business input и audit archive | USE-003/USE-005/USE-006/USE-007 и UX-112: реализовывать по источникам и потребностям пилота |
| P2 | После 1.9 | Search, saved views и personalization | UX-301–UX-305; telemetry и первичный usability перенесены в ближайшую очередь |
