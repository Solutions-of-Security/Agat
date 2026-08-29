# HTTP API

Префикс API: `/api/v1`.

## Авторизация

- Kubernetes dashboard: `Authorization: Bearer <Keycloak access token>`; поддерживается только проверенный RS256 JWT для issuer/client АГАТ.
- Активный проект: `X-Agat-Project-Id`; backend проверяет роль и claim `agat_projects`.
- Legacy local mode без OIDC: чувствительные/mutating запросы используют `X-Agat-Admin-Token`.
- Worker registration: `enrollmentToken` в JSON body.
- Worker calls: `Authorization: Bearer <node token>`.
- Public process webhook: отдельный `Authorization: Bearer <webhook token>`; start также требует `Idempotency-Key`.
- Temporal Activity: отдельный `X-Agat-Temporal-Token` только внутри ClusterIP-контура.

Node token возвращается один раз при регистрации и хранится worker локально. В базе хранится только SHA-256 hash.

После регистрации worker передаёт в heartbeat текущие `models`, `modelProfiles`, VRAM, `endpoint`, `maxConcurrency`, `labels`, `agentRuntimes` и `agentRuntimeProfiles`. Поэтому изменение списка моделей или установленного runtime/profile не требует новой регистрации или повторной передачи enrollment token.

## Dashboard

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/health` | Liveness и версия |
| `GET` | `/auth/config` | Публичная конфигурация OIDC adapter |
| `GET` | `/auth/me` | Профиль, роли и доступные проекты |
| `GET` | `/projects` | Проекты пользователя и активный project ID |
| `POST` | `/projects` | Создать проект (`admin`) |
| `GET` | `/overview` | Runs, stages, nodes, approvals, события и счётчики |
| `GET` | `/agents` | Каталог агентов |
| `GET` | `/credentials` | Metadata credentials без secret values (`admin/designer`) |
| `POST` | `/credentials` | Создать зашифрованные credentials (`admin/designer`) |
| `PATCH/DELETE` | `/credentials/:id` | Обновить/удалить credentials (`admin/designer`) |
| `GET` | `/mcp/policy` | Активная project policy, immutable version/hash и глобальный emergency state |
| `POST` | `/mcp/policy/preview` | Валидировать candidate policy и получить effective before/after (`admin/designer`) |
| `PUT` | `/mcp/policy` | Активировать immutable policy по preview `baseSha256` (`admin/designer`) |
| `PUT` | `/mcp/emergency-deny` | Глобально включить/снять MCP kill switch с причиной (`admin`) |
| `GET` | `/local-workers` | Состояние локального Kubernetes launcher, модели и worker-пулы |
| `POST` | `/local-workers` | Создать 1–8 локальных workers с выбранной моделью |
| `POST` | `/local-workers/:poolId/start` | Запустить остановленный worker-пул |
| `POST` | `/local-workers/:poolId/stop` | Остановить worker-пул, сохранив конфигурацию |
| `DELETE` | `/local-workers/:poolId` | Удалить Deployments управляемого worker-пула |
| `POST` | `/agents` | Создать агента |
| `PATCH` | `/agents/:id` | Обновить metadata/runtime; prompt/model меняются только через eval promotion |
| `GET` | `/evals` | Prompt registries, golden datasets и experiment summaries |
| `GET` | `/evals/experiments/:id` | Experiment items, outputs, scores и audit trail |
| `POST` | `/evals/prompts` | Создать standalone prompt registry (`admin/designer`) |
| `POST` | `/evals/prompts/:id/versions` | Создать immutable prompt version (`admin/designer`) |
| `POST` | `/evals/prompts/:id/promote` | Активировать prompt/model после matching PASS experiment (`admin/designer`) |
| `POST` | `/evals/datasets` | Создать golden dataset v1 (`admin/designer`) |
| `POST` | `/evals/datasets/:id/versions` | Создать полный immutable dataset snapshot (`admin/designer`) |
| `POST` | `/evals/experiments` | Запустить batch candidate runs (`admin/designer/operator`) |
| `POST` | `/evals/experiments/:id/judge` | Запустить локальный model judge (`admin/designer/operator`) |
| `POST` | `/evals/items/:id/reviews` | Добавить append-only human rubric review |
| `GET` | `/knowledge` | Collections, documents, indexing status и активная memory |
| `POST` | `/knowledge/collections` | Создать project-scoped collection (`admin/designer`) |
| `DELETE` | `/knowledge/collections/:id` | Каскадно удалить collection (`admin/designer`) |
| `POST` | `/knowledge/collections/:id/documents` | Добавить `text/*` document (`admin/designer`) |
| `DELETE` | `/knowledge/documents/:id` | Удалить document/chunks (`admin/designer`) |
| `POST` | `/knowledge/memory` | Явно сохранить working/episodic memory (`admin/designer/operator`) |
| `DELETE` | `/knowledge/memory/:id` | Удалить memory (`admin/designer`) |
| `GET` | `/knowledge/export` | Скачать project JSON (`admin/designer/auditor`) |
| `GET` | `/processes` | Список процессов с черновиками и опубликованными версиями |
| `POST` | `/processes` | Создать процесс с начальным графом |
| `POST` | `/processes/import/bpmn` | Импортировать BPMN 2.0 XML в новый draft (`admin/designer`) |
| `GET` | `/processes/:id` | Получить процесс |
| `PATCH` | `/processes/:id` | Сохранить название, описание и черновой граф |
| `POST` | `/processes/:id/publish` | Опубликовать неизменяемую версию черновика |
| `GET` | `/processes/:id/versions/:version` | Получить version number либо `draft` |
| `GET` | `/processes/:id/diff?from=&to=` | Структурный diff двух версий/draft |
| `GET` | `/processes/:id/export/bpmn?version=` | Скачать BPMN 2.0 XML |
| `POST` | `/processes/:id/start` | Запустить последнюю опубликованную версию |
| `GET` | `/processes/:id/schedule` | Получить interval/cron/calendar Schedule и следующие запуски |
| `PUT` | `/processes/:id/schedule` | Создать/заменить Temporal Schedule (`admin/designer`) |
| `DELETE` | `/processes/:id/schedule` | Удалить schedule (`admin/designer`) |
| `POST` | `/processes/:id/schedule/trigger` | Немедленно инициировать scheduled run (`admin/designer/operator`) |
| `GET` | `/processes/:id/webhooks` | Список start/signal webhooks без tokens |
| `POST` | `/processes/:id/webhooks` | Создать webhook и один раз получить token (`admin/designer`) |
| `POST` | `/processes/:id/signals/:name` | Доставить внешний signal (`admin/designer/operator`) |
| `POST` | `/process-webhooks/:id/rotate` | Ротировать и один раз получить новый token (`admin/designer`) |
| `DELETE` | `/process-webhooks/:id` | Удалить webhook (`admin/designer`) |
| `POST` | `/process-webhooks/:id` | Публичный invocation по отдельному Bearer token |
| `POST` | `/processes/:id/test-node` | Выполнить/поставить в очередь один draft node |
| `GET` | `/process-instances` | Список экземпляров процессов |
| `GET` | `/process-instances/:id` | Один экземпляр и история переходов |
| `POST` | `/process-instances/:id/replay` | Safe/live replay terminal instance |
| `POST` | `/process-instances/:id/cancel` | Отменить активный экземпляр и его run |
| `GET` | `/runs/:id` | Один запуск и его этапы |
| `GET` | `/runs/:id/trace` | Trace, outputs, artifacts, execution manifest и eval comparison |
| `POST` | `/runs/:id/replay` | Создать один replay либо два A/B-кандидата (`admin/designer/operator`) |
| `POST` | `/runs` | Создать запуск |
| `GET` | `/artifacts/:id/download` | Скачать authenticated артефакт по ID |
| `PATCH` | `/settings/scheduler` | Изменить глобальный режим |
| `PATCH` | `/settings/model-router` | Изменить глобальную Model Router policy (`admin`) |
| `POST` | `/approvals/:stageId` | `approve` или `reject` |
| `GET` | `/events` | SSE с `Last-Event-ID` |

Управление `/local-workers`, scheduler, Model Router, MCP emergency deny и создание проекта требуют роли `admin`. Agents/process definitions/credentials и MCP policy доступны `admin` и `designer`; запуск/отмена — также `operator`; approval — `admin/operator`; чтение — любой роли АГАТ. В legacy mode те же mutating endpoints требуют admin token, но один субъект `local-admin` не может выполнить обязательный MCP four-eyes.

Создание локального worker-пула:

```json
{
  "name": "Llama для анализа",
  "model": "llama3.2:latest",
  "workers": 3,
  "concurrency": 1,
  "webEnabled": true
}
```

`workers` ограничен операторским `AGAT_LOCAL_WORKER_MAX_PER_LAUNCH`, по умолчанию 8. `concurrency` одного worker — от 1 до 8. API не принимает image, Kubernetes manifest, command, Secret или model URL. Подробнее: [локальный запуск нескольких workers](./local-workers.md).

Создание запуска:

```json
{
  "name": "Проверка релиза",
  "input": "Проверить release notes и результаты тестов",
  "executionMode": "sequential",
  "priority": 70,
  "approvalRequired": true,
  "agentIds": ["collector", "analyst", "editor"],
  "resultDestination": "artifacts",
  "artifactPath": "reports/releases",
  "knowledgeCollectionIds": ["product-handbook-id"]
}
```

`resultDestination` принимает `history` или `artifacts`. В обоих случаях input, события и stage outputs остаются в SQLite. `artifacts` дополнительно записывает stage outputs и `result.md` в настроенный `AGAT_ARTIFACTS_DIR`. `artifactPath` — только безопасный относительный каталог; UUID запуска добавляется автоматически.

Создание агента:

```json
{
  "name": "QA-инспектор",
  "role": "Проверяет результат процесса и перечисляет отклонения",
  "systemPrompt": "Проверь переданные данные и верни структурированный отчёт без домыслов.",
  "model": "qwen3:8b",
  "runtime": "langgraph",
  "runtimeConfig": {
    "profile": "tool_loop_v1",
    "maxIterations": 6
  }
}
```

`model: null` включает автовыбор. `model: "qwen3:8b"` ограничивает выдачу этапа workers, которые объявили это точное имя модели. `runtime` принимает `single` или `langgraph`; неизвестные runtime и `maxIterations` вне `1..12` отклоняются. Если поля runtime не переданы, новый агент получает безопасный default `single/tool_loop_v1/6`; legacy PATCH без этих полей сохраняет текущую конфигурацию. Ответ агента дополнительно содержит `runtime`, `runtimeConfig`, `isBuiltIn`, `totalRuns`, `activeRuns` и `lastRunAt`.

Team создаётся тем же endpoint:

```json
{
  "name": "Исследовательская команда",
  "role": "Делегирует проверку фактов и рецензию",
  "systemPrompt": "Выбирай специалиста по задаче и заверши итог только по проверенным результатам.",
  "model": "qwen3:8b",
  "runtime": "langgraph",
  "runtimeConfig": {
    "profile": "specialist_team_v1",
    "maxIterations": 4,
    "maxHandoffs": 3,
    "stateSchema": "specialist_team_state_v1",
    "specialistAgentIds": ["researcher-agent-id", "reviewer-agent-id"]
  }
}
```

`specialistAgentIds` требует 2–8 разных обычных агентов того же project. Self-reference, внутренние служебные system/eval agents, nested teams, неизвестная state schema, `maxHandoffs` вне `1..8` и попытка превратить уже используемого specialist в team отклоняются. Run/process/eval creation фиксирует ordered immutable snapshots всех участников.

Worker сообщает совместимость при регистрации и в heartbeat:

```json
{
  "capabilities": {
    "endpoint": "http://127.0.0.1:11434/v1",
    "models": ["qwen3:8b"],
    "embeddingModels": ["embeddinggemma"],
    "maxConcurrency": 1,
    "labels": { "web": "controlled" },
    "agentRuntimes": ["single", "langgraph"],
    "agentRuntimeProfiles": ["tool_loop_v1", "specialist_team_v1"]
  }
}
```

Scheduler сопоставляет модель, runtime и профиль. Для team один worker должен объявить модели supervisor и всех pinned specialists. Старый worker без `agentRuntimes` считается совместимым только с `single`; worker без `agentRuntimeProfiles` — только с `tool_loop_v1` и не получает team stage.

Ресурсная политика:

```json
{
  "mode": "auto",
  "globalMaxConcurrency": 1
}
```

## Процессы

Schedule принимает bounded input и явный вид `interval`, `cron` или `calendar`. Для interval минимальное значение — 60 секунд, максимальное — один год:

```json
{
  "input": "Собери ежедневный отчёт",
  "kind": "interval",
  "everySeconds": 86400,
  "timezone": "Europe/Moscow",
  "priority": 70,
  "paused": false,
  "knowledgeCollectionIds": ["product-handbook-id"]
}
```

Cron и calendar используют то же тело, но заменяют интервал:

```json
{
  "input": "Собери отчёт по рабочим дням",
  "kind": "cron",
  "cronExpression": "0 8 * * MON-FRI",
  "timezone": "Europe/Moscow",
  "priority": 70,
  "paused": false,
  "knowledgeCollectionIds": []
}
```

```json
{
  "input": "Закрой смену",
  "kind": "calendar",
  "calendar": { "minute": 30, "hour": [9, 18], "dayOfWeek": ["MONDAY", "FRIDAY"] },
  "timezone": "Europe/Moscow",
  "priority": 50,
  "paused": false,
  "knowledgeCollectionIds": []
}
```

Temporal применяет overlap `SKIP`, catch-up window 5 минут и pause-on-failure. Schedule доступен только при `processRuntime.mode=temporal`; иначе API возвращает `503`. Каждый action создаёт новый process instance через parent→child workflow.

Start/signal webhook создаётся для опубликованного процесса. Token присутствует только в create/rotate response. Публичный start требует `Idempotency-Key` и возвращает тот же instance при повторе:

```http
POST /api/v1/process-webhooks/WEBHOOK_ID
Authorization: Bearer ONE_TIME_TOKEN
Idempotency-Key: crm-event-42
Content-Type: application/json

{ "input": "Новый заказ 42", "priority": 70 }
```

Signal можно доставить webhook либо OIDC-вызовом:

```http
POST /api/v1/processes/PROCESS_ID/signals/order.confirmed
Content-Type: application/json

{
  "instanceId": "optional-instance-id",
  "correlationKey": "order-42",
  "payload": { "approved": true }
}
```

Создание процесса без `graph` возвращает безопасный стартовый шаблон. Свой граф можно передать сразу:

```json
{
  "name": "Проверка публикации",
  "description": "Собрать материал и повторять редактуру до готовности",
  "graph": {
    "nodes": [
      { "id": "start", "type": "start", "name": "Старт", "position": { "x": 40, "y": 160 }, "config": {} },
      { "id": "writer", "type": "agent", "name": "Редактор", "position": { "x": 260, "y": 140 }, "config": { "agentId": "editor", "approvalRequired": false } },
      { "id": "loop", "type": "loop", "name": "Проверка", "position": { "x": 520, "y": 140 }, "config": { "condition": { "source": "last_output", "operator": "contains", "value": "доработать", "caseSensitive": false }, "maxIterations": 3 } },
      { "id": "end", "type": "end", "name": "Готово", "position": { "x": 780, "y": 160 }, "config": {} }
    ],
    "edges": [
      { "id": "e1", "source": "start", "target": "writer", "branch": "default" },
      { "id": "e2", "source": "writer", "target": "loop", "branch": "default" },
      { "id": "e3", "source": "loop", "target": "writer", "branch": "repeat" },
      { "id": "e4", "source": "loop", "target": "end", "branch": "exit" }
    ]
  }
}
```

Поддерживаемые операторы `condition`: `always`, `contains`, `not_contains`, `equals`, `not_equals`. Источник условия — `last_output`, то есть output последнего завершённого шага, который записал результат.

После сохранения черновика процесс нужно опубликовать. Запуск без опубликованной версии отклоняется:

```json
{
  "input": "Проверь черновик release notes",
  "priority": 70,
  "resultDestination": "artifacts",
  "artifactPath": "process-runs/releases",
  "knowledgeCollectionIds": ["product-handbook-id"]
}
```

Экземпляр возвращает `processId`, `processVersion`, связанный `runId`, `currentNode`, `activeNodes`, `pendingSignals`, `compensations`, счётчики `loopCounts`, replay metadata, `runtime` (`database|temporal|embedded`) и `workflowId`. Для agent/HTTP/compensation lease поле `stage.processNodeId` показывает исходный шаг визуального graph.

Помимо узлов примера поддерживаются `http`, `transform`, `wait`, `approval`, `artifact`, `parallel_fork`, `parallel_join`, `signal` и `subprocess`. Шаблоны используют только `{{input}}`, `{{lastOutput}}`, `{{json.path}}` и `{{loop.nodeId}}`; произвольный JavaScript не выполняется.

`POST /process-instances/:id/replay` принимает `{ "mode": "safe" | "live", "priority"?: 0..100 }`. Diff принимает `from`/`to` как номер либо `draft`. BPMN import принимает XML body до 1 MiB; export возвращает attachment `application/xml`. Полный контракт и security invariants: [Process Builder 1.2](./process-builder-1.2.md).

## Worker

| Метод | Путь | Назначение |
|---|---|---|
| `POST` | `/workers/register` | Регистрация или повторная привязка имени узла |
| `POST` | `/workers/heartbeat` | Метрики, online status и актуальные capabilities worker |
| `POST` | `/workers/knowledge/lease` | Получить embedding batch; `204` означает отсутствие подходящей работы |
| `POST` | `/workers/knowledge/leases/:id/renew` | Продлить embedding lease |
| `POST` | `/workers/knowledge/leases/:id/complete` | Вернуть vectors для всех chunks batch |
| `POST` | `/workers/knowledge/leases/:id/fail` | Повторить job или зафиксировать ошибку |
| `POST` | `/workers/lease` | Получить следующий этап; `204` означает отсутствие работы |
| `POST` | `/leases/:id/renew` | Продлить TTL |
| `POST` | `/leases/:id/events` | Добавить live-событие |
| `POST` | `/leases/:id/knowledge/search` | Выполнить поиск только по collections snapshot текущего stage |
| `POST` | `/leases/:id/complete` | Сохранить output, опциональные текстовые artifacts и разблокировать следующий этап |
| `POST` | `/leases/:id/fail` | Retry или окончательная ошибка |

Регистрация:

```json
{
  "enrollmentToken": "...",
  "name": "gpu-box-01",
  "platform": "Ubuntu 24.04",
  "architecture": "x64",
  "endpoint": "http://127.0.0.1:11434/v1",
  "models": ["qwen3:8b"],
  "embeddingModels": ["embeddinggemma"],
  "cpuCores": 24,
  "memoryMb": 131072,
  "vramMb": 24576,
  "gpu": "RTX 4090",
  "maxConcurrency": 1,
  "labels": { "zone": "office", "trust": "internal" },
  "modelProfiles": [{
    "name": "qwen3:8b",
    "provider": "ollama",
    "contextWindow": 65536,
    "parameterCount": 8200000000,
    "capabilities": ["completion", "tools"],
    "qualityScore": 84
  }]
}
```

Worker запрашивает lease с собственной версией:

```json
{ "workerVersion": "1.3.0" }
```

Lease содержит run input, immutable agent snapshot, ordered `agent.specialists` для team, outputs уже завершённых этапов, `routing` с requested/selected model и объясняющими signals, `knowledge.groups`/активную memory, а также `traceContext` с `traceId`/W3C `traceparent`. Model API key никогда не передаётся coordinator. Team целиком исполняется на одном lease/worker; specialist prompts и models берутся только из pinned snapshots.

Embedding lease:

```json
{
  "leaseId": "...",
  "expiresAt": "2026-08-16T12:00:00.000Z",
  "projectId": "default",
  "collection": {
    "id": "...",
    "name": "Product handbook",
    "embeddingModel": "embeddinggemma"
  },
  "document": { "id": "...", "name": "privacy.md" },
  "chunks": [
    { "id": "...", "ordinal": 0, "content": "..." }
  ]
}
```

Завершение batch обязано вернуть vector для каждого выданного chunk ровно один раз:

```json
{
  "embeddings": [
    { "chunkId": "...", "embedding": [0.012, -0.031, 0.044] }
  ]
}
```

Для retrieval worker локально embedding-ит вход и отправляет один query на каждую группу модели:

```json
{
  "queries": [{
    "embeddingModel": "embeddinggemma",
    "collectionIds": ["product-handbook-id"],
    "topK": 6,
    "vector": [0.012, -0.031, 0.044]
  }]
}
```

Coordinator проверяет node/stage lease, project и snapshot collections, ранжирует только vectors той же модели/размерности, сохраняет hash запроса и provenance. Лимиты и lifecycle: [Local RAG и управляемая память](./local-rag-and-memory.md).

Завершение этапа специализированным worker:

```json
{
  "output": "Готово",
  "artifacts": [
    {
      "name": "report.csv",
      "mediaType": "text/csv; charset=utf-8",
      "content": "metric,value\nstatus,ok"
    }
  ],
  "metrics": {
    "durationMs": 1420,
    "modelDurationMs": 1180,
    "modelCalls": 2,
    "inputTokens": 912,
    "outputTokens": 188,
    "toolCalls": 1,
    "model": "qwen3:8b",
    "provider": "openai-compatible",
    "toolSchemaVersion": "agat.tools.v2",
    "energyJoules": 71.4
  }
}
```

Дополнительные artifacts сохраняются только если для запуска выбран `resultDestination: "artifacts"`. До восьми файлов, суммарно до 800 000 UTF-8 байт на этап. Основной `output` ограничен 900 000 символов. Все поля `metrics` опциональны и проходят числовые/строковые лимиты на coordinator. Пара `outputTokens + modelDurationMs` обновляет node/model EWMA benchmark; `energyJoules` дополнительно формирует `joulesPer1kTokens`. Политика и rollout описаны в [Model Router и hardware benchmarks](./model-router.md).

## Execution trace

`GET /runs/:id/trace` требует OIDC Bearer token (либо legacy admin token, когда OIDC выключен). Ответ содержит:

```json
{
  "run": { "id": "...", "input": "...", "stages": [] },
  "events": [],
  "artifacts": [],
  "manifest": {
    "schemaVersion": 3,
    "traceId": "...",
    "inputSha256": "...",
    "stages": [],
    "manifestSha256": "..."
  },
  "comparison": null,
  "truncated": false,
  "tracePolicy": {
    "rawReasoningStored": false,
    "observableProgressStored": true,
    "exactInputsStored": true,
    "exactOutputsStored": true,
    "toolArguments": "redacted"
  }
}
```

Trace содержит до 10 000 событий в исходном порядке. `manifest` schema v3 фиксирует prompt/model/runtime/worker snapshots, registry prompt/version, routing decision и hashes; для team она дополнительно содержит ordered specialist snapshots schema v1. `agent_handoff` events хранят member ID/name/hash и размеры assignment/output без их текста. `comparison` появляется после ad-hoc replay и содержит baseline, кандидатов и gates completion/latency/token budget; его quality честно остаётся `not_evaluated`. Для candidate/judge run поле `goldenEvaluation` содержит experiment, exact versions, score source и release gates. Скрытая chain-of-thought не записывается.

Replay терминального agent-only запуска:

```json
{
  "variants": [
    { "name": "Manifest replay" },
    { "name": "Model B", "modelOverrides": { "collector": "qwen3:14b" } }
  ]
}
```

Разрешена одна или две версии с уникальными именами до 60 символов. Process runs и run с не-agent stage отклоняются, чтобы не повторять внешние side effects. Полная модель описана в [руководстве по OpenTelemetry, manifest, replay и eval](./observability-replay-evals.md).

## Ограничения

- JSON body: не более 1 MiB.
- Golden dataset create/version body: не более 8 MiB, максимум 200 examples и 20 rubric criteria.
- Worker log message: обрезается до 4000 символов.
- Run trace: до 10 000 событий в одном ответе.
- Agent artifacts: до 8 файлов и 800 000 UTF-8 байт на этап.
- Overview: до 100 последних запусков и 80 событий.
- Процесс: не более 100 шагов и 200 связей; цикл — от 1 до 50 повторов; экземпляр — не более 1000 переходов.
- SSE: heartbeat раз в 15 секунд.

## MCP gateway

Dashboard routes используют обычный OIDC/project context. Читать catalog/policy могут все authenticated roles; создавать/изменять servers, legacy tool policy, scoped credentials и policy-as-code — `admin/designer`; принимать tool approval — `admin/operator`; глобальный emergency deny — только `admin`.

### Scoped credential

`POST/PATCH /api/v1/credentials` принимает необязательный `scope`. Отсутствующий scope означает совместимый `project`; рекомендуемый MCP-вариант:

```json
{
  "name": "CRM read token",
  "type": "api_key",
  "data": { "apiKey": "secret-value" },
  "scope": {
    "kind": "mcp",
    "serverNamespaces": ["crm"],
    "toolPatterns": ["get_*", "search_*"],
    "risks": ["read"],
    "allowCatalog": true,
    "expiresAt": "2026-12-31T21:00:00.000Z"
  }
}
```

MCP scope требует 1–50 точных namespaces, 1–100 tool globs и хотя бы один risk. `expiresAt` — будущий ISO timestamp либо `null`. List API возвращает scope и имена secret fields, но не значения. Такой credential нельзя использовать в HTTP process step.

### Policy-as-code

```http
GET /api/v1/mcp/policy
```

Ответ содержит `version`, `sha256`, канонический `document`, actor/time и глобальный `emergencyDeny`. До первой публикации действует виртуальная baseline policy `v0`.

Preview не изменяет runtime:

```http
POST /api/v1/mcp/policy/preview
Content-Type: application/json

{
  "document": {
    "schemaVersion": 1,
    "name": "production",
    "defaults": {
      "read": { "tier": "low", "effect": "allow", "approvals": 0 },
      "write": { "tier": "elevated", "effect": "approval", "approvals": 1 },
      "destructive": { "tier": "critical", "effect": "approval", "approvals": 2 },
      "unknown": { "tier": "high", "effect": "approval", "approvals": 1 }
    },
    "rules": []
  }
}
```

`defaults` обязан содержать `read/write/destructive/unknown`. Ответ возвращает `baseVersion`, `baseSha256`, `candidateSha256`, канонический документ, `changed`, агрегированный `summary` и `changes[]` с effective decision до/после для catalog tools.

Активация использует optimistic concurrency:

```http
PUT /api/v1/mcp/policy
Content-Type: application/json

{
  "baseSha256": "sha256-from-preview",
  "document": {
    "schemaVersion": 1,
    "name": "production",
    "defaults": {
      "read": { "tier": "low", "effect": "allow", "approvals": 0 },
      "write": { "tier": "elevated", "effect": "approval", "approvals": 1 },
      "destructive": { "tier": "critical", "effect": "approval", "approvals": 2 },
      "unknown": { "tier": "high", "effect": "approval", "approvals": 1 }
    },
    "rules": []
  }
}
```

Если активный hash изменился после preview, API требует новый preview. Успех создаёт immutable project-scoped версию; тот же canonical hash не создаёт дубликат. Полная schema и пример: [Risk-tier approvals и emergency deny](./mcp-risk-tier-approvals.md#формат-policy-as-code).

### Emergency deny

```http
PUT /api/v1/mcp/emergency-deny
Content-Type: application/json

{ "enabled": true, "reason": "Incident INC-2026-041" }
```

Switch глобален для coordinator, persisted и требует непустую причину и роль `admin`. Ответ содержит `enabled`, `reason`, `actor`, `changedAt`, `pendingCallsDenied` и текущее `executingCalls`. Включение отвергает все ожидающие calls; уже отправленный upstream request не отзывается. Снятие использует тот же endpoint с `enabled: false` и новой причиной.

### Список серверов

```http
GET /api/v1/mcp/servers
```

Ответ содержит `enabled`, безопасные server metadata, catalog tools, legacy и effective `risk/policy`, `riskTier`, `requiredApprovals`, policy version/hash/rule/reason, состояние синхронизации и policy snapshot. Credentials и полные call arguments/results не возвращаются.

### Создать сервер

```http
POST /api/v1/mcp/servers
Content-Type: application/json
```

```json
{
  "name": "CRM",
  "namespace": "crm",
  "endpoint": "https://mcp.example.com/mcp",
  "credentialId": "credential-uuid",
  "enabled": true,
  "trustAnnotations": false,
  "allowInsecureHttp": false,
  "defaultPolicy": "deny",
  "catalogTtlSeconds": 300
}
```

Создание сразу пробует `tools/list`. Ошибка подключения сохраняется в `lastError`, но server definition остаётся для исправления endpoint/credentials.

### Изменить, удалить и синхронизировать

```http
PATCH  /api/v1/mcp/servers/:serverId
DELETE /api/v1/mcp/servers/:serverId
POST   /api/v1/mcp/servers/:serverId/sync
```

Изменение namespace, endpoint или credentials очищает старый каталог. Сервер с `waiting_approval/executing` call удалить нельзя.

### Tool policy

```http
PATCH /api/v1/mcp/servers/:serverId/tools/:toolName
Content-Type: application/json

{ "policy": "allow" }
```

Допустимы `allow`, `approval`, `deny`. Override имеет приоритет над server default policy, но служит только legacy-границей: policy-as-code может сделать решение строже, legacy `deny` нельзя ослабить, а critical/destructive всегда требует deny либо two-person approval.

### Решение оператора

```http
POST /api/v1/mcp/tool-calls/:callId/decision
Content-Type: application/json

{ "decision": "approve" }
```

`reject` терминален и никогда не соединяется с upstream. Для одного approval `approve` атомарно переводит активный call в `executing` и синхронно запускает upstream `tools/call`. Для four-eyes первый distinct OIDC subject получает `202` и оставляет call в `waiting_approval`; второй subject завершает порог и запускает call. Повторное решение того же subject возвращает ошибку. Перед upstream coordinator повторно проверяет актуальную policy, lease и kill switch.

### Lease-scoped worker proxy

```http
POST /api/v1/leases/:leaseId/mcp/tools/call
Authorization: Bearer <node-token>
Content-Type: application/json

{
  "publicName": "crm__find_customer",
  "clientCallId": "model-tool-call-id",
  "arguments": { "id": "42" }
}
```

`200` означает terminal `completed/failed/rejected/expired`; `202` — `waiting_approval/executing`. Approval response дополнительно показывает прогресс:

```json
{
  "callId": "uuid",
  "status": "waiting_approval",
  "requiredApprovals": 2,
  "approvalCount": 1,
  "approvers": ["operator-1"]
}
```

Worker получает состояние тем же node token:

```http
GET /api/v1/leases/:leaseId/mcp/tool-calls/:callId
```

Coordinator проверяет ownership `node_id + lease_id`. Пара `leaseId + clientCallId` уникальна и служит transport-level idempotency key.

```http
POST /api/v1/leases/:leaseId/mcp/tool-calls/:callId/cancel
```

Атомарно переводит ещё ожидающий approval вызов в `expired`, когда worker прекращает его ждать. Уже выполняющийся вызов не прерывается: API возвращает `executing`, а worker продолжает ждать в отдельном bounded execution window.

Полный контракт и policy model: [MCP gateway и risk policy](./mcp-gateway.md).

## A2A adapter

Dashboard registry использует обычную авторизацию `/api/v1`, project header и RBAC:

```http
GET /api/v1/a2a
```

Возвращает `enabled`, `publicBaseUrl`, protocol/adapter versions, counts, endpoints с `agentCardUrl/interfaceUrl` и последние 100 task summaries. Полный endpoint token не возвращается.

```http
POST /api/v1/a2a/endpoints
Content-Type: application/json

{
  "agentId": "collector",
  "name": "Research agent",
  "description": "Проверяет факты в локальном контуре",
  "version": "1.0.0",
  "skillId": "research.verify",
  "skillName": "Fact verification",
  "skillDescription": "Возвращает проверенный текст",
  "tags": ["research", "local"],
  "inputModes": ["text/plain"],
  "knowledgeCollectionIds": [],
  "approvalRequired": false,
  "priority": 50,
  "maxInputCharacters": 20000,
  "maxActiveTasks": 10,
  "enabled": true
}
```

Ответ `201` содержит `{ endpoint, accessToken }`. `accessToken` показывается только в этом ответе.

```http
PATCH  /api/v1/a2a/endpoints/:endpointId
DELETE /api/v1/a2a/endpoints/:endpointId
POST   /api/v1/a2a/endpoints/:endpointId/token/rotate
```

Создание, изменение, rotation и удаление требуют `admin/designer`. Привязку существующего endpoint к другому агенту менять нельзя. Удаление запрещено при активных tasks; rotation возвращает новый one-time token и сразу отзывает старый.

Внешний A2A interface не использует dashboard/OIDC token. Для всех task operations обязательны отдельный endpoint bearer и `A2A-Version: 1.0`; для `POST` также нужен `Content-Type: application/a2a+json`:

```http
GET  /a2a/v1/endpoints/:endpointId/agent-card.json
POST /a2a/v1/endpoints/:endpointId/message:send
GET  /a2a/v1/endpoints/:endpointId/tasks/:taskId?historyLength=1
GET  /a2a/v1/endpoints/:endpointId/tasks?pageSize=50&includeArtifacts=false
POST /a2a/v1/endpoints/:endpointId/tasks/:taskId:cancel
```

`GET /tasks` принимает `contextId`, `status`, `pageSize=1..100`, opaque `pageToken`, `historyLength=0..100`, ISO `statusTimestampAfter` и `includeArtifacts=true|false`. Send возвращает `SendMessageResponse` wrapper `{ task }`; Get/Cancel — `Task` без обёртки; List — `tasks`, `nextPageToken`, `pageSize`, `totalSize`.

Protocol errors используют media type `application/a2a+json` и `google.rpc.ErrorInfo`-подобные `reason/metadata`. Missing/wrong bearer получает `401` и `WWW-Authenticate`; неподдерживаемая версия, streaming, subscription или push возвращают явную A2A error, а не fallback dashboard JSON.

Полный wire example, state mapping и boundary policy: [A2A adapter](./a2a-adapter.md).

## Internal API

`POST /api/v1/internal/processes/:processId/scheduled-start` вызывается только Activity scheduled parent workflow, проверяет отдельный `X-Agat-Temporal-Token` и создаёт project-scoped экземпляр опубликованного процесса. Публичный Gateway блокирует весь `/api/v1/internal`.

`POST /api/v1/internal/processes/:instanceId/tick` не является публичным API. Его вызывает только Temporal worker с отдельным token и project ID. Kong возвращает `404` для всего `/api/v1/internal`, даже если клиент знает путь; прямой coordinator `ClusterIP` дополнительно проверяет token constant-time.
