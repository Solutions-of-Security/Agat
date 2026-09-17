# LangGraph specialist teams 1.3

Релиз 1.3 добавляет управляемые команды специалистов внутри одного agent stage. Coordinator и Temporal по-прежнему владеют очередью, retries, approvals, timers и бизнес-переходами; supervisor и specialist subgraphs существуют только во время одного worker lease attempt и компилируются без LangGraph checkpointer.

## Профиль runtime

Team — обычный проектный агент с `runtime: "langgraph"` и версионированным профилем:

```json
{
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

- `specialistAgentIds` содержит 2–8 разных агентов, видимых в том же project;
- team не может включать себя, другую team или агента, который позднее превращается во вложенную team;
- `maxIterations` ограничивает каждый specialist tool-loop диапазоном `1..12` и дополнительно уменьшается настройкой конкретного specialist и `AGAT_WEB_MAX_TOOL_ROUNDS` worker;
- `maxHandoffs` ограничен диапазоном `1..8` на один lease attempt;
- принимается только известная schema `specialist_team_state_v1`; произвольный JSON Schema или Python graph загрузить нельзя.

Промпты участников не копируются в изменяемую team-конфигурацию. Team ссылается на существующих агентов, поэтому их prompt/model по-прежнему продвигаются через Prompt registry и Golden eval.

## Immutable specialist snapshots

При создании run или постановке process agent node coordinator разрешает member IDs в эффективные project-scoped конфигурации и сохраняет ordered snapshots:

```json
{
  "schemaVersion": 1,
  "id": "researcher-agent-id",
  "name": "Исследователь",
  "role": "Проверяет факты",
  "model": "qwen3:8b",
  "runtimeConfig": { "profile": "tool_loop_v1", "maxIterations": 3 },
  "promptVersion": "sha256",
  "definitionVersion": "sha256"
}
```

Snapshot также содержит фактический system prompt, но он доступен только в authenticated project trace и lease конкретного worker. `definitionVersion` учитывает имя, роль, prompt, model и tool-loop bound. Team agent snapshot schema — `3`; её собственный `definitionVersion` включает ordered specialist snapshots. Изменение участника после создания run не меняет уже сохранённую команду, а manifest replay повторно использует те же snapshots.

## Supervisor и handoff

Supervisor получает каталог `id/name/role/definitionVersion`, исходную задачу и уже полученные specialist results. Каждый ответ supervisor обязан быть одним JSON-решением:

```json
{ "action": "delegate", "specialistId": "exact-id", "task": "bounded assignment" }
```

или:

```json
{ "action": "finish", "answer": "final answer" }
```

Worker отклоняет неизвестный ID, пустую/слишком большую задачу, неизвестное action и неструктурированный ответ. Raw reasoning не сохраняется. После исчерпания `maxHandoffs` отдельный finalizer формирует ответ только по уже полученным результатам без новых tools или делегаций.

Каждый выбранный specialist запускается как вложенный `tool_loop_v1` subgraph с собственными pinned prompt/model/bound. Он видит исходный разрешённый контекст, точное назначение и предыдущие outputs как недоверенные данные. Specialist не может динамически добавить нового участника или перенести graph в другой lease.

## Проверяемое состояние

`specialist_team_state_v1` валидируется worker перед каждым node transition и после завершения graph. Контракт содержит:

| Поле | Ограничение |
|---|---|
| `schema_version` | ровно `1` |
| `task` | непустая строка до 100 000 символов |
| `specialist_outputs` | только известные IDs, до 60 000 символов на specialist и 480 000 суммарно |
| `visited_specialists` | только известные IDs, длина не больше `maxHandoffs` |
| `handoff_count` | равен длине history, `0..maxHandoffs` |
| `next_specialist_id` | пустой или точный ID каталога |
| `assignment` | до 20 000 символов |
| `final_output` | до 200 000 символов |

Состояние не записывается в отдельный checkpointer. При потере worker coordinator возвращает весь stage в очередь по обычному lease TTL; новый attempt начинает bounded team заново из immutable stage snapshot.

## Tools, MCP и идемпотентность

Specialists получают только tools текущего lease: контролируемые web tools и project-scoped MCP catalog после существующих policy/RBAC checks. MCP approvals, four-eyes, scoped credentials и emergency deny продолжают исполняться gateway, а не LangGraph.

Каждый handoff добавляет к model-generated tool call ID scope на основе номера handoff и SHA-256 member ID. Поэтому одинаковые call IDs от разных specialist subgraphs не сталкиваются в уникальном `(lease_id, client_call_id)` и не превращаются в ложную дедупликацию. Side effects всё равно остаются at-least-once между lease attempts и обязаны соблюдать MCP policy/idempotency контракт.

## Совместимость worker fleet

Кроме `agentRuntimes`, worker публикует `agentRuntimeProfiles`:

```json
{
  "agentRuntimes": ["single", "langgraph"],
  "agentRuntimeProfiles": ["tool_loop_v1", "specialist_team_v1"]
}
```

Worker без LangGraph публикует пустой список profiles. Legacy worker, который не знает нового поля, мигрируется к безопасному `tool_loop_v1` и не получает team stage. Scheduler также требует, чтобы один worker объявил runtime/profile и все pinned supervisor/specialist models; команда не распределяется между несколькими machines внутри одного lease.

SQLite schema version — `16`; в `nodes` добавлено `agent_runtime_profiles_json`. Изменений в Temporal Workflow history нет, поэтому существующие replay fixtures остаются совместимыми.

## Audit и эксплуатация

- `stage.started` хранит runtime profile и specialist definition versions;
- `trace.input` хранит member IDs, names, models и hashes, но не дублирует member prompts;
- worker создаёт `agent_handoff` events с phase, member ID/name, definition hash и размерами assignment/output, без их текста;
- OTel stage/lease spans получают `agat.agent.runtime_profile` и `agat.agent.specialist_count`;
- execution manifest v3 содержит полные immutable specialist snapshots и общий SHA-256.

Перед production rollout:

1. Обновите coordinator и workers до `1.3.0`, затем проверьте `specialist_team_v1` в карточке node.
2. Создайте минимум двух обычных агентов и проведите их prompts/models через нужный quality gate.
3. Создайте team, ограничьте subgraph iterations и handoffs минимально достаточными значениями.
4. Запустите team и проверьте paired `agent_handoff started/completed`, definition hashes и manifest v3.
5. Остановите worker во время handoff и подтвердите, что coordinator повторяет весь stage, не пытаясь восстановить частичный LangGraph state.
6. Для MCP side effects проверьте approvals, idempotency и emergency deny на каждом specialist tool call.

Базовые контракты: [Runtime агентов](./agent-runtimes.md), [API](./api.md#агенты), [Security](./security.md) и [Durable runtime](./durable-runtime.md).
