# Запуск и подключение устройств

## Требования

- Node.js 22.13+;
- Python 3.10+ на машинах-воркерах;
- `workers/requirements.txt` для LangGraph и OpenTelemetry; `single` без OTel работает без установки Python-пакетов;
- локальный OpenAI-совместимый model server;
- Docker 24+ только для контейнерного сценария.

Для Kubernetes-сценария нужен включённый встроенный кластер Docker Desktop. Полный запуск одной командой описан в [отдельном руководстве](./kubernetes-docker-desktop.md).

## Локальная разработка

Терминал 1:

```bash
npm install
npm run dev:api
```

Терминал 2:

```bash
npm run dev:web
```

Vite откроет панель на `http://127.0.0.1:5173` и проксирует `/api` на coordinator.

Чистый live-режим используется по умолчанию. Демонстрационные узлы и запуски включаются только явно:

```bash
AGAT_SEED_DEMO=true npm run dev:api
```

При открытии существующей базы с `AGAT_SEED_DEMO=false` coordinator удалит только известные fixtures старой версии (`demo-*` и три связанных запуска), не затрагивая пользовательские данные.

## Production-сборка без Docker

```bash
npm install
npm run build
AGAT_DB_PATH=./data/agat.db \
AGAT_ARTIFACTS_DIR=./data/artifacts \
AGAT_ENROLLMENT_TOKEN="$(openssl rand -hex 32)" \
AGAT_ADMIN_TOKEN="$(openssl rand -hex 32)" \
AGAT_A2A_PUBLIC_BASE_URL=http://127.0.0.1:8787 \
npm start
```

Если coordinator слушает адрес, отличный от loopback, оба токена обязательны. Публикуйте его только за HTTPS reverse proxy или внутри защищённой overlay-сети.

Этот вариант использует SQLite и поддерживает ровно один coordinator. Для production Temporal Cloud/self-hosted, TLS, worker versioning и canary rollout используйте [production durable runtime](./production-durable-runtime.md). PostgreSQL/несколько coordinator в 1.0 ещё не включены.

## Docker Compose

Создайте `.env` рядом с `docker-compose.yml`:

```dotenv
AGAT_ADMIN_TOKEN=<64 hex символа>
AGAT_ENROLLMENT_TOKEN=<другие 64 hex символа>
AGAT_SEARCH_SECRET=<ещё 64 hex символа>
AGAT_SEED_DEMO=false
AGAT_A2A_ENABLED=true
AGAT_A2A_PUBLIC_BASE_URL=http://127.0.0.1:8787
```

Сгенерировать значения:

```bash
openssl rand -hex 32
openssl rand -hex 32
openssl rand -hex 32
```

Запуск coordinator:

```bash
docker compose up -d --build coordinator
```

По умолчанию порт привязан только к `127.0.0.1:8787`. Для доступа из сети поставьте перед ним HTTPS reverse proxy или измените bind осознанно.

Именованный volume `agat-data` одновременно хранит `/data/agat.db` и `/data/artifacts`. При создании запуска выберите «Журнал + Artifact Store» и, при необходимости, относительный каталог. Результаты с любого worker будут централизованно доступны в карточке запуска.

Запуск контейнерного воркера, который обращается к Ollama на host:

```bash
AGAT_WORKER_NAME=gpu-box-01 \
AGAT_WORKER_MODELS=qwen3:8b \
AGAT_EMBEDDING_MODELS=embeddinggemma \
docker compose --profile worker up -d --build
```

## Ollama

