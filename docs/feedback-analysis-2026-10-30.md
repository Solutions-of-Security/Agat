# Анализ доработок Агата по обратной связи

Дата среза: **30 октября 2026 года, до реализации FB-001**. Текущий результат FB-001 — в [протоколе](./status/fb-001-validation-2026-10-30.md). Ближайшие задачи и критерии готовности: [бэклог по обратной связи](./feedback-priority-backlog-2026-10-30.md).

## Вывод и границы анализа

Замечания выявляют реальные ограничения пользовательского пути: нет подтверждённого чатового адаптера с сохраняемым диалогом, удобного подключения облачных моделей, управляемого рабочего каталога агента и отдельных пользователей в лёгком режиме. Ближайшую работу следует выстроить в три проверяемых среза: **лёгкий режим с правами и моделями → бот с историей диалога и доставкой ответа → выполнение кода, Git и STDIO в изоляции**.

Два исходных утверждения слишком широки. Изолированное выполнение WASI/OCI уже реализовано, а внешний запуск доступен также через авторизованный API runs и A2A. Права проверяет coordinator: Kong, Temporal и PostgreSQL решают другие задачи и не являются обязательной логической зависимостью RBAC. Эти уточнения сокращают доработку: нужно развить существующие границы, сохранив scheduler, MCP policy, credentials, audit и durable runtime.

Полномочие этой работы — анализ и добавление задач. Реализация, эксплуатация, доступность production и качество моделей не проверялись. Основные читатели — Product, backend/runtime, integration, frontend и QA. Горизонт — ближайшая очередь разработки без обещания дат или состава команды.

**Граница источников.** Локальный HEAD — `78d4cfba1387cf41f59c2965cefbaec3f0c3820e`. Прочитаны целевые auth/server/MCP/sandbox/A2A/config/worker участки рабочего дерева, roadmap и бэклог от 17 октября. Учтены прежние SAFE-101, EVAL-103, MEM-101, RUN-105, UX-308 и перенос CLD-006 в P1. Код также сопоставлен с GitHub на фиксированном HEAD; рабочие server/worker отличаются, их hashes приведены в SRC-011. Новые задачи добавлены в локальный roadmap; этот анализ и карточки FB раскрывают его, не создавая второй независимый план. Проверка статическая, эксплуатация и runtime E2E в неё не входят.

## Текущее состояние AS-IS

| Обратная связь | Наблюдение и источник | Разрыв и влияние | Ближайшая работа |
|---|---|---|---|
| Бот и диалог | В проверенном дереве исходников адаптеры Telegram, Slack, Пачки и других чатов не найдены. Worker создаёт system/user messages заново для этапа. A2A принимает `contextId`, но отклоняет продолжение по `taskId`. Есть working/episodic memory, которая не задаёт порядок пользовательских реплик. SRC-001, SRC-002, SRC-003, SRC-007 | Нельзя обещать продолжение разговора, изоляцию личного контекста и восстановление ответа после перезапуска. Нужен отдельный контракт диалога поверх существующих runs | FB-003, FB-004, FB-005 |
| Только вебхук для запуска | Есть `POST /api/v1/runs`, `POST /api/v1/processes/:id/start`, process webhook start/signal и A2A. Есть чтение статуса и SSE. У A2A предусмотрен push; универсальный result callback для runs/process instances в проверенных маршрутах не найден. SRC-003, SRC-004 | Проблема в доставке результата и identity вызывающей интеграции. Вебхук не является единственным входом | FB-002, FB-004; переиспользовать существующий API |
| Только HTTP tools и нет исполнения | MCP принимает `http`, `wasi`, `container`; HTTP использует Streamable HTTP. WASI/OCI исполняются через Kubernetes sandbox и общий policy/approval путь. `stdio` не входит в допустимые transports. OCI получает фиксированный exec-array и временный каталог, но нет подтверждённого workspace/Git/терминального контракта агента. SRC-005, SRC-006 | Изолированные tools уже есть. Не хватает подключения существующих STDIO-серверов и рабочего окружения для последовательности edit/test/diff. Выдача shell на host worker расширила бы полномочия в обход нынешней границы | FB-006, FB-007, FB-008; CON-001 |
| Подключение моделей | Worker поддерживает base URL, Bearer key, `/chat/completions` и Ollama discovery. В локальном roadmap CLD-001–CLD-005 и model/tools часть UX-207 запланированы в P0; внешний A2A performer CLD-006 — P1. SRC-002, SRC-008 | Ручной endpoint — техническая совместимость, но не готовое продуктовое подключение: нет доказанного пути registry → модель → агент → запуск с безопасным хранением ключа и бюджетом | Уточнить существующие CLD-001–CLD-005, UX-207; не создавать второй model registry |
| Зрелость и права | В OIDC-режиме роли и project scope проверяет `authorize()` coordinator. При выключенном OIDC он возвращает `local-admin`; проверки индивидуальных ролей в этой ветке нет. Admin-token запрашивается только для маршрутов с соответствующим флагом. SQLite и OIDC поддерживаются раздельными настройками. SRC-009, SRC-010 | Лёгкий режим годится для доверенного локального оператора, но не даёт индивидуального разграничения и accountability. Наличие Keycloak/Kong/Temporal/PostgreSQL само по себе не доказывает зрелость | FB-001, FB-002, FB-009; отдельная квалификация production сохраняется |

