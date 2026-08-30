# Локальный Kubernetes в Docker Desktop

Этот контур запускает полный локальный control plane в Kubernetes Docker Desktop: Kong Gateway, coordinator с web UI, Keycloak/PostgreSQL, Temporal server/worker, базовый model worker и внутренний web-search. Из интерфейса можно дополнительно создавать несколько worker-пулов с выбранными моделями. Состояние хранится в PVC, случайные токены и ключи — в Kubernetes Secret.

## Что разворачивается

- namespace `agat`, не затрагивающий остальные namespace локального кластера;
- `agat-gateway` — публичный Kong DB-less `LoadBalancer` на `http://127.0.0.1:8787`; coordinator доступен только как `ClusterIP`;
- `agat-coordinator` — один pod со стратегией `Recreate`, потому что основной SQLite volume нельзя безопасно писать из нескольких coordinator одновременно;
- `agat-keycloak` и `agat-keycloak-postgres` — OIDC, роли, пользователи и проекты; Keycloak доступен на `http://127.0.0.1:8080`;
- `agat-temporal` — persistent локальный dev-server с namespace `agat`, gRPC/HTTP/metrics и UI на `http://127.0.0.1:8233`;
- `agat-temporal-worker` — отдельный TypeScript worker с prebuilt Workflow bundle и Prometheus metrics;
- `agat-worker` — один pod с runtime `single` и `langgraph`, исполняющий lease через локальный OpenAI-compatible model server;
- управляемые `agat-local-*` Deployments — по одному pod на каждый worker, созданный кнопкой в разделе **Узлы**;
- одноразовые `agat-tool-*` Jobs для admin-managed WASI tools и, после явного CNI gate, digest-pinned OCI tools;
- `agat-search` — внутренний SearXNG `ClusterIP` для `web_search`; наружу сервис не публикуется;
- `agat-data` на 1 GiB, `agat-keycloak-postgres` и `agat-temporal-data` по 2 GiB, `agat-worker-state` на 128 MiB;
- init/startup/readiness/liveness checks; coordinator и Temporal worker начинают работу только после готовности Temporal namespace;
- resource requests/limits и урезанные Linux capabilities; worker pods не получают service-account token;
- namespace-scoped service account coordinator с Role для worker Deployments и одноразовых sandbox Jobs, Pods/log, Secrets и NetworkPolicies.

Манифесты находятся в `deploy/k8s/docker-desktop`. Secret намеренно не хранится в репозитории: скрипт создаёт его при первом запуске и не ротирует при последующих.

Образы SearXNG и локального Temporal server закреплены версией и multi-arch digest, чтобы повторный запуск не получил непроверенное изменение из плавающего `latest`. Keycloak, PostgreSQL и Kong также используют фиксированные версии. Скрипт дополнительно собирает доверенный `agat-local/sandbox-wasi:1.6.0`; загружаемые OCI tools обязаны указывать полный digest.

## Требования

1. Docker Desktop запущен.
2. В `Settings → Kubernetes` создан и запущен встроенный кластер.
3. Активен kube-context `docker-desktop`.
4. Установлены `kubectl`, `docker`, `curl` и `openssl`.

Проверка:

```bash
kubectl config current-context
kubectl get nodes
```

Скрипт намеренно откажется работать с другим context, чтобы не отправить локальные образы и секреты в посторонний кластер.

## Запуск

```bash
npm run k8s:up
```

Команда:

1. проверит `docker-desktop` и готовность node;
2. создаст namespace и все случайные tokens/passwords/keys при первом запуске;
3. соберёт три образа под архитектуру Kubernetes node: coordinator/web, model worker и Temporal worker;
4. применит Kustomize-манифесты и передаст образы в kind через встроенный registry mirror Docker Desktop;
5. последовательно дождётся PostgreSQL, Keycloak, Temporal, coordinator, Temporal worker, SearXNG, model worker и Kong;
6. проверит Kong `/api/v1/health`, OIDC discovery и Temporal UI через localhost.

Если Docker Desktop не опубликовал LoadBalancer или порт занят, запустите в отдельном терминале безопасный loopback port-forward:

```bash
npm run k8s:forward
```

Войдите в панель пользователем `agat-admin`. Локальный пароль извлекается из Secret только по команде:

```bash
npm run --silent k8s:keycloak-password
```

Legacy admin token сохраняется для сценария без OIDC, но в Kubernetes UI не используется:

```bash
npm run --silent k8s:admin-token
```

Не публикуйте пароли и токены в логах, issue или commit.

Для регистрации нового worker используется другой секрет — enrollment token:

```bash
npm run --silent k8s:enrollment-token
```

Не подставляйте admin token или старый демонстрационный `agat-local-enrollment`: Kubernetes-контур при первом запуске генерирует собственное случайное значение.

