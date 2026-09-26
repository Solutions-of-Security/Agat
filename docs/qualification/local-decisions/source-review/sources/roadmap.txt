# Roadmap и дополнительные фичи

Публичные версии и инструкции обновления: [CHANGELOG](./CHANGELOG.md).
Ниже сохранены текущие планы и исторические этапы реализации; они не означают
существование отдельных опубликованных GitHub Releases.

Актуализировано **19 сентября 2026 года** по сравнению с [Portable Computer](./agat-vs-perplexity-portable-computer-2026-09-18.md), текущему коду и запросу на облачных агентов/SaaS. Срез: commit 5aec6d3 и рабочие изменения, включая [USE-002](./scenario-preflight.md); версия пакетов 1.7.0. Это план работ: новые подключения и задачи ниже ещё не реализованы. Исторические проверки от 8 сентября не объявляются свежими.

## Текущее состояние и решение о приоритетах

Ближайший результат — **один законченный сценарий работы с документами и отчётом**, доступный с локальной моделью либо через явно подключённого облачного провайдера. **OpenRouter — первый SaaS-провайдер моделей.** Внешний облачный агент подключается отдельно через существующую A2A-основу. Повторяемость, управляемость и стоимость принятого результата должны стать проверяемыми преимуществами Агата.

| Трек | Подтверждённое состояние | Следующая работа |
|---|---|---|
| Operator UX 1.8 | Основной scope UX-101–UX-110 готов; release gate открыт | Проверки реального исполнения, ролей, устройств и usability; UX-111 до многопользовательских согласований |
| Готовность сценария | USE-002 реализован в рабочем дереве: причины блокировки, версия, очередь/сейчас и evidence завершённого экземпляра | Проверить с настоящими LLM/Temporal/tools; расширять тем же контрактом облачные исполнители, не создавать второй preflight |
| Документы и результаты | Текстовый ingest, RAG, Artifact Store и вывод result.md существуют | PDF/DOCX/таблицы, читаемый результат, экспорт и принятие человеком |
| Облачные модели и агенты | Worker умеет OpenAI-compatible endpoint/API key; A2A умеет регистрацию внешнего peer и send/poll/cancel | Единое добавление подключений, OpenRouter, облачная модель в агенте, внешний агент в процессе, контроль данных и расходов |
| Solution packs | 13 ролей и 13 схем процессов; отдельные части SP-101/SP-005 существуют | Один готовый SP-102; остальные packs после доказательства повторной пользы |
| Production Fleet / DR | Механизмы реализованы; qualification выбранного контура не подтверждён | OPS-101–OPS-105 перед соответствующим production cutover |

Статусы: **основной scope готов** — реализовано описанное поведение с указанной границей проверки; **частично** — остаётся реализация; **код в рабочем дереве** — ещё не подтверждён поставленный релиз; **запланировано** — новое поведение не заявляется готовым. Завершение одного запуска не доказывает качество результата или готовность продукта к production.

## Приоритеты и последовательность поставки

Эта очередь заменяет прежнее обещание пяти packs одновременно в P0 и календарные горизонты 0–3/3–6/6–12 месяцев. **P0** — необходимое для первого принятого сценария и базовых облачных подключений; **P1** — надёжное повторное использование и расширение после P0; **P2** — развитие по подтверждённому спросу. Gate применяется до соответствующего использования независимо от места в очереди. Команда, сроки и capacity не подтверждены; размеры S/M/L относительные. P0 выполняется последовательно, а не как одновременный запуск всех эпиков.

| Порядок | Приоритет | Результат | Основные задачи | Условие перехода |
|---|---|---|---|---|
| 1 | P0 | Контракты подключений, доступа и расходов; критерии пилота | CLD-001, CLD-002, CLD-004, CON-001; SP-001, SP-002; контракт UX-306 и подготовка USE-008 | Согласованы типы исполнителей, ограничения передачи данных, модель бюджета и вход/выход первого сценария |
| 2 | P0 | Подключить OpenRouter и внешнего агента из интерфейса | CLD-003, CLD-005, CLD-006, UX-207; проверка USE-002 и UX-111 | Облачный агент работает без локальной GPU; внешний peer исполняет разрешённую задачу; запрещённая отправка и перерасход блокируются |
| 3 | P0 | Документы → проверяемый отчёт → принятие | USE-009, USE-003, UX-206, USE-004, USE-001; SP-005, SP-101, EVAL-101, SP-102 | Чистая установка, локальный и облачный варианты проверены отдельно; пользователь получает пригодный отчёт |
| 4 | P0 | Доказать полезность и закрыть gates пилота | USE-008, UX-306, UX-307; оставшиеся проверки 1.8 | Две команды, две недели, ручной baseline, измеренные принятие/rework/возврат/стоимость; решение продолжать или исправить |
| 5 | P1 | Повторные и долгие задачи, дополнительные SaaS и модели | USE-005/USE-006/USE-007, CLD-007/CLD-008, CON-002/CON-104, RUN-101–RUN-103, DOC-101, RAG-101, EVAL-102 | Сбои, отказ провайдера, отмена, контекст и внешние действия обработаны без ложного успеха |
| 6 | P1 | Развить подтверждённые корпоративные сценарии | SP-003/SP-004, CON-101/CON-102/CON-103, SP-103/SP-104/SP-105 и остальные роли SP-101; UX-112 и оставшийся creator/admin scope | Каждый следующий pack имеет владельца, данные и измеримую повторную задачу |
| 7 | P2 | Отраслевое расширение и удобство | SP-106, SP-201–SP-206, SP-301/SP-302, UX-301–UX-305 | Подтверждены demand/ROI и готовность нужных коннекторов; не тормозит первый сценарий |
| До production | Обязательный gate | Qualification выбранного развёртывания | OPS-101–OPS-105 и security/operations gates применяемых подключений | Evidence конкретного контура; локальный pilot не считается HA/DR qualification |
| По условиям входа | Watch | Голос, управление ПК, generic marketing | EXP-401–EXP-403 | Только после entry conditions соответствующего эксперимента |

Общий workflow, scheduler, Temporal, credentials, MCP/A2A и Golden eval переиспользуются. Для нового облачного пути нужны адаптеры и проверяемые границы, а не второй оркестратор. Разработка разных треков может идти независимо при наличии исполнителей; зависимости и выпускные gates сохраняются.

Ближайшее распределение по ролям: backend/runtime — CLD-001/CLD-002/CLD-004 и контракты SP-001/SP-002; frontend/UX — UX-207 и путь USE-001/USE-004; data/backend — USE-003/UX-206; QA/agent-eval — квалификация USE-002 и EVAL-101; product/владелец процесса — данные и baseline USE-008. Это необходимые роли, а не назначения конкретным людям или запуск задач агентам.

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

## Открытые gates и продуктовые треки

### Production Fleet readiness и DR

- **Готово, этап 1:** canonical offline SQLite→PostgreSQL migrator, artifact backfill, reconciliation/verify report и rehearsal rollback;
- **Готово, этап 2:** отдельная migration Job/role, catalog drift gate, DDL-free runtime role и connection admission/load testing;
- **Готово, этап 3:** managed multi-AZ/PITR evidence gate, DR canaries (введены в v22, текущая schema v25), фактический physical restore/failover rehearsal и HMAC-sealed RPO/RTO/SLO evaluation; каждый production cluster всё равно обязан пройти собственную provider qualification;
- **Готово, этап 4:** versioned S3-compatible artifact authority, conditional PUT/HEAD reconciliation, cross-replica verified cache, retention/legal hold, exact-version delete outbox, safe lifecycle merge и bounded BYTEA backfill;
- **Готово, этап 5:** residency-aware whole-cell region-loss DR без active-active writes: sealed PostgreSQL/S3/Temporal evidence, residency policy, four-eyes, snapshot-bound activation, monotonic write epoch и safe failback;
- **Готово, этап 6:** verified OCI/SLSA provenance admission с отдельным trust root, one-time SPIFFE-compatible runtime attestation и refresh; exact SIEM batch acknowledgement, bounded retry, redacted retained DLQ, RBAC replay/resolve и operational retention.

