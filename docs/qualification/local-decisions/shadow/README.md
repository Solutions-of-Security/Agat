# Локальные решения в worker: shadow-профили

26.09.2026. Runtime подключён к существующим agent leases; текущая версия — **0.12.0**, исторические свидетельства ниже сохраняют свои профили. Включение требует трёх настроек: разрешения coordinator, локального URL worker и опубликованной конфигурации конкретного агентного шага. Основной агент продолжает выполнять задачу; shadow-ответ сохраняется как наблюдение с `fallback: primary`.

Реальный сквозной прогон через Python worker, HTTP coordinator и локальный `Mapika/decider-2b` завершился **`integration_pass`**. [Свидетельство после добавления контекста в trace](./evidence/2026-09-26/real-mlx-trace-context.json) содержит SHA исходников интеграции, identity модели, logits и результат. [Исходный протокол проверок](./evidence/2026-09-26/verification.json), [дополнение PostgreSQL](./evidence/2026-09-26/verification-postgres.json) и [проверка интерфейса](./evidence/2026-09-26/verification-ui.json) фиксируют тесты и ограничения. Основной chat-completions endpoint в MLX-прогоне — явно обозначенная тестовая заглушка. Это доказательство интеграции, статус качества модели — `not_assessed`.

В runtime `0.5.0` отдельно прошли [Score v2](./evidence/2026-09-26/real-mlx-score-v2.json) и [совместимость прежнего Choice](./evidence/2026-09-26/real-mlx-choice-runtime-0.5.json). В числовом примере выбран уровень `two`, его вероятность — 0.95981448, взвешенное среднее — 0.6535024448554396, 87 токенов, 658.897 мс. Оба запуска сохранили `PRIMARY_BRANCH`. [Протокол Score](./evidence/2026-09-26/verification-score-v2.json) закрепляет хеши и проверки: 203 coordinator, 52 frontend, 44 worker (1 skip), 69 runtime (3 skip), Temporal/replay, process pack, сборка, typecheck, PostgreSQL 6/6 и browser 2/2 — без ошибок. Пропущены необязательные unittest для LangGraph/MLX; фактический MLX выполнен отдельными smoke. Экран Score проверен на [desktop](./evidence/2026-09-26/ui-desktop-score-v2.png) и [mobile](./evidence/2026-09-26/ui-mobile-score-v2.png).

[Runtime 0.6.0 с cache 128 МиБ](./evidence/2026-09-26/real-mlx-runtime-0.6-cache128.json) также прошёл реальный Score smoke. Параметр `allocatorCacheLimitBytes: 134217728` включён в модельный профиль, основной маршрут сохранён. [Дополнительный протокол](./evidence/2026-09-26/verification-runtime-cache.json) фиксирует 70 runtime-тестов (3 необязательных skip), 15 shadow/HTTP-тестов coordinator и ресурсные проверки без ошибок.

В runtime `0.7.0` добавлен [отдельный процесс MLX с серверным deadline](./isolation.md). В `0.7.1` тот же режим доступен в offline-командах; [реальный калибровочный путь](../calibration/isolated.md) также проверен. Проверены успешный Score и реальный timeout через worker/coordinator, остановка вычислительного процесса, последующий отказ и сохранение primary-маршрута.

В `0.10.0` [проверено завершение отказавшего сервиса и foreground recovery](./service-recovery.md); подготовлен launchd renderer. Нативный LaunchAgent на этом хосте ожидал доступа macOS к Documents до инициализации runtime; автоматический restart ещё не подтверждён. Все временные службы опыта удалены.

В `0.11.0` [отмена после ухода клиента](./cancellation.md) останавливает изолированный inference по явному opt-in заголовку worker. Реальные MLX и сквозной coordinator smoke проверили клиентский timeout, завершение процесса и сохранение `PRIMARY_BRANCH`; 105 runtime-тестов и полная регрессия прошли.

## Контракт и привязка версий

`local_decision_shadow_v1` поддерживает **Choice и Boolean**, `local_decision_shadow_v2` добавляет **Score**. Новый worker принимает оба профиля. В существующий `config` агентного узла добавляется `decisionShadow`:

