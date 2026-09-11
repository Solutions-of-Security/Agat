# Визуальные процессы и циклы

АГАТ 1.2 позволяет собрать исполняемый graph на canvas, опубликовать неизменяемую версию и запустить её на распределённом пуле workers. Редактор использует знакомые BPMN-паттерны, а BPMN 2.0 XML служит интеграционным форматом; runtime остаётся компактной, проверяемой моделью АГАТ.

## Что доступно в интерфейсе

Раздел `Процессы` содержит:

- библиотеку обычных процессов и переиспользуемых шаблонов;
- React Flow canvas с drag-and-drop, minimap, поиском, picker и вставкой шага в связь;
- undo/redo, autosave draft, test node и запуск с выбранного шага;
- inspector узла, публикацию immutable version и запуск последней версии;
- release-панель для interval/cron/calendar Schedules, start/signal webhooks, version diff и BPMN import/export;
- список instances с активными узлами, ожидаемыми signals, compensation status и ссылкой в Temporal UI;
- safe/live replay terminal instance, переход в связанный run и отмену активного instance;
- выбор сохранения результата в history либо вместе с Artifact Store.

На смартфоне canvas, palette, inspector и release-панель перестраиваются в вертикальный операторский layout.

## Исполняемая модель

| Шаг | Назначение | Исходящие ветки |
|---|---|---|
| `start` | Единственная точка входа | `default` |
| `agent` | Создаёт stage выбранного агента | `default` |
| `http` | Ставит управляемый публичный HTTP(S)-запрос в worker queue | `default` |
| `transform` | Формирует output ограниченным шаблоном без `eval` | `default` |
| `wait` | Durable timer | `default` |
| `approval` | Ждёт решения оператора | `default` |
| `artifact` | Сохраняет файл в Artifact Store | `default` |
| `condition` | Проверяет последний output | `true`, `false` |
| `loop` | Выполняет bounded feedback loop | `repeat`, `exit` |
| `parallel_fork` | Создаёт execution token для каждой ветки | 2–16 `default` |
| `parallel_join` | Ждёт все ветки парного fork | `default` |
| `signal` | Ждёт именованное внешнее событие | `default` |
| `subprocess` | Запускает закреплённую версию дочернего процесса | `default` |
| `end` | Завершает token и, после остальных веток, instance | нет |

Условия поддерживают `contains`, `not_contains`, `equals`, `not_equals` и `always`; источником является `lastOutput`. Проверка может быть регистрозависимой.

`transform`, HTTP, compensation, signal correlation, subprocess input и artifact поддерживают только `{{ input }}`, `{{ lastOutput }}`, `{{ json }}`, безопасные пути `{{ json.field.subfield }}` и `{{ loop.nodeId }}`. JavaScript, функции, прототипы и произвольный доступ к объектам не исполняются.

HTTP credentials зашифрованы AES-256-GCM и не возвращаются через API. Worker разрешает только публичные HTTP(S) адреса и порты 80/443, повторно проверяет DNS и redirects, запрещает hop-by-hop headers и ограничивает body, response и timeout.

Каждый non-GET HTTP visit получает стабильный `Idempotency-Key`; retry сохраняет его. Настроенная compensation регистрируется только после успешного source call и при failure/cancel выполняется в обратном порядке. Подробная семантика, triggers и примеры приведены в [Process Builder 1.2](./process-builder-1.2.md).

## Parallel fork/join

```mermaid
flowchart LR
    S[Start] --> F{Parallel fork}
    F --> A[Branch A]
    F --> B[Branch B]
    A --> J{Parallel join}
    B --> J
    J --> E[End]
```

Fork создаёт дочерний token для каждой исходящей edge. Join принимает только tokens своего `forkId`, сохраняет каждое прибытие ровно один раз и продолжает родительский token после всех веток. Объединённый JSON-output упорядочен по fork edges, поэтому результат воспроизводим независимо от скорости workers.

## Циклы

1. Coordinator вычисляет условие.
2. Если оно истинно и выполнено меньше `maxIterations`, выбирается `repeat`.
3. Иначе выбирается `exit`.
4. Каждый повторный вход в agent/HTTP создаёт отдельный stage и новый visit.

`maxIterations = 3` означает не более трёх обратных переходов; исходное прохождение не входит в счётчик.

## Проверка graph

Публикация отклоняется, если нарушено хотя бы одно правило:

