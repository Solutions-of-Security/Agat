# ADR 023 Профили развёртывания и контракты новых входов

Статус: **принято для реализации FB-001 и FB-002**, 30 октября 2026 года. Основание — [ближайший бэклог](./feedback-priority-backlog-2026-10-30.md). Решение фиксирует технические границы; не утверждает qualification окружения или передачу реальных пользовательских данных внешнему сервису.

## Контекст и решение

Один legacy admin-token представляет одного local-admin. Для нескольких пользователей нужен общий authorization path с отдельными субъектами, а для новых tools и входов — проверка конкретной возможности. Наличие PostgreSQL, Kong или контейнера не означает готовность identity, чата или developer workspace.

Принят `DeploymentSnapshot` версии 1: профиль, источник identity, state/runtime/ingress, blockers и список именованных capabilities. Один resolver используется API, общим scenario preflight и React UI. Переключение профиля не включает неподдерживаемую функцию и не ослабляет существующие policy. Сервер повторно вычисляет snapshot на запросе; клиент не может передать собственную готовность.

| Профиль | Identity и state | Runtime и ingress | Граница |
|---|---|---|---|
| single-user | Один local-admin; SQLite по умолчанию | Database process runtime; прямой ingress или proxy | Без credentials доверяется только loopback-оператор; удалённый клиент всегда предъявляет admin-token. Некорректные credentials не игнорируются |
| lite | Индивидуальный OIDC либо local identity из FB-002; SQLite | Обычные runs/database processes; Kong и Temporal не обязательны | До FB-002 local provider сообщает unavailable и блокирует API. Critical four-eyes требует отдельно квалифицированной identity |
| durable | Индивидуальные субъекты; SQLite либо PostgreSQL | Подключённый Temporal; ingress независим от RBAC | При потере runtime блокируются durable процессы/расписания; ordinary runs и диагностика остаются доступны. Database runtime не подставляется молча |
| production-ha | Индивидуальные субъекты и PostgreSQL | Подключённый Temporal; ingress выбирает оператор | Снимок конфигурации не выдаёт production qualification. OPS gates, backup/restore и topology проверяются отдельно |

`AGAT_DEPLOYMENT_PROFILE` выбирает профиль явно. Без него legacy выбирает single-user, индивидуальный вход — lite либо durable при включённом Temporal. `AGAT_AUTH_MODE=legacy|oidc|local` задаёт источник; существующий `AGAT_OIDC_ENABLED` сохраняет совместимость, но конфликт источников закрывается отказом. `AGAT_DEPLOYMENT_INGRESS=direct|gateway` описывает ingress и не даёт полномочий. Неизвестные значения отклоняются при чтении config.

## Контракт локальных субъектов FB-002

Выбран минимальный локальный identity provider на SQLite. Публичная регистрация и default admin отсутствуют. Первый пользователь создаётся оператором через локальную CLI; браузер обменивает персональный high-entropy token на ограниченную по времени HttpOnly session. API/service credentials выдаются отдельно, хранятся в виде hash, имеют project/role/scope/expiry и могут быть отозваны. Общий authorization path проверяет актуальный principal и credential; client-supplied username/project/messenger ID полномочий не создаёт.

UI не сохраняет персональные bearer secrets в browser storage. Cookie transport учитывает HTTPS, SameSite, session expiry и CSRF. Service principal не выдаётся за пользователя и не входит в UI как человек. Local identity не выдаётся за OIDC approver. Legacy audit остаётся историческим; изменения схемы не переписывают subjects старых записей. CLI/bootstrap и локальный provider не зависят от Keycloak/Kong/Temporal/PostgreSQL.

## Контракт диалога и доставки

Первый кандидат канала — Telegram, **личный текстовый диалог на синтетических данных**. Реальный аккаунт, внешний трафик, группы, вложения и передача непубличных данных требуют отдельного разрешения владельца данных до FB-005. Это выбор интерфейса для реализации, не создание или запуск бота.

Authoritative store coordinator хранит `installationId/projectId/subject/conversationId/turnId/runId`, inbox с уникальным внешним event ID и ограниченную историю. Turn создаёт новый run с immutable разрешённым снимком истории. Порядок задаёт код; A2A contextId и embedding session не переинтерпретируются как история чата. Retention, reset/delete и revoke распространяются на производные summaries; история сама не становится утверждённой episodic memory.

Terminal result и outbox event сохраняются атомарно либо восстанавливаются доказанным transactional equivalent. Доставка имеет собственные pending/delivered/retry/DLQ/unknown состояния и стабильный event ID. Polling/status остаётся источником восстановления; после неопределённого внешнего эффекта запрещён слепой resend. Получатель и destination policy проверяются перед send. UX-308 использует эти primitives для opt-in внимания, но его UI-эксперимент не блокирует FB-004.

## Контракт executor

Gateway policy, approval, credentials broker и audit остаются общей границей HTTP/WASI/OCI/будущих STDIO tools. Изолированный OCI Job не объявляется workspace или STDIO server. Будущий workspace связывается с project/subject/run, имеет file/process/network/resource limits и отдельные read/write/execute/Git scopes. Первый developer outcome — patch/test evidence на pinned копии repo; remote write/CI — отдельный FB-010.

STDIO bridge использует pinned executable/image, admin-controlled exec-array, lifecycle и negotiated protocol revision. Worker/модель получают разрешённые schema/public names; raw command, credentials и host shell не выдаются. Недоступный executor даёт blocked, а не fallback на coordinator/worker host. Отмена, restart и неизвестный эффект сохраняют operation ID и audit; повтор записи требует reconciliation.

## Ответственность и открытые rollout gates

Code/release review направляется существующему CODEOWNER `@TitanUser`; это не назначение его владельцем чужих данных. Product выбирает пользовательский сценарий и первый реальный канал, data owner — допустимые данные и retention, operator — runtime/backup/recovery, release owner — приёмку, incident owner — отзыв credentials и остановку. Эти роли должны быть назначены конкретному deployment до real-data pilot; отсутствующие назначения не заменяются вымышленными именами. Техническая приёмка FB-001 не открывает этот rollout gate.

## Альтернативы и последствия

Сохранение неявного local-admin для любого входа отклонено: оно не различает субъектов и скрывает отсутствующую capability. Обязательный полный enterprise stack для lite отклонён: RBAC проверяет coordinator, а Temporal/PostgreSQL отвечают за другие свойства. Выбран additive контракт и отдельный local provider; их цена — явные миграции, scopes, UI login и negative tests. Повторное использование внешнего OIDC остаётся вариантом для организации с готовым IdP.

## Проверка и откат

Матрица тестирует отсутствие identity/Temporal/PostgreSQL/sandbox и смену ingress независимо; неподдержанные operation IDs отклоняются. HTTP и UI показывают одинаковые reason codes. Включение lite не принимает legacy token; missing/invalid identity не становится local-admin. Production HA всегда сохраняет `productionQualified=false` до отдельного qualification contract.

Откат на single-user разрешает только прежнюю модель одного оператора и закрывает новые multiuser/chat/developer входы. Старые runs/manifests/audit сохраняются. Отключение обязательной зависимости блокирует связанную возможность; снятие ограничения не считается способом исправить провал тестов. Доказательства — [приёмка FB-001](./status/fb-001-validation-2026-10-30.md).
