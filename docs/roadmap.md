# Roadmap и дополнительные фичи

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

## Следующий релиз

### Fleet и HA

- PostgreSQL backend для нескольких coordinator replicas и project-scoped task queues/quotas;
- региональные очереди и data residency;
- signed worker releases и staged rollout;
- hard multi-tenant isolation поверх уже реализованных OIDC/RBAC/projects;
- audit export в SIEM.

## Рекомендуемый порядок

| Приоритет | Статус | Фича | Почему сейчас |
|---|---|---|---|
| P0 | Foundation готов в 0.4 | OTel traces + manifest/replay | Следующий инкремент — golden quality eval |
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
| P3 | Следующий шаг | Fleet и HA | PostgreSQL replicas, regional queues, signed rollout, hard tenant isolation и SIEM export |
