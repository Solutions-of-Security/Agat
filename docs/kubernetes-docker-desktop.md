# Локальный Kubernetes в Docker Desktop

Этот контур запускает полный локальный control plane в Kubernetes Docker Desktop: Kong Gateway, две coordinator replicas с web UI, отдельный PostgreSQL Fleet state store, versioned MinIO Artifact Store, Keycloak/PostgreSQL, Temporal server/worker, базовый model worker и внутренний web-search. Из интерфейса можно дополнительно создавать несколько worker-пулов с выбранными моделями. Состояние хранится в PVC, случайные токены/пароли/ключи — в раздельных Kubernetes Secrets.

## Что разворачивается

- namespace `agat`, не затрагивающий остальные namespace локального кластера;
- `agat-gateway` — публичный Kong DB-less `LoadBalancer` на `http://127.0.0.1:8787`; coordinator доступен только как `ClusterIP`;
- `agat-coordinator` — две stateless replicas с RollingUpdate (`maxUnavailable=0`), PDB `minAvailable=1` и preferred pod anti-affinity;
- `agat-coordinator-postgres` — локальный PostgreSQL 17 state store с migration/runtime/tenant roles, FORCE RLS и отдельным PVC; это test/staging topology, а не production HA database;
- `agat-artifact-store` — local MinIO/PVC с bucket versioning; это developer profile, а не production durability claim;
- `agat-artifact-store-bootstrap-v24`, `agat-postgres-role-bootstrap-v24` и `agat-postgres-schema-v24` — versioned one-shot Jobs для bucket, ownership/role boundary, transactional schema, DR canaries, region-loss marker и connection admission;
- `agat-keycloak` и `agat-keycloak-postgres` — OIDC, роли, пользователи и проекты; Keycloak доступен на `http://127.0.0.1:8080`;
- `agat-temporal` — persistent локальный dev-server с namespace `agat`, gRPC/HTTP/metrics и UI на `http://127.0.0.1:8233`;
- `agat-temporal-worker` — отдельный TypeScript worker с prebuilt Workflow bundle и Prometheus metrics;
- `agat-worker` — один pod с runtime `single` и `langgraph`, исполняющий lease через локальный OpenAI-compatible model server;
- управляемые `agat-local-*` Deployments — по одному pod на каждый worker, созданный кнопкой в разделе **Узлы**;
- одноразовые `agat-tool-*` Jobs для admin-managed WASI tools и, после явного CNI gate, digest-pinned OCI tools;
- `agat-search` — внутренний SearXNG `ClusterIP` для `web_search`; наружу сервис не публикуется;
- `agat-coordinator-postgres` и local `agat-artifact-store` по 4 GiB, `agat-keycloak-postgres` и `agat-temporal-data` по 2 GiB, `agat-worker-state` на 128 MiB; coordinator file cache использует pod-local `emptyDir` и не является source of truth;
- init/startup/readiness/liveness checks; coordinator и Temporal worker начинают работу только после готовности Temporal namespace;
- resource requests/limits и урезанные Linux capabilities; worker pods не получают service-account token;
- namespace-scoped service account coordinator с Role для worker Deployments и одноразовых sandbox Jobs, Pods/log, Secrets и NetworkPolicies.

Манифесты находятся в `deploy/k8s/docker-desktop`. Secret намеренно не хранится в репозитории: скрипт создаёт его при первом запуске и не ротирует при последующих.

Образы SearXNG и локального Temporal server закреплены версией и multi-arch digest, чтобы повторный запуск не получил непроверенное изменение из плавающего `latest`. Keycloak, оба PostgreSQL и Kong также используют фиксированные версии. Скрипт дополнительно собирает доверенный `agat-local/sandbox-wasi:1.7.0`; загружаемые OCI tools обязаны указывать полный digest.

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
2. создаст namespace, application Secret с отдельными Artifact Store keys и coordinator PostgreSQL Secret при первом запуске;
3. соберёт четыре образа под архитектуру Kubernetes node: coordinator/web, model worker, Temporal worker и WASI sandbox;
4. при upgrade остановит coordinator replicas, применит Kustomize и дождётся bucket/role-bootstrap/schema/admission Jobs;
5. только после schema v24 marker и versioned bucket поднимет PostgreSQL runtime, Keycloak, Temporal, coordinator, Temporal worker, SearXNG, model worker и Kong;
6. проверит Kong `/api/v1/health`, OIDC discovery и Temporal UI через localhost.

Чистый namespace сразу использует PostgreSQL и две coordinator replicas. При обнаружении существующего SQLite coordinator скрипт завершится до apply/build: state migrator существует, но намеренно не запускается автоматически без maintenance/reconciliation. После [offline migration](./sqlite-postgresql-migration.md) можно подтвердить authority cutover:

```bash
AGAT_K8S_ALLOW_POSTGRES_CUTOVER=true npm run k8s:up
```

Этот флаг не мигрирует ни одной записи и не удаляет старый PVC. Используйте его только после reconciliation либо для осознанного старта пустого local state store.

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

## Fleet cell, releases и SIEM

Локальная cell по умолчанию имеет `AGAT_REGION=local`, `AGAT_RESIDENCY_DOMAIN=local` и две coordinator replicas. Идентичность cell меняют только до появления project data:

```bash
AGAT_REGION=ru-central1 \
AGAT_RESIDENCY_DOMAIN=ru \
AGAT_K8S_COORDINATOR_REPLICAS=2 \
npm run k8s:up
```