Подробности: [worker supply-chain attestation](./worker-supply-chain-attestation.md), [SIEM retention/DLQ](./siem-retention-dlq.md), [ADR-020](./adr-020-worker-supply-chain-attestation.md) и [ADR-021](./adr-021-siem-retention-dlq.md).

### Что осталось после этапов 1–6

Запланированные repository capabilities этого блока отмечены закрытыми в предыдущих этапах. Перед production cutover остаётся qualification каждого конкретного контура. На 19 сентября свежий комплект evidence выбранного production-контура в этой актуализации не проверен; задачи ниже **открыты**, а наличие кода не является их закрытием.

| ID | Задача и критерий приёмки | Зависимости | Роль исполнителя |
|---|---|---|---|
| OPS-101 | Подтвердить managed PostgreSQL cell: актуальный provider evidence, измеренные restore/failover/RPO/RTO/SLO и пройденный admission gate | Выбранный provider, region/residency и конкретный cluster | DevOps + DBA |
| OPS-102 | Выполнить migration и rollback rehearsal на копии целевого объёма данных; reconciliation без необъяснённых расхождений, документированное окно простоя | OPS-101, доступная согласованная копия данных | DBA + QA |
| OPS-103 | Проверить сохранение версий S3, lifecycle/legal hold и fencing в выбранных регионах; evidence привязан к exact bucket/version и DR-политике | Целевые S3/region, OPS-101 | DevOps + storage |
| OPS-104 | Подключить pinned Sigstore release pipeline, trusted local attestor и реальный SIEM sink; проверить reject недоверенной сборки, exact ack/dedupe и DLQ/replay | Реальные CI/trust roots/attestor/SIEM | DevOps + security |
| OPS-105 | Провести cross-system region-loss game day и review scoped secrets/RBAC/trust roots; сохранить решение об activation/failback и измеренные результаты | OPS-101–OPS-104 | DevOps + security + владелец сервиса |

Все пять — обязательные задачи перед соответствующим production cutover. Evidence и протоколы сохраняются в `/docs`; тестовый локальный контур не подменяет выбранный production deployment.

**1.8 — Operator UX foundation** сохраняет открытый release gate; основной scope уже реализован по [UX/UI-аудиту](./ux-ui-audit.md). Новая очередь разработки определена в начале этого документа. Production qualification Fleet применяется к выбранному развёртыванию и не закрывается UI-работами.

[Исследование приоритетов агентов и процессов](./agent-process-priorities-2026.md) сохраняется как исходное обоснование сценариев. Его календарные волны и прежняя одновременная поставка пяти packs пересмотрены этим роадмапом от 19 сентября. Привязка новой очереди к версиям и датам выполняется после оценки команды и объёма работ.

### 1.8 — Operator UX foundation: основной scope реализован, gate открыт

Цель релиза: сократить когнитивную нагрузку в ежедневной работе оператора, исправить мобильную навигацию и доступность, не удаляя экспертные возможности платформы.

#### Поставленный scope релиза 1.8: не новая очередь разработки

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

### Облачные агенты, модели и SaaS-подключения

**AS-IS:** [worker](../workers/agat_worker.py) принимает OpenAI-compatible endpoint/API key; [A2A](./a2a-adapter.md) уже регистрирует внешние peers и умеет send/poll/cancel. В [AgentDialog](../apps/web/src/components/AgentDialog.tsx) нет самостоятельного типа облачного исполнителя с project-scoped provider connection. Поэтому ниже планируется развитие имеющихся механизмов. Наличие возможности вручную указать endpoint не означает готовность продуктового пути OpenRouter.

Различаются три сущности:

| Тип | Что подключается | Где выполняется работа |
|---|---|---|
| Облачная модель | OpenRouter или другой поддерживаемый inference API | Агат управляет агентным циклом; модель получает только разрешённый контекст; инструменты Агата проходят его gateway |
| Внешний облачный агент | Сервис с собственным агентным runtime, первоначально A2A | Агат передаёт разрешённое задание и отслеживает результат; внутренние действия peer нельзя считать контролируемыми MCP policy Агата |
| SaaS-инструменты и данные | Подключение бизнес-приложения через поддерживаемый MCP/REST adapter | Модель предлагает действие; gateway проверяет права, scopes и согласование перед обращением к SaaS |

OpenRouter в первом инкременте — **провайдер моделей**, а не внешний A2A-агент. Настройки места исполнения и разрешения передачи данных раздельны. Профиль «без внешней передачи» должен блокировать все внешние model/embedding/tool/web/telemetry пути; выбор локальной модели сам по себе такого обещания не даёт.

Все задачи CLD ниже **запланированы**. Оплачиваемые проверки проводятся позднее на выделенном тестовом аккаунте в утверждённом бюджете; эта актуализация роадмапа не создаёт аккаунты, ключи или платные запросы.