## Запуск workers кнопкой

После `npm run k8s:up` откройте раздел **Узлы**. Блок **Запустить локальные workers** автоматически читает список установленных Ollama-моделей. Выберите модель, укажите количество экземпляров и нажмите **Запустить workers**.

Каждый экземпляр создаётся отдельным Deployment и отдельно регистрируется в coordinator. Поэтому можно одновременно держать, например, один worker для `qwen3:8b` и два workers для `llama3.2:latest`. Режим планировщика `sequential` всё равно ограничит весь контур одним активным lease, если железо слабое.

Пул можно остановить и повторно запустить из той же карточки. Подробная модель, API, RBAC и диагностика: [локальный запуск нескольких workers](./local-workers.md).

## Локальная модель

По умолчанию worker обращается к Ollama на host через `http://host.docker.internal:11434/v1`. Скрипт читает локальный `/api/tags`, предпочитает `qwen3:8b`, если она установлена, иначе выбирает самую компактную completion-модель, исключая embedding-only варианты. Это уменьшает риск нехватки памяти на слабом ноутбуке:

```bash
ollama pull qwen3:8b
ollama pull embeddinggemma
ollama serve
npm run k8s:up
```

Если Ollama недоступна во время повторного запуска, скрипт сохраняет модель текущего worker. Для первого запуска fallback — `qwen3:8b`, а в консоль выводится предупреждение. Явный `AGAT_WORKER_MODELS` всегда имеет приоритет над автоопределением.

Переопределения перед запуском:

```bash
AGAT_WORKER_NAME=macbook-gpu \
AGAT_WORKER_MODELS=qwen3:14b \
AGAT_EMBEDDING_MODELS=embeddinggemma \
AGAT_MODEL_BASE_URL=http://host.docker.internal:11434/v1 \
AGAT_WORKER_CONCURRENCY=1 \
npm run k8s:up
```

Подключение отдельного worker к уже работающему Kubernetes coordinator:

```bash
AGAT_COORDINATOR_URL=http://127.0.0.1:8787 \
AGAT_ENROLLMENT_TOKEN="$(npm run --silent k8s:enrollment-token)" \
AGAT_WORKER_NAME=additional-worker \
AGAT_WORKER_MODELS=llama3.2:latest \
AGAT_EMBEDDING_MODELS=embeddinggemma \
python3 workers/agat_worker.py
```

Для удалённой машины получите token на coordinator и передайте его через защищённый канал; команда `npm run` на удалённом worker не нужна.

Для LM Studio обычно достаточно заменить URL и имя модели. Если model server использует настоящий API key, при первом запуске задайте `AGAT_MODEL_API_KEY`. Для замены ключа существующего контура используйте ротацию ниже.

Уже зарегистрированный worker публикует изменённый список моделей через защищённый node token в ближайшем heartbeat. Повторная регистрация и enrollment token для обычной смены `AGAT_WORKER_MODELS` не нужны.

То же относится к `AGAT_EMBEDDING_MODELS`. `k8s:up` по умолчанию передаёт `embeddinggemma` базовому worker и управляемым пулам; модель должна быть установлена в Ollama. Переопределить или отключить можно через `AGAT_EMBEDDING_MODELS=...` и `AGAT_LOCAL_WORKER_EMBEDDING_MODELS=...`. Статус индексации и наличие подходящего worker видны в разделе **Knowledge**.

## Web-доступ модели

В Kubernetes-контуре `AGAT_WEB_ENABLED=true` по умолчанию. Worker передаёт модели инструменты `web_search` и `web_fetch`, обращается к SearXNG по `http://agat-search:8080/search` и читает только публичные HTTP(S)-страницы через ограниченный reader. В карточке узла UI эти tools появляются после heartbeat.

Отключение:

```bash
AGAT_WEB_ENABLED=false npm run k8s:up
```

Переопределение лимитов:

```bash
AGAT_WEB_TIMEOUT=15 \
AGAT_WEB_FETCH_MAX_CHARS=16000 \
AGAT_WEB_MAX_TOOL_ROUNDS=8 \
npm run k8s:up
```

Model server и модель должны поддерживать OpenAI-compatible tool calling. Архитектура, тестовый prompt, настройки и границы безопасности описаны в [руководстве по web-доступу](./web-access.md).

Worker можно не запускать, оставив только coordinator и UI:

```bash
AGAT_K8S_WORKER_ENABLED=false npm run k8s:up
```

## Секреты и ротация

При первом запуске можно передать готовые значения; незаданные ключи будут сгенерированы:

```bash
AGAT_ADMIN_TOKEN="$(openssl rand -hex 32)" \
AGAT_ENROLLMENT_TOKEN="$(openssl rand -hex 32)" \
npm run k8s:up
```

