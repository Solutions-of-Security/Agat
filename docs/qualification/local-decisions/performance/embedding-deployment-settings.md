# Передача настроек embedding в deployment

Дата: 28.09.2026. Исправлена поставка трёх существующих параметров worker: `AGAT_EMBEDDING_TRANSPORT`, `AGAT_EMBEDDING_TIMEOUT`, `AGAT_EMBEDDING_IDLE_TIMEOUT`. Runtime и defaults не менялись: `isolated` / `900` / `0`.

## Причина и изменение

[Baseline](./evidence/2026-09-28/embedding-deployment-settings/baseline.json) воспроизвёл потерю всех трёх параметров: `.env` с `session` / `45` / `30` не давал соответствующих ключей в environment контейнера после `docker compose config`. Compose интерполирует только явно подключённые настройки; само наличие переменной в `.env` недостаточно. Это соответствует [официальному описанию Docker](https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/).

Параметры добавлены в [.env.example](../../../../.env.example), [environment worker в Compose](../../../../docker-compose.yml) и [общий Kubernetes ConfigMap](../../../../deploy/k8s/docker-desktop/worker-configmap.yaml). [k8s-up.sh](../../../../scripts/k8s-up.sh) передаёт environment команды в тот же ConfigMap вместе с OpenTelemetry. Базовый Deployment и управляемые worker-пулы используют уже существующий `envFrom`; отдельные несовместимые настройки launcher не вводились. Системный сервис уже читает `/etc/agat/worker.env` через `EnvironmentFile`.

## Проверка цепочки

[Qualification script](../../../../scripts/qualify-embedding-deployment.py) выполняет настоящий offline-рендер Compose с синтетическим env-файлом и очищенным окружением, Kustomize и production `buildWorkerDeployment`. Для `k8s-up.sh` исполняются только реальные фрагменты построения JSON и вызова patch; `kubectl` в этом фрагменте заменён функцией, сохраняющей аргументы. Полный deploy-скрипт и обращения к живому Kubernetes API не запускаются.

Проверены 16 наборов: defaults, session с idle, отключение idle, возврат к isolated, дробные сроки, пустые значения с fallback, неизвестный transport, нулевой/слишком большой/NaN/Infinity deadline и отрицательный/слишком большой/NaN/Infinity idle, а также несовместимый isolated + positive idle. Для каждого проверены Compose, базовый Kubernetes worker и managed worker; ещё два случая проверяют чистый Kustomize. Полученные значения передаются настоящему `agat_worker.parse_args`.

Итог: **50/50 round trips**, из них **20 приняты**, **30 отклонены** с exit 2; transport и оба числовых значения совпали с ожиданиями. Все ошибочные значения дошли до штатной валидации worker. Проверено отсутствие явных overrides трёх переменных в обоих типах Deployment; поля OpenTelemetry сохранились. Существующие launcher tests — **3/3**, `bash -n scripts/k8s-up.sh` — pass. Повторный вызов с существующим output отклонён до запуска команд, байты evidence сохранены. `docs:check`: 12 Node + 316 Python tests, 1696 ссылок в 203 Markdown-файлах, 13 process templates; architecture audit — 0 ошибок/предупреждений.

[Результат](./evidence/2026-09-28/embedding-deployment-settings/result.json) содержит все входы/выходы, версии инструментов и SHA-256 исходников. Он получен на macOS/Python 3.14.3. Попытка повторить parsing внутри ранее проверенного Linux image не завершилась: Docker API не ответил на inspect и `_ping`; журнал backend фиксирует остановку движка из-за `no space left on device`. [Ограничение среды сохранено отдельно](./evidence/2026-09-28/embedding-deployment-settings/docker-unavailable.json). Общий Docker Desktop не перезапускался, чужие файлы/образы не очищались. Это не Linux-pass этого этапа; полный Linux suite неизменённого runtime ранее прошёл в [idle opt-in](./embedding-worker-idle-opt-in.md).

Воспроизведение без deployment:

```bash
python3 scripts/qualify-embedding-deployment.py \
  --root "$PWD" --output /tmp/embedding-deployment-result.json --host
```

Нужны Node.js с установленным `tsx`, Docker Compose CLI, `kubectl` и Ruby со стандартным YAML parser. Проверка не запускает services и не читает настоящий `.env`; фиксируются только три embedding-настройки. Для отдельной повторной Linux-проверки вместо `--host` можно указать `--image <локальный-образ>`: скрипт не скачивает образ, запускает его с `--network none` и побайтно проверяет шесть runtime-модулей.

## Применение и откат

Дополнение 28.09.2026: после восстановления Docker выполнена [отдельная Linux image-проверка](./linux-worker-deployment.md) — все 50 round trips, 143 worker tests и три сценария настоящего PID 1 прошли. Исходный неуспешный опыт выше сохранён.

[Руководство Compose](../../../getting-started.md) описывает изменение `.env` и `up -d --no-deps worker`: обычный `restart` не перечитывает environment, как указано в [Docker CLI reference](https://docs.docker.com/reference/cli/docker/compose/restart/). [Руководство Kubernetes](../../../kubernetes-docker-desktop.md) описывает environment команды, общий ConfigMap и перезапуск нужных Deployment. Согласно [Kubernetes ConfigMaps](https://kubernetes.io/docs/concepts/configuration/configmap/), переменные из ConfigMap обновляются при новом запуске pod; существующий managed pool не переключается автоматически.

Idle timeout 30 секунд в примерах не является выбранным production SLO. Откат — idle `0`, при необходимости transport `isolated`, затем применение конфигурации и перезапуск нужных worker. Изменение не управляет Ollama, не меняет deadline/grace period и не разрешает автоматические decision routes. Живой deployment в этом этапе не выполнялся.

[Итоговые проверки и hashes](./evidence/2026-09-28/embedding-deployment-settings/checks.json) дополняют сохранённые результаты. Следующий инженерный gate исходного плана — сквозной RAG через Temporal, включая retry/replay и сохранность принятого результата; независимые бизнес-данные и qualification модели остаются отдельными открытыми gates.