```json
{
  "mode": "shadow",
  "profileJson": "<точная строка profileJson из GET /health локального runtime>",
  "timeoutMs": 1000,
  "kind": "boolean",
  "question": "Подтверждён ли запуск исходными данными?",
  "options": [
    {"id": "yes", "description": "Подтверждён", "value": true},
    {"id": "no", "description": "Не подтверждён", "value": false}
  ]
}
```

`profileJson` закрепляет schema/runtime, полную model identity, tokenizer, prompt, реализацию, policy и calibration. Сохраняется именно строка из health: её SHA-256 передаётся в `X-Agat-Decision-Profile`; сервер отвергает несовпадение до вычисления. Такой способ сохраняет исходное представление чисел Python, в том числе температуры. При смене реализации или калибровки профиль нужно заново проверить и опубликовать.

`state` формирует coordinator из фактического входа этапа: предыдущего результата процесса, либо исходного run input для первого шага. Контекст других этапов, RAG и память автоматически в него не добавляются. При необходимости подготовьте вход узлом `transform`. `id` — ID этапа, а fingerprint входа включает вопрос, тип, порядок, описания и свойства вариантов. Coordinator независимо рассчитывает этот fingerprint и проверяет ответ.

Кандидаты ограничены 2–10 вариантами, состояние — 24 000 символов и 128 КиБ JSON. Превышение не обрезается. Для **Score** используются `kind: "score"` и различные конечные числовые `value` у всех уровней. Профиль runtime должен объявлять `binary64-v1`; coordinator создаёт lease v2 с этой версией fingerprint. [Контракт чисел и совместимости](./score-contract.md) описывает канонизацию, проверку взвешенного среднего и поведение старых workers. Неизвестные поля, режим маршрутизации и настройки endpoint внутри process config отклоняются.

## Включение

Из корня репозитория запустите уже установленную модель:

```bash
.venv/decision/bin/python -m decision_runtime serve \
  --manifest .local-models/decisions/decider-2b.json \
  --policy docs/qualification/local-decisions/policy.shadow.v1.json \
  --port 8766
```

Разрешите профиль на coordinator и настройте worker на **своём** узле:

При ограниченном бюджете памяти к `serve` можно добавить `--cache-limit-mib 128`; [ресурсные опыты](../performance/resources.md) описывают границы этой настройки. Получайте `/health` после выбора параметра: изменение cache меняет закреплённый профиль.

```bash
AGAT_DECISION_SHADOW_ENABLED=true npm run dev:api

python3 workers/agat_worker.py \
  --enrollment-token "$AGAT_ENROLLMENT_TOKEN" \
  --models qwen3:8b \
  --decision-url http://127.0.0.1:8766 \
  --no-web
```

Альтернатива флагу worker — `AGAT_DECISION_URL`. Он принимает только `http://127.0.0.1:<port>`. Вызов не использует системные HTTP proxy, DNS или redirects. В контейнере loopback означает сам контейнер, поэтому runtime должен быть доступен в том же сетевом пространстве.

Получите `profileJson` через `GET http://127.0.0.1:8766/health`, вставьте его в [форму «Локальная проверка»](./editor.md) агентного шага и опубликуйте версию. Существующий API процессов с `config.decisionShadow` также поддерживается. Версия процесса и вход этапа фиксируют конфигурацию; LLM не может менять пороги или адрес сервиса.

По умолчанию coordinator выключает профиль. Worker без локального URL получает обычный lease. Наличие shadow не меняет выбор основной модели, поэтому отсутствие capability регистрируется как `unsupported_worker`, а основной шаг выполняется штатно.

## Проверка, fallback и хранение

Worker сначала получает основной ответ, затем выполняет ограниченную shadow-проверку и отправляет наблюдение в `POST /api/v1/leases/:leaseId/decision-shadow`. Право записи определяется worker token и действующим lease. Чужой узел, отменённый, устаревший или истёкший lease не может сохранить результат. Истёкшую аренду нельзя оживить поздним renew.