АГАТ использует OpenAI-compatible endpoint Ollama, описанный в [официальной документации](https://docs.ollama.com/api/openai-compatibility).

```bash
ollama pull qwen3:8b
ollama pull embeddinggemma
ollama serve
```

В другом терминале:

```bash
AGAT_COORDINATOR_URL=http://127.0.0.1:8787 \
AGAT_ENROLLMENT_TOKEN=agat-local-enrollment \
AGAT_WORKER_NAME=studio-mac \
AGAT_WORKER_MODELS=qwen3:8b \
AGAT_EMBEDDING_MODELS=embeddinggemma \
AGAT_MODEL_BASE_URL=http://127.0.0.1:11434/v1 \
AGAT_WORKER_CONCURRENCY=1 \
python3 workers/agat_worker.py
```

Чтобы этот worker принимал агентов с runtime `langgraph` или экспортировал OpenTelemetry, один раз установите закреплённые зависимости:

```bash
python3 -m pip install --requirement workers/requirements.txt
```

Без неё worker объявит только `single` и scheduler не выдаст ему несовместимый stage.

`AGAT_MODEL_API_KEY=ollama` используется по умолчанию: Ollama требует поле, но локально его не проверяет.

По умолчанию worker один раз читает native Ollama `/api/tags` и `/api/show`, публикует context/capabilities/size и затем накапливает throughput по реальным запускам. VRAM, power и доверенный quality score задаются оператором. Полный список параметров и строгий rollout: [Model Router и hardware benchmarks](./model-router.md).

`AGAT_EMBEDDING_MODELS` — отдельный allowlist локальных моделей для Local RAG. Worker вызывает их через `/v1/embeddings`; coordinator к model endpoint не подключается. Создание collections, provenance, память и ограничения описаны в [руководстве Local RAG](./local-rag-and-memory.md).

## LM Studio, vLLM и llama.cpp

Меняется только base URL и имя модели:

```bash
AGAT_MODEL_BASE_URL=http://127.0.0.1:1234/v1 \
AGAT_MODEL_API_KEY=lm-studio \
AGAT_MODEL_DISCOVERY=off \
AGAT_WORKER_MODELS=my-local-model \
python3 workers/agat_worker.py
```

Укажите ровно то имя модели, которое принимает `/v1/chat/completions` выбранного server.

## Удалённая машина

1. Опубликуйте coordinator как `https://agat.internal.example`.
2. Разрешите машине исходящий TCP/443 к этому адресу.
3. Скопируйте `workers/agat_worker.py`, `workers/web_tools.py` и `workers/telemetry.py` в одну директорию; для LangGraph/OTel также скопируйте `workers/requirements.txt` и установите его.
4. Передайте enrollment token через защищённый secret channel.
5. Запустите worker с `AGAT_COORDINATOR_URL=https://agat.internal.example`.

Входящий порт на worker и публикация Ollama не нужны.

### Постоянная работа на Linux

```bash
sudo useradd --system --home-dir /var/lib/agat --shell /usr/sbin/nologin agat
sudo install -d -o agat -g agat /opt/agat /var/lib/agat /etc/agat
sudo install -o agat -g agat -m 0755 workers/agat_worker.py /opt/agat/agat_worker.py
sudo install -o agat -g agat -m 0644 workers/web_tools.py /opt/agat/web_tools.py
sudo install -o agat -g agat -m 0644 workers/telemetry.py /opt/agat/telemetry.py
sudo install -m 0644 workers/agat-worker.service /etc/systemd/system/agat-worker.service
```

Если пользователь `agat` уже существует, пропустите первую команду.

Файл `/etc/agat/worker.env`:

```dotenv
AGAT_COORDINATOR_URL=https://agat.internal.example
AGAT_ENROLLMENT_TOKEN=<secret>
AGAT_WORKER_NAME=gpu-box-01
AGAT_WORKER_MODELS=qwen3:8b
AGAT_EMBEDDING_MODELS=embeddinggemma
AGAT_MODEL_BASE_URL=http://127.0.0.1:11434/v1
AGAT_MODEL_API_KEY=ollama
AGAT_MODEL_DISCOVERY=auto
AGAT_WORKER_CONCURRENCY=1
AGAT_WORKER_VRAM_MB=24576
AGAT_WORKER_POWER_WATTS=320
AGAT_WORKER_CREDENTIALS=/var/lib/agat/worker.json
```

Для web-доступа добавьте локальный/общий SearXNG и параметры `AGAT_WEB_ENABLED=true`, `AGAT_WEB_SEARCH_URL=...`. Отдельное руководство: [web-доступ локальных агентов](./web-access.md).

```bash
sudo chmod 0600 /etc/agat/worker.env
sudo systemctl daemon-reload
sudo systemctl enable --now agat-worker
```

## Подключение MCP tools

MCP client работает в coordinator, поэтому удалённому worker не нужен сетевой доступ к MCP server и не передаются upstream secrets. После запуска:

1. откройте раздел **MCP**;
2. при необходимости создайте Bearer/header credentials;
3. добавьте HTTPS Streamable HTTP endpoint с default policy `deny`;
4. синхронизируйте каталог;
5. назначьте `allow`, `approval` или `deny` каждому нужному tool.

HTTP endpoint разрешается отдельным флагом только для контролируемой локальной сети. Для production ограничьте исходящий трафик coordinator и не включайте доверие к annotations непроверенного server. Все параметры и lifecycle описаны в [руководстве MCP gateway](./mcp-gateway.md).

Worker ожидает operator approval до `AGAT_MCP_APPROVAL_TIMEOUT_SECONDS=690`; его lease в это время продолжает продлеваться. Если worker прекращает ожидание раньше из-за пользовательского тайм-аута, он атомарно отменяет ещё не подтверждённый вызов.

## Публикация агента через A2A

1. Задайте точный внешний origin в `AGAT_A2A_PUBLIC_BASE_URL`. Для сетевого hostname разрешён только HTTPS.
2. Откройте раздел **A2A** и создайте endpoint для одного агента.
3. Выберите доступные этому endpoint knowledge collections, input modes, approval и task limits.
4. Сохраните показанный один раз bearer token в secret manager и передайте доверенному A2A client.
5. Сначала проверьте Agent Card, затем отправьте `message:send` с `returnImmediately: true` и poll task.

```dotenv
AGAT_A2A_ENABLED=true
AGAT_A2A_PUBLIC_BASE_URL=https://agat.internal.example
```

Endpoint token не заменяет OIDC/dashboard token и не даёт доступа к `/api/v1`. Примеры `curl`, protocol headers, lifecycle и ограничения приведены в [руководстве A2A adapter](./a2a-adapter.md).

## Смартфоны

Панель адаптирована под телефон и может быть установлена как PWA через меню браузера. Это рекомендуемый режим: запускать, подтверждать и наблюдать за агентами с телефона, а inference оставлять серверу или ноутбуку.

Android с Termux может быть worker для небольших моделей, если на устройстве доступен Python и OpenAI-совместимый локальный server. Установите `AGAT_WORKER_CONCURRENCY=1` и используйте `auto`, чтобы не брать работу на низком заряде.

iOS жёстко ограничивает фоновые процессы браузера. Для надёжного on-device worker понадобится отдельное нативное приложение; оно запланировано отдельно и не имитируется веб-панелью.

## Сквозной тест без модели

Создайте запуск в панели, затем выполните команду по одному разу на каждый этап:

```bash
AGAT_ENROLLMENT_TOKEN=agat-local-enrollment \
python3 workers/agat_worker.py --once --dry-run
```

В панели должны последовательно появиться `Сборщик`, `Аналитик`, `Редактор`, а итоговый статус — `Завершён`.
