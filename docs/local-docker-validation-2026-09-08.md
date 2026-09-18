# Протокол локального Docker-стенда — 8 сентября 2026

## Текущий стенд

Панель и API доступны по адресу <http://127.0.0.1:8788>. Порт 8787 занят другим сервисом Docker Desktop; опубликованный порт задаётся переменной `AGAT_HTTP_PORT` в `.env`. Внутри контейнера coordinator использует порт 8787.

Стек включает coordinator с интерфейсом, Python-воркер и SearXNG. Данные SQLite и состояние воркера сохраняются в именованных Docker volumes. Этот профиль использует встроенный runtime процессов; Temporal и Kubernetes в него не входят.

Воркер подключён к Ollama на хосте через `host.docker.internal:11434`. В локальном `.env` выбраны уже установленные `qwen3.5:4b` и `nomic-embed-text:latest`; обнаружение остальных локальных моделей включено. Перед запуском стека Ollama должна работать.

## Команды

Выполнять из корня репозитория:

```sh
docker compose --profile worker up -d --build --wait
docker compose --profile worker ps
docker compose --profile worker logs --tail=100 coordinator worker
curl --fail http://127.0.0.1:8788/api/v1/health
```

Остановить стек с сохранением данных:

```sh
docker compose --profile worker down
```

Для доступа к защищённым действиям интерфейс запрашивает `AGAT_ADMIN_TOKEN` из локального `.env`. Файл содержит отдельные случайные ключи, имеет права `0600` и исключён из Git и Docker build context. Не публикуйте его.

При настройке другой машины создайте `.env` на основе `.env.example`, задайте отдельные случайные значения для `AGAT_ADMIN_TOKEN`, `AGAT_ENROLLMENT_TOKEN`, `AGAT_CREDENTIALS_KEY` и `AGAT_SEARCH_SECRET`. Настройте `AGAT_WORKER_MODELS`, `AGAT_EMBEDDING_MODELS` и `AGAT_LOCAL_WORKER_EMBEDDING_MODELS` под установленные модели. Если меняете внешний порт, обновите и `AGAT_A2A_PUBLIC_BASE_URL`.

## Проверка запуска — 8 сентября 2026

- Docker собрал coordinator и интерфейс из исходников текущего рабочего дерева; TypeScript-проверки прошли.
- Все 32 frontend-теста прошли; контрольные суммы 75 файлов исходников, тестов и публичных ресурсов совпали с проверенной локальной копией.
- `docker compose --profile worker up -d --no-build --wait` завершился успешно. Coordinator и SearXNG получили состояние `healthy`; воркер зарегистрирован как `docker-worker`.
- `/api/v1/health` вернул `status: ok` и подключённый runtime `database`.
- В браузере открыт новый каталог агентов; все три встроенных агента показывают готовность и один совместимый узел.

При первом запуске SearXNG получил `Input/output error` на bind mount конфигурации, которую iCloud выгрузил с диска. После загрузки `deploy/searxng/settings.yml` и `deploy/searxng/limiter.toml` на локальный диск контейнер поиска был пересоздан командой `docker compose --profile worker up -d --no-build --force-recreate search`. Для стабильного повторного запуска держите эти файлы локально доступными.
