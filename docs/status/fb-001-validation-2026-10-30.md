# Приёмка FB 001

Дата: 30 октября 2026 года. Статус: **техническая реализация проверена**; обязательный Linux check `required` пройден, [PR 242](https://github.com/Solutions-of-Security/Agat/pull/242) смержен в main (`f6451ff83442767c3071d58f9fb62dfc61b7270b`). Scope — deployment contract v1, общий preflight/UI и запрет неявного identity fallback. Реальные cloud/chat/workspace функции этим статусом не объявляются реализованными.

## Реализовано

- Явные single-user/lite/durable/production-ha и auth/ingress настройки с fail-closed проверкой.
- GET `/api/v1/deployment`, POST `/api/v1/deployment/preflight`; тот же contract в overview, auth config и process preflight. UI показывает серверные причины без собственной матрицы доступности.
- Legacy loopback-оператор обозначен одним субъектом; удалённый клиент требует token даже на чтение. Некорректный admin/Bearer не игнорируется. В non-legacy профиле отсутствующий identity provider не включает local-admin.
- WASI/OCI отделены от ещё не реализованных workspace/STDIO; чаты/callbacks/cloud registry и HA qualification пока blocked.
- Принят [ADR 023](../adr-023-deployment-identity-and-integration-contracts.md). Конкретные real-data pilot owners и внешние аккаунты остаются rollout gate, а не вымышленными назначениями.

## Проверки

Среда: чистая копия актуального remote main `8b697d5f888ed74273f2ca05b4351911c0d3e995`, Node 25.8.0, npm lockfile; CI использует Node 24. Browser plugin not available — использован существующий Playwright workflow. URL: `http://127.0.0.1:8796`; desktop 1440×1000 и Pixel 7.

| Проверка | Результат и evidence |
|---|---|
| Typecheck и production build | PASS для coordinator/web/Temporal worker |
| Coordinator regression | 424 PASS, 28 инфраструктурных SKIP; после финального legacy guard 16 targeted auth/deployment/preflight checks PASS |
| Web unit suite | 79 PASS |
| Temporal worker/replay | Config tests и все сохранённые replay fixtures PASS |
| Полный browser suite | 34 PASS, включая новый profile и существующие first-run/report/knowledge flows на desktop/mobile |
| Scripts/docs suite | JS checker tests PASS; Python: 1532 cases, 4 SKIP, 2 Darwin process-group cleanup PermissionError в неизменённых tests. Обязательный Linux CI должен пройти до merge |
| Профильный UI | Page identity/not blank/no overlay/console/expand-collapse — PASS; текст доступности и причины совпали с API, horizontal overflow отсутствует |
| Снимки | `deployment-profile.png` на обоих viewports сохраняется в test-results и прикладывается к Playwright report/CI browser-smoke artifact |

Инфраструктурные SKIP локально не считаются PASS: PostgreSQL/Temporal/S3/DR integration jobs выполняет обязательный CI. Реальный IdP outage на выбранном production и HA/DR qualification остаются отдельными OPS gates. Синтетические API/UI тесты не подтверждают качество модели или разрешение передавать реальные данные.

## Совместимость и восстановление

State schema не меняется. Существующие OIDC и database runtime сохраняются; новые поля response additive. Неизвестные profile/auth значения не принимаются. При несовместимом профиле health/control API дают явный отказ. Rollback не удаляет старые результаты; multiuser по legacy token не включается.

## Подтверждение интеграции

[Verify run, attempt 2](https://github.com/Solutions-of-Security/Agat/actions/runs/40965504144/attempts/2): `test`, `browser`, все шесть integration suites и `required` — PASS. В первой попытке неизменённый active HTTP cancellation test не дождался fixture за одну секунду; изолированный набор 14/14 и повторный полный Linux job прошли без ослабления assertions или веточной защиты.
