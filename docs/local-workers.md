# Локальный запуск нескольких workers

Панель АГАТ умеет запускать workers непосредственно в локальном Kubernetes Docker Desktop. Пользователь выбирает модель, число экземпляров и лимит параллельности одного worker; coordinator создаёт отдельный Kubernetes Deployment для каждого экземпляра.

## Запуск из интерфейса

1. Откройте раздел **Узлы**.
2. В блоке **Запустить локальные workers** выберите установленную модель.
3. Укажите количество workers от 1 до 8.
4. Оставьте **Слотов на worker = 1**, если несколько запросов к одной модели могут перегрузить память или GPU.
5. При необходимости отключите web-инструменты.
6. Нажмите **Запустить workers**.

Панель показывает два независимых признака готовности:

- `Kubernetes ready` — pod прошёл startup/readiness probe;
- `Heartbeat online` — worker зарегистрирован в АГАТ и отправляет heartbeat.

Карточка узла отдельно показывает `Agent runtimes`. Managed Docker workers публикуют `single` и `langgraph`; переносимый Python worker без установленных requirements публикует только `single`. Scheduler не выдаёт LangGraph-агента несовместимому узлу.

Остановка пула масштабирует все его Deployments до нуля, но сохраняет конфигурацию. Кнопка **Запустить** возобновляет тот же пул. Кнопка удаления использует отдельное подтверждение и удаляет только Deployments выбранного управляемого пула. После остановки активный lease может оставаться `running` до завершения worker или истечения `AGAT_LEASE_TTL_SECONDS`, после чего coordinator безопасно вернёт этап в очередь.

## Модели

Coordinator получает список установленных Ollama-моделей через:

```dotenv
AGAT_LOCAL_MODEL_DISCOVERY_URL=http://host.docker.internal:11434/api/tags
```

Worker обращается к OpenAI-compatible endpoint:

```dotenv
AGAT_LOCAL_MODEL_BASE_URL=http://host.docker.internal:11434/v1
```

Если discovery временно недоступен, точное имя модели можно ввести вручную. Worker зарегистрируется с этим именем, но inference завершится ошибкой, если model server фактически не предоставляет модель.

Embedding-модели задаются оператором отдельно и одинаковы для всех pods управляемого пула:

```dotenv
AGAT_LOCAL_WORKER_EMBEDDING_MODELS=embeddinggemma
```

Launcher передаёт значение как `AGAT_EMBEDDING_MODELS`; оно не выбирается из completion dropdown. Сначала установите модель (`ollama pull embeddinggemma`). Пустое значение оставляет worker пригодным для agent stages, но не для Local RAG indexing/retrieval. Подробнее: [Local RAG и управляемая память](./local-rag-and-memory.md).

## LangGraph runtime

Image `agat-local/worker` устанавливает `workers/requirements.txt`, поэтому дополнительные Kubernetes-пулы сразу поддерживают оба runtime. Для worker, запущенного прямо из исходников:

```bash
python3 -m pip install --requirement workers/requirements.txt
python3 workers/agat_worker.py
```

Установка не нужна для `single`. Подробности о границе с Temporal, bounded profile и восстановлении: [runtime агентов](./agent-runtimes.md).

## Несколько workers и слабое железо

Количество workers не означает обязательное параллельное выполнение:

- режим `sequential` выдаёт только один lease во всём контуре;
- режим `parallel` позволяет независимым workers брать задания одновременно;
- режим `auto` учитывает лимиты и телеметрию узлов.

Поэтому несколько workers можно держать готовыми для разных моделей, сохраняя последовательную обработку на слабом ноутбуке. Несколько workers с одной Ollama-моделью используют один model server на host; фактическое потребление RAM/VRAM и параллелизм определяет Ollama.

## Устройство пула

Один запрос создаёт от 1 до 8 Deployments с одной репликой каждый. Это намеренно сделано вместо одного Deployment с несколькими репликами:

- каждый worker получает стабильное уникальное имя;
- повторный старт обновляет ту же запись узла, а не создаёт дубликат;
- у каждого pod отдельный `emptyDir` для node token;
- можно точно сопоставить Kubernetes readiness и heartbeat worker.

Созданные pods работают от UID/GID `10001`, с read-only root filesystem, без service-account token и без Linux capabilities. Model API key и enrollment token подключаются из существующего `agat-secrets`.

Coordinator использует отдельный service account `agat-coordinator`. Namespace Role разрешает только операции `get/list/create/patch/delete` над Deployments. Публичный API не принимает Kubernetes manifest, image, command, URL model server или имя Secret от пользователя — они задаются доверенной конфигурацией оператора.

## API

Получить состояние launcher, модели и пулы:

```http
GET /api/v1/local-workers
```

Создать пул; требуется `X-Agat-Admin-Token`:

```http
POST /api/v1/local-workers
Content-Type: application/json

{
  "name": "Llama для анализа",
  "model": "llama3.2:latest",
  "workers": 3,
  "concurrency": 1,
  "webEnabled": true
}
```

Остановить и снова запустить:

```http
POST /api/v1/local-workers/:poolId/stop
DELETE /api/v1/local-workers/:poolId
POST /api/v1/local-workers/:poolId/start
```

Изменения записываются в общий event log как `local-worker.pool.created`, `local-worker.pool.stopped`, `local-worker.pool.started` и `local-worker.pool.deleted`.

## Конфигурация coordinator

```dotenv
AGAT_LOCAL_WORKER_LAUNCHER=true
AGAT_LOCAL_WORKER_NAMESPACE=agat
AGAT_LOCAL_WORKER_IMAGE=agat-local/worker:1.2.0
AGAT_LOCAL_WORKER_CONFIG_MAP=agat-worker-config
AGAT_LOCAL_WORKER_SECRET=agat-secrets
AGAT_LOCAL_MODEL_BASE_URL=http://host.docker.internal:11434/v1
AGAT_LOCAL_MODEL_DISCOVERY_URL=http://host.docker.internal:11434/api/tags
AGAT_LOCAL_WORKER_EMBEDDING_MODELS=embeddinggemma
AGAT_LOCAL_WORKER_WEB_ENABLED=true
AGAT_LOCAL_WORKER_MAX_PER_LAUNCH=8
```

В Docker Compose launcher выключен: container не получает Docker socket и не может создавать host-процессы. Это осознанная граница безопасности. Запуск одной командой из UI поддерживается в локальном Docker Desktop Kubernetes-контуре.

## Диагностика

```bash
kubectl get deployments,pods -n agat -l agat.local/managed=true
kubectl auth can-i create deployments.apps \
  --namespace agat \
  --as system:serviceaccount:agat:agat-coordinator
kubectl logs -n agat deployment/<имя-worker-deployment> --tail=100
```

Если launcher недоступен, проверьте:

1. применён ли `local-worker-launcher-rbac.yaml`;
2. использует ли coordinator service account `agat-coordinator`;
3. заданы ли `AGAT_LOCAL_WORKER_LAUNCHER=true` и корректный worker image;
4. доступен ли Ollama на `http://127.0.0.1:11434` с host-машины.
