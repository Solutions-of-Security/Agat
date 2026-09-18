# Локальный запуск в Docker Compose

Руководство для чистого checkout public preview 1.7.0. Нужны Git, Python 3.10+,
Docker Engine/Desktop с Compose v2 и запущенная Ollama на хосте.
Порт по умолчанию — **8787**. Протокол прежнего стенда на 8788 сохранён
[отдельно](./local-docker-validation-2026-09-08.md).

## Подготовка

```bash
git clone --branch v1.7.0 https://github.com/Solutions-of-Security/Agat.git
cd Agat
ollama pull llama3.2:latest
python3 scripts/setup-local-env.py
```

Скрипт создаёт `.env` из `.env.example` с четырьмя независимыми случайными секретами
и правами `0600`. Существующий `.env` он не заменяет. Токены не выводятся в терминал.
Если порт занят, при первом создании используйте `--port 8788`; для другой модели
добавьте `--model имя-модели`. На существующем стенде меняйте конфигурацию явно,
сохраняя действующие секреты.

SearXNG публикуется на loopback-порту 8888. Если он занят, измените
`AGAT_SEARCH_HTTP_PORT` в созданном `.env`; внутренний адрес worker не меняется.

Worker использует `host.docker.internal:11434`. На Docker Desktop это адрес хоста;
на Linux Compose добавляет `host-gateway`, но Ollama должна быть доступна из Docker-сети.
Если Ollama слушает только loopback Linux-хоста, настройте доступ к ней из выбранной
локальной Docker-сети с ограничением firewall или используйте Python worker на хосте
из [быстрого запуска](./README.md#быстрый-запуск).

## Запуск

```bash
docker compose --profile worker up -d --build --wait
docker compose --profile worker ps
docker compose --profile worker logs --tail=100 coordinator worker
curl --fail http://127.0.0.1:8787/api/v1/health
```

Если выбрали другой порт, замените его в URL проверки и браузера. Ожидается успешный
health response и зарегистрированный worker. Откройте `http://127.0.0.1:8787`.
Для защищённых действий UI попросит `AGAT_ADMIN_TOKEN` из вашего `.env`.
Передавать этот файл или токен в issue не нужно.

Повторите [первый сценарий](./first-run.md). Контейнерный worker использует
`AGAT_WORKER_MODELS=llama3.2:latest`, а coordinator и worker разделяют enrollment token.
Для Local RAG дополнительно загрузите `embeddinggemma` в Ollama либо измените
`AGAT_EMBEDDING_MODELS` и `AGAT_LOCAL_WORKER_EMBEDDING_MODELS` под доступную модель.

Этот профиль включает coordinator с UI, Python worker и SearXNG. Данные SQLite,
артефакты и credential worker сохраняются в именованных volumes. Используется
встроенный runtime процессов; Temporal и production HA требуют отдельной настройки.

## Остановка и обновление

```bash
docker compose --profile worker down
```

Команда сохраняет volumes. Инструкция обновления и backup — в
[release notes](./releases/1.7.0.md). Удаление volumes не является обычной остановкой.
При изменении внешнего порта обновляйте также `AGAT_A2A_PUBLIC_BASE_URL`.
Ошибки модели, регистрации и очереди разобраны в [первом сценарии](./first-run.md).
