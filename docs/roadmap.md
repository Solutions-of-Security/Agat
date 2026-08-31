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

Запланированные repository capabilities этого блока закрыты. Перед production cutover остаётся не новый общий кодовый этап, а qualification каждого конкретного контура:

- предъявить fresh provider evidence и measured restore/failover/RPO/RTO/SLO для каждой managed PostgreSQL cell;
- выполнить production-sized offline migration rehearsal и rollback rehearsal на копии конкретных данных;
- подтвердить version-preserving S3 replication/lifecycle/legal hold и region-loss fencing в выбранных регионах;
- подключить реальный pinned Sigstore release pipeline, SPIRE/другой trusted local attestor и SIEM sink с exact ack/dedupe;
- провести cross-system region-loss game day и security review trust roots/scoped secrets/RBAC.

Следующий продуктовый релиз определён как **1.8 — Operator UX foundation**. Его scope сформирован по результатам [UX/UI-аудита всех страниц и пользовательских сценариев](./ux-ui-audit.md). Production qualification Fleet остаётся обязательным параллельным gate и не заменяется UI-работами.

### Запланировано в 1.8 — Operator UX foundation

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

#### Прогресс реализации на 1 сентября 2026 года

Первые два инкремента зафиксированы в [Operator UX foundation](./design/ux-shell-baseline.md), status-first Runs — в [спецификации UX-106](./design/ux-106-runs-status-first.md), новый запуск — в [спецификации UX-108](./design/ux-108-new-run-wizard.md). Статусы ниже относятся к фактически проверенному scope, а не ко всему release gate 1.8.

| ID | Статус | Сделано | Остаётся |
|---|---|---|---|
| UX-101 | Основной scope готов | Ролевая матрица, task-oriented группы, русские labels, breadcrumbs, защита deep links и read-only actions; run/approval deep links реализованы | Shareable routes остальных сущностей остаются в UX-302 |
| UX-102 | Готово | Один mobile-ряд из пяти пунктов и ролевой sheet `Ещё` | Проверка на реальных iOS/Android устройствах входит в release gate |
| UX-103 | Готово | Убраны дубли metrics/CTA; кнопка открывает project-scoped панель с pending approvals, warn/error-событиями, seen/unseen, `Прочитать всё` и точными переходами; profile menu явное | Server-side синхронизация seen-state между браузерами не входит в scope 1.8 |
| UX-104 | Базовый gate готов | 14/12 px для стандартных страниц, controls ≥44 px, responsive Fleet | Populated Process canvas и системная visual regression fixture |
| UX-105 | Базовый gate готов | Рабочий skip link; WAI-ARIA tabs с roving focus; подписанные dialogs; focus trap/return, Escape и объяснение недоступного experiment; keyboard-only smoke пройден | Screen reader и реальные устройства входят в release gate |
| UX-106 | Основной scope готов | Master-detail list, status/current stage/duration/result/error/next action, state-valid Repeat/Cancel/Open, inline cancel confirmation, lazy technical disclosure и shareable run/approval routes | Реальные устройства, screen reader и operator usability-test входят в release gate |
| UX-107 | Частично | Отдельная очередь, счётчик и доступные роли решений | Фильтры, risk/effect context, комментарий и полный audit trail |
| UX-108 | Основной scope готов | Трёхшаговый wizard, один безопасный default-agent, явный reorder, optional knowledge, summary/readiness, priority presets, journal-default и advanced disclosure; desktop/mobile Browser QA пройден | Реальный coordinator/worker e2e, реальные устройства, screen reader и operator usability-test входят в release gate |
| UX-109 | Выявленные дефекты исправлены | 36 route/viewport checks без overflow; Fleet и fixed bottom nav исправлены | Реальные устройства и populated Quality/Process fixtures |
| UX-110 | Частично | Аватар открывает меню; logout явный; декоративный topbar control удалён | Замена оставшихся системных `prompt/confirm` единым product dialog |

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
| UX-306 | UX-telemetry без чувствительного content | Task completion, time-to-action, ошибки навигации и раскрытие advanced без prompt/output data | P2 |
| UX-307 | Регулярные usability regressions | Набор сценариев и квартальная проверка operator/designer/admin на desktop/mobile | P2 |

### Рекомендуемая последовательность UX-работ

1. Параллельно выполнить UX-101 и UX-104.
2. На новой IA собрать UX-102, UX-103 и UX-105.
3. После foundation реализовать UX-106 и UX-108.
4. Поверх новой модели Runs добавить UX-107.
5. Закрыть mobile defects UX-109 и единые dangerous actions UX-110.
6. Провести release-gate usability test 1.8.
7. Зафиксировать общий Basic / Advanced паттерн UX-201.
8. Параллельно развивать creator-поток UX-202–UX-206 и admin-поток UX-207–UX-208.
9. Завершить 1.9 терминологией, системными состояниями и autosave guard.
10. Не начинать UX-301–UX-307 до стабилизации shell и основных workflows, чтобы не автоматизировать текущую перегрузку.

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
| P3 | Готово в 1.7 | Fleet и HA | PostgreSQL replicas, regional queues, signed rollout, hard tenant isolation и SIEM export |
| P1 | Этапы 1–6 готовы | Production Fleet readiness и DR | Все repository capabilities закрыты; остаётся qualification конкретных provider/CI/attestor/SIEM deployment и production game day |
| P0 | Foundation готова в 1.8 | Role-based IA, mobile navigation, notifications и accessibility foundation | Shell, базовые уведомления и keyboard gate реализованы; остаются device/screen-reader release checks |
| P0 | UX-106 и UX-108 готовы; следующий инкремент 1.8 | Полный inbox согласований UX-107 | Завершает decide-часть основного create→monitor→decide operator workflow |
| P1 | Предварительно в 1.9 | Basic / Advanced и creator workflows | Упрощает Agents, Processes, Knowledge и Quality без удаления экспертных возможностей |
| P1 | Предварительно в 1.9 | Integrations и Infrastructure workspaces | Убирает MCP/A2A/Fleet из общего пользовательского потока |
| P2 | После 1.9 | Search, saved views, personalization и UX-telemetry | Повышает эффективность после стабилизации базовой структуры |
