# АГАТ (Agat)

[![CI](https://github.com/Solutions-of-Security/Agat/actions/workflows/ci.yml/badge.svg)](https://github.com/Solutions-of-Security/Agat/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](../LICENSE)
[![Status: Public preview](https://img.shields.io/badge/status-public_preview-amber)](./releases/1.7.0.md)

**Запускайте AI-агентов и визуальные процессы на своих моделях — с согласованиями и журналом выполнения.**

[English](./README.en.md) · [Первый результат](./first-run.md) · [Документация](./index.md) · [Релизы](https://github.com/Solutions-of-Security/Agat/releases) · [Участие](./CONTRIBUTING.md)

АГАТ объединяет модели, вычислительные узлы и задачи в одном веб-интерфейсе.
Подходит для работы с документами, поиска по базе знаний и процессов, в которых
несколько агентов выполняют шаги с участием человека. Модели работают на ваших
машинах через Ollama, LM Studio, vLLM или llama.cpp.

![Каталог агентов АГАТ: роли, модели, готовность и запуск задачи](./design/2026-09-workspace/agents-desktop.png)

*Интерфейс приложения с тестовыми данными. [Редактор процессов](./design/2026-09-workspace/process-editor-desktop.png) · [Знакомство за минуту](./product-tour.md).*

## Что можно сделать

| Задача | Как работает АГАТ | Результат |
| --- | --- | --- |
| Разобрать документ | Передать текст агенту на локальной модели | Краткое содержание и сохранённый ответ |
| Найти ответ в своих материалах | Подключить Local RAG и локальную embedding-модель | Ответ с источниками использованных фрагментов |
| Подготовить материал с проверкой | Собрать агентов и согласование в визуальную цепочку | Результат, решения человека и журнал шагов |

## Статус версии

**1.7.0 — public preview**, публикуемый как GitHub prerelease для локального знакомства
и оценки в своём контуре. Номер версии не означает прохождения production qualification.

| Доступно в локальном запуске | Требует отдельной настройки | Открытые работы |
| --- | --- | --- |
| SQLite, агенты, процессы, согласования, журнал | RAG/embeddings, MCP/A2A, OIDC, PostgreSQL, Temporal | Полные solution packs и production gates |

Ограничения и обновление: [release notes 1.7.0](./releases/1.7.0.md).
Дальнейшие работы: [roadmap](./roadmap.md).

## Возможности

- **Агенты и процессы:** каталог готовых агентов, визуальный редактор, 13 шаблонов процессов, условия, циклы и согласования.
- **Управление нагрузкой:** локальные и удалённые воркеры, очередь задач, выбор моделей и ограничения параллельного выполнения.
- **Знания и инструменты:** локальный RAG, поиск в интернете, MCP-инструменты и обмен задачами по A2A.
- **Контроль выполнения:** подтверждение действий, журнал запусков, сохранение результатов и сравнение качества ответов.
- **Варианты развёртывания:** локальный запуск с SQLite, Docker Compose или Kubernetes; PostgreSQL и Temporal для расширенных сценариев.

## Быстрый запуск

Для локального знакомства нужны Git, **Node.js 24** (минимум 22.13),
**Python 3.10+** и запущенная [Ollama](https://ollama.com/download).
Объём памяти зависит от модели, квантования и контекста; ниже используется `llama3.2:latest`.

В первом терминале:

```bash
git clone --branch v1.7.0 https://github.com/Solutions-of-Security/Agat.git
cd Agat
npm ci
npm run build
npm start
```

Во втором терминале, из корня репозитория, загрузите модель и подключите воркер:

```bash
ollama pull llama3.2:latest
python3 workers/agat_worker.py \
  --enrollment-token agat-local-enrollment \
  --models llama3.2:latest \
  --no-web
```

Откройте **[http://127.0.0.1:8787](http://127.0.0.1:8787)**.
В разделе **«Узлы»** проверьте подключение воркера, затем откройте
**Создание → Агенты → Новый агент**. В [первом сценарии](./first-run.md) даны
готовые имя, инструкция, входной текст и критерии ответа. Результат появится в **«Запуски»**.

Этот сценарий использует один coordinator, SQLite и локальный доступ без
администраторского токена. Встроенный enrollment token предназначен для
знакомства на своём компьютере. Для сети настройте отдельные секреты и HTTPS
по [руководству по установке](./getting-started.md).

Альтернативы: [Docker Compose](./local-docker.md), [другие модели и удалённые машины](./getting-started.md),
[Kubernetes в Docker Desktop](./kubernetes-docker-desktop.md).

## Документация

- [Установка, Docker Compose и подключение воркеров](./getting-started.md)
- [Создание агентов](./agents.md) и [маркетплейс](./agent-marketplace.md)
- [Визуальные процессы](./processes.md) и [каталог шаблонов](./llm-processes/README.md)
- [Архитектура](./architecture.md) и [HTTP API](./api.md)
- [Безопасность](./security.md) и [эксплуатация](./operations.md)
- [Планы развития](./roadmap.md)
- **[Полное оглавление документации](./index.md)**

## Разработка и участие

После `npm ci` запустите `npm run dev:api` и `npm run dev:web` в разных
терминалах. Интерфейс разработки доступен на `http://127.0.0.1:5173`.

Перед отправкой изменений:

```bash
npm run typecheck
npm test
npm run build
npm run docs:check
```

Обычные тесты не требуют запущенной модели. Интеграционные проверки с Ollama,
PostgreSQL и другими сервисами описаны в [оглавлении](./index.md#разработка-и-проверки).

Проект поддерживает [Solutions-of-Security](https://github.com/Solutions-of-Security).
Подготовка окружения, браузерные проверки и правила PR: [CONTRIBUTING](./CONTRIBUTING.md).
Ошибки и вопросы: [SUPPORT](./SUPPORT.md). Уязвимости: [приватный канал](./security.md).

| Каталог | Назначение |
| --- | --- |
| `apps/coordinator` | API, очередь, состояние и оркестрация |
| `apps/web` | Веб-интерфейс |
| `apps/temporal-worker` | Durable workflows |
| `workers`, `edge` | Python runtime и нативные мобильные клиенты |
| `deploy`, `scripts` | Развёртывание и проверки |
| `docs` | Руководства, решения, исследования и дизайн |

## Лицензия

[Apache License 2.0](../LICENSE). Уведомление об авторских правах — [NOTICE](../NOTICE).
Сторонние компоненты сохраняют собственные лицензии.
