# Runtime агентов: Single и LangGraph

АГАТ разделяет две разные задачи оркестрации:

- **Temporal** удерживает верхнеуровневый бизнес-процесс: durable timers, Updates/Signals, Schedules, восстановление и Continue-As-New;
- **LangGraph** опционально управляет внутренним циклом одного агентного этапа: вызов модели, выбор инструмента, результат инструмента и следующий вызов модели.

LangGraph не импортируется в Temporal Workflow и не заменяет очередь АГАТ. Он запускается model worker внутри уже выданного lease.

```mermaid
flowchart LR
    UI["Process UI"] --> C["Coordinator + SQLite"]
    C <-->|"start / update"| T["Temporal Workflow"]
    T -->|"Activity: internal tick"| C
    W["Model worker"] -->|"outbound lease poll"| C
    W --> R{"Agent runtime"}
    R --> S["single\nпрямой model/tool loop"]
    R --> L["langgraph\nStateGraph tool_loop_v1"]
    S --> M["Local OpenAI-compatible model"]
    L --> M
    L --> TOOLS["Controlled tools"]
```

## Контракт агента

У агента сохраняются runtime и версионированная конфигурация:

```json
{
  "runtime": "langgraph",
  "runtimeConfig": {
    "profile": "tool_loop_v1",
    "maxIterations": 6
  }
}
```

Поддерживаются:

| Runtime | Семантика | Когда использовать |
|---|---|---|
| `single` | Существующий прямой вызов модели с ограниченным циклом web-tools | Простые роли, минимальный overhead, переносимый worker без Python-зависимостей |
| `langgraph` | Скомпилированный `StateGraph`: `model → tools → model`, conditional edges и явное завершение | Агент с несколькими итерациями рассуждения и инструментов, будущие subgraphs и specialist teams |

Профиль `tool_loop_v1` ограничивает число model-итераций диапазоном `1..12`. Фактический предел равен меньшему из настройки агента и операторского `AGAT_WEB_MAX_TOOL_ROUNDS`; поэтому агент не может расширить лимит worker.

## Маршрутизация по возможностям

Worker публикует поле `agentRuntimes` при регистрации и в каждом heartbeat. Минимальный worker без зависимости LangGraph публикует `['single']`; worker с установленным пакетом — `['single', 'langgraph']`.

Scheduler выдаёт агентный stage только при выполнении обоих условий:

1. совпадает закреплённая модель либо агент использует автовыбор;
2. worker объявил runtime агента.

HTTP stages не зависят от agent runtime. В UI готовность агента и число совместимых узлов также учитывают оба условия, поэтому карточка не обещает запуск на несовместимой машине.

## Граница долговечности

Граф LangGraph компилируется **без checkpointer** и живёт только в рамках одного lease attempt. Это осознанная граница:

- SQLite остаётся источником истины для run, stage, trace, approvals и artifacts;
- Temporal остаётся владельцем долговечного lifecycle процесса;
- потеря worker возвращает весь stage в очередь после lease TTL;
- внутреннее состояние незавершённого graph attempt не переносится на другой worker.

LangGraph поддерживает checkpoints, threads и fault-tolerant resume, когда граф компилируется с checkpointer. В АГАТ это пока не включено: отдельный checkpointer создал бы третий durable state, который пришлось бы согласованно восстанавливать вместе с SQLite и Temporal. Официальное описание механизма: [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence).

Следствие at-least-once: read-only `web_search` и `web_fetch` можно безопасно повторить, но будущие tools с side effects обязаны принимать idempotency key как минимум на основе `stage.id` и подтверждаться policy/approval до исполнения.

## Установка

Docker image worker уже устанавливает закреплённый `langgraph==1.2.9` из `workers/requirements.txt`.

Для отдельного Python worker:

```bash
python3 -m pip install --requirement workers/requirements.txt
python3 workers/agat_worker.py
```

Без установки worker продолжает выполнять `single`-агентов и не рекламирует `langgraph` coordinator. Если несовместимый lease всё же получен от старого coordinator, worker возвращает явную ошибку вместо скрытого fallback на другой runtime.

Базовый пакет LangGraph требует Python 3.10+ и устанавливается отдельно от provider adapters; АГАТ продолжает вызывать локальный OpenAI-compatible endpoint собственным клиентом. См. [официальную установку LangGraph](https://docs.langchain.com/oss/python/langgraph/install) и [Graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api).

## Наблюдаемость и приватность

В `trace.input`, `stage.started` и model-call events записываются название runtime и безопасная runtime-конфигурация. Полные tool arguments по-прежнему редактируются существующей trace policy.

АГАТ не настраивает LangSmith и не отправляет graph state во внешний сервис. Если оператор самостоятельно включает сторонний tracing через environment, он отвечает за egress, redaction и соответствие политике данных.

## Текущие ограничения

- реализован один профиль `tool_loop_v1`, а не произвольная загрузка Python-графов пользователем;
- LangGraph graph не пересекает границу одного stage и не управляет approval/wait процесса;
- durable memory и LangGraph checkpoints не включены;
- specialist subgraphs и полноценный `agent_team` остаются следующей эволюцией runtime после OTel/eval и policy-controlled tools.

Такое ограничение оставляет архитектурный шов для multi-agent graph, но не дублирует уже работающие гарантии Temporal и coordinator.