Coordinator проверяет полный профиль, request ID/fingerprint, состав и порядок распределения, конечные logits и softmax, выбранный вариант, пороги, abstain и тип значения. Boolean `false` сохраняется как полноценный ответ; для Score независимо пересчитывается взвешенное среднее. Неверный ответ превращается в `unavailable / invalid_response`; его сырое содержимое не сохраняется. `ok`, `abstain`, `error` и транспортная недоступность сохраняют основной output и маршрут.

Наблюдение записывается один раз в `stages.activity_json`, в отдельной транзакции до завершения основного этапа. PostgreSQL блокирует строку этапа при записи и completion. Повтор отправки возвращает ранее записанное наблюдение. После restart/retry завершённая shadow-проверка не повторяется. Если worker потерял ответ до записи, остаются штатные ограниченные попытки этапа; отдельной бесконечной очереди inference нет.

Safe replay сопоставляет исходный этап и номер посещения узла, проверяет input и profile SHA, затем переносит сохранённое наблюдение с `reusedFromStageId`. Если вход изменился или результат отсутствует, сохраняется `safe_replay_unavailable`; новый inference не запускается. Live replay создаёт новый эксперимент и новое наблюдение.

Результаты доступны в `decisionObservations` ответа `GET /api/v1/runs/:runId/trace` и в execution manifest. Они хранятся в том же проекте и удаляются вместе с этапом/run. В событие `decision.shadow` попадают status, reason, fingerprint и короткие метаданные, без state, question, descriptions и внутренних exceptions. Обычный trace Агат по своей прежней политике может содержать вход основного этапа.

| Ситуация | Поведение |
|---|---|
| Профиль выключен / worker без capability | `disabled` / `unsupported_worker`, основной шаг продолжается |
| Длинный или некорректный вход | `invalid_input`, без скрытой обрезки |
| Runtime занят | `busy`, без очереди и автоматического локального retry |
| Смена модели/policy/calibration | `profile_mismatch`, до inference |
| Таймаут, отказ соединения, неверный JSON | `timeout`, `unreachable`, `invalid_response` |
| Worker не передал observation | `missing_result` при completion |
| Safe replay не может подтвердить прежний вход | `safe_replay_unavailable`, без нового inference |

`timeoutMs` ограничен 100–10 000 мс. Watchdog ограничивает полное время локального HTTP-вызова, включая медленное чтение ответа. Проверка lease перед ним и отправка observation имеют сетевой timeout по 5 секунд. Отмена закрывает клиентское соединение; coordinator отвергает позднюю запись. С `0.11.0` worker передаёт явный [opt-in отмены](./cancellation.md), и изолированный backend останавливается при EOF/reset. Прямой backend и старые клиенты без opt-in могут продолжать уже начатый forward. `serve --inference-timeout-ms` задаёт независимый серверный предел; после остановки требуется restart. [Контракт изоляции](./isolation.md) различает клиентский timeout, серверный deadline и завершение процесса. Shadow добавляет ограниченную задержку и нагрузку на worker — это надо учитывать при измерении SLO.

## Воспроизведение и проверенные границы

### Просмотр в интерфейсе

Откройте запуск → «Технические детали» → «Ход и логи». Для этапов с наблюдениями отображаются вопрос, статус, ответ, вероятность выбранного варианта, время runtime и вид калибровки. «Распределение и профиль» раскрывает описания вариантов, logits, пороги и закреплённые версии. Отрицательный Boolean показан как «Нет (false)», abstain — как «Решение не принято»; недоступные значения не заменяются нулями. Старые запуски без наблюдений не получают пустую панель. Контекст берётся из сохранённой конфигурации этапа и доступен в пределах ACL запуска.

Проверены [desktop](./evidence/2026-09-26/ui-desktop-decision-shadow-viewport.png) и [mobile](./evidence/2026-09-26/ui-mobile-decision-shadow-viewport.png), клавиатурное раскрытие, длинные SHA и неизменность основного результата. Browser-тест подставляет явно тестовые observations в реальный trace временного запуска; это проверка отображения. Реальный Python worker/MLX проверяется отдельным smoke. Протокол: 51/51 frontend, 199/199 coordinator, 2/2 browser и сборка workspace — pass; это не сертификация доступности интерфейса.

### Команды

