# Индивидуальный вход в лёгкой установке

FB-002 добавляет пользователей и service principals в ту же SQLite authority, где находятся проекты, запуски и audit. Keycloak, Kong, Temporal и PostgreSQL для этого режима не нужны. [ADR 023](./adr-023-deployment-identity-and-integration-contracts.md) задаёт границы; [протокол приёмки](./status/fb-002-validation-2026-10-30.md) содержит проверки.

## Настройка и первый вход

Остановите coordinator и сделайте согласованную резервную копию существующей базы и artifacts. Сохраните прежний `AGAT_CREDENTIALS_KEY`: его замена не является ротацией индивидуальных ключей. Используйте защищённые enrollment/encryption secrets из своей установки; для удалённого lite индивидуального входа общий admin-token не требуется. TLS обеспечивает выбранный ingress, HTTPS origin задаётся явно.

Пример для разработки на loopback из корня репозитория:

```sh
export AGAT_AUTH_MODE=local
export AGAT_DEPLOYMENT_PROFILE=lite
export AGAT_OIDC_ENABLED=false
export AGAT_STATE_STORE_DRIVER=sqlite
export AGAT_TEMPORAL_ENABLED=false
export AGAT_LOCAL_AUTH_ORIGIN=http://127.0.0.1:8796
export AGAT_PORT=8796
export AGAT_DB_PATH="$PWD/runtime/agat.db"
export AGAT_SEED_DEMO=false
npm run auth:local -- bootstrap --username operator --confirm-offline COORDINATOR_STOPPED
npm run build
npm start
```

Замените origin на точный адрес браузера, включая порт. Для внешнего адреса допускается только HTTPS origin без пути, credentials или query. HTTP допускается исключительно для `localhost`, `127.0.0.1`, `[::1]`. Несовпадение origin блокирует вход и cookie mutations. Пример не публикует установку наружу и не создаёт внешний аккаунт.

Bootstrap требует выбранное имя, выполняется один раз и атомарно создаёт первый global admin с новым UUID subject. Публичного signup/bootstrap API и стандартного пользователя/пароля нет. CLI показывает случайный 256-bit персональный ключ один раз: сохраните его в менеджере секретов. Он действует 30 дней; в базе остаётся SHA-256, а не исходный token. В браузере введите ключ в форму входа. API credentials и service credentials для входа в UI не подходят.

## Пользователи и ключи

Global admin открывает **Меню профиля → Пользователи и доступ**: создаёт пользователя либо приложение, назначает роль и проекты, выдаёт ограниченный ключ, отключает субъекта, заменяет/отзывает ключ. Локальный service principal не получает `admin` и не участвует в human approvals/reviews. `*` означает явный доступ global admin ко всем проектам; scoped admin не может управлять всей установкой, настраивать общий изолированный executor или обойти project isolation.

Ключ закрепляет подмножество roles/projects/scopes субъекта, application и expiry (максимум 90 дней). Проверка пересекает сохранённые grants с актуальными полномочиями пользователя на каждом запросе; назначения в старом ключе не расширяются при добавлении новой роли. Все роли должны быть явно разрешены и в principal, и в credential. Новый ключ выдаётся после изменения роли, если пересечение стало пустым.

| Scope | Разрешение с учётом роли |
|---|---|
| read | Чтение доступного проекта и preflight |
| run | Запуск, отмена, replay и исполнительные действия |
| design | Настройка агентов, процессов и tools |
| credentials | Изменение project credentials |
| approve | Обычные пользовательские согласования |
| review | Human reviews/extraction review; только пользователь |
| admin | Административные изменения; global actions дополнительно требуют `admin` и `*` |

Сессия живёт не более восьми часов и не дольше ключа. Cookie имеет `HttpOnly`, `SameSite=Strict`, `Path=/`; при HTTPS — `Secure` и имя `__Host-agat-session`. Для loopback HTTP используется отдельная development cookie. Изменения через cookie требуют точный Origin и `X-Agat-CSRF-Token`; CSRF token хранится только в памяти страницы. Персональный bearer не сохраняется в localStorage/sessionStorage; session secret хранится в базе только как hash. При перезагрузке браузер восстанавливает сессию через cookie.

Ротация отменяет старый credential и его сессии, сохраняя прежний срок и ограничения. При замене ключа собственной UI-сессии сервер выдаёт новую cookie и CSRF, чтобы оператор мог сохранить новый ключ; остальные старые сессии закрываются. Отзыв собственного ключа завершает вход. Выход удаляет текущую сессию, но не отзывает сам персональный ключ. Реактивация principal не возобновляет отменённые запуски и отозванные ключи.