Уверенность высокая для перечисленных веток кода и статусов локальных задач; средняя для отсутствия адаптеров по дереву и целевым исходникам. Отсутствие найденного кода не доказывает отсутствие внешних интеграций за границами проверенных источников.

## Рекомендуемая целевая архитектура TO-BE

### Диалог и доставка результата

Адаптер проверяет подлинность входящего события, сохраняет его в inbox и сопоставляет внешнего пользователя с внутренним субъектом и проектом. Имя пользователя, `chat_id` и текст сообщения сами по себе прав не дают. Чатовая установка действует как service principal с ограниченными разрешениями; операции выполняются от проверенного участника либо запрещаются.

Хранилище coordinator становится источником истины для `conversation`, `turn`, `runId`/`instanceId` и состояния доставки. Новая реплика создаёт новый run с ограниченным снимком разрешённой истории. A2A `contextId`, embedding HTTP session, working memory и checkpoint агента сохраняют свои значения и не объявляются одной сущностью «сессия». Порядок реплик и конкурентные обращения регулирует код, а не модель.

Результат сначала сохраняется вместе с записью outbox, затем доставляется адаптеру или разрешённому callback-получателю. Статус выполнения и статус доставки различаются. Polling/status остаётся источником восстановления; callback не заменяет его. После timeout исходящей записи результат считается неопределённым до сверки, если канал не обеспечивает безопасный повтор. A2A push переиспользуется там, где подходят его контракты; новая очередь не дублирует бизнес-оркестрацию.

### Инструменты и рабочий каталог

Для ближайшего developer-сценария нужен `workspaceId`, закреплённый за проектом, субъектом и run/сессией, с изолированным filesystem, лимитами ресурсов и управляемым временем жизни. Чтение, изменение файлов, исполнение команды и Git — отдельные операции MCP с явными scopes. Недоверенный код и данные репозитория проходят через тот же policy/audit путь, что остальные tools.

STDIO реализуется управляемым bridge: на одной стороне стандартный MCP-клиент, запускающий subprocess, на другой — существующая gateway-граница Агата. Запуск сервера разрешает администратор по version/digest и exec-array; модель получает только разрешённые schemas. Это расширение текущей изоляции. Переиспользование WASI/OCI означает сохранение контролей, а не достаточность текущего одноразового Job для долгоживущего MCP-процесса. Транспортный стандарт действительно предусматривает STDIO; ограничение HTTP является выбором Агата. [Спецификация MCP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).

В первом developer-срезе результат — patch, test evidence и diff на копии репозитория с зафиксированным commit. Внешний push/PR, CI и release входят в отдельную задачу FB-010. Это позволяет проверить пользу выполнения кода до выдачи внешних write-полномочий.

### Модели и лёгкий режим