| ID | Приоритет | Задача и критерий приёмки | Зависимости | Исполнитель / размер |
|---|---|---|---|---|
| CLD-001 | P0 | Ввести project-scoped реестр подключений с типами model provider / remote agent / SaaS tools. CRUD, enable/disable, проверка состояния, ревизии конфигурации, ссылки использования и аудит. Старые MCP/A2A записи переиспользуются без копирования секретов; их прежние API продолжают работать. Удаление используемого подключения не ломает историю: новые вызовы блокируются, старый manifest остаётся читаемым | Текущие credentials, MCP/A2A registry и RBAC | Backend + frontend / L |
| CLD-002 | P0 | Дополнить общий контроль исходящих данных и secrets для подключений. Ключи шифруются на сервере, не попадают в prompts, browser storage, traces или leases обычных workers. Доступ ограничен проектом, моделью/provider/tool и назначением. Policy применяется ко всему собранному контексту, включая RAG, memory и tool results; запрет проекта не снимается решением модели. Проверены ротация, отзыв, cross-project deny, SSRF и смена policy между preflight и вызовом. Согласование передачи привязано к конкретным данным/получателю; изменение требует повторной проверки | CLD-001, существующие credentials/policy/approval contracts | Backend + security + QA / L |
| CLD-003 | P0 | Реализовать первый adapter OpenRouter: API key, catalog моделей, capabilities/context/pricing с датой обновления, нормализованный chat/tool-call контракт и диагностика auth/quota/rate-limit/timeout. Для tool/schema задач выбираются только совместимые endpoints. Provider fallback по умолчанию выключен; разрешённый fallback сохраняет provider allowlist, data policy и бюджет. Фактические model/provider/request ID и usage фиксируются, неизвестные поля остаются unknown. Провайдерские server tools/plugins выключены в базовом профиле; вызов инструмента модели не обходит gateway Агата | CLD-001, CLD-002; оплачиваемые E2E после CLD-004 | Backend/runtime + QA / L |
| CLD-004 | P0 | Ввести budgets и учёт внешних расходов на проект/run/connection. Перед вызовом атомарно резервируется верхняя оценка по зафиксированным тарифам и лимитам; actual usage сверяется без двойного списания. Учитываются retries, отменённые и неопределённые запросы; неизвестная стоимость не считается нулевой. В строгом режиме невозможность ограничить стоимость блокирует запуск; для remote agent нужны подтверждённая верхняя цена либо отдельный явно ограниченный договором бюджет. Проверены конкурентный расход, исчерпание, поздний usage и смена тарифа; cancellation не обещает возврат уже начисленной платы | CLD-001, существующие queues/quotas/OTel; тарифный контракт adapter | Backend + QA / L |
| CLD-005 | P0 | Добавлять агента Агата с облачной моделью через API и Agent UI: connection → модель → инструкция/разрешённые tools → тест → сохранение. Исполнение идёт через доверенный серверный executor, не требует локальной GPU/генеративной модели и не выдаёт vendor key обычному worker. Scheduler, NewRun, process agent step, preflight, manifest и eval понимают новый способ исполнения. Закрепляются connection revision/model/routing policy; локальные агенты сохраняют прежнее поведение. Для RAG отдельно проверяется доступность выбранных embeddings; облачная генерация не отменяет эту зависимость. Один процесс успешно выполняет локальный и облачный этапы без скрытого переключения | CLD-002, CLD-003, CLD-004, текущий USE-002 | Backend/runtime + frontend + QA / L |
| CLD-006 | P0 | Добавлять внешнего облачного агента как переиспользуемого исполнителя каталога/процесса поверх существующего A2A peer. Закрепляются Agent Card/skill/contract revision; preflight проверяет режимы и доступ. Durable связь instance/stage → remoteTaskId переживает рестарт; late response, duplicate send, timeout, partial/failed и cancel без подтверждения не превращаются в успех или слепой повтор. Полномочия и payload ограничены контрактом; worker/MCP secrets не передаются. P0 допускает read/draft задачи доверенного peer; внешние write-действия требуют отдельной квалификации. REST-only агенты не объявляются A2A-совместимыми | CLD-001, CLD-002, CLD-004, SP-002; существующий outbound A2A и process runtime | Backend/runtime + frontend + QA / L |
| CLD-007 | P1 | Добавить управляемую помощь более сильной модели: разрешённый внутренний либо облачный advisor. Показаны причина, адресат, минимальный контекст и предел расходов; автоматический вызов возможен только внутри заранее разрешённой policy. Advisor возвращает рекомендации без полномочий на tools. Запрещены скрытый local→cloud fallback и обход data policy; refusal/timeout/budget exhaustion оставляют частичный результат или handoff. Сравнить качество и стоимость с локальным baseline | CLD-002–CLD-005, RUN-101, EVAL-101 | Runtime + frontend + QA / L |
| CLD-008 | P1 | Подключать дополнительные inference SaaS через versioned adapter contract. Поставить preset custom OpenAI-compatible и проверить вторую реальную реализацию API. Для каждого adapter заявлены auth, capabilities, errors, тарифы, streaming/cancel и пределы совместимости; неизвестные metadata не выдумываются. Настроенный base URL не означает поддержку произвольного API; adapter проходит общую conformance suite и ограничения CLD-002/CLD-004 | CLD-001–CLD-005, CON-001 | Backend/runtime + QA / M–L |

**Пользовательский путь P0:** «Подключения → Добавить → OpenRouter → API key → Проверить → выбрать разрешённые модели и бюджет»; затем «Агенты → Новый → Облачная модель → подключение/модель → тест». Для внешнего агента отдельный вариант «Внешний агент → Agent Card → разрешённые задачи → тест». UX-207 владеет интерфейсом подключений, CLD-005/CLD-006 — поведением исполнителей; отдельные конкурирующие каталоги не создаются.

**Gate облачного пути:** успешный реальный OpenRouter run без локальной генеративной модели, разрешённый A2A read/draft run внутри процесса и отказ на недопустимом контексте; секреты отсутствуют в клиентских ответах/trace. Проверены 401/402/429/5xx, timeout, неизвестный исход, отмена, исчерпание бюджета и смена provider policy. Старый локальный сценарий проходит регрессию. Переключатель выключения блокирует новые вызовы; уже принятый внешним сервисом запрос показывается как отдельное состояние до сверки. При миграции новые типы выключены по умолчанию; отключение и откат сохраняют manifests и историю.