Project queues, quotas, coordinator heartbeats, signed releases, rollouts и SIEM backlog видны в разделе **Fleet / HA**. Production worker signing включается только после установки public trust roots и регистрации baseline release. Private Ed25519 key остаётся вне cluster:

```bash
npm run fleet:sign-worker -- manifest.json release-private-key.pem release-prod-2026
```

Передайте JSON map public PEM keys через `AGAT_WORKER_RELEASE_PUBLIC_KEYS`, release identity server worker — через `AGAT_WORKER_RELEASE_ID`, `AGAT_WORKER_ARTIFACT_DIGEST`, `AGAT_WORKER_RELEASE_KEY_ID`, `AGAT_WORKER_RELEASE_SIGNATURE`, затем включите `AGAT_REQUIRE_SIGNED_WORKER_RELEASES=true`. Значение JSON и signature удобнее передавать из защищённого environment/CI, не из shell history. Native mobile nodes остаются на отдельной Play Integrity/App Attest boundary.

SIEM exporter включается отдельными параметрами; bearer добавляется в `agat-secrets` только если задан:

```bash
AGAT_SIEM_ENABLED=true \
AGAT_SIEM_URL=https://siem.example.internal/ingest/agat \
AGAT_SIEM_BEARER_TOKEN='scoped-secret' \
npm run k8s:up
```

Локальные PostgreSQL и MinIO работают без TLS только внутри namespace. Production Artifact Store требует private HTTPS, encryption, scoped workload identity и отдельный residency/DR review: [S3 Artifact Store](./s3-artifact-store-lifecycle.md). Production resilience Job требует строго `verify-full`, private managed endpoint, multi-AZ/PITR provider evidence и реальные DR reports; Docker Desktop manifest не является production deployment template. Полный contract: [Managed PostgreSQL](./managed-postgresql-resilience.md) и [Fleet и HA 1.7](./fleet-ha-1.7.md).

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

`AGAT_SEARCH_SECRET` и `AGAT_TEMPORAL_INTERNAL_TOKEN` можно передать вместе с ротацией. Попытка незаметно заменить `AGAT_CREDENTIALS_KEY` или один из persistent Keycloak secrets отклоняется до изменения Secret. Ключ credentials меняют только вместе с миграцией уже зашифрованных записей; пароль Keycloak PostgreSQL — средствами PostgreSQL; пароль `agat-admin` — через Keycloak.

Fleet state store использует отдельный `agat-postgres-secrets` с admin, migration, runtime-system и tenant passwords/URLs. Admin получает только role-bootstrap Job, migration URL — только schema Job, а coordinator Deployment — runtime+tenant. При первом запуске можно передать `AGAT_POSTGRES_ADMIN_PASSWORD`, `AGAT_POSTGRES_MIGRATION_PASSWORD`, `AGAT_POSTGRES_SYSTEM_PASSWORD`, `AGAT_POSTGRES_TENANT_PASSWORD`; после создания PVC скрипт отклоняет незаметную замену. Ротация выполняется отдельной database procedure с одновременным обновлением role password и Secret.

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

Основной способ проверки артефактов — вкладка **Артефакты** и authenticated download API. В Fleet mode bytes хранятся в PostgreSQL, а pod-local `/data/artifacts` является только materialization cache; его наличие, потеря или прямое изменение не меняет authoritative metadata/content.

Остановка без потери базы, Secret, identity базового worker и определений локальных worker-пулов:

```bash
npm run k8s:stop
```

Повторный `npm run k8s:up` вернёт Gateway, identity, durable runtime, две coordinator replicas, coordinator PostgreSQL, search и базовый worker к заданным значениям и использует прежние PVC/Secrets. Управляемые локальные пулы останутся остановленными до нажатия **Запустить** в интерфейсе.

Полное удаление контура:

```bash
kubectl delete namespace agat
```

Это удалит также coordinator/Keycloak PostgreSQL, Temporal и worker PVC, а также любой сохранённый legacy SQLite PVC; восстановление возможно только из отдельного backup.

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

- Это single-machine среда разработки, а не production HA.
- Две coordinator replicas проверяют shared-state semantics, но один Docker Desktop node и один `agat-coordinator-postgres` pod/PVC остаются общим failure domain. Production использует отдельный managed/multi-AZ/PITR gate и per-cluster restore/failover reports.
- `temporal server start-dev` и Keycloak `start-dev` предназначены только для локальной разработки. Production требует полноценного Temporal/Cloud, оптимизированного Keycloak, TLS и backup каждой внешней БД.
- PDB защищает только от части добровольных disruptions и не спасает от потери Docker Desktop node/database.
- SQLite→PostgreSQL migration требует явного offline runbook и не запускается `k8s:up`; cross-region authority transfer, object store, runtime attestation обычного worker и SIEM poison-event/retention UI ещё отсутствуют.
- Kong rate limit в локальном профиле хранится в памяти одного pod; несколько Gateway replicas требуют Redis-backed policy.
- `host.docker.internal` предназначен для связи контейнера с model server на машине Docker Desktop. Для удалённых GPU-узлов используйте отдельный worker, защищённый URL coordinator и SearXNG, доступный с той машины.
- Сброс Kubernetes-кластера в Docker Desktop удаляет workload и локальные volumes; делайте backup перед reset.
