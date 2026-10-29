# Приёмка FB 002

Дата: 30 октября 2026 года. Scope: индивидуальные local users/service principals на SQLite, общий RBAC, cookie UI session и отзыв исполнения. Статус: **техническая реализация проверена**; merge допускается только после обязательного GitHub check `required`. Установка и реальные данные пилота по-прежнему проходят FB-009/OPS gates.

## Реализовано

- Offline bootstrap/recovery без публичного default admin; UUID subjects, hashed random credentials, актуальные роли/проекты/scopes/expiry, ротация и отзыв.
- Вход в UI, управление субъектами/ключами, HttpOnly/SameSite cookie, Secure на HTTPS, Origin/CSRF; bearer не сохраняется в browser storage.
- Общий `authorize` с OIDC/legacy; scoped admin не становится global admin. Неизвестные messenger IDs не разрешаются без явной project/installation binding.
- Атомарный initiator для всех run creation paths и наследование subprocess; проверка перед lease/renew/completion/MCP upstream, остановка без новых compensation effects, завершение SSE после потери прав.
- Административный identity audit закрыт от ленты проектов; actual subject/source/application закреплены сервером. Critical/four-eyes не принимает local assurance.
- Additive SQLite schema/backup/restore; явный fail-closed при неподдерживаемой миграции и переключении identity provider.

## Проверки и evidence

Среда: отдельная чистая копия remote main после FB-001 merge `f6451ff83442767c3071d58f9fb62dfc61b7270b`; Node 24, SQLite, синтетические данные и fake MCP upstream. Browser plugin not available — использован Playwright репозитория.

| Проверка | Результат |
|---|---|
| Local identity unit/HTTP | 18 PASS: bootstrap/hash/expiry/revoke/rotation/CSRF, две identity и два проекта, service deny, admin audit isolation, critical deny, медленный body после revoke, restart/backup/schema/migration |
| MCP effect admission | Для revoke/expiry/permissions: 0 upstream effects, renewal/completion отклонены |
| Process ownership | Subprocess сохраняет исходный credential; отзыв останавливает armed compensation без нового stage |
| Полная coordinator regression | 442 PASS, 28 infrastructure SKIP; финальные identity changes дополнительно проверены targeted suite |
| Web/worker/decision/Temporal/process pack | `npm test` PASS; web 79 PASS; Python worker 170 cases / 3 SKIP; decision и сохранённые Temporal histories replay PASS; pack 3 PASS |
| Typecheck / production build | Coordinator/web/Temporal PASS |
| Browser regression | 36 PASS на desktop и Pixel 7, включая local login, неверный ключ, restart, self-rotation, создание scoped user, logout и отсутствие bearer в storage |
| Visual QA | `local-access.png` обоих viewports просмотрены; sticky close/header, читаемые поля, нет horizontal overflow; screenshots без показанного ключа приложены к Playwright report |

Последние targeted проверки дополняют полный прогон; обязательный Linux `required` повторяет docs/scripts, unit, browser и все infrastructure integration jobs до merge. Локальные SKIP не считаются PASS. Полный docs/scripts Python набор на Darwin имеет ранее воспроизведённый process-group cleanup `PermissionError`; scripts не изменялись и этот результат не используется вместо Linux gate.

## Границы и восстановление

Поддерживаемый новый срез — local auth + SQLite lite и индивидуальный REST/UI вход. Endpoint-token webhooks/A2A push и автоматические schedule identities остаются blocked до отдельной квалификации service binding; chat delivery появится в FB-003–FB-005. Temporal/HA, реальные messenger/model calls, реальные данные и qualification critical assurance не объявляются принятыми.

Старые run IDs и `local-admin` audit не переписываются. Исторические активные задачи без owner binding останавливаются при включении local auth; terminal history остаётся доступной по прежним project permissions. Rollback сохраняет local mode либо останавливает сервис; переключение multiuser authority на общего администратора не разрешено. Уже допущенный внешний effect не отменяется задним числом. Практический runbook — [локальные идентичности](../local-identities.md).