```bash
node --import tsx --test apps/coordinator/test/local-decisions.test.ts
node --import tsx --test apps/coordinator/test/local-decisions-http.test.ts
npm run test:worker
npm run test:decision
npm run fleet:test-ha-postgres

npm run qualify:decision-shadow -- \
  --url http://127.0.0.1:8766 \
  --request docs/qualification/local-decisions/request.example.json \
  --output docs/qualification/local-decisions/shadow/evidence/new-run/real-mlx.json
```

Последняя команда создаёт временные coordinator, SQLite и primary fixture, запускает настоящий Python worker и обращается к указанному decision runtime. Она проверяет выбор исходной ветки `PRIMARY_BRANCH`, одиночную запись и отсутствие state в shadow-событии. Файлы доказательств не перезаписываются, временные сервисы и база убираются после прогона. Встроенный CI-тест использует тот же путь с явно тестовым backend без MLX.

В [первом реальном прогоне](./evidence/2026-09-26/real-mlx.json) decider обработал 117 токенов, вернул `ok / supported`, `selectedProbability = 0.985172`, время runtime — **868.439 мс**. Повтор с SHA исходников дал тот же ответ за **1636.167 мс**. Это два исполнения одного интеграционного примера; они не определяют p95, калибровку или частоту ошибок. Девять отложенных случаев предметного корпуса не запускались.

Проверяются подмены identity/distribution, отрицательный Boolean, abstain, deadline, cancellation, busy, redirects/proxy, дублированный JSON, сохранность после restart, retry, safe/live replay, ACL и сохранение обязательного approval. PostgreSQL использует существующий формат `activity_json`, схема и миграционный номер не меняются.

Отдельный прогон на временном **PostgreSQL 17.6** завершился **6/6 pass**: два экземпляра coordinator видят одно observation, повторная запись идемпотентна, закрытие и пересоздание первого экземпляра сохраняет результат, завершение через второй оставляет `PRIMARY OUTPUT`. Проверены project scope и реальная tenant RLS. Decision backend в этом PostgreSQL-тесте — явно тестовый; модель отдельно проверена MLX smoke. Это проверка нескольких экземпляров приложения на одной БД, не испытание отказа PostgreSQL primary или production HA.

Прогон обнаружил существовавшую ошибку `listAgents`: PostgreSQL не принимал поля присоединённых prompt-таблиц при `GROUP BY a.id`. Добавлены первичные ключи `prompt_registry` и `prompt_versions`, как требует [правило функциональной зависимости PostgreSQL](https://www.postgresql.org/docs/17/sql-select.html#SQL-GROUPBY). После исправления прошли все 199 тестов coordinator и повторный реальный MLX smoke: 117 токенов, тот же ответ `supported`, 838.499 мс.

## Основание решения и дальнейшая работа

Из [официальной документации Temporal](https://docs.temporal.io/activity-execution) использованы требования учитывать повтор Activity и потерю ответа, а также доставку cancellation через heartbeat. Из [практики SageMaker shadow testing](https://docs.aws.amazon.com/sagemaker/latest/dg/model-shadow-deployment.html) — наблюдение за альтернативной моделью при сохранении основного ответа. Проектное решение Агат: локальная копия входа с собственным deadline и сохранённым наблюдением в существующем lease; нового deterministic Workflow здесь нет.

Открыты независимые экспертные данные и qualification, сравнение с предметными правилами, подключение постоянного мониторинга и измерение производственной нагрузки. [Генеративный baseline](../baselines/README.md) уже измерен отдельно; остановка изолированного процесса при отмене проверена в `0.11.0`. Автоматическая маршрутизация потребует отдельной предметной приёмки.

В `0.12.0` реализованы [readiness, `/metrics` и проверенные примеры alerts](./observability/README.md). Экспорт не включает входные тексты и не смешивает busy с latency вычислений. Постоянный scraper и получатели уведомлений остаются задачей эксплуатации.

[Отмена через измерительный прокси](./proxy-cancellation.md) также проверена на реальном MLX: worker timeout 100 мс привёл к завершению процесса через 196.611 мс от старта вызова. Это исправление workload-harness; новая производственная сетевая служба не устанавливалась.