**Первичные источники, проверены 19 сентября 2026 года:** [Quickstart](https://openrouter.ai/docs/quickstart) — единый API и OpenAI-совместимый путь; [Provider Routing](https://openrouter.ai/docs/guides/routing/provider-selection) — provider allowlist, fallback, поддержка параметров и data policy; [ZDR](https://openrouter.ai/docs/guides/features/zdr) и [Provider Logging](https://openrouter.ai/docs/guides/privacy/provider-logging) — retention различается по endpoints, региональный режим требует отдельного подтверждения; [OAuth PKCE](https://openrouter.ai/docs/guides/overview/auth/oauth) — обмен кода на пользовательский API key; [Server Tools](https://openrouter.ai/docs/guides/features/server-tools/overview) — часть инструментов может исполняться у провайдера. Эти свойства API не доказывают готовность Агата. Реальные цены, доступные модели, аккаунтные политики и региональные гарантии перепроверяются при реализации и подключении.

### Качество, документы и агентное исполнение — задачи из сравнения

Основание — [сравнение с Portable Computer](./agat-vs-perplexity-portable-computer-2026-09-18.md). Все строки ниже запланированы; движок Golden eval, Artifact Store и state plane переиспользуются. Новые профили сначала сравниваются с текущим runtime, затем допускаются в сценарий.

| ID | Приоритет | Задача и критерий приёмки | Зависимости | Исполнитель / размер |
|---|---|---|---|---|
| EVAL-101 | P0 | Выпустить проверенные профили «сценарий + модель + runtime/tools + железо/provider». Для SP-102 есть русскоязычный holdout, минимум три прогона каждого случая, human review и проверки источников/чисел/отказа при недостатке данных. Локальные и облачные результаты раздельны; provider/model ID, доступная у поставщика ревизия, context, latency и полная стоимость доступны в отчёте. Если ревизия скрыта поставщиком, это ограничение воспроизводимости явно указано. Совместимый online-узел не получает отметку качества без evidence | SP-001, SP-002, CLD-005; существующие templates и Golden eval | Agent/eval + QA + владелец процесса / L |
| EVAL-102 | P1 | Провести сравнение с Portable Computer: 40 согласованных задач по три прогона на продукт; документы, процессы и подключения показаны отдельно. Общие данные/права/поддерживаемое железо, отдельные local/hybrid режимы и ручной baseline. До испытания выбрать главный критерий; ориентир — качество не хуже и снижение стоимости принятого результата либо труда человека на 20%. Неуспешные/неподдержанные задачи входят в знаменатель; доступ к конкуренту, бюджет и ограничения выборки указаны | SP-102, EVAL-101, USE-008; тестовый доступ и budget владельца пилота | Product + QA + аналитик / M |
| DOC-101 | P1 | Добавить локальный OCR и понимание изображений/сложных таблиц для документного сценария. Показаны страницы, распознанные фрагменты и ошибки; низкая уверенность ведёт к review. Limits/parser версии закреплены, очистка удаляет производные данные; OCR/vision не уходит в облако скрыто. Сравнить с USE-003 на эталоне сканов и таблиц до включения профиля | USE-003, EVAL-101 | Data/backend + QA / L |
| RAG-101 | P1; по провалам retrieval | Проверить масштабирование поиска сверх текущего ограничения 5000 кандидатов; внедрять hybrid search/reranker только при измеримом улучшении выбранного корпуса. Измерены recall@k, точность ссылок и latency; права применяются до выдачи результатов, переиндексация/удаление и rollback сохраняют provenance. При отсутствии выигрыша оставить baseline и зафиксировать результат эксперимента | USE-003, EVAL-101, существующий RAG | Data/backend + QA / L |
| RUN-101 | P1 | Добавить budget-aware сборку контекста и загрузку skills/tools по потребности. На длинных задачах сохраняются цель, ограничения, источники и открытые действия; compaction имеет версию и provenance. Доступ к инструментам по-прежнему определяет gateway, а не загруженный skill. Повторные eval сравнивают качество, контекст, latency и стоимость с текущим bounded loop | EVAL-101, существующий worker и MCP policy | Runtime + agent/eval / L |
| RUN-102 | P1 | Сохранять прогресс долгого агентного этапа с восстановлением после потери worker. Определены authoritative store, attempt/checkpoint версия, связь с Temporal/lease и operation IDs; отдельная несогласованная БД не создаётся. Crash до/после внешнего эффекта не приводит к повторной записи: unknown outcome сверяется; отмена и смена policy запрещают продолжение. Проверить stage/team resume, миграцию и rollback формата | RUN-101, USE-005, текущие state/lease/idempotency contracts | Runtime + backend + QA / L |
| RUN-103 | P1 | Добавить ограниченную самопроверку результата перед завершением: валидаторы схем/чисел/источников и проверка созданного артефакта; при ошибке — ограниченное исправление или handoff. Лимиты model/tool calls и бюджета сохраняются; исчерпание не маркируется успехом. Улучшение подтверждено EVAL-101, model judge не подменяет обязательный human approval | RUN-101, SP-002, USE-004, EVAL-101 | Runtime + agent/eval + QA / M–L |

### Использование продукта — первый полезный результат и повторный сценарий

Основания: [исследование использования от 8 сентября](./product-usage-research-2026-09-08.md) и [сравнение с Portable Computer](./agat-vs-perplexity-portable-computer-2026-09-18.md). USE-002 уже имеет реализацию в рабочем дереве; его остаток — квалификация и поддержка новых типов исполнителей. Остальные задачи в этой таблице запланированы. Production adoption и ROI пока не измерены.

Документный путь USE-003 поднят в P0. Новый USE-009 отвечает за установку и первое подключение; USE-001 продолжает владеть прохождением конкретного сценария. Release gate 1.8 и продуктовый pilot учитываются раздельно.

| ID | Приоритет | Задача и критерий приёмки | Зависимости | Исполнитель / размер |
|---|---|---|---|---|
| USE-001 | P0 | Провести пользователя от выбранного сценария до первого полезного результата: шаблон → документы/источники → готовность → тест → результат. Сохраняется прогресс, есть безопасный пример и передача технического blocker администратору. Минимум 4 из 5 пользователей проходят путь без подсказки; local/cloud варианты явно подписаны, время установки и время до результата измеряются отдельно | USE-002, USE-009, SP-005, USE-003, USE-004, UX-306; текущий UX-108 | Frontend + UX + QA / L |
| USE-002 | P0 / код в рабочем дереве; gate открыт | Квалифицировать существующий общий scenario preflight на настоящих LLM/Temporal/tools: pin версии, ресурсы, знания, scopes, trigger, queue/now и recovery links. Проверить policy race и успешный evidence полного запуска. CLD-005/CLD-006 расширяют тот же контракт облачными исполнителями/бюджетами; scenarioVerified остаётся доказательством выполнения, не бизнес-качества | Текущая реализация scenario-preflight; реальные зависимости тестового контура; pack requirements из SP-001 | Backend + frontend + QA / M |
| USE-003 | P0 | Локально принимать PDF с текстовым слоем, DOCX, CSV/XLSX и text-файлы: preview, provenance страницы/ячейки, статус extraction/indexing, дедупликация и безопасный retry. Явно ограничены MIME/size/time/листов/строк; макросы и внешние ссылки не исполняются. Сканы без DOC-101 объясняются как неподдерживаемые. Backend extraction/indexing поставляется здесь, интерфейс — в UX-206; полный SQL/BI-коннектор CON-103 не нужен для загрузки файлов. Права, удаление исходника и производных данных проверены; передача извлечённого текста облачной модели отдельно контролируется CLD-002 | Текущий knowledge/Artifact Store, SP-002 | Backend + frontend + QA / L |
| USE-004 | P0 | Показывать читаемый результат с безопасным Markdown, таблицами, ссылками на фрагменты/страницы и разделением промежуточного/итогового output; экспорт DOCX/PDF из одного проверенного содержания. Действия «Принять / Нужны исправления» сохраняются с run/version; исправление создаёт связанную задачу, пример можно передать в Golden eval. Typed output проверяется SP-002 до side effect. Принятие отчёта не выдаёт tool approval; экспорт проверяется открытием и визуальным просмотром | UX-106, RAG provenance, Artifact Store, SP-002, UX-306; существующий Golden eval | Frontend + backend + QA / L |
| USE-005 | P1; до action-пилота | Собрать диагностику ожиданий и безопасное восстановление: нет worker/model, неполные знания, credentials/policy deny, timeout/unknown side effect. Оператор видит причину, владельца шага и разрешённые Cancel/Repeat/Reconcile; тест повтора не дублирует внешнюю запись, audit связывает исходный и восстановленный run | UX-106, UX-107, USE-002, существующие replay/idempotency/compensation | Backend + frontend + QA / L |
| USE-006 | P1; до scheduled-пилота | Довести эксплуатационный путь trigger → run → результат: timezone/next run, последний успешный результат, причина пропуска, корреляция webhook, тест доставки, pause/resume. Проверены Temporal и внешний webhook, дубли и сбой worker; при выключенном runtime показаны причина и инструкция. Уведомления только о необходимом действии/сбое/восстановлении, без шума на каждом успешном тике | UX-205, USE-002, Temporal test contour; CON-001 для внешнего канала | Backend + frontend + DevOps + QA / L |
| USE-007 | P1; после первого pack | Дать бизнес-пользователю форму запуска опубликованного решения внутри текущего приложения: поля по schema, ссылки/файлы допустимых типов, образец результата, доступный статус и handoff человеку. Пользователь не редактирует prompt/graph; обе границы UI/API соблюдают RBAC, URL не раскрывает недоступный объект | SP-001, SP-002, SP-005, USE-004; USE-003 для файлов | Frontend + backend + UX / L |
| USE-008 | P0 | Подтвердить две команды и владельца SP-102, снять ручной baseline и задать quality/effort gate до старта; провести двухнедельный пилот. Отдельно учесть local/cloud режимы, принятие, rework, возврат за 7 дней, latency и расходы inference/review/support. Отчёт содержит решение продолжать/исправить/остановить; отсутствие команд/данных/бюджета не заменяется демо. EVAL-102 — отдельное испытание конкурента, не условие начать сбор собственного baseline | Подготовка сразу; выполнение после SP-102, USE-001, USE-004, EVAL-101, UX-306; cloud costs из CLD-004 | Product + аналитик + владелец процесса / M |
| USE-009 | P0 | Поставить поддерживаемый установочный комплект с мастером: локальная модель либо облачное подключение. Пользователь не настраивает вручную Node/Python/worker; версии, health-check и ошибки prerequisites проверяются автоматически. Облачная генерация работает без GPU/загрузки локальной генеративной модели; для документного RAG мастер отдельно настраивает поддерживаемые embeddings, в P0 — локальный CPU-профиль. Внешние embeddings допускаются только через квалифицированный adapter и CLD-002, их поддержка не подразумевается поддержкой chat API. Локальный профиль проверяет ресурсы и модель. Определена матрица ОС, проверены чистая установка, обновление, сохранение данных и rollback. Документация и повторяемый пример в /docs | Текущие Docker/worker/runtime; облачный профиль после CLD-005; существующий USE-002 | DevOps + frontend + QA / L |

Два существующих эпика подняты из backlog после 1.9; новые ID для них не создаются:

| ID | Приоритет / статус | Уточнённый результат | Зависимости | Исполнитель / размер |
|---|---|---|---|---|
| UX-306 | P0 / запланировано | Минимальная воронка от начала сценария до принятия и повторной задачи; event IDs/dedupe, project/version/cohort, разделение test/demo, client/server events и missing data. Никаких prompt/output, имён документов, URL источников и raw ошибок в telemetry. Определения метрик — в исследовании; pack KPI использует тот же контракт | Текущие run/OTel; контракт метрик фиксируется до разработки consumer flows | Backend + frontend + аналитик / M |
| UX-307 | P0 gate 1.8 и пилота / запланировано | Протокол проверки пяти участников и пяти ролей, реальные устройства/screen reader, проблемы по шагам и повтор исправленных сценариев. Квартальные регрессии остаются дальнейшей регулярной работой | Реализованный UX 1.8; USE-001/USE-004 для расширенного пилота | UX + QA / M |

**Связь с существующими эпиками:** `SP-001` хранит provenance/version; `SP-002` владеет schema validation; `SP-005` — каталогом/установкой/upgrade; `UX-206` и `CON-103` — ingestion; `SP-004` — pack KPI; `UX-203`/`SP-003` — evaluation/promotion. `USE-*` задают сквозной пользовательский результат и не создают вторые реализации этих механизмов.

Порядок этого трека: контракт UX-306 и подготовка USE-008 → квалификация USE-002 → USE-009, USE-003/UX-206 и USE-004 → USE-001 и пилот SP-102. Открытие cloud-варианта требует CLD-001–CLD-006 и UX-207. USE-005 обязателен перед внешними write-сценариями, USE-006 — перед обещанием регулярного автоматического выполнения; они не заменяются успешным ручным запуском. USE-007 создаёт универсальную бизнес-форму после первого pack. Gate P0 теперь относится к одному SP-102 и базовым cloud-подключениям; требование пяти packs отменено этой приоритизацией.

### Solution packs и прикладные подключения

Цель — устанавливаемое бизнес-решение из агентов, процесса, подключений, knowledge requirements, схем, policy, eval и правил восстановления. Первым выпускается SP-102 для внутренних документов; дополнительные сценарии выбираются по повторной потребности. Прежние временные горизонты заменены общей очередью выше, без обещания дат.

**Состояние на 19 сентября:** curated-каталог содержит 13 ролей и установку копии; SP-101/SP-005 реализованы частично. Встроенные схемы процессов не являются готовыми интеграциями. Backend provenance установленной agent-копии, pack registry и runnable packs не подтверждены. USE-002 теперь имеет отдельный рабочий инкремент; установка pack должна использовать его. Переименование копии не должно определять её происхождение: SP-001 хранит stable source ID/version/hash, legacy provenance остаётся unknown.

#### Foundation: минимальная поставка и последующее управление

Контракты и установщик проверяются на небольшом тестовом pack с фиксированными результатами. Их реализация не ждёт готового SP-102; публикация рабочего сценария проходит собственный quality gate EVAL-101. Это разделяет готовность механизма и качество конкретного решения.

| ID | Приоритет | Задача и проверяемый результат | Зависимости | Исполнитель / размер |
|---|---|---|---|---|
| SP-001 | P0 | Ввести versioned solution-pack manifest и immutable registry: версии agents/process, тип исполнителя, connection/tool/knowledge requirements, schemas, policy, eval и rollback. Секреты в manifest не включаются. Backend хранит provenance установки; публикация первого read-only pack связывается с passed evidence существующего Golden eval и действующей policy | Текущие snapshots/prompt registry, контракты CLD-001/CON-001 | Backend + QA / L |
| SP-002 | P0 | Ввести общий structured business input/output contract. JSON Schema, обязательные поля, provenance и artifact references проверяются кодом; invalid/unknown output не проходит в условие или side effect. Ответ с нехваткой данных ведёт к review, а не подставляет выдуманные значения. Contract применим к локальному, cloud-model и remote-agent результату | SP-001, Artifact Store, текущий RAG | Backend + QA / L |
| SP-003 | P1 | Автоматизировать полный pack lifecycle draft → eval → policy review → approve → publish → observe → rollback. Promotion привязан к exact version, golden gate и policy preview; high-risk publication требует four-eyes. Rollback не меняет историю. Первую ограниченную публикацию через существующие gates поставляют SP-001/SP-005, полного workflow ждать не требуется | SP-001, существующие Golden eval/MCP policy/replay | Backend + frontend + QA / L |
| SP-004 | P1 | Добавить pack-level KPI/SLO и value dashboard: cycle time, completion, escalation, factual/field error, approval reject, rollback и стоимость принятого результата. Используются события UX-306, решения USE-004 и расходы CLD-004; отсутствующие расходы не обнуляются. Ручной отчёт первого пилота не ждёт dashboard | SP-001, UX-306, USE-004, CLD-004, OTel | Backend + frontend + аналитик / L |
| SP-005 | P0 | Установка и обновление первого pack в чистый project: requirements, idempotent install агентов/процесса/bindings, общий preflight, preview diff и rollback. Новые scopes/secrets не выдаются установкой автоматически. Первый release использует существующие eval/policy gates; каталог фильтруется по задаче и доступности, не по внутреннему P0/P1 | SP-001, SP-002, USE-002, текущие Golden eval/policy и UX-101/UX-105 | Backend + frontend + QA / L |
| CON-001 | P0 | Стандарт connector pack и conformance suite: install/health/capabilities/auth/scopes/schema/idempotency/rate limits/sandbox/secrets. Registry CLD-001 хранит подключение, этот контракт определяет adapter/tool поведение. Явны read/write/unknown effects, поддержка отмены и поведение после timeout; несовместимый adapter не публикуется | Текущие MCP/HTTP gateway, isolated tools и credentials | Backend/integration + QA / M |

#### SaaS и domain connectors

OpenRouter входит в CLD-003 как первый inference SaaS. Следующие задачи добавляют удобную авторизацию и бизнес-инструменты; они не требуют второго хранилища secrets. Начальный OpenRouter API-key flow не блокируется ожиданием всех OAuth adapters.

| ID | Приоритет | Задача и критерий приёмки | Зависимости | Исполнитель / размер |
|---|---|---|---|---|
| CON-002 | P1 | Добавить поддерживаемые OAuth/PKCE подключения SaaS поверх encrypted credentials: state/PKCE/redirect validation, изоляция проекта/владельца, показ scopes, disconnect/revoke и rotation. Refresh реализуется только для провайдеров, которые его поддерживают; отказ/expiry возвращает понятный reconnect. Первый удобный flow — OpenRouter PKCE с серверным обменом кода на API key; существующий ручной key остаётся доступен. Проверены подмена callback/project и повтор кода | CLD-001, CLD-002, CON-001; OpenRouter adapter CLD-003 | Backend + frontend + security + QA / L |
| CON-104 | P1 | Поставить один прикладной read-only SaaS connector для SP-102. Кандидат — GitLab: чтение issue/MR и связанных материалов; Product фиксирует сервис и три операции до разработки. Есть установка, auth/scopes, health, пагинация/rate limits, provenance и отрицательные проверки прав на реальном тестовом tenant. Unsupported операции явно недоступны; credentials не уходят модели. Запись/публикация — отдельный расширенный scope после action gate | CON-001, CLD-001, CLD-002, SP-102; CON-002 только если выбран OAuth | Integration + Product + QA / M–L |
| CON-101 | P1; перед SP-104 | ITSM + SIEM connector pack: read/search/create/update для service request/incident, alert/evidence, preview/dry-run, least privilege и risk-tier действия. Проверены дубли и reconciliation неизвестного результата | CON-001, MCP policy, SIEM audit, USE-005 | Integration + QA / L |
| CON-102 | P1; перед SP-103 | ЭДО/DMS + 1C/ERP connector pack: документ/metadata, поиск объекта учёта, draft-заявка. Posting/signing требуют approval; проверены scopes, ошибка после записи и восстановление | CON-001, isolated tools, four-eyes, USE-005 | Integration + QA / L |
| CON-103 | P1; перед SP-105 | SQL/BI и автоматические файловые источники: allowlisted read-only datasets, bounded schema, дедупликация/обновление данных, export в Artifact Store. Для документов переиспользуется parser USE-003; ручная загрузка первого pack не ждёт SQL/BI adapter | CON-001, SP-002, USE-003, текущий RAG | Data/integration + QA / L |

#### Первый pack и последующие процессы

| ID | Приоритет | Задача / агенты и процесс | Проверяемый результат | Зависимости | Исполнитель / размер |
|---|---|---|---|---|---|
| SP-101 | P0: три роли первого pack; P1: остальные три | Довести шесть базовых templates: Researcher, Data Analyst, Reviewer/Guardian первыми; Document Operator, ITSM Specialist, Supervisor для следующих packs | Bounded prompt, schema, tool allowlist, eval examples, handoff и immutable version у каждой роли. Существующие 13 ролей переиспользуются; весь эпик готов только после шести, gate P0 требует первые три | SP-001, SP-002, EVAL-101 | Agent/eval + QA / L |
| SP-102 | P0 | Внутренний Research-to-Report: документы → Researcher → Analyst → Reviewer → Artifact → решение человека | Установка в чистый project, PDF/DOCX/таблицы, проверяемые источники и числа, unsupported claims блокируются; экспорт и исправление связаны с исходным run. Local/cloud варианты проверены отдельно. Минимальные KPI из UX-306/USE-004/CLD-004, без ожидания большого dashboard | SP-002, SP-005, первые три роли SP-101, USE-003, USE-004, UX-306, EVAL-101; cloud вариант после CLD-005 | Product + agent/eval + frontend + QA / L |
| SP-103 | P1 | Document-to-Approval: Intake → Extract → Validate → Approval → Archive/API | Schema-valid record, видимые расхождения, provenance и невозможность side effect без требуемого решения; recovery проверен | SP-002, Document Operator из SP-101, CON-102, USE-005 | Product + integration + QA / L |
| SP-104 | P1 | IT Incident / Service Request: Trigger → Triage → RAG/runbook → Approval → MCP action → Postmortem | Advice отделён от action, есть preview/risk, allowlisted исполнение, postmortem и проверка восстановления | SP-003, SP-004, ITSM Specialist из SP-101, CON-101, UX-111, USE-005 | Product + integration + QA / L |
| SP-105 | P1 | Data Monitoring-to-Report: Schedule/signal → Collect → Diagnose → Review → Report | Регулярный reviewed report показывает отклонения, исходные данные и допущения; timezone/пропуски/дубли/сбой worker проверены | SP-002, SP-004, SP-101, CON-103, USE-006 | Product + data + QA / L |
| SP-106 | P2 | Agent Release Governance: Golden eval → Reviewer → Policy review → Four-eyes → Publish → Observe → Rollback | Отдельный runnable pack управляет выпуском exact pack version и хранит gate/rollback evidence. Базовые проверки публикации остаются обязательными раньше; этот pack автоматизирует их использование | SP-003, SP-004, SP-101 | Backend + agent/eval + QA / M–L |

#### Отраслевое расширение — P2 после повторной пользы

SP-201–SP-206 перенесены из прежнего P1 в P2. В работу выбирается один сценарий по владельцу, данным и экономике, а не весь список. Domain коннектор строится по CON-001; универсальный raw HTTP не заменяет его контракт.

| ID | Приоритет | Задача / процесс | Проверяемый результат | Зависимости | Размер |
|---|---|---|---|---|---:|
| SP-201 | P2 | Software Delivery и Git/CI: Planner → Coder → Tester → Security Reviewer → release gate | Связь с issue/commit/test evidence, разграниченные write/release actions и approval | P1 exit gate, CON-001, sandbox policy | L |
| SP-202 | P2 | Customer Support и channel/CRM: Intake → Triage → RAG → Safe action → Human fallback → QA | Измерены response/resolution/escalation/QA; human escape и запрет неподтверждённых account actions | P1 exit gate, CON-001, SP-004 | L |
| SP-203 | P2 | Finance Close / Reconciliation: Collect → Reconcile → Variance → Control → Sign-off | Каждая цифра/корректировка имеет источник, разделение полномочий и sign-off | P1 exit gate, CON-102, SP-002 | L |
| SP-204 | P2 | Contract Review: Ingest → Clauses → Legal RAG → Risk → Redline → Approval → Record | Schema-valid clauses/risks с provenance, обязательная человеческая проверка, без автономного подписания | P1 exit gate, CON-102, SP-002 | L |
| SP-205 | P2 | Procurement Comparison: Intake → Compare → Finance/risk → Request → Approval → ERP draft | Зафиксированные критерии, обработка конфликтов данных, approval перед ERP posting | P1 exit gate, CON-102, SP-004 | L |
| SP-206 | P2 | Security / Compliance Response: Alert → Evidence → Guardian → Four-eyes → Action/SIEM | Immutable evidence, обоснованная severity, two-person approval, проверенные rollback/circuit breaker | P1 exit gate, CON-101, SP-003 | L |
| SP-301 | P2; после SP-204 и спроса | Sales / RFP и CRM/CPQ: Research → Proposal → Pricing/legal → Approval → CRM update | Цены/условия из authoritative systems; review исходящих сообщений и CRM writes | P1 exit gate, CON-001, SP-204 | L |
| SP-302 | P2; после подтверждения HR-сценария | HR Onboarding / Offboarding и HRIS/IdP/email: Request → Documents → Knowledge → Access → Approvals → Audit | PII/retention определены, кадровые решения не автономны; выдача/отзыв доступа прослеживаются и согласуются | P1 exit gate, CON-001, identity/RBAC | L |

#### Watchlist и условия входа

| ID | Приоритет | Направление | Условие перевода в работу |
|---|---|---|---|
| EXP-401 | Watch | Voice front office | Не менее трёх клиентов подтверждают один сценарий; определены latency/SLA, consent, recording/PII и human transfer |
| EXP-402 | Watch | Browser / computer-use execution | API/MCP не закрывают выбранный legacy UI; есть evidence, selector/version strategy, approval/circuit breaker, rollback и bounded pilot |
| EXP-403 | Watch | Marketing/content pack | Подтверждён платный repeat demand и ценность сверх commodity generation; не перехватывает первый корпоративный сценарий |

#### Definition of Done каждого solution pack

- immutable manifest, pinned версии и воспроизводимая установка в чистый project;
- agent/process/connection/tool/knowledge requirements без встроенных secrets;
- input/output schema, provenance и явные invalid/unknown состояния;
- least-privilege credentials, data/egress policy, approvals и human escape;
- golden dataset, evidence проверки exact версии и отрицательные тесты действий;
- KPI/SLO и полная стоимость, trace/audit и runbook; для первого пилота достаточно отчёта по UX-306/USE-004/CLD-004, dashboard SP-004 позднее;
- upgrade/rollback rehearsal, сохранение истории, документация в /docs и воспроизводимый E2E;
- cloud-вариант отдельно проходит gates CLD-002/CLD-004; local-вариант не требует cloud credentials.

#### Exit criteria и порядок расширения

- **P0:** один SP-102, три проверенные роли, установка/документы/экспорт/принятие; OpenRouter и A2A read/draft путь доступны с ограничениями данных и расходов; квалифицированы USE-002 и gates применяемого UI. USE-008 подтверждает повторную пользу в двух командах. Пять packs и три domain connectors больше не являются условием P0.
- **P1:** выбранные повторные/action сценарии проходят USE-005/USE-006; хотя бы один дополнительный pack принят в реальном пилоте с измеренными review/support затратами. Конкретный production deployment проходит OPS и gates используемых внешних систем. Остальные P1 capabilities начинаются по измеренным ограничениям, не обязаны выпускаться разом.
- **P2:** для каждого нового domain pack подтверждены владелец, повторный спрос, данные/подключения и ожидаемый эффект; high-risk действия имеют отдельный gate до эксплуатации.
- **Watch:** требуется выполнение entry conditions; календарной даты выпуска нет.

Зависимости первого pack: SP-001 → SP-002; затем EVAL-101 → первые роли SP-101. SP-005, первые роли, документы и результат сходятся в SP-102; USE-001 собирает пользовательский путь, USE-008 проверяет пользу. UX-306, USE-003/USE-004 и облачный трек готовятся по своим контрактам. SP-003/SP-004 и SQL/BI-коннектор не блокируют первый read-only отчёт. Для каждого следующего pack прежде фиксируются триггер, источники, владелец результата и критерии приёмки; отзывы «нравится» не заменяют измерений.

### Creator и admin: UX-206/UX-207 в P0, остальные расширения в P1

Ранее этот блок целиком планировался на 1.9. Теперь UX-206 и UX-207 нужны первому документному и облачному сценариям и поднимаются в P0. Они используют готовые компоненты shell/forms; завершение общего UX-201 их не блокирует. Остальной scope упрощения authoring и инфраструктуры остаётся P1. Новая дата или номер релиза не назначены.

| ID | Приоритет / статус | Задача и критерий приёмки | Зависимости | Исполнитель / размер |
|---|---|---|---|---|
| UX-201 | P1 / частично | Ввести общий паттерн «Базовый / Расширенный»: disclosure, presets и advanced settings для целевых форм; сохранить уже работающие локальные варианты | UX-104, UX-105 | Frontend + UX / L |
| UX-202 | P1 / частично | Довести Agent workspace: «Настройка», «Промпт», «Тесты», «Версии», «Запуски»; candidate prompt создаётся без ручного поиска Golden eval | UX-201 | Frontend + backend + UX / L |
| UX-203 | P1 / запланировано | Собрать путь Dataset → Experiment → Review → Promote с одним contextual CTA и последовательным mobile list/detail | UX-201, UX-202 | Frontend + agent/eval + QA / L |
| UX-204 | P1 / частично | Довести Process Builder: категории и базовый picker шагов, mobile controls; сохранить готовые режимы проектирования, запусков и публикации | UX-201 | Frontend + UX + QA / L |
| UX-205 | Основной scope готов; остаток P1 | Сохранить отдельные publish, automations и BPMN-намерения; реальные trigger E2E и эксплуатационный путь выполнять в USE-006, упрощение raw настроек — в UX-201 | Готовые Process UI; зависимости остатка указаны в соответствующих задачах | Frontend + QA / M |
| UX-206 | P0 / запланировано | Сделать понятный ingest: база знаний → файлы/источник → preview извлечённого текста → индексация → готовность. Показаны ошибки и неподдерживаемые сканы; chunking/Top K/embeddings — в advanced. PDF/DOCX/таблицы используют единую реализацию USE-003; доступность embeddings видна до запуска | Готовые UX-104/UX-105; контракт и backend USE-003 | Frontend + UX + QA / M–L |
| UX-207 | P0 / запланировано | Создать единый workspace «Подключения»: OpenRouter/модели, внешние агенты, SaaS-инструменты. Wizard ведёт через тип, авторизацию, доступность, разрешения данных/действий, модели/skills и бюджет; позволяет проверить, отключить и заменить credential. MCP/A2A переиспользуются, transport/policy JSON скрыты в advanced; secret не возвращается в UI. Ошибка содержит понятное действие восстановления | UX-101, текущие формы/credentials UI; API и контракты CLD-001–CLD-006 | Frontend + backend + UX + QA / L |
| UX-208 | P1 / запланировано | Разделить Infrastructure workspace: task-oriented tabs для Nodes/Models/Fleet, compact cards и incident summary; проверить populated mobile states | UX-101, UX-201 | Frontend + UX + QA / L |
| UX-209 | P1 / частично | Завершить glossary, русские labels, единые empty/loading/error/success состояния оставшихся страниц; понятный язык новых P0 flows входит в их приёмку сразу | UX-201 | Frontend + UX / M |
| UX-210 | P1 / частично; Process scope готов | Расширить dirty/in-flight guard на остальные формы, включая Agent/NewRun; проверить close/Escape/ошибку сохранения и реальные устройства, не переделывая готовый Process autosave | UX-204, текущий Process guard | Frontend + QA / S–M |

#### Прогресс Creator/admin на 8 сентября 2026 года

Основания: активные [AgentsDirectory](../apps/web/src/components/AgentsDirectory.tsx), [ProcessesPage](../apps/web/src/components/ProcessesPage.tsx), [ProcessNodePicker](../apps/web/src/components/ProcessNodePicker.tsx), [ProcessReleasePanel](../apps/web/src/components/ProcessReleasePanel.tsx) и [сентябрьский отчёт проверок](./design/2026-09-workspace/design.md).

| ID | Статус | Что реализовано | Что осталось |
|---|---|---|---|
| UX-201 | Частично | Локальное раскрытие инструкций/деталей и advanced в отдельных сценариях | Общий Basic/Advanced contract и его применение ко всем целевым формам |
| UX-202 | Частично | Новый каталог: поиск, взаимоисключающие фильтры состояния, модель/готовность, настройка и запуск | Workspace `Настройка / Промпт / Тесты / Версии / Запуски`, переход candidate → experiment → promote |
| UX-203 | Запланировано | Существующий Golden eval остаётся функциональной основой | Упрощённый lifecycle, contextual CTA и mobile list/detail |
| UX-204 | Частично | Категории и поиск шагов, инспектор, проверки схемы, вкладки, тест шага, responsive canvas | Picker с ≤6 базовыми типами до advanced, общая UX-201 схема, сложные графы/устройства и полный release gate |
| UX-205 | Основной scope готов | Публикация на схеме, отдельные Триггеры, Выполнения и Версии; BPMN import/export внутри вкладки версий, отдельно от публикации | Реальный trigger E2E и operational UX — USE-006; сокращение raw cron/calendar/expression в базовом режиме — UX-201 |
| UX-206 | Запланировано; с 19 сентября P0 | Text ingestion, embeddings, collections и provenance уже существуют | Flow с preview/ошибками и embeddings readiness; PDF/DOCX/таблицы через USE-003 |
| UX-207 | Запланировано; с 19 сентября P0 | Отдельные MCP/A2A экраны и административная навигация | Единый workspace и wizard для OpenRouter, cloud agent и SaaS с проверкой доступа, policy и бюджета |
| UX-208 | Запланировано | Существующие Nodes/Models/Fleet и исправления responsive | Task-oriented tabs, compact cards и incident summary |
| UX-209 | Частично | Русские labels и empty/error состояния обновлённых Agents/Processes | Общий glossary и согласованность оставшихся Quality/Knowledge/MCP/A2A/Fleet экранов |
| UX-210 | Частично; Process scope готов | Сериализация autosave, сохранение до смены раздела/процесса/проекта, блокирование ухода при ошибке, beforeunload; проверены быстрый уход и сбой сети | Guard остальных форм, включая Agent/NewRun; общая проверка close/Escape/ошибки/in-flight, реальные устройства |

Не переносить повторно выполненные части в разработку. Таблица сохраняет основание проверки от 8 сентября, а приоритеты обновлены 19 сентября. Готовность каталогов и Process UI не закрывает весь agent lifecycle.

#### Общие критерии creator/admin; для P0 применяются к UX-206/UX-207

- raw JSON, hashes, manifests и transport details не появляются в базовом режиме;
- Agent workspace поддерживает путь `candidate → experiment → review → promote` без разрыва навигации;
- Process picker сначала показывает не более шести базовых типов шагов;
- A2A endpoint разбит минимум на три последовательных шага и автоматически заполняет skill metadata;
- MCP сначала предлагает подключить источник, а не редактировать policy JSON;
- TTL, priority, quota и retention имеют человеко-понятные presets;
- mobile master-detail заменён на последовательные list/detail экраны;
- все destructive actions используют единый product dialog с целью, последствием и способом восстановления.

### P2 — Efficiency and personalization backlog

| ID | Задача | Результат | Приоритет |
|---|---|---|---:|
| UX-301 | Глобальный поиск и command palette | Быстрый переход к run, process, agent, node, approval и доступным действиям | P2 |
| UX-302 | Shareable deep links | URL сохраняет выбранную сущность, вкладку и безопасный фильтр | P2 |
| UX-303 | Saved views и bulk actions | Сохранённые фильтры для очередей, узлов, моделей и audit; безопасные массовые операции | P2 |
| UX-304 | Персонализируемый Обзор | Presets для operator, designer, admin и auditor | P2 |
| UX-305 | Контекстная справка и glossary | Объяснения protocol/infrastructure терминов без ухода из текущего процесса | P2 |

`UX-306` и первый цикл `UX-307` перенесены в ближайший трек использования продукта. Дальнейшие квартальные usability regressions выполняются после baseline; они не откладывают первичную проверку релиза.

### Рекомендуемая последовательность UX-работ

1. Зафиксировать события UX-306 и протокол UX-307; закончить реальные проверки готового 1.8 и UX-111 до многопользовательских согласований. Найденные дефекты исправляются в исходных задачах.
2. Собрать UX-207 для OpenRouter и внешнего агента на контрактах CLD; реестр, API и формы разрабатываются согласованно. Использовать готовые shell/forms без ожидания всего UX-201.
3. Квалифицировать текущий USE-002, поставить установку USE-009 и документный flow USE-003/UX-206; читаемый результат USE-004 и принятие входят в USE-001 и пилот SP-102.
4. После первого полезного сценария завершать UX-201/UX-202/UX-203/UX-204, guard остальных форм UX-210, Infrastructure UX-208 и общий язык UX-209 по измеренным препятствиям. UX-112 нужен до обещания полного audit archive.
5. UX-301–UX-305 остаются P2. Первичные измерения и проверки UX-306/UX-307 уже входят в P0.

## Полная карта приоритетов открытых задач

Таблица сводит очередь выше. При расхождении с прежними исследованиями действует эта приоритизация от 19 сентября; критерии приёмки и зависимости находятся в строках соответствующих ID. P0 — последовательная ближайшая программа, а не обещание выполнить все крупные задачи в один спринт.

| Приоритет / состояние | Задачи | Граница и причина |
|---|---|---|
| Основной scope готов; открыты проверки | UX-101–UX-110, основной UX-205 | Не планировать повторную реализацию; реальные проверки 1.8 остаются gate, остатки automations учтены в USE-006/UX-201 |
| P0 — подключение и исполнение | CLD-001–CLD-006, CON-001, UX-207 | OpenRouter, облачная модель в агенте и внешний A2A performer; обязательны ограничения данных, secrets и расходов |
| P0 — установка, документы и результат | USE-001–USE-004, USE-009, UX-206 | Один понятный путь от установки до принятого отчёта; USE-002 уже реализован в рабочем дереве и требует квалификации/расширения |
| P0 — первый готовый сценарий | SP-001, SP-002, SP-005, первые три роли SP-101, SP-102, EVAL-101 | Версии, схемы, воспроизводимая установка и качество первого Research-to-Report |
| P0 — доказательство пользы и приёмка | USE-008, UX-111, UX-306, UX-307 | Реальный пилот, deadline согласований, измерения и usability; подготовка начинается до разработки, проверка — на готовом пути |
| P1 — повторение и надёжность | USE-005–USE-007, RUN-101–RUN-103, DOC-101, RAG-101, EVAL-102 | Восстановление, регулярные задачи, контекст/самопроверка, сложные документы и сравнение; RAG-101 только при выявленном ограничении retrieval |
| P1 — расширение подключений | CLD-007, CLD-008, CON-002, CON-104 | Контролируемая помощь модели, дополнительные inference SaaS, OAuth/PKCE и один прикладной SaaS connector |
| P1 — корпоративные процессы | SP-003, SP-004, оставшиеся три роли SP-101, SP-103–SP-105, CON-101–CON-103 | Полный lifecycle/KPI и следующие packs после первого пилота; write/scheduled сценарии не обходят USE-005/USE-006 |
| P1 — authoring и администрирование | UX-112, UX-201–UX-204, UX-208–UX-210; остаток UX-205 через USE-006/UX-201 | Полный audit archive, Agent/Quality/Process/Infrastructure flows; готовые части не переделываются |
| P2 — расширение по спросу | SP-106, SP-201–SP-206, SP-301, SP-302, UX-301–UX-305 | Отраслевые решения, полное change governance и удобство после подтверждённой повторной пользы |
| Обязательный gate выбранного production | OPS-101–OPS-105 | Подтверждение конкретного deployment/DR; это не фича, которую можно отложить после cutover |
| Watch | EXP-401–EXP-403 | Голос, управление ПК и generic marketing только по записанным условиям входа |

## Проверка актуализации от 19 сентября 2026 года

Сохранены все 68 прежних ID, добавлены 18 новых; каждый из 86 ID имеет строку задачи и место в общей карте. История реализации 0.3–1.7 сохранена. Проверены Markdown-таблицы, локальные ссылки, отсутствие циклов в объявленных зависимостях и зависимостей P0 от отложенных P1/P2; профиль architecture assessment и проверка whitespace проходят без замечаний. Проверка документов не закрывает E2E, пользовательские, сравнительные или production gates: они остаются задачами роадмапа. В этой актуализации исходный код не менялся.
