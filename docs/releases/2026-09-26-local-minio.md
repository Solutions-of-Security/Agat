# MinIO из исходников для Docker Desktop

26.09.2026. После [исправления CI](./2026-09-26-ci-minio.md) локальный Kubernetes
всё ещё ссылался на upstream binary image. MinIO перешёл на
[source-only distribution](https://github.com/minio/minio#source-only-distribution),
поэтому `k8s:up` теперь собирает `agat-local/minio` вместе с приложением.

## Изменение

Общий [Dockerfile](../../deploy/minio/Dockerfile) используется Docker Desktop
и одноразовым S3-стендом. Commit MinIO, checksum архива, Go image digest и
проверка зависимостей сохранены из предыдущего этапа. Версия MinIO в image label
указывает исходный релиз; сам бинарник при обычном `go build` сообщает
`DEVELOPMENT.GOGET`, это собственная сборка Agat.

UID/GID 1000 совпадает с прежним Kubernetes security context. `/data` и `/tmp`
принадлежат этому пользователю; сервер работает без дополнительных capabilities.
Локальный Kubernetes сохраняет прежний PVC, ключи из Secret, bucket versioning,
Service и health probes. Самообновление сервера выключено.

`k8s:up` собирает образ для архитектуры node. `AGAT_K8S_IMAGE_TAG` применяется
к MinIO и bucket bootstrap до `kubectl apply`. Режим `AGAT_K8S_SKIP_BUILD`
проверяет наличие MinIO image до изменения кластера. После пересборки существующий
MinIO перезапускается, затем скрипт ожидает readiness до ожидания bootstrap Job.
При одном экземпляре MinIO это maintenance с краткой недоступностью S3.

## Проверка

`npm run fleet:test-artifact-store` использует отдельный Docker volume, UID/GID
1000, `--cap-drop ALL` и `no-new-privileges`. Интеграция проверяет PostgreSQL→S3,
cache разных реплик, outbox isolation, legal hold и удаление конкретной версии,
затем создаёт ещё один артефакт и сохраняет его object key/version ID из БД.
После настоящего `docker restart` новый S3 client получает **эту же версию**
и сравнивает её содержимое. Это исключает прохождение проверки за счёт
coordinator file cache. EXIT удаляет контейнеры, volume и сеть.

Во время проверки Docker Desktop переназначил автоматически выбранный host port
при restart (`58979` → `58984`); MinIO оставался `running`. Тест теперь получает
актуальный loopback endpoint через `docker port`, как рекомендует
[документация Docker](https://docs.docker.com/engine/containers/run/).
Порт не фиксируется глобально, поэтому независимые прогоны не конфликтуют.

Локальный прогон Apple Silicon: **1/1 integration pass, 0 skipped**, включая
чтение сохранённой версии после restart. Coordinator typecheck, `bash -n`,
рендер реального блока `k8s:up` с default/custom tag и offline Kustomize — pass.
Проверка с отсутствующим image подтвердила выход до любого вызова `kubectl`.
Логи и SHA изменённых исходников: [evidence manifest](./evidence/2026-09-26-local-minio/checks.json).
Полный Kubernetes rollout и upgrade
существующего PVC этим тестом не подтверждаются; пользовательский кластер
в рамках изменения не разворачивался. Это локальный профиль разработки;
production storage остаётся в [отдельном контракте](../s3-artifact-store-lifecycle.md).