Существующий Secret автоматически не меняется. Общая ротация требует новые legacy admin/enrollment tokens. При этом скрипт сохраняет ключ шифрования credentials, пароль persistent PostgreSQL и импортированные Keycloak passwords:

```bash
AGAT_K8S_ROTATE_SECRETS=true \
AGAT_ADMIN_TOKEN="$(openssl rand -hex 32)" \
AGAT_ENROLLMENT_TOKEN="$(openssl rand -hex 32)" \
AGAT_MODEL_API_KEY=ollama \
npm run k8s:up
```

`AGAT_SEARCH_SECRET` и `AGAT_TEMPORAL_INTERNAL_TOKEN` можно передать вместе с ротацией. Попытка незаметно заменить `AGAT_CREDENTIALS_KEY` или один из persistent Keycloak secrets отклоняется до изменения Secret. Ключ credentials меняют только вместе с миграцией уже зашифрованных записей; пароль PostgreSQL — средствами PostgreSQL; пароль `agat-admin` — через Keycloak.

После ротации enrollment token уже зарегистрированный worker продолжит использовать node token из PVC. Для принудительной повторной регистрации удалите только файл identity внутри worker pod или пересоздайте `agat-worker-state` осознанно.

## Эксплуатация

Текущее состояние и health:

```bash
npm run k8s:status
kubectl logs -n agat deployment/agat-coordinator --tail=100
kubectl logs -n agat deployment/agat-gateway --tail=100
kubectl logs -n agat deployment/agat-keycloak --tail=100
kubectl logs -n agat deployment/agat-temporal-worker --tail=100
kubectl logs -n agat deployment/agat-worker --tail=100
kubectl logs -n agat deployment/agat-search --tail=100
kubectl get deployments,pods -n agat -l agat.local/managed=true
```

Проверить созданные файлы без раскрытия их содержимого:

```bash
kubectl exec -n agat deployment/agat-coordinator -- \
  find /data/artifacts -type f -maxdepth 6 -print
```

Основной способ чтения — вкладка «Артефакты» и authenticated download API. Прямое изменение файлов внутри PVC нарушит соответствие с metadata SQLite.

Остановка без потери базы, Secret, identity базового worker и определений локальных worker-пулов:

```bash
npm run k8s:stop
```

Повторный `npm run k8s:up` вернёт Gateway, identity, durable runtime, coordinator, search и базовый worker к единице и использует прежние PVC/Secret. Управляемые локальные пулы останутся остановленными до нажатия **Запустить** в интерфейсе.

Полное удаление контура:

```bash
kubectl delete namespace agat
```

Это удалит также PVC, локальную SQLite-базу и все файловые артефакты; восстановление возможно только из backup.

Чтобы повторно использовать уже собранные образы:

```bash
AGAT_K8S_SKIP_BUILD=true npm run k8s:up
```

Для локальных mutable tags задан `imagePullPolicy: Always`: при каждом rollout node запрашивает актуальный образ у registry mirror Docker Desktop, а не оставляет предыдущую сборку в containerd cache.

Если задан нестандартный tag, используйте одинаковое значение при сборке и последующих стартах:

```bash
AGAT_K8S_IMAGE_TAG=dev npm run k8s:up
```

## Ограничения локального контура

Встроенный Temporal работает как `start-dev`; `AGAT_TEMPORAL_TARGET=local`, TLS и Worker Deployment Versioning выключены намеренно. Этот профиль нельзя переносить в production простым изменением image tag. Production-переход описан в [отдельном runbook](./production-durable-runtime.md).

Coordinator использует SQLite и одну replica. `AGAT_STATE_STORE_DRIVER=postgresql` до реализации adapter fail-closed; проект миграции и HA acceptance gate: [PostgreSQL state-store design](./postgresql-state-store-design.md).

- Это single-machine среда разработки, а не production HA.
- `temporal server start-dev` и Keycloak `start-dev` предназначены только для локальной разработки. Production требует полноценного Temporal/Cloud, оптимизированного Keycloak, TLS и backup каждой внешней БД.
- SQLite требует ровно один coordinator. Temporal уже делает жизненный цикл процессов durable, но горизонтальное масштабирование coordinator дополнительно требует миграции основного state store на PostgreSQL.
- Kong rate limit в локальном профиле хранится в памяти одного pod; несколько Gateway replicas требуют Redis-backed policy.
- `host.docker.internal` предназначен для связи контейнера с model server на машине Docker Desktop. Для удалённых GPU-узлов используйте отдельный worker, защищённый URL coordinator и SearXNG, доступный с той машины.
- Сброс Kubernetes-кластера в Docker Desktop удаляет workload и локальные volumes; делайте backup перед reset.
