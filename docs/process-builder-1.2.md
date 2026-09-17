# Process Builder 1.2

Релиз 1.2 расширяет исполняемый граф АГАТ параллельными ветками, внешними событиями, subprocess, версионированием и saga-компенсациями. SQLite остаётся доменным источником истины, Temporal — durable-владельцем ожиданий и расписаний, а agent/HTTP/compensation activity выполняются через существующую lease-очередь workers.

## Гарантии runtime

- опубликованный graph snapshot неизменяем и закрепляется в каждом instance;
- один execution token проходит обычную ветку, `parallel_fork` создаёт отдельный token на каждую исходящую связь;
- парный `parallel_join` продолжает родительский token только после прибытия всех веток;
- порядок объединённого output определяется порядком исходящих fork-edges, а не скоростью workers;
- signal wait, subprocess link, join arrivals и compensation stack сохраняются в SQLite;
- Temporal Workflow не выполняет I/O и не интерпретирует пользовательский graph: он будит идемпотентный coordinator tick и остаётся replay-safe;
- embedded subprocess использует закреплённую версию дочернего процесса, а его события будят корневой Temporal owner и не создают второй источник durable state;
- failure/cancel родителя атомарно закрывает его signal waits и execution tokens, отсоединяет активные subprocess links и каскадно отменяет embedded children; запоздалый signal или child completion не может возобновить terminal instance;
- при рестарте coordinator состояние восстанавливается из сохранённых tokens, waits, links и stages.

## Новые узлы

| Узел | Конфигурация | Семантика |
|---|---|---|
| `parallel_fork` | без config | Запускает 2–16 `default`-веток с разными target |
| `parallel_join` | `forkId` | Ждёт все tokens одного конкретного fork и объединяет output |
| `signal` | `signalName`, `signalCorrelationKey`, `signalTimeoutSeconds` | Переходит в `waiting_external` до прямого API-вызова или signal webhook |
| `subprocess` | `subprocessProcessId`, `subprocessVersion`, `subprocessInputTemplate` | Запускает опубликованный дочерний процесс и ждёт terminal status |

Join output — JSON-массив в стабильном порядке:

```json
[
  { "branch": "fork-left", "output": "left result" },
  { "branch": "fork-right", "output": "right result" }
]
```

`forkId` обязателен. При публикации backend проверяет ровно один парный join, число входов join, достижимость join из каждой ветки, отсутствие обходного пути к end, уникальные targets и общий предел 200 nodes/400 edges. Runtime дополнительно проверяет происхождение token и не объединяет ветку другого fork.

Signal name соответствует `^[A-Za-z][A-Za-z0-9_.-]{0,127}$`. Correlation key строится безопасным шаблоном и ограничен 1000 символами. Timeout `0` означает бессрочное ожидание; положительное значение ограничено одним годом. Payload становится `lastOutput` и ограничен 900 000 символами.

Subprocess version закрепляется при публикации родителя. Самовызов, циклическая цепочка и глубина больше восьми отклоняются. Дочерний instance наследует project, priority, destination, artifact path и knowledge snapshot родителя.

## Шаблоны процессов

Процесс можно отметить `isTemplate: true`. Новый процесс с `templateId` получает независимую копию последней опубликованной версии шаблона; если публикаций ещё нет — текущего draft. Последующие изменения шаблона не меняют созданные процессы.

```http
POST /api/v1/processes
Content-Type: application/json

{
  "name": "Онбординг клиента",
  "description": "Копия утверждённого шаблона",
  "templateId": "template-process-id"
}
```

## Triggers

### Temporal Schedule

Один process поддерживает один управляемый Temporal Schedule трёх видов. Для всех видов действуют `overlap: SKIP`, catch-up window пять минут и pause-on-failure.

Interval:

```http
PUT /api/v1/processes/:id/schedule
Content-Type: application/json

{
  "input": "Собери отчёт",
  "kind": "interval",
  "everySeconds": 3600,
  "timezone": "UTC",
  "priority": 50,
  "paused": false,
  "knowledgeCollectionIds": []
}
```

