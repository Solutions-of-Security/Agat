# АГАТ

АГАТ 1.7 — local-first платформа для запуска внутренних AI-агентов и визуальных процессов на локальных моделях с управлением вычислительными узлами с одного экрана.

MVP уже включает:

- глобальный последовательный режим для слабого железа;
- параллельный и автоматический режимы с лимитами каждого узла;
- coordinator с SQLite developer mode или PostgreSQL Fleet/HA mode, очередью, lease TTL, повторными попытками и возвратом потерянной задачи;
- переносимый Python-воркер: `single` работает без сторонних зависимостей, Docker image также включает LangGraph runtime;
- поддержку OpenAI-совместимых локальных API: Ollama, LM Studio, vLLM и llama.cpp server;
- удалённые узлы, которым нужен только исходящий HTTPS-доступ к coordinator;
- цепочку `Сборщик → Аналитик → Редактор` с передачей контекста;
- ручное подтверждение перед финальным этапом;
- live-события через SSE;
- live-разделы `Обзор`, `Агенты`, `Запуски`, `Узлы` и `Модели` без статических карточек;
- создание и редактирование агентов из интерфейса: роль, system prompt и привязка модели;
- выбор `single | langgraph`, профили `tool_loop_v1 | specialist_team_v1` и проверку совместимости всех pinned моделей/runtime/profile с online-workers;
- визуальный конструктор процессов с агентами, условиями и ограниченными циклами;
- n8n-подобный редактор с picker, drag-and-drop, поиском узлов, undo/redo, вставкой в edge, autosave, test node и запуском с выбранного шага;
- шаги `HTTP`, `Transform`, `Wait`, `Approval` и `Artifact`, зашифрованные credentials и безопасные шаблонные выражения;
- черновики, неизменяемые опубликованные версии и реальные экземпляры процессов;
- журнал переходов процесса, approval на отдельных шагах и отмену активного экземпляра;
- responsive-панель для desktop и смартфона;
- Docker Compose и systemd unit для постоянной работы;
- локальный Kubernetes-контур Docker Desktop с PVC, probes и отдельным worker Deployment;
- управляемые `web_search`/`web_fetch` для локальных моделей через внутренний SearXNG, с SSRF-фильтрацией и журналом tool calls;
- полный execution trace: входы, контекст, model/tool calls, наблюдаемый ход, ошибки и outputs без сохранения скрытой chain-of-thought;
- опциональный сквозной OpenTelemetry export через W3C Trace Context и OTLP/HTTP без prompt/output content;
- immutable execution manifest, безопасный agent-only replay и side-by-side latency/token eval;
- выбор назначения результата, постоянный Artifact Store, SHA-256 metadata и скачивание файлов из запуска;
- запуск из интерфейса локальных worker-пулов с выбранной моделью, несколькими независимыми экземплярами и stop/start;
- durable orchestration через Temporal с timers, signals, Continue-As-New и отдельным production-bundled worker;
- OIDC Authorization Code + PKCE через Keycloak, роли и project isolation;
- Kong DB-less Gateway с rate/body limits, correlation ID, metrics и закрытым internal API.
- центральный MCP gateway 2026-07-28: project catalogs, risk tiers, immutable policy-as-code и preview, distinct four-eyes, scoped credentials, lease-scoped proxy, emergency deny и зашифрованный audit.
- изолированные MCP tools: WASI без filesystem/network capabilities, digest-pinned OCI Jobs, non-root/read-only/seccomp, exact-IP egress allowlist, one-shot scoped Secrets и optional gVisor/Kata RuntimeClass.
- benchmark-aware Model Router: Ollama capabilities, RAM/VRAM/energy profiles, пассивный EWMA throughput, SLA policy, explainable selection и retry fallback.
- Local RAG: project collections, pull-based локальные embeddings, source provenance, working memory с TTL, явно сохраняемая episodic memory, удаление и JSON export.
- Golden eval: immutable datasets и prompt versions, batch runs, deterministic checks, human rubric, локальный model judge, knowledge-drift detection и promotion gate для prompt/model.
- A2A 1.0 interoperability: inbound/outbound HTTP+JSON, project endpoint bearer, Agent Card discovery, RFC 8693 delegation, SSE, durable push outbox, bounded files, SSRF-safe transport и redacted audit mirrors.
- Production Temporal runtime: TLS/auth fail-closed profiles, immutable versioned worker builds, canary rollout, history replay в CI, acknowledged Updates и interval Schedules через parent→child workflows.
- Process Builder 1.2: deterministic parallel fork/join, cron/calendar и idempotent webhooks, внешний signal, version-pinned subprocess/templates, structural diff, safe/live instance replay, BPMN 2.0 integration и reverse-order compensation.
- Native edge workers: Android llama.cpp/Vulkan service и iOS Core ML/Metal app, Play Integrity/App Attest broker verification, hardware-attested scoped credentials и admin remote wipe.
- Fleet/HA 1.7: несколько coordinator replicas на PostgreSQL, exact project queues/quotas, residency cells, FORCE RLS tenant role, signed worker releases/staged rollout и durable SIEM audit outbox.
- Production PostgreSQL resilience: provider-neutral multi-AZ/PITR gate, schema v23 DR canaries, physical failover/restore rehearsal и HMAC-sealed RPO/RTO/SLO evidence.
- S3-compatible Artifact Store: versioned content authority, SHA-256 verified replica cache, retention/legal hold, exact-version delete outbox и bounded reconciliation/backfill.