- не ровно один `start`, отсутствует `end` или связь входит в start;
- node/edge IDs повторяются, связь ссылается на неизвестный node или замыкает его на себя;
- обязательная branch отсутствует либо имеет несколько назначений;
- node недостижим из start или из него нет пути к end;
- loop не имеет `repeat`, `exit` или лимита `1..50`;
- fork имеет не 2–16 уникальных targets, не имеет ровно одного paired join либо ветка не достигает join;
- join ссылается не на fork или число входящих веток не совпадает;
- signal/subprocess/HTTP/compensation config не проходит bounded validation;
- subprocess не опубликован, создаёт цикл либо превышает глубину 8;
- превышены 200 nodes, 400 edges или runtime budget 1000 transitions.

За один вызов coordinator выполняет максимум 128 мгновенных переходов. Все проверки действуют на backend и не отключаются прямым API-вызовом.

## Draft, версии, diff и replay

```mermaid
flowchart LR
    D[Редактируемый draft] -->|publish| V1[Version 1 · immutable]
    V1 --> I1[Instance A · snapshot v1]
    D -->|change + publish| V2[Version 2 · immutable]
    V2 --> I2[Instance B · snapshot v2]
    I1 -->|safe/live replay| R[Instance replay · snapshot v1]
```

- `processes` хранит identity, template flag и draft graph;
- `process_versions` хранит immutable snapshots;
- `process_instances` закрепляет version, graph, replay metadata и terminal state;
- `process_tokens`, join arrivals, signal waits, subprocess links и compensation stack образуют durable runtime state;
- `runs/stages/events` остаются единым execution и audit trace.

Version diff сравнивает metadata, nodes/config и edges. Safe replay повторно использует записанные результаты HTTP/subprocess; live replay выполняет side effects заново с новыми keys. Редактирование draft никогда не меняет уже запущенный или replayed instance.

## Triggers и внешние ожидания

Temporal Schedule поддерживает `interval`, `cron` и structured `calendar` с IANA timezone. Каждое срабатывание создаёт новый immutable instance через scheduled parent→child Workflow; overlap пропускается, а repeated failure ставит Schedule на паузу.

Start webhook требует Bearer token и `Idempotency-Key`. Signal webhook либо OIDC API доставляет payload ожидающему `signal` по process, name и опциональным instance/correlation key. Token webhook показывается только при создании/rotation и хранится как hash. Отмена/failure закрывает signal waits и каскадно отменяет embedded subprocess, поэтому позднее внешнее событие или завершение child не возобновляет terminal instance.

Instance использует состояния `queued`, `running`, `waiting_approval`, `waiting_external`, `compensating`, `completed`, `failed`, `cancelled`.

## BPMN 2.0 boundary

Экспорт содержит стандартные BPMN namespaces/DI, sequence flows, gateways и AGAT extensions для точного round-trip. Импорт поддерживает start/end, tasks, exclusive/parallel gateways, timer/signal catch events и call activity, создавая новый draft. XML ограничен 1 MiB; DTD/entities запрещены.

Полная BPMN execution semantics не заявляется: неподдерживаемые элементы не становятся скрытым исполняемым поведением, а импортированный draft обязан пройти обычную публикационную проверку.

## Граница технологий

| Компонент | Роль |
|---|---|
| React Flow | Визуальный editor; не исполняет graph |
| SQLite | Project-scoped domain state, tokens, audit и immutable snapshots |
| Temporal | Durable Workflow, timers, Updates/signals и Schedules; без пользовательского I/O в Workflow code |
| Lease workers | Agent, HTTP и compensation activities |
| LangGraph | Только внутренний bounded runtime одного agent stage |

Архитектурная граница подробнее описана в [durable runtime](./durable-runtime.md), production transport — в [production hardening](./production-durable-runtime.md), wire contracts — в [HTTP API](./api.md#процессы).

## Что намеренно не реализовано

- полная BPMN engine compatibility, event subprocess, boundary events и arbitrary scripts;
- перенос уже активного instance на другую process version;
- distributed HA coordinator поверх SQLite;
- автоматическое доказательство бизнес-корректности compensation;
- произвольные connector binaries или shell/code nodes.

## Каталог типовых процессов

В форме **Новый процесс** можно выбрать одну из 7 категорий и 13 встроенных схем подготовки и согласования материала. Каталог содержит задания этапов, роли, входы, результаты и критерии приёмки. Назначения агентов необязательны при создании черновика, но обязательны для публикации. API: `GET /api/v1/process-templates`, создание через `POST /api/v1/processes` с `catalogTemplateId`, `catalogTemplateVersion` и `templateBindings`.

Полные описания и границы: [процессы с LLM-агентами](./llm-processes/README.md). Поставляемые шаблоны не являются готовыми интеграциями с внешними системами и не закрывают qualification соответствующих solution packs.