Cron:

```json
{
  "input": "Собери утренний отчёт",
  "kind": "cron",
  "cronExpression": "0 8 * * MON-FRI",
  "timezone": "Europe/Moscow",
  "priority": 60,
  "paused": false,
  "knowledgeCollectionIds": []
}
```

Calendar:

```json
{
  "input": "Закрой операционный день",
  "kind": "calendar",
  "calendar": {
    "minute": 30,
    "hour": [9, 18],
    "dayOfWeek": ["MONDAY", "FRIDAY"]
  },
  "timezone": "Europe/Moscow",
  "priority": 50,
  "paused": false,
  "knowledgeCollectionIds": []
}
```

Timezone проверяется как IANA name. Calendar принимает bounded поля Temporal `second`, `minute`, `hour`, `dayOfMonth`, `month`, `year`, `dayOfWeek`, диапазоны и шаги. Перед чтением, изменением, ручным запуском или удалением coordinator сверяет `processId/projectId` в Schedule action и не захватывает коллизионный schedule другого владельца. Расписания доступны только при `processRuntime.mode=temporal`; database runtime отвечает `503`.

### Webhooks

Designer/admin создаёт `start` или `signal` webhook. Bearer token возвращается только при создании и rotation; в SQLite хранится SHA-256. Webhook project-scoped, но публичный invocation аутентифицируется самим token до раскрытия process metadata.

```http
POST /api/v1/process-webhooks/:webhookId
Authorization: Bearer <one-time-token>
Content-Type: application/json
Idempotency-Key: source-event-42

{ "input": "Новый заказ 42", "priority": 70 }
```

`start` обязательно требует `Idempotency-Key` до 200 символов; итоговый process input ограничен 100 000 символами независимо от того, передана строка или JSON. Повтор с той же парой webhook/key возвращает тот же instance и не создаёт второй запуск. Receipt сохраняется при rotation token. Если запуск Temporal временно не принят, повтор идемпотентно запускает тот же instance.

Signal webhook:

```json
{
  "instanceId": "optional-instance-id",
  "correlationKey": "order-42",
  "payload": { "approved": true }
}
```

Signal можно доставить и через OIDC API:

```http
POST /api/v1/processes/:processId/signals/order.confirmed
Content-Type: application/json

{
  "instanceId": "optional-instance-id",
  "correlationKey": "order-42",
  "payload": { "approved": true }
}
```

Без `instanceId` выбирается самое раннее подходящее ожидание. Response сообщает `delivered` и `instanceIds`; отсутствие ожидающего signal не считается повторным side effect. Доставка проверяет непрошедший timeout, активный instance и всё ещё ожидающий stage. При failure/cancel wait помечается `cancelled`, поэтому поздний callback возвращает `delivered: false` и не меняет terminal state.

## Идемпотентные HTTP side effects и compensation

Для каждого non-GET HTTP-шага coordinator добавляет настраиваемый header, по умолчанию `Idempotency-Key`:

```text
agat-<sha256(instanceId:nodeId:visit)>
```

Retry одного stage повторно использует тот же ключ. Следующий вход в узел через loop получает новый `visit`; live replay — новый instance и новый ключ.

HTTP node может иметь compensation request с отдельными URL, method, headers, body, timeout и credential. Compensation ставится на saga stack только после подтверждённого успешного source call; её шаблон получает фактический source output. При failure/reject/cancel зарегистрированные действия выполняются в обратном порядке. Ключ compensation стабилен и равен `<source-key>-compensate`. Ошибка одной compensation фиксируется, после чего runtime пытается выполнить оставшиеся; исходный terminal status остаётся `failed` или `cancelled`.

Это не делает произвольный upstream транзакционным: принимающая система должна дедуплицировать ключ и реализовывать корректную обратную операцию.

## Version diff и replay instance

