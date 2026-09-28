# RAG через Temporal: retry, restart и history replay

Дата: **28.09.2026**. Продолжение [deployment настроек embedding](./embedding-deployment-settings.md). Добавлен воспроизводимый сквозной сценарий с настоящим Temporal dev server, собранными coordinator/Temporal worker и обычным Python worker. Production runtime и defaults не менялись.

## Проверяемый путь

[Тест](../../../../apps/coordinator/test/temporal-rag.integration.test.ts) поднимает собственный SQLite store и HTTP endpoints. Python worker индексирует два [авторских учебных документа](./rag-workflow.fixture.json), затем выполняет три агентных шага с retrieval и shadow. Процесс запускается через штатный HTTP API coordinator с `runtime=temporal`; перед агентными шагами вставлено пятисекундное durable wait. Модельные ответы и векторы детерминированно задаются локальным HTTP fixture, а shadow использует настоящий DecisionEngine с тестовым scorer. Это проверка интеграции, не качества модели.

Сценарий повторяется для `isolated` и `session` embedding transport:

1. Loopback proxy получает успешный ответ настоящего `/internal/processes/:id/tick`, полностью читает его и закрывает соединение, не передав подтверждение Activity. History должна содержать успешную **вторую попытку** Activity, затем `TimerFired` до первого model call. Нельзя засчитать только повтор HTTP-запроса или сообщение в логе.
2. Второй primary model response удерживается, когда первый результат уже записан. Первый Temporal worker завершается через **SIGKILL**. Запускается новый процесс с другой identity; query к существующему Workflow ID требует восстановления его состояния.
3. После освобождения model response процесс завершается. В history проверяются обе worker identities, Activity retry и `TimerFired`.
4. В каждом режиме остаются ровно **3 primary-вызова**, **5 embedded inputs** (два документа и три query), **3 shadow-наблюдения** с primary fallback и **6 источников**. SHA документов/фрагментов, маркеры, результаты и отсутствие повторного model execution проверяются независимо от Temporal history.
5. History переводится в стандартный JSON через SDK `historyToJSON`, читается обратно и передаётся в `Worker.runReplayHistory`. Счётчики model/embedding/Activity HTTP и полный trace БД должны остаться прежними. Штатный drain Python worker, replacement Temporal worker и coordinator обязан закончиться кодом 0.

Это следует из контрактов Temporal: [Activity может исполняться повторно при потере подтверждения, поэтому должна быть идемпотентной](https://docs.temporal.io/activity-definition#idempotency); [history replay проверяет совместимость workflow и рекомендован для CI](https://docs.temporal.io/develop/typescript/best-practices/testing-suite#how-to-replay-a-workflow-execution). Обработчики Activities и workflow здесь не подменены. Test server работает в обычном времени, без time skipping.

## Evidence и регрессия

[Isolated](./evidence/2026-09-28/temporal-rag/final/isolated.json) и [session](./evidence/2026-09-28/temporal-rag/final/session.json) содержат исходные trace, Temporal history, принятые ответы tick, SHA model inputs и полные тестовые outputs. [Интеграционный лог](./evidence/2026-09-28/temporal-rag/verified-integration.log) сохраняет оба результата. Две полученные history также добавлены в обычные [replay fixtures](../../../../apps/temporal-worker/test/fixtures/), поэтому их проверка выполняется без живого Temporal при `npm test`.

В экспортируемых history клиентские identity заменены на `fixture-client`, чтобы не сохранять hostname машины. Две заданные тестом worker identity, последовательность событий, timestamps и payloads учебного процесса сохранены. Offline replay проверяет уже такую экспортированную history.

Первый экспорт использовал обычный `JSON.stringify` protobuf-объекта: прямой in-memory replay прошёл, но повторное чтение файла выявило несовместимое представление Timestamp. Этот [неудачный offline replay](./evidence/2026-09-28/temporal-rag/initial/replay.log) и исходные данные сохранены. Финальный тест проверяет именно JSON round trip через штатный SDK serializer; исправление касается тестового экспорта, не рабочего workflow.

Первоначальный wait после второго агента иногда успевал истечь до обработки очередного Update, поэтому завершался через Activity tick без `TimerFired`; [непройденная проверка сохранена](./evidence/2026-09-28/temporal-rag/initial/timer-race.log). Увеличение интервала само по себе [не устранило гонку теста](./evidence/2026-09-28/temporal-rag/final-integration.log). Финальная fixture ставит пятисекундный wait перед агентами: до первого lease completion нет конкурирующих Updates. Проверка `TimerFired` сохранена, production timer logic не менялась.

[Итоговые проверки и hashes](./evidence/2026-09-28/temporal-rag/checks.json) закрепляют исходный commit, версии и проверенные файлы. Полный coordinator-набор: **284 pass, 2 skip**; два skip — новые gated-сценарии, которые отдельно выполняются с настоящим сервером. Temporal config и все пять сохранённых histories проверяются отдельно. В CI добавлен обязательный integration job `temporal-rag`; он использует собственный контейнер с loopback port и удаляет его через EXIT trap.

```bash
npm run fleet:test-temporal-rag
npm run test:temporal
npm run test:coordinator
```

Нужны Node.js 24, Python 3 и Docker. Launcher закрепляет образ `temporalio/temporal:1.8.1`. Для нового evidence set задайте `AGAT_TEMPORAL_RAG_EVIDENCE_DIR` на новый каталог внутри `docs`; существующие JSON-файлы не перезаписываются. Состояние теста, дочерние процессы и его контейнер удаляются при успехе и при assertion failure. Пользовательские deployments не затрагиваются.

## Границы

Это два контролируемых процесса на локальном SQLite с учебными model responses. Долгая нагрузка, PostgreSQL вместе с Temporal, живые weights, независимые бизнес-источники, многопроектная fairness и production SLO здесь не измерялись. Таймеры ожидания теста ограничивают зависание assertion, а не обещают время восстановления сервиса. Неизменность конкретного trace после replay не означает exactly-once для любых внешних side effects.

Следующий инженерный gate — потеря подтверждения **создания процесса по расписанию** (`startScheduledProcess` / `scheduled-start`). Этот endpoint создаёт новый instance и требует отдельной проверки идемпотентности при Activity retry. Маршрутизация автоматических решений и предметная qualification остаются выключенными/открытыми согласно исходному плану.