## Быстрый запуск

```bash
npm install
npm run build
npm start
```

Панель откроется на `http://127.0.0.1:8787`.

Для запуска через встроенный Kubernetes Docker Desktop:

```bash
npm run k8s:up
```

Подробности и параметры: [локальный Kubernetes в Docker Desktop](./kubernetes-docker-desktop.md).

После запуска войдите пользователем `agat-admin`; пароль выводится без сохранения в репозитории:

```bash
npm run --silent k8s:keycloak-password
```

Панель: `http://127.0.0.1:8787`, Keycloak: `http://127.0.0.1:8080`, Temporal UI: `http://127.0.0.1:8233`.

По умолчанию запускается чистый live-контур. Если нужен демонстрационный набор:

```bash
AGAT_SEED_DEMO=true npm start
```

При переходе со старой версии live-режим удаляет только известные системные fixtures и сохраняет пользовательские агенты и запуски.

Для запуска реального локального воркера с Ollama:

```bash
AGAT_ENROLLMENT_TOKEN=agat-local-enrollment \
AGAT_WORKER_MODELS=qwen3:8b \
AGAT_EMBEDDING_MODELS=embeddinggemma \
AGAT_MODEL_BASE_URL=http://127.0.0.1:11434/v1 \
python3 workers/agat_worker.py
```

Стандартный enrollment token допустим только на loopback для локальной разработки. Для сети используйте случайные токены и HTTPS — полный сценарий описан в [руководстве по запуску](./getting-started.md).

## Документация

