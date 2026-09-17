# Документация АГАТ

[Краткое описание и быстрый запуск](./README.md).

Для первого знакомства начните с установки, затем переходите к агентам и процессам.
Расширенные варианты развёртывания и интеграции описаны в отдельных руководствах.

## Установка и запуск

- [Установка и подключение машин](./getting-started.md)
- [Локальный запуск в Docker Compose](./local-docker.md)
- [Локальный Kubernetes в Docker Desktop](./kubernetes-docker-desktop.md)
- [Локальный запуск нескольких workers](./local-workers.md)

## Агенты и процессы

- [Создание и настройка агентов](./agents.md)
- [Маркетплейс готовых агентов](./agent-marketplace.md)
- [Runtime агентов: Single и LangGraph](./agent-runtimes.md)
- [LangGraph specialist teams 1.3](./langgraph-specialist-teams.md)
- [Системный промпт агента «Юрист РФ»](./prompts/lawyer-rf.md)
- [Визуальные процессы и циклы](./processes.md)
- [Process Builder 1.2](./process-builder-1.2.md)
- [Durable runtime процессов](./durable-runtime.md)
- [Golden eval и prompt registry](./golden-eval-prompt-registry.md)
- [Каталог процессов с LLM-агентами: категории, требования и шаблоны](./llm-processes/README.md)

## Модели, знания и интеграции

- [Model Router и hardware benchmarks](./model-router.md)
- [Local RAG, provenance и управляемая память](./local-rag-and-memory.md)
- [Web-доступ локальных агентов](./web-access.md)
- [MCP gateway и risk policy](./mcp-gateway.md)
- [A2A interoperability 1.4](./a2a-adapter.md)
- [HTTP API](./api.md)

## Архитектура и эксплуатация

- [Архитектура и модель выполнения](./architecture.md)
- [Fleet и HA 1.7](./fleet-ha-1.7.md)
- [Offline SQLite → PostgreSQL migration](./sqlite-postgresql-migration.md)
- [PostgreSQL migration Job и DDL-free runtime](./postgresql-migration-job-runtime-role.md)
- [Managed PostgreSQL: multi-AZ, PITR и измеримый DR](./managed-postgresql-resilience.md)
- [ADR-017: PostgreSQL HA-cell](./adr-017-fleet-ha-cell.md)
- [S3-compatible Artifact Store и lifecycle](./s3-artifact-store-lifecycle.md)
- [ADR-018: PostgreSQL metadata и S3 payload authority](./adr-018-s3-artifact-authority.md)
- [Residency-aware region-loss DR](./region-loss-dr.md)
- [ADR-019: whole-cell region-loss DR](./adr-019-residency-region-loss-dr.md)
- [PostgreSQL state-store: реализация и дальнейший переход](./postgresql-state-store-design.md)
- [Production hardening durable runtime](./production-durable-runtime.md)
- [Операции и восстановление](./operations.md)

## Безопасность и управление доступом

- [Безопасность](./security.md)
- [Identity, проекты и API Gateway](./identity-and-gateway.md)
- [Risk-tier approvals и emergency deny](./mcp-risk-tier-approvals.md)
- [Изолированное выполнение MCP tools](./isolated-tool-execution.md)
- [Native edge worker 1.6](./native-edge-worker.md)
- [OCI provenance и runtime attestation workers](./worker-supply-chain-attestation.md)
- [ADR-020: worker supply-chain attestation](./adr-020-worker-supply-chain-attestation.md)
- [SIEM delivery conformance, retention и DLQ](./siem-retention-dlq.md)
- [ADR-021: acknowledged SIEM delivery и retained DLQ](./adr-021-siem-retention-dlq.md)

## Наблюдаемость

- [Журнал выполнения и артефакты](./execution-traces-and-artifacts.md)
- [OpenTelemetry, execution manifest, replay и eval](./observability-replay-evals.md)

## Продукт, исследования и интерфейс

- [Roadmap и дополнительные фичи](./roadmap.md)
- [Использование Агата: сценарии, барьеры и приоритеты — 8 сентября 2026](./product-usage-research-2026-09-08.md)
- [Приоритеты агентов и процессов: сентябрь 2026 — август 2027](./agent-process-priorities-2026.md)
- [Дизайн-система и visual QA](./design-system.md)
- [UX/UI-аудит всех страниц и пользовательских сценариев](./ux-ui-audit.md)
- [Operator UX foundation: shell, роли и responsive QA](./design/ux-shell-baseline.md)
- [UX-106: status-first workspace запусков](./design/ux-106-runs-status-first.md)
- [UX-108: трёхшаговое создание запуска](./design/ux-108-new-run-wizard.md)

## Разработка и проверки

Команды выполняются из корня репозитория после `npm ci`:

```bash
npm run typecheck
npm test
npm run build
npm run docs:processes:check
```

`npm test` включает проверки coordinator, интерфейса, Python-воркера и replay
историй Temporal; запущенная модель не требуется. Для LangGraph и OpenTelemetry
установите зависимости из `workers/requirements.txt` в отдельное Python-окружение.
GitHub Actions использует Node.js 24 и Python 3.13.

Интеграционные проверки запускаются отдельно. Необходимые сервисы и настройки
указаны в соответствующих руководствах.

| Проверка | Команда | Руководство |
| --- | --- | --- |
| Ollama и локальный RAG | `npm run test:ollama-rag` | [Local RAG](./local-rag-and-memory.md) |
| PostgreSQL и несколько coordinator | `npm run fleet:test-ha-postgres` | [Fleet и HA](./fleet-ha-1.7.md) |
| Миграция SQLite → PostgreSQL | `npm run fleet:test-state-migration` | [Миграция данных](./sqlite-postgresql-migration.md) |
| PostgreSQL failover и восстановление | `npm run fleet:test-postgres-dr` | [Устойчивость PostgreSQL](./managed-postgresql-resilience.md) |
| S3 Artifact Store | `npm run fleet:test-artifact-store` | [Artifact Store](./s3-artifact-store-lifecycle.md) |
| Восстановление после потери региона | `npm run fleet:test-region-loss-dr` | [Region-loss DR](./region-loss-dr.md) |