## API и внешние идентичности

API-клиент передаёт индивидуальный `Authorization: Bearer …` и `X-Agat-Project-Id`; в local режиме legacy header отвергается. Неверный Authorization не подменяется действующей cookie. Подмена username/subject/application заголовком или телом run не меняет права.

| Метод и путь относительно `/api/v1` | Доступ |
|---|---|
| POST `/auth/local/login`, GET `/auth/local/session`, POST `/auth/local/logout` | Обмен персонального ключа, восстановление и завершение UI session |
| GET/POST `/auth/principals`, PATCH `/auth/principals/:subject` | Global admin: список/создание, изменение разрешений или disable |
| GET/POST `/auth/principals/:subject/credentials` | Global admin: metadata/выдача ключа; secret только в ответе выдачи |
| POST `/auth/credentials/:id/rotate`, POST `/auth/credentials/:id/revoke` | Global admin: замена/отзыв |
| GET `/auth/audit?after=…` | Global admin: identity audit; приватные административные события не попадают в ленту чужих проектов |
| GET/POST `/auth/channel-bindings`, DELETE `/auth/channel-bindings/:id` | Admin активного проекта: явные привязки |

Channel binding содержит provider, installation ID, внешний user ID, внутренний subject и project. Непривязанный внешний ID получает deny; другой installation/project не совпадает. Привязать можно только действующего пользователя выбранного проекта. Binding сам по себе не авторизует transport: FB-003–FB-005 добавят ingress/session/delivery с повторной проверкой прав. Общие endpoint-token process webhooks, A2A ingress/push и автоматические расписания в local профиле пока не квалифицированы и не запускаются под вымышленным администратором. Для запуска используйте REST с индивидуальным user/service ключом; существующий OIDC профиль сохраняет свои возможности.

## Исполнение, audit и отзыв

Все виды новых runs, включая eval, replay и process, атомарно получают initiator subject/credential/project/source/application. Subprocess наследует инициатора родительского run, даже если другой человек согласовал переход. Audit сохраняет реальный subject, transport (`local-bearer`/`local-session`) и закреплённое приложение; CLI отдельно записывает OS account и `local-cli`. Исторические `local-admin` записи не переименовываются.

Актуальные grants проверяются при выдаче/продлении lease, сохранении completion и непосредственно перед отправкой MCP upstream. Отзыв/истечение/сужение прав останавливает связанные активные runs; compensation не начинает новые эффекты без полномочий. SSE повторно проверяет credential/session/project при отправке и закрывается после отзыва или logout. При shutdown завершаются также SSE sockets, чтобы keep-alive retries не удерживали restart. Незавершённое чтение HTTP body не сохраняет прежние права после отзыва. Уже допущенная внешняя попытка может быть in flight; отмена не обещает undo подтверждённого эффекта.

Local identity остаётся локальной: critical/destructive/four-eyes approval запрещён до отдельной qualification assurance. Два разных локальных UUID не считаются двумя квалифицированными OIDC approvers. Отклонить опасный запрос пользователь с обычным approval scope может.

## Backup, миграция и восстановление

Новые таблицы additive и находятся в основной SQLite; UUID runs, старые subjects/audit и project credentials сохраняются. Backup выполняется при остановленных writers с SQLite checkpoint либо штатным согласованным SQLite backup, вместе с artifacts и прежними encryption secrets. Восстановление сохраняет subjects, credential hashes и сессии с их прежним сроком. Актуальный build проверяет неподдерживаемую identity schema и закрывает доступ вместо fallback.

После включения индивидуальных subjects переключение этой базы на legacy/OIDC блокируется до явной миграции identity. Offline SQLite → PostgreSQL migrator также отказывается переносить local authority без квалифицированного mapping; subjects не отбрасываются и не объединяются. Для rollback сохраните `AUTH_MODE=local` и совместимый build; FB-001 при отсутствии provider откажет во входе. Старую версию, игнорирующую local auth, с multiuser-базой запускать нельзя.

Если admin key утерян, остановите coordinator и выполните `npm run auth:local -- recover --subject EXISTING_GLOBAL_ADMIN_SUBJECT --confirm-offline COORDINATOR_STOPPED`. Recovery сохраняет существующий subject, отзывает его старые ключи/сессии, выдаёт новый персональный ключ и пишет audit. Этот CLI требует доступ к authority DB; HTTP recovery отсутствует. Последнего global admin нельзя отключить через API.