- [Архитектура и модель выполнения](./architecture.md)
- [Установка и подключение машин](./getting-started.md)
- [Локальный Kubernetes в Docker Desktop](./kubernetes-docker-desktop.md)
- [Локальный запуск нескольких workers](./local-workers.md)
- [Web-доступ локальных агентов](./web-access.md)
- [MCP gateway и risk policy](./mcp-gateway.md)
- [Risk-tier approvals и emergency deny](./mcp-risk-tier-approvals.md)
- [Изолированное выполнение MCP tools](./isolated-tool-execution.md)
- [Native edge worker 1.6](./native-edge-worker.md)
- [Fleet и HA 1.7](./fleet-ha-1.7.md)
- [Offline SQLite → PostgreSQL migration](./sqlite-postgresql-migration.md)
- [PostgreSQL migration Job и DDL-free runtime](./postgresql-migration-job-runtime-role.md)
- [Managed PostgreSQL: multi-AZ, PITR и измеримый DR](./managed-postgresql-resilience.md)
- [ADR-017: PostgreSQL HA-cell](./adr-017-fleet-ha-cell.md)
- [S3-compatible Artifact Store и lifecycle](./s3-artifact-store-lifecycle.md)
- [ADR-018: PostgreSQL metadata и S3 payload authority](./adr-018-s3-artifact-authority.md)
- [Model Router и hardware benchmarks](./model-router.md)
- [Local RAG, provenance и управляемая память](./local-rag-and-memory.md)
- [Golden eval и prompt registry](./golden-eval-prompt-registry.md)
- [A2A interoperability 1.4](./a2a-adapter.md)
- [Журнал выполнения и артефакты](./execution-traces-and-artifacts.md)
- [OpenTelemetry, execution manifest, replay и eval](./observability-replay-evals.md)
- [Создание и настройка агентов](./agents.md)
- [Runtime агентов: Single и LangGraph](./agent-runtimes.md)
- [LangGraph specialist teams 1.3](./langgraph-specialist-teams.md)
- [Системный промпт агента «Юрист РФ»](./prompts/lawyer-rf.md)
- [Визуальные процессы и циклы](./processes.md)
- [Process Builder 1.2](./process-builder-1.2.md)
- [Durable runtime процессов](./durable-runtime.md)
- [Production hardening durable runtime](./production-durable-runtime.md)
- [PostgreSQL state-store: реализация и дальнейший переход](./postgresql-state-store-design.md)
- [Identity, проекты и API Gateway](./identity-and-gateway.md)
- [HTTP API](./api.md)
- [Безопасность](./security.md)
- [Операции и восстановление](./operations.md)
- [Roadmap и дополнительные фичи](./roadmap.md)
- [Дизайн-система и visual QA](./design-system.md)

## Проверка

```bash
npm run typecheck
npm test
npm run test:temporal
npm run test:ollama-rag
npm run build
npm audit --omit=dev
kubectl kustomize deploy/k8s/docker-desktop >/dev/null
bash -n scripts/*.sh
python3 -m py_compile workers/agat_worker.py workers/web_tools.py workers/telemetry.py sandbox/runner.py
PYTHONPATH=workers python3 -m unittest discover -s workers -p 'test_*.py'
```

Обычный `npm test` не требует запущенной модели. `npm run test:ollama-rag` — отдельный реальный интеграционный тест через локальные Ollama, coordinator и Python-worker; его prerequisites и гарантии описаны в разделе [Local RAG](./local-rag-and-memory.md#реальный-ollama-e2e).

Тесты покрывают последовательную и параллельную выдачу, PostgreSQL multi-replica claims/quotas/RLS/cross-replica artifacts/SIEM outbox, Ed25519 releases/rollout/revoke, benchmark-aware routing и retry fallback, локальную embedding-очередь/RAG provenance/project isolation, immutable prompt/dataset versions, batch eval, human/model-judge audit, knowledge drift и promotion gate, A2A Agent Card/token/project/task/trace boundaries, SSE lifecycle, push/outbox, bounded files, outbound discovery/RFC 8693/SSRF и реальный HTTP+JSON contract, Kubernetes worker launcher, OIDC/JWKS, шифрование credentials, изоляцию и масштабирование worker-пулов, порядок цепочки, создание/обновление агентов, model/runtime/profile routing, реальный LangGraph StateGraph, immutable specialist snapshots, bounded supervisor handoffs, validated team state и legacy-worker fail-closed compatibility, approval gate, live-migration, восстановление просроченного lease, fork/join tokens, signals/webhooks, subprocess pinning/templates, version diff/replay, BPMN round-trip, HTTP idempotency/compensation, Temporal interval/cron/calendar Schedules, Updates/child workflow boundaries, production config validation, history replay, W3C trace propagation, OTel span export, immutable manifest/replay, SSRF-фильтрацию, model tool loop, MCP catalog/risk/policy preview/four-eyes/scoped-secret/emergency-deny/idempotency boundaries, Kubernetes WASI/OCI sandbox manifests/cleanup и native edge challenge/attestation/revocation/wipe lifecycle.
