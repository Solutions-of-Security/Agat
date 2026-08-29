# Журнал выполнения и артефакты

АГАТ сохраняет наблюдаемую историю каждого запуска и позволяет заранее выбрать, останется ли результат только в журнале или дополнительно будет записан файлами в Artifact Store координатора.

## Что видно пользователю

В карточке запуска доступны четыре вкладки:

1. **Ход и логи** — полный упорядоченный журнал запуска, а не только последние события общего overview.
2. **Вход и результат** — исходный input, output каждого этапа и скачивание последнего результата как `result.md`.
3. **Артефакты** — файлы, путь внутри хранилища, размер, SHA-256 и кнопка скачивания.
4. **Replay / Eval** — execution manifest, его hashes, безопасный replay и сравнение latency/tokens.

Для каждого этапа trace фиксирует:

- точный снимок run input;
- agent ID, имя, роль, system prompt, запрошенную модель, runtime и bounded runtime config;
- outputs предыдущих этапов, реально переданные как контекст;
- worker и номер попытки;
- explainable routing: requested/selected model, выбранный node, policy, benchmark/health signals, alternatives и fallback;
- начало и окончание model call, выбранную модель, runtime и размеры input/output;
- tool call, фазу, безопасные аргументы и результат выполнения;
- snapshot подключённых knowledge collections и событие `knowledge.retrieved` с markers, score, source URI, document/chunk IDs, позициями и SHA-256;
- переходы процесса, условия, итерации bounded loop, approval, retry и ошибки;
- финальный output и созданные артефакты.

На выдаче lease coordinator также фиксирует W3C trace context, версию worker, hardware/model capabilities, routing decision, tool schema и измеримые token/time/energy counters. Опциональный OpenTelemetry exporter продолжает этот trace через coordinator, worker, model и tool spans, но не экспортирует тексты prompt/output. Подробности: [OpenTelemetry, execution manifest, replay и eval](./observability-replay-evals.md).

Vector поискового запроса в trace не сохраняется: вместо него записываются имя embedding-модели, collection IDs, `topK`, размерность и SHA-256. UI выделяет эти события отдельным фильтром **Knowledge**. Полный lifecycle и границы provenance: [Local RAG и управляемая память](./local-rag-and-memory.md).

## Почему нет скрытых «раздумываний»

АГАТ намеренно не сохраняет скрытую chain-of-thought модели. Она не является надёжным журналом действий, может содержать чувствительные фрагменты и не должна использоваться как объяснение решения.

Вместо неё показывается проверяемый наблюдаемый ход: какой input был передан, какую модель вызвали, какие tools она запросила, что сделал worker, какие ветки процесса выбрал coordinator и какой output был принят. Поле `thinking` или аналогичное поле model provider игнорируется.

У web-tools поисковая строка, полный URL path/query и содержимое страницы не попадают в trace. Пользователь видит название tool, фазу, длину запроса, публичный host, тип контента, объём, статус и ошибку. Это позволяет диагностировать выполнение без копирования секретов в журнал.

## Назначения результата

При создании обычного запуска и при запуске опубликованного процесса доступны два режима:

| Режим | Что сохраняется |
|---|---|
| `history` | Input, events и stage outputs в SQLite. Последний output можно скачать из UI как `result.md`. |
| `artifacts` | Всё из `history`, плюс output каждого этапа и финальный результат в файловом Artifact Store. |

Файловый режим выбран в UI по умолчанию. Пользователь может указать относительный каталог, например `reports/releases`. Coordinator добавит UUID запуска и сформирует структуру:

```text
reports/releases/<run-id>/
├── result.md
├── stages/
│   ├── 01-сборщик.md
│   └── 02-редактор.md
└── artifacts/
    └── <unique-prefix>-facts.json
```

Если каталог не задан, используется `runs/<run-id>`. Абсолютные пути, `..`, пустые сегменты и выход за корень хранилища отклоняются. Coordinator проверяет каждый сегмент и не следует по символическим ссылкам.

`result.md` и stage-файлы создаются автоматически. Worker API также принимает до восьми текстовых agent artifacts на этап общим размером до 800 000 байт:

```json
{
  "output": "Итог этапа",
  "artifacts": [
    {
      "name": "facts.json",
      "mediaType": "application/json",
      "content": "{\"status\":\"ok\"}"
    }
  ]
}
```

Текущий универсальный LLM-worker автоматически передаёт обычный текстовый output. Дополнительные `artifacts` предназначены для специализированных workers/tools; их не следует имитировать разбором произвольного model output.

## Хранилище

Корень задаётся переменной:

```dotenv
AGAT_ARTIFACTS_DIR=./data/artifacts
```

В Docker Compose и Docker Desktop Kubernetes используется `/data/artifacts` на том же persistent volume, что и SQLite. В базе `artifacts` хранится только metadata:

- `run_id`, `stage_id`;
- имя и тип артефакта;
- media type и относительный путь;
- размер и SHA-256;
- время создания.

Скачивание выполняется через authenticated API по artifact ID. Клиент никогда не передаёт файловый путь в download endpoint.

## API

Полный trace:

```http
GET /api/v1/runs/:runId/trace
X-Agat-Admin-Token: ...
```

Ответ содержит `run`, все `events`, `artifacts`, неизменяемый `manifest`, опциональный `comparison`, признак `truncated` и `tracePolicy`. Лимит одного ответа — 10 000 событий. UI обновляет trace после SSE-инвалидации и периодического overview refresh.

Скачивание:

```http
GET /api/v1/artifacts/:artifactId/download
X-Agat-Admin-Token: ...
```

Trace и скачивание требуют OIDC Bearer token; в legacy local mode без OIDC используется admin token. Экспорт `trace.json` формируется браузером из уже полученного ответа.

## Backup и удаление

SQLite и каталог `AGAT_ARTIFACTS_DIR` образуют один логический backup. Восстановление только базы вернёт metadata без файлов; восстановление только файлов потеряет привязку к запускам.

Удаление run удаляет metadata каскадно, но автоматическая сборка оставшихся файлов пока не реализована. Retention policy, object storage и garbage collector остаются следующими этапами. До их появления удаляйте orphaned files только после сверки с таблицей `artifacts` и проверенного backup.