CLD-001 остаётся владельцем реестра подключений; UX-207 — пользовательского интерфейса. Локальные модели, custom endpoint и OpenRouter должны использовать общий binding/revision/capability contract. Модель выбирается для требуемых tools/schema/context, а фактические provider/model/usage попадают в manifest. Поддержка chat API не означает поддержку embeddings или любых provider-specific функций. OpenRouter документирует нормализованный API и отдельные настройки provider routing; их ограничения проверяются conformance suite, а не выводятся из совместимости URL. [API OpenRouter](https://openrouter.ai/docs/api_reference/overview), [Provider Routing](https://openrouter.ai/docs/guides/routing/provider-selection).

Предлагаемый лёгкий multiuser-профиль: coordinator, SQLite, обычный worker либо доверенный cloud executor, персональные субъекты и scoped service credentials. Он использует общий механизм авторизации с OIDC-профилем. Legacy single-user остаётся явно обозначенным локальным режимом. Kong — вариант ingress, Temporal — durable process runtime, PostgreSQL — масштабирование/HA state; их необходимость определяется выбранной возможностью. Если sandbox или durable runtime недоступен, preflight показывает ограничение и блокирует соответствующее действие.

Обработка токенов и сессий должна оставаться детерминированной. Локальные субъекты не маскируются под OIDC-учётные записи; критические four-eyes операции остаются закрытыми до отдельной квалификации требуемой идентификации. Production qualification OPS-101–OPS-105 сохраняется для выбранного контура.

## Варианты и статус решения

Это предложение к ближайшей реализации; технологические решения по локальной аутентификации, workspace executor и первому мессенджеру фиксируются в ADR на gate G0 задачи FB-001.

| Вариант | Результат и трудоёмкость | Эксплуатация и риски | Решение |
|---|---|---|---|
| Внешний glue над webhook/polling | Быстрый ручной бот без изменения ядра | Контекст, identity, повторы и доставка оказываются ответственностью каждой интеграции; пригоден как baseline | Использовать для сравнения и прототипа, не как целевой общий контракт |
| Адаптеры и session/delivery/workspace контракты поверх Агата | Потребуется расширение state и worker/gateway boundaries; сохраняются orchestration, policy и audit | Можно внедрять по срезам, включать флагами и откатывать адаптеры; нужны миграции и crash-тесты | Рекомендуется |
| Универсальный shell на worker и отдельный чатовый agent runtime | Раннее демо может быть простым, но появляется второй центр управления | Новые credentials, несогласованное состояние и большой радиус ошибки; цена эксплуатации не оценена | Не выбирать для ближайшей реализации; отдельный runtime допустим только с доказанной границей и пользой |

Финансовая оценка и staffing отсутствуют; размеры задач в бэклоге относительные. Отказаться от выбранного подхода или сузить срез, если не обеспечены изоляция субъектов, повторяемая доставка и прекращение выполнения при отзыве полномочий. Не заменять провал gate увеличением автономности или снятием policy.

## Переход и проверка готовности

1. G0: сверить локальный код и прежние задачи, закрепить deployment/capability contracts и владельцев. Подготовить тестовые аккаунты и ручной baseline.
2. G1: лёгкий multiuser, registry, data policy и budgets; затем проверенный OpenRouter/local-model сценарий.
3. G2: session/inbox/outbox и один адаптер; доказать «сообщение → ответ → уточнение → ответ» с перезапуском и разными пользователями.
4. G3: workspace, Git и STDIO; доказать «задача → изменение на копии repo → тесты → patch» без доступа к host и чужим данным.
5. G4: чистая установка/upgrade/restore/rollback и пилот; измерить пользу относительно ручного выполнения, учесть неуспешные случаи.

Функции включаются по отдельности. Отключение адаптера прекращает приём и доставку, сохраняя доступность уже принятых результатов. Отмена run и emergency deny останавливают дальнейшие tool calls. Старые runs и manifests остаются читаемыми; миграции state имеют проверенный backup/restore и план отката. Удаление временного workspace не удаляет уже принятые артефакты.

## Риски и открытые вопросы

| Вопрос или риск | Ответственный и gate | Необходимое доказательство и поведение до него |
|---|---|---|
| Exact implementation и фактические deployment возможности могут измениться до разработки | Backend + QA, G0 / FB-001 | Закрепить новые code/config versions и проверить capability matrix на настоящем окружении. Статический анализ текущего среза не заменяет runtime gate |
| Первый мессенджер и допустимость передачи данных | Product + владелец данных, G0 / FB-005 | В бэклоге рабочий кандидат Telegram, текстовые личные диалоги. Подтвердить реальный тестовый канал и данные; при запрете выбрать корпоративный канал с тем же core contract |
| Способ локального входа и assurance critical approvals | Backend + security, G0 / FB-002 | ADR и negative auth-тесты; до квалификации critical four-eyes в lite недоступен |
| Поддерживаемый runtime для workspace/STDIO в лёгкой установке | Runtime + DevOps, G0/G3 / FB-006–FB-008 | PoC на целевых ОС с реальной изоляцией process/filesystem/network; отсутствие backend даёт blocked capability |
| Дубликат внешнего ответа после timeout | Integration + QA, G2 / FB-004 | Инъекция сбоя после внешнего успеха, до local ack; доказанная дедупликация/сверка либо видимый unknown delivery |
| Staffing, сроки, performance и стоимость | Product + технические владельцы, до планирования итерации / FB-009 | Назначенные люди, оценка по spike и согласованные latency/cost limits; относительные S/M/L не являются сроками |

Build-ответственные приведены в карточках задач; конкретные владельцы продукта, данных, эксплуатации и инцидентов назначаются на G0. Release owner допускает соответствующий срез только после QA evidence, data owner определяет retention и разрешённые внешние назначения. Эта работа не назначает людей и не подтверждает production readiness.

## Источники и доказательства

Кодовые GitHub-ссылки закреплены на локальном HEAD `78d4cfba1387cf41f59c2965cefbaec3f0c3820e`; рабочие файлы проверены отдельно. Все источники прочитаны 30 октября 2026 года. Это статические доказательства, не наблюдение запущенного окружения. Внешняя документация прочитана на ту же дату; конкретная MCP revision и provider behavior перепроверяются при реализации.

| ID | Источник | Что подтверждает |
|---|---|---|
| SRC-001 | [Дерево репозитория](https://api.github.com/repos/Solutions-of-Security/Agat/git/trees/8b697d5f888ed74273f2ca05b4351911c0d3e995?recursive=1) | Проверенный набор путей; tree не усечён. Имена session в embedding/qualification не считаются чатовым адаптером |
| SRC-002 | [Рабочий worker](../workers/agat_worker.py); HEAD: [messages](https://github.com/Solutions-of-Security/Agat/blob/78d4cfba1387cf41f59c2965cefbaec3f0c3820e/workers/agat_worker.py#L769), [model HTTP](https://github.com/Solutions-of-Security/Agat/blob/78d4cfba1387cf41f59c2965cefbaec3f0c3820e/workers/agat_worker.py#L1797), [discovery](https://github.com/Solutions-of-Security/Agat/blob/78d4cfba1387cf41f59c2965cefbaec3f0c3820e/workers/agat_worker.py#L2340) | Рабочие messages 798–801, chat endpoint 1838, discovery 2353. Контекст этапа, OpenAI-compatible chat и Ollama; `subprocess.run` для sysctl не является tool терминала агента |
| SRC-003 | [Рабочий server](../apps/coordinator/src/server.ts); HEAD: [webhook](https://github.com/Solutions-of-Security/Agat/blob/78d4cfba1387cf41f59c2965cefbaec3f0c3820e/apps/coordinator/src/server.ts#L729), [start](https://github.com/Solutions-of-Security/Agat/blob/78d4cfba1387cf41f59c2965cefbaec3f0c3820e/apps/coordinator/src/server.ts#L1989), [runs](https://github.com/Solutions-of-Security/Agat/blob/78d4cfba1387cf41f59c2965cefbaec3f0c3820e/apps/coordinator/src/server.ts#L2269) | Webhook start/signal, авторизованные start/runs, status/SSE |
| SRC-004 | [A2A request validation](https://github.com/Solutions-of-Security/Agat/blob/78d4cfba1387cf41f59c2965cefbaec3f0c3820e/apps/coordinator/src/a2a.ts#L499) | `contextId`, запрет продолжения `taskId`, push configuration |
| SRC-005 | [MCP input](https://github.com/Solutions-of-Security/Agat/blob/78d4cfba1387cf41f59c2965cefbaec3f0c3820e/apps/coordinator/src/mcp.ts#L192), [dispatch](https://github.com/Solutions-of-Security/Agat/blob/78d4cfba1387cf41f59c2965cefbaec3f0c3820e/apps/coordinator/src/mcp.ts#L474) | Допустимые transports и передача isolated вызовов executor |
| SRC-006 | [Sandbox Job](https://github.com/Solutions-of-Security/Agat/blob/78d4cfba1387cf41f59c2965cefbaec3f0c3820e/apps/coordinator/src/sandbox.ts#L301), [isolated tools](https://github.com/Solutions-of-Security/Agat/blob/78d4cfba1387cf41f59c2965cefbaec3f0c3820e/docs/isolated-tool-execution.md) | Exec-array, ограничения контейнера, Kubernetes boundary и default-off в Compose |
| SRC-007 | [Working и episodic memory](https://github.com/Solutions-of-Security/Agat/blob/78d4cfba1387cf41f59c2965cefbaec3f0c3820e/docs/local-rag-and-memory.md#L157) | Явная память с lifecycle; не доказательство conversational session |
| SRC-008 | [Локальный roadmap](./roadmap.md), [бэклог от 17 октября](./agent-evolution-backlog-2026-10-17.md) | Существующие ID и статусы; CLD-006/P1; границы SAFE-101/EVAL-103/MEM-101/RUN-105/UX-308. Приоритеты актуализированы 30 октября |
| SRC-009 | [Рабочий authorize](../apps/coordinator/src/server.ts), [auth](../apps/coordinator/src/auth.ts); HEAD: [authorize](https://github.com/Solutions-of-Security/Agat/blob/78d4cfba1387cf41f59c2965cefbaec3f0c3820e/apps/coordinator/src/server.ts#L282), [localAdminContext](https://github.com/Solutions-of-Security/Agat/blob/78d4cfba1387cf41f59c2965cefbaec3f0c3820e/apps/coordinator/src/auth.ts#L190) | Рабочая authorize 283–317, localAdminContext 190–199. Разделение OIDC и legacy local-admin, контроль ролей и проектов |
| SRC-010 | [Identity и gateway](https://github.com/Solutions-of-Security/Agat/blob/78d4cfba1387cf41f59c2965cefbaec3f0c3820e/docs/identity-and-gateway.md), [config](https://github.com/Solutions-of-Security/Agat/blob/78d4cfba1387cf41f59c2965cefbaec3f0c3820e/apps/coordinator/src/config.ts#L309) | SQLite/OIDC, four-eyes и отдельные настройки state/runtime |
| SRC-011 | SHA-256 рабочего среза: server `83fdad0ad369068f35664d2f1adfa5f413b968de4a62cb5e09a4a070d5a00c88`; worker `24b617f0a000d163edeb8869919fefc2aa2b56a8353cdbab00d215ab9f6ff9fe` | Воспроизводимость проверки изменённых относительно HEAD файлов. Auth/MCP/sandbox/A2A/config совпали с HEAD по Git blob hashes |

## Проверка документов

Roadmap — единственный источник приоритетов; новый бэклог раскрывает 11 карточек: 9 P0 и 2 P1. Сохранены все 99 ранее упомянутых ID и без изменения содержание между разделами «История реализации» и «Полная карта приоритетов». Прежние CLD, SAFE/EVAL, RUN-105, UX-308 и MEM-101 не продублированы новыми карточками с тем же результатом.

Проверены уникальность FB-ID, наличие размеров/зависимостей/приёмки/отката, отсутствие циклов среди новых обязательных зависимостей, соответствие строк задач общей карте, Markdown-таблицы, локальные ссылки и whitespace. Архитектурный audit для analysis/roadmap и структурная проверка карточек проходят без ошибок и предупреждений. На этапе подготовки анализа программный код не менялся. Последующая техническая реализация и её проверки учитываются отдельными протоколами; stochastic eval и пилот остаются gates включаемых возможностей.