```text
GET  /api/v1/processes/:id/versions/:version
GET  /api/v1/processes/:id/diff?from=1&to=draft
POST /api/v1/process-instances/:instanceId/replay
```

Diff сравнивает process metadata, nodes/config и edges и возвращает структурные entries `node_added`, `node_removed`, `node_changed`, `edge_added`, `edge_removed`, `metadata_changed`.

Replay разрешён только для terminal instance и всегда использует точную исходную published version, input, result destination, artifact path и knowledge collection IDs:

```json
{ "mode": "safe", "priority": 50 }
```

- `safe` повторно использует записанные результаты HTTP/subprocess по node visit и не выполняет эти side effects;
- `live` выполняет их заново с новыми idempotency keys;
- agent, transform, condition, loop и artifact проходят обычный runtime path;
- replay хранит `replayOfInstanceId` и `replayMode` в instance/trace.

Safe replay fail-closed, если для нужного visit нет завершённого записанного результата.

## BPMN 2.0 import/export

```text
POST /api/v1/processes/import/bpmn?name=...&description=...&template=true
GET  /api/v1/processes/:id/export/bpmn?version=draft
```

Import принимает `application/xml`, `text/xml` или `application/bpmn+xml`, максимум 1 MiB, ровно один BPMN `process` и создаёт новый draft. DTD и entity declarations запрещены. Поддерживается интеграционное подмножество:

| BPMN element | Узел АГАТ |
|---|---|
| `startEvent`, `endEvent` | `start`, `end` |
| `serviceTask`, `task` | безопасный draft `http` |
| `scriptTask` | `transform` |
| `userTask` | `approval` |
| `exclusiveGateway` | `condition` |
| diverging/converging `parallelGateway` | `parallel_fork` / `parallel_join` |
| timer/signal `intermediateCatchEvent` | `wait` / `signal` |
| `callActivity` | `subprocess` |

Exporter пишет стандартные namespaces, NCName-safe BPMN IDs, sequence flows, gateway direction, BPMN DI coordinates и `agat:id/type/config/branch` extensions для lossless round-trip исходных внутренних IDs. При импорте чужого BPMN gateway direction и fork/join pairing выводятся из топологии, когда соответствие однозначно. Неподдерживаемые элементы не исполняются; итоговый draft обязан пройти обычную backend-валидацию перед публикацией. АГАТ не заявляет поддержку полной BPMN execution semantics.

## Состояние и аудит

В 1.2 добавлены состояния `waiting_external` и `compensating`. Instance DTO содержит `activeNodes`, `pendingSignals`, `compensations`, replay metadata и embedded subprocess data. События `process.parallel.*`, `process.signal.*`, `process.subprocess.*`, `process.webhook.*`, `process.compensation.*` и `process.instance.replayed` входят в существующий project-scoped audit trace.

SQLite schema version — `15`. Новые durable tables: `process_tokens`, `process_join_arrivals`, `process_signal_waits`, `process_subprocess_links`, `process_compensations`, `process_webhooks`, `process_webhook_receipts`; legacy active instances получают root token при миграции.

## Операторская проверка

Перед production rollout:

1. Опубликуйте subprocess до родителя и проверьте pinned version в inspector.
2. Убедитесь, что fork имеет один парный join и все ветки доходят до него.
3. Проверьте timezone и `nextActionTimes` Schedule.
4. Сохраните webhook token в secret manager и проверьте retry с одним `Idempotency-Key`.
5. Подтвердите дедупликацию source/compensation keys на тестовом upstream.
6. Выполните safe replay terminal instance и убедитесь, что HTTP endpoint не был вызван повторно.
7. Экспортируйте опубликованную версию в BPMN, импортируйте как новый draft и опубликуйте только после review diff.

Базовая модель редактора: [Визуальные процессы и циклы](./processes.md). HTTP-контракты: [API](./api.md#процессы). Durable boundary и восстановление: [Production Temporal runtime](./production-durable-runtime.md) и [Операции](./operations.md#восстановление-durable-process).
