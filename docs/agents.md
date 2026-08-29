# Агенты в АГАТ

## Создание из интерфейса

1. Откройте раздел `Агенты`.
2. Нажмите `Новый агент`.
3. Заполните имя, роль и системный промпт.
4. Оставьте модель пустой для автовыбора либо укажите точное имя из worker, например `qwen3:8b`.
5. Выберите runtime: `Single` для простого агента или `LangGraph` для bounded model/tool-графа.
6. Для LangGraph задайте максимум 1–12 model-итераций.
7. Сохраните агента — initial prompt/model станут `v1` project-specific prompt registry, а агент сразу появится в каталоге и конструкторе запуска.

Карточка показывает не расчётные заглушки, а реальные данные coordinator:

- количество запусков, где агент участвует;
- активные цепочки;
- дату последнего запуска;
- число online-узлов, совместимых одновременно по модели и runtime;
- состояние `Готов` или `Нет узла`.

`Нет узла` не запрещает поставить задачу в очередь. Она начнётся, когда подключится совместимый worker.

## Модель

- пустое значение — Model Router выбирает модель и узел по hardware/profile policy; при выключенном router сохраняется legacy fallback на первую модель polling worker;
- конкретное значение — scheduler отдаёт этап только worker с совпадающим элементом `AGAT_WORKER_MODELS`;
- произвольное имя можно сохранить заранее: раздел `Модели` покажет его как недоступное, пока worker не подключится.

Для автовыбора доступны стратегии `balanced`, `performance` и `efficiency`, context/quality thresholds и ограничения температуры/батареи. Выбранная модель не изменяет immutable snapshot агента: requested и selected model отдельно видны в trace. Полный контракт: [Model Router и hardware benchmarks](./model-router.md).

## Runtime

- `single` — текущий прямой model/tool loop с минимальным overhead;
- `langgraph` — профиль `tool_loop_v1`, реализованный через LangGraph `StateGraph` с conditional edges и жёстким пределом итераций;
- worker без установленного LangGraph не получает такие этапы и продолжает обслуживать `single`;
- Docker/Kubernetes worker уже содержит нужную зависимость; для отдельной машины выполните `python3 -m pip install -r workers/requirements.txt`.

Runtime относится только к выбранному агенту. Wait, approval, process loops и восстановление бизнес-процесса по-прежнему принадлежат coordinator/Temporal. Полный контракт и ограничения: [Runtime агентов: Single и LangGraph](./agent-runtimes.md).

## Цепочки и подтверждение

Кнопка `Запустить` на карточке открывает новый запуск с уже выбранным агентом. В общем конструкторе можно выбрать несколько агентов; номера на выбранных карточках задают порядок передачи контекста.

Approval gate применяется перед последним агентом. Для одноагентной цепочки это означает подтверждение до первого выполнения, а не после него.

## Редактирование

`Настроить` изменяет имя, роль, runtime и предел итераций. Активные prompt/model показаны read-only: новая immutable prompt version, model candidate и их promotion выполняются в **Golden eval → Prompt registry** только после matching PASS experiment. Подробности: [Golden eval и prompt registry](./golden-eval-prompt-registry.md).

Каждый stage получает immutable agent snapshot при создании/постановке в очередь. Поэтому редактирование metadata или последующий promotion не изменяют уже созданные runs и их replay manifest. Удаление агента намеренно не добавлено, чтобы не нарушать историю запусков; безопасная архивация будет отдельным состоянием.
