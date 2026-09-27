# Локальная проверка в готовности сценария

Дата: **27.09.2026**. После [визуального редактора](./editor.md) добавлены сведения о shadow в существующий preflight опубликованной версии. Пользователь видит состояние проверки до запуска, а основной агент сохраняет прежние правила допуска и назначения.

## Поведение

На вкладке «Готовность» для агентных шагов с `decisionShadow` показываются неблокирующие сообщения:

| Состояние | Значение |
|---|---|
| Флаг coordinator выключен | Shadow не будет вызван; основной шаг работает по обычным правилам |
| Нет подходящих свободных workers | Основному шагу по-прежнему нужны его обычные runtime/model/capacity условия |
| Ни один подходящий worker не заявил capability | Primary может выполнить шаг с `unsupported_worker` вместо локального наблюдения |
| Capability есть у части workers | Показаны числитель и знаменатель; назначение на worker без capability остаётся допустимым |
| Capability заявлена всеми текущими кандидатами | Известна только заявленная поддержка протокола; доступность runtime и совпадение pinned profile выясняются при вызове |

Choice/Boolean допускают `local_decision_shadow_v1` и v2; Score требует v2. Проверка использует тот же `supportsDecisionShadow`, что выдача lease. Публикация или обновление capability не подтверждают качество модели. Общая подсказка прямо отделяет успешное выполнение основного сценария от качества локальных проверок.

## Границы выбора

Список берётся из существующей проверки primary: свежесть heartbeat и worker trust, активные credentials, rollout/region, свободные слоты, runtime/профиль агента, model pin и модели команды, embeddings, ограничения scheduler и model router. При включённом router учитывается его **текущий выбор** для первой попытки; shadow не меняет этот выбор. Прежний поиск первого подходящего результата `some` заменён эквивалентным `find`, чтобы использовать уже найденный worker без повторного обхода.

Проверка относится к закреплённой опубликованной версии, включая вложенные процессы. Изменение черновика не меняет сведения прежней версии. Состояние workers может измениться после preflight; права и доступность по-прежнему проверяются при выполнении. Адрес runtime, health и точный profile SHA не запрашиваются через worker во время preflight. Сообщения не содержат вопроса, вариантов, сырого profile JSON или secrets.

Shadow добавляет только `notices`; `blockers`, `queueable`, `runnableNow`, fingerprint сценария и правила подтверждения завершённого основного сценария сохраняют свою семантику. Одинаковые сообщения удаляются перед ответом, чтобы несколько одноимённых шагов не создавали повторных React keys. Схема БД, протокол lease и API-форма preflight не меняются.

Разделение наблюдения и продвижения модели соответствует назначению [shadow variants SageMaker](https://docs.aws.amazon.com/sagemaker/latest/dg/model-shadow-deployment.html): кандидат проверяется до production promotion. Конкретный неблокирующий preflight и правила выбора workers здесь — решение Агат, основанное на его действующем primary fallback.

## Воспроизведение

```sh
node --import tsx --test apps/coordinator/test/decision-shadow-preflight.test.ts \
  apps/coordinator/test/scenario-preflight.test.ts \
  apps/coordinator/test/scenario-preflight-http.test.ts
npm run test:coordinator
npm run typecheck
bash scripts/test-fleet-ha-postgres.sh --test-name-pattern='persists one shadow observation'
npm run build --workspace @agat/coordinator
npm run build --workspace @agat/web
npx playwright test tests/browser/decision-shadow-editor.spec.mjs
npm run docs:check
```

Baseline `0464fa9b97a13bda4f5e50025f1bb3c4ac36d469` не возвращал этих сведений: семь новых сценариев завершились ожидаемым FAIL на отсутствии notices. После реализации целевые **22/22** и полная регрессия coordinator **255/255**, typecheck всех workspaces — pass. Матрица включает 12 сочетаний типа решения и capability, частичный rollout, неподходящие model/runtime, отозванные credentials, устаревший heartbeat, нагрузку, занятость, router, опубликованные версии и вложенные процессы. Реальная выдача lease проверяется независимо от текста notices.

Один расширенный сценарий на **PostgreSQL 17.6** подтвердил readiness через второй coordinator, повторное чтение после restart, project scope и прежний primary output. Это проверка нескольких экземпляров на одной БД; runtime ответа явно тестовый.

Browser plugin not available: используется Playwright репозитория, настоящий собранный coordinator, отдельная SQLite и Python worker `--dry-run`. API-ответы не подменяются. Все **4/4** browser-сценария прошли. Desktop/mobile проверяют notices, доступность основного запуска, сохранённый `disabled / primary`, обратный переход на схему, console/overlay и отсутствие горизонтального переполнения. Отдельный снимок показывает полную подсказку выше мобильной навигации. Первый прогон ошибочно ожидал, что переход на тот же hash сбросит активную вкладку; тест теперь проходит реальный обратный путь через «Схема» и выбор шага.

[Логи, SHA и итог](./evidence/2026-09-27/shadow-preflight/checks.json), [desktop](./evidence/2026-09-27/shadow-preflight/desktop-readiness.png), [mobile](./evidence/2026-09-27/shadow-preflight/mobile-readiness-notices.png). Это инженерная проверка готовности и fallback, без нового MLX измерения или предметной qualification.

Следующий инженерный gate — запись shadow-наблюдения при ожидании SQL-lock и истечении lease: проверить границу поздней записи и rollback по аналогии с [retrieval](../performance/retrieval-lease-write-fence.md), исправлять только подтверждённые дефекты.
