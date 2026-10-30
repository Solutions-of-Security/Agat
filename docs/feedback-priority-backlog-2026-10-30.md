# Ближайший бэклог доработок Агата по обратной связи

Дата: **30 октября 2026 года**. FB-001 и FB-002 **реализованы и технически проверены**; FB-003–FB-011 запланированы. Основание, фактическое состояние и источники: [анализ доработок](./feedback-analysis-2026-10-30.md).

Эта очередь добавляет ближайшие работы по четырём замечаниям пользователя. **P0** означает gate перед обещанием соответствующей возможности: multiuser, бот, облачные модели либо developer workspace. **P1** — расширение после первого проверенного среза. Общий ближайший результат — лёгкая установка с индивидуальными правами, подключением модели, диалоговым ботом и ограниченным developer-сценарием. Выпускать срезы можно отдельно, сохраняя gates каждой возможности.

Источник истины для приоритетов и последовательности — [roadmap](./roadmap.md). Этот файл раскрывает новые карточки FB-001–FB-011 и уточняет ближайший scope прежних CLD/UX/CON/USE/EVAL/OPS без второго независимого плана. Сверка с локальным бэклогом от 17 октября выполнена: RUN-105, UX-308 и MEM-101 сохраняют отдельные границы, указанные ниже. Статус каждой задачи подтверждается её карточкой и evidence; qualification production остаётся отдельным gate.

## Порядок ближайшей работы

| Порядок | Приоритет и gate | Результат | Задачи | Доказательство перехода |
|---|---|---|---|---|
| 1 | P0 / G0 | Закреплены capability/auth/data contracts | FB-001; контракты FB-002, FB-003, FB-004, FB-006–FB-008; CLD-001/CLD-002/CLD-004, CON-001 | Сверены exact implementation versions, выбран первый канал, записаны ADR и версия acceptance suite |
| 2 | P0 / G1 | Лёгкий multiuser и рабочее подключение модели | FB-002; CLD-001–CLD-005, UX-207; существующий USE-002 | Два субъекта изолированы; local/custom и OpenRouter пути проверены раздельно; ключ и бюджет остаются под контролем сервера |
| 3 | P0 / G2 | Бот с сохраняемым диалогом и доставкой | FB-003 → FB-004 → FB-005; data/permission gates G1 | Две реплики в личном чате используют правильную историю; повтор и рестарт не создают новый run; доставка имеет отдельный статус |
| 4 | P0 / G3 | Код и Git в workspace, один управляемый STDIO-сервер | FB-006 → FB-007 и FB-008; CON-001 | На изолированной копии repo получены patch/test evidence; STDIO проходит gateway policy, отмену и cleanup |
| 5 | P0 / G4 | Поставляемая установка и evidence пилота | FB-009; USE-009, EVAL-101, USE-008; UX-111 при использовании согласований | Clean install, upgrade/restore/rollback и повторные eval проходят для каждого заявленного профиля |
| 6 | P1 | Внешние developer writes и второй чатовый канал | FB-010, FB-011 | Польза первого среза подтверждена, отдельные scopes/данные/эксплуатация квалифицированы |

FB-001/FB-002 реализованы в техническом scope, контракты G0 закреплены ADR 023. Следом registry/data/budget foundations; после них — два вертикальных среза G2 и G3. Установка USE-009 готовится с G0, а FB-009 принимает каждый срез перед его пилотом. Отсутствие исполнителей не превращает таблицу в обещание параллельной поставки. S/M/L — относительный объём; сроки и capacity определяются после сверки и spike. Выделение реальных владельцев build/runtime/data/release/incident — выход G0.

## Уточнения существующих P0

| ID из roadmap | Ближайший scope по обратной связи | Дополнительное доказательство готовности |
|---|---|---|
| CLD-001 | Единый versioned реестр local/Ollama, custom OpenAI-compatible и OpenRouter bindings; сохранить MCP/A2A API и исторические manifests | Два подключения с разными моделями/ключами используются без изменения worker env; disable/rotation/cross-project deny проверены. Приоритеты выбора local worker и server executor явны |
| CLD-002 | Одна policy для полного контекста: user input, session history, RAG, memory, tools и workspace artifacts | Смена прав/получателя после preflight блокирует передачу; ключи не попадают в browser storage, prompts, обычные leases и traces |
| CLD-003 | Реальный OpenRouter adapter с catalog/capabilities/errors, а не только preset base URL | Проверены текст, tool call, schema support и неподдержанная capability; 401/429/quota/timeout различаются. Fallback выключен по умолчанию, provider allowlist/data policy не теряются |
| CLD-004 | Reservation/reconciliation бюджета на run/project/connection, включая диалоговые turns и retries | Конкурентные turns не превышают заданный предел; timeout/неизвестный usage не записывается как нулевая стоимость |
| CLD-005 | Полный путь connection → модель → агент → API/process/chat run через доверенный executor без GPU | Local и cloud агенты работают в одном процессе; manifest закрепляет connection revision и фактическую модель. Embeddings квалифицируются отдельно |
| UX-207, USE-002 | «Подключения → проверить → модель → агент → тест», затем один capability preflight для всех входов | UI, REST, bot и process одинаково объясняют blocked/ready; отсутствие sandbox, identity, embeddings или бюджета не обходится другим входом |
| CON-001 | Расширить существующий conformance contract на STDIO, workspace и канал доставки | Зафиксированы namespace/schema, auth/scopes, размер/timeout/cancel, side effects и unknown-result semantics; нет второго policy engine |
| USE-009, EVAL-101, USE-008 | Установочный комплект и пилот принимаются вместе с новыми путями | Есть evidence чистой установки и повторных задач, latency/cost/rework; ручной baseline и ошибочные случаи учитываются |
| SAFE-101, EVAL-103 | Сохраняются существующие negative suite и выбор profile по evidence | Новые chat/tool/context входы расширяют SAFE-101; новый pack profile проходит EVAL-103, отдельные конкурирующие quality gates не создаются |

CLD-006 сохраняет **P1** для внешних A2A-исполнителей и не является зависимостью первого чатового бота. Для него достаточно обычного run или разрешённого опубликованного процесса. CLD-008 с общей квалификацией дополнительных inference SaaS остаётся P1; минимальный custom endpoint binding в CLD-001 не означает поддержку любого API.

RUN-105 остаётся P1 для steer/pause/resume долгого stage через checkpoint/intent revision; FB-003 добавляет историю обычных turns и не обещает resume. UX-308 остаётся P1 для opt-in внимания, quiet hours и измерения времени реакции; FB-004 предоставляет transport/delivery foundation и не ждёт этого UI-эксперимента. MEM-101 управляет долговременным evidence/preferences; chat history не становится постоянной памятью автоматически. FB-007 открывает ограниченный workspace/Git substrate; весь Software Delivery pack SP-201 остаётся P2.

## Новые задачи ближайшего приоритета

### FB-001 Профили развёртывания и честная проверка возможностей

**Статус:** реализовано; техническая приёмка пройдена 30 октября 2026. [Evidence](./status/fb-001-validation-2026-10-30.md), [ADR 023](./adr-023-deployment-identity-and-integration-contracts.md). Назначения real-data pilot owners и production qualification остаются gates выбранного deployment.

**P0, G0. Ответственные:** backend + DevOps + Product + QA. **Размер:** M. **Основание:** SRC-008–SRC-010, все четыре замечания. **Вход:** текущие config/auth/preflight и статическая сверка, указанная в анализе.

Зафиксировать versioned матрицу профилей: single-user local, lite multiuser, full/durable, production HA. Для каждого перечислить auth, state, runtime, ingress, model executor, sandbox и поддерживаемые возможности. Перед реализацией сверить exact code versions с анализом; сохранить прежние ID, contracts и указанное разделение с RUN-105/UX-308/MEM-101.

**Критерии приёмки:**

- ADR в `/docs` фиксируют local auth/subject mapping, session/delivery contracts и executor boundary; выбран первый мессенджер и допустимые данные. Назначены владельцы продукта, данных, эксплуатации, релиза и инцидентов.
- Один preflight показывает доступность каждой операции; отсутствие Temporal блокирует требующую его process-возможность, отсутствие sandbox — code/STDIO. API и UI сообщают одну причину.
- Legacy single-user явно показан как один субъект. Публикация внешнего bot/callback API и multiuser не разрешается как неявный режим admin-token. Missing/invalid auth не превращается в `local-admin`.
- При отключении Keycloak/Kong/Temporal/PostgreSQL по отдельности матрица совпадает с проверенным поведением. Недоступность обязательной identity/policy зависимости даёт fail-closed.

**Gate и откат:** документация и тест матрицы проходят до открытия нового входа. Версия capability contract закреплена; возврат на single-user закрывает внешние входы, а не расширяет права.

### FB-002 Индивидуальные субъекты и RBAC в лёгком режиме

**Статус:** реализовано; техническая приёмка пройдена 30 октября 2026. [Evidence](./status/fb-002-validation-2026-10-30.md), [runbook](./local-identities.md). Critical assurance, внешние service bindings и release qualification остаются отдельными gates.

**P0, G1; blocker для внешнего бота. Ответственные:** backend + frontend + security + QA. **Размер:** L. **Зависимости:** FB-001, существующие `AuthContext`, roles/project checks и credentials. **Основание:** SRC-009, SRC-010.

Добавить локальные идентичности пользователей и service principals поверх SQLite, сохранив общий authorization path с OIDC. На G0 выбрать минимальный способ bootstrap и входа; персональные токены могут использоваться для обмена на защищённую UI session, а не как общий admin-token. Для API задать project/role/scope, срок жизни, rotation/revoke и hashed token storage. Mapping messenger identity хранится явно; новый внешний ID получает deny до привязки.

**Критерии приёмки:**

- На SQLite без Keycloak/Kong/Temporal/PostgreSQL два пользователя и два проекта проходят allow/deny matrix для runs, history, artifacts, credentials, approvals и channel bindings. Service principal не повышает права пользователя.
- Audit хранит реальный `subject`, источник identity и действующее приложение; подмена username/project/channel binding не меняет полномочий. Отзыв проверяется до следующего tool call/turn/delivery.
- UI session защищена HttpOnly/Secure cookie и CSRF-контролем для выбранного deployment; bearer secrets не остаются в browser storage. Bootstrap не создаёт публичный default admin.
- Critical/four-eyes операции недоступны в lite до отдельной квалификации assurance; локальный пользователь не выдаётся за OIDC approver. Старые local-admin записи остаются историческими.

**Gate и откат:** auth regression и revoke-тесты проходят до G1. Миграция без потери subjects/audit; отказ локального auth закрывает доступ. Откат не объединяет несколько субъектов под admin.

### FB-003 Сохраняемый диалог и жизненный цикл сессии

**P0, G2. Ответственные:** backend/runtime + QA. **Размер:** L. **Зависимости:** FB-001, FB-002; общий context/data contract CLD-002. **Основание:** SRC-002, SRC-004, SRC-007.

Ввести project/owner-scoped `conversation`, `turn` и связь turn → run/instance с immutable agent/model/policy snapshot. Для MVP — личный текстовый диалог и ограниченная история. `session` определяет продолжение/сброс диалога; не переиспользовать embedding session или episodic memory как chat history.

**Критерии приёмки:**

- API create/read/append/reset/close/delete проверяет права; внешний ключ включает installation, channel/chat, thread при наличии и субъект. Общие групповые диалоги недоступны до отдельного privacy contract.
- Durable inbox дедуплицирует event/message ID; turn и idempotent start-intent сохраняются до постановки run. Crash между записью и запуском восстанавливается без второго run.
- Для одной сессии действует определённый порядок turns; конкурентные реплики ставятся в очередь либо явно отклоняются. `/new` создаёт новую границу истории, отмена не завершает следующий turn.
- После restart сохраняются input, run relation и terminal result. Timeout/отмена/нехватка контекста дают явное состояние, не invented success.
- Retention/TTL/reset/deletion распространяются на историю, summaries и производные snapshots согласно lifecycle; права применяются при сборке каждого контекста. Проверено отсутствие контекста другого субъекта/проекта.

**Gate и откат:** migration/restore, concurrency и crash tests до G2. Feature flag останавливает новые turns; принятые runs и история остаются доступны авторизованному владельцу.

### FB-004 Надёжная доставка событий и callback результата

**P0, G2. Ответственные:** backend/integration + QA. **Размер:** L. **Зависимости:** FB-001, FB-002; FB-003 для chat correlation. **Основание:** SRC-003, SRC-004; существующие A2A push/audit механизмы.

Создать общий versioned delivery contract для событий run/process: terminal result, failed/cancelled и требуемое действие пользователя. Состояние выполнения и доставка независимы. Существующий state store — источник истины; terminal transition и outbox event атомарны либо имеют доказанный transactional equivalent. Изучить переиспользование текущих A2A/SIEM outbox примитивов без смешения их подписчиков.

**Критерии приёмки:**

- Event содержит стабильный `eventId`, type/version, project, run/instance/turn, sequence и bounded artifact references. Payload ограничен правами получателя; raw credentials и лишняя история исключены.
- Chat adapter и произвольный разрешённый HTTPS callback используют один delivery state: pending/delivered/retry/dead-letter/unknown. Есть backoff, retry budget, `Retry-After`, disable и авторизованный replay с тем же event ID.
- Callback привязан к заранее проверенной project subscription; адрес из model output не принимается. Проверены HMAC/timestamp/replay, rotation, SSRF/DNS rebinding/redirect и отзыв subscription между enqueue/send.
- Crash до/после внешнего эффекта и до local ack не теряет событие. At-least-once не объявляется exactly-once: получатель дедуплицирует event ID, а для канала без такой гарантии есть reconciliation либо видимый unknown без слепого resend.
- Polling/status и существующий SSE продолжают работать; неудачная доставка не меняет успешный run на failed и не скрывает failed run. Метрики outbox age/retries/DLQ и runbook готовы.

**Gate и откат:** отказ сети/429/5xx/restart/late ack и tenant-deny тесты до G2. Отключение subscription сохраняет историю результатов и прекращает дальнейшие send; replay всегда повторно авторизуется.

### FB-005 Первый адаптер мессенджера

**P0, G2. Ответственные:** integration + backend + frontend + Product + QA. **Размер:** L. **Зависимости:** FB-002–FB-004, USE-002; CLD-005 только для cloud-варианта. **Основание:** SRC-001–SRC-004.

Рабочий кандидат — **Telegram webhook, личные текстовые диалоги**. Product подтверждает канал и data policy на G0; при необходимости корпоративного мессенджера заменить только adapter-specific контракт. Общий inbox/session/delivery слой от выбранного канала не зависит. [Первичный API-контракт Telegram](https://core.telegram.org/bots/api#setwebhook).

**Критерии приёмки:**

- Поддержаны установка/проверка/disable, безопасное хранение bot secret, проверка webhook и allowlist привязанных пользователей. Ответ HTTP об успешном приёме выдаётся после durable inbox; inference выполняется асинхронно.
- Путь «сообщение → ack/status → финальный ответ → уточнение → ответ с разрешённой историей» проходит с локальной и OpenRouter моделью отдельно. UI даёт ссылку на соответствующий run/trace без обхода RBAC.
- `/new`, `/status`, `/cancel`, разбиение длинного ответа и правила форматирования проверены. У ошибки auth/model/tool/delivery есть понятный пользовательский исход; недоступный tool не предлагается как выполненный.
- Повтор входящего события, два одновременных сообщения, restart и исходящий timeout проходят FB-003/FB-004 semantics. Чужой chat ID или пересланная ссылка не открывают контекст/артефакт.
- Approval из чата в MVP ведёт на авторизованный UI. Messenger identity не считается автоматически подходящим подтверждением critical операции. Streaming, вложения и группы остаются в FB-011.

**Gate и откат:** тестовый tenant и реальные bot API вызовы, журнал delivery и synthetic probe до G2. Disable прекращает ingress/send, token можно отозвать; принятые задачи доступны через обычный API.

### FB-006 Изолированный workspace и выполнение команд агента

**P0, G3; blocker для обещания code execution. Ответственные:** runtime + backend + DevOps + security + QA. **Размер:** L. **Зависимости:** FB-001, FB-002, CON-001; существующие sandbox/policy/kill switch. **Основание:** SRC-005, SRC-006.

Добавить versioned workspace contract: create/import/read/write/list/execute/export/close с ownership, lifecycle, quotas и manifest. Выбрать поддерживаемый executor на G0/spike; текущие WASI/OCI controls переиспользовать, ограничения одноразового Job закрыть явно. Команда работает внутри изолированного workspace, а не в host cwd coordinator/worker.

**Критерии приёмки:**

- MCP scopes различают файловое чтение/запись и execute; policy/approval применяется перед каждым вызовом, профиль закреплён digest/revision. Exec-array, cwd/env, exit code, bounded stdout/stderr и artifact references имеют schema; shell-профиль, если нужен, явно разрешён.
- В lite-профиле есть поддерживаемый способ подключить executor либо capability остаётся blocked. Запуск без изоляции при недоступности backend запрещён.
- Проверены path traversal, symlink escape, чужой workspace, host filesystem/socket, process escape, fork/output/disk/resource limits и egress. Credentials передаются только согласованным операциям через broker и редактируются в выводе.
- Отмена, deadline и emergency deny останавливают process tree; неизвестный эффект не повторяется автоматически. Restart либо восстанавливает checkpoint по контракту, либо явно завершает попытку; скрытого повторного выполнения нет.
- Объявлены TTL, очистка, retention разрешённых артефактов, backup/restore persistent state и revoke behavior. Operator видит workspace/call/version и причину отказа.

**Gate и откат:** изоляционные и cleanup-тесты на каждой заявленной ОС/backend до G3. Отключение execute немедленно запрещает новые вызовы; сохранённые артефакты и manifests читаются без запуска кода.

### FB-007 Git и проверяемое изменение кода

**P0, G3. Ответственные:** runtime/integration + QA + владелец developer-сценария. **Размер:** M–L. **Зависимости:** FB-006, CON-001; CLD-002 для внешних данных. **Основание:** разрыв workspace/Git в SRC-005, SRC-006.

Первый сценарий: импорт разрешённого repo/снимка на зафиксированном commit → чтение/status/diff → изменение изолированной копии → тесты → patch и evidence. Сохранять repo URL/reference, base commit, изменённые paths и версии test commands в manifest.

**Критерии приёмки:**

- Исходный пользовательский checkout не меняется; dirty base имеет явный исход. Repo hooks, submodules, LFS, credential helpers и test scripts считаются исполняемыми входами и проходят sandbox/egress contract.
- Ошибка clone/fetch, auth, merge conflict и failed tests не выдаёт «готово». После crash patch сопоставляется с base commit, а не применяется повторно на неизвестное состояние.
- Не менее трёх повторов каждого выбранного задания дают проверяемый patch/test result. Проверка результата независима от текстового self-report модели.
- На G3 удалённые write-действия запрещены; артефакт review содержит diff, перечень команд, exit codes, ограничения и ссылки на trace. Токен чтения repo не предоставляет push.

**Gate и откат:** fixture repo и реальный разрешённый тестовый repo до G3. Workspace можно удалить/пересоздать с base commit; исходный repo и история артефактов сохраняются.

### FB-008 Управляемый bridge для MCP STDIO

**P0, G3; blocker для обещания подключения STDIO. Ответственные:** integration/runtime + DevOps + security + QA. **Размер:** L. **Зависимости:** FB-006, CON-001; CLD-001 для connection binding. **Основание:** SRC-005, SRC-006 и [MCP transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).

Администратор регистрирует pinned server image/package, exec-array, env references, permissions, lifecycle и допустимую protocol revision. Bridge запускает MCP subprocess в изоляции и подключает его к текущему gateway. Не менять одноразовый JSON stdout contract OCI tool так, чтобы произвольный STDIO-процесс стал обходом policy.

**Критерии приёмки:**

- Инициализация/capability negotiation, `tools/list`, `tools/call`, ошибки, таймаут, cancel и shutdown проверены на fixture и одном реальном выбранном STDIO-сервере. Несовместимая revision отклоняется явно.
- Catalog и schemas проходят текущую namespace/policy/approval проверку; schema drift/version change инвалидирует предыдущие approvals. Secrets и raw env не раскрываются модели и обычному worker.
- stdout содержит только MCP messages; stderr bounded/redacted. Процесс не получает неразрешённые файловые каталоги/сеть. Проверены malformed/oversized messages, зависший startup и дочерние процессы.
- Нет неявного повторного side effect после bridge/worker restart. Принятие call, operation ID, unknown outcome и cleanup отражены в audit. Stateful server изолирован по project/subject/workspace, pooling не смешивает пользователей.
- UI/preflight показывает недоступность executor или несовместимость сервера. HTTP/WASI/OCI regression проходит с прежними контрактами.

**Gate и откат:** conformance + crash/deny/cleanup evidence до G3. Disable bridge убивает разрешённые сессии и закрывает новые вызовы; HTTP-подключения сохраняют прежнюю работу.

### FB-009 Приёмка установки и трёх пользовательских срезов

**P0, G1–G4 перед пилотом соответствующей возможности. Ответственные:** QA + DevOps + Product + владельцы данных/эксплуатации. **Размер:** L. **Зависимости:** FB-001; для каждого профиля — только его завершённые задачи; USE-009, EVAL-101, USE-008, применимый SAFE-101; EVAL-103 для нового pack profile и OPS gates для соответствующего production.

Собрать воспроизводимый acceptance pack в `/docs` с версиями установки, конфигураций, моделей, policy, adapters и fixtures. Использовать существующие eval/trace/manifest механизмы. Техническая доступность API отдельно от полезности результата и от production qualification.

**Критерии приёмки:**

- На чистой системе пройдены install → два пользователя/проекта → модель → первый результат. Отдельно пройдены chat и developer profiles; заявленные ОС/backend перечислены. Unsupported profile не получает отметку ready.
- Пройдены upgrade, backup/restore и rollback со state миграцией; сохранены identity/history/outbox/credentials references и старые runs. Missing dependency, revoke, duplicate, restart, cancellation и unknown outcome входят в обязательную suite.
- Для каждой заявленной модели и task family минимум три повтора каждого holdout case; deterministic проверки результата и human review неоднозначных ответов. Измерены task success, rework, latency, стоимость, delivery lag и cleanup. Пороговые значения и объём набора согласованы до испытания; один успешный прогон gate не закрывает.
- У каждого профиля есть health/capability check, runbook для DLQ/unknown delivery/stuck process/credential revoke и назначенные runtime/incident owners. В логи не попадают secrets и полная история по умолчанию.
- Product сравнивает с ручным baseline и решает продолжать/сузить/исправить. Пилот не объявляется HA/DR certification; существующие OPS-101–OPS-105 применяются перед соответствующим production cutover.

**Gate и откат:** release owner принимает evidence конкретного профиля. При провале возможность остаётся выключенной, миграция восстанавливается по проверенному плану; исправление возвращается в исходную задачу.

## Расширения следующего приоритета

### FB-010 Удалённые Git writes и CI

**P1. Ответственные:** integration + security + QA. **Размер:** L. **Зависимости:** FB-007/FB-009, USE-005 и write-scope CON-001.

Добавить branch/commit/push/PR и ограниченный CI trigger с preview точного diff/target, отдельными credentials/scopes и policy approval. Проверить protected branches, изменение base SHA после review, duplicate push/PR, unknown remote outcome и отмену после отправки. Merge/release не включаются автоматически. Приёмка — разрешённый тестовый repo, отсутствие повторного PR после restart и аудит actor/target/commit. Rollback — отозвать write scope; уже подтверждённый внешний эффект требует явного закрытия/компенсации, не обещания автоматического undo.

### FB-011 Второй канал и расширение диалога

**P1. Ответственные:** integration + frontend + Product + data/security + QA. **Размер:** L. **Зависимости:** FB-005/FB-009, подтверждённый спрос и data lifecycle.

Поставить один корпоративный адаптер по выбору пилота, вложения допустимых типов, ограниченное редактирование/streaming ответа и human handoff. Для групп/threads отдельно определить участников, видимость истории и отзыв доступа; не переносить приватный контекст в общий чат. Приёмка — второй adapter без копирования session/outbox/policy ядра, проверки прав, файла/лимитов/удаления и rate-limit/timeout. Rollback — disable установки/типа входа, сохранение статуса принятых задач.

## Общие выпускные проверки

| Сценарий | Ожидаемый результат | Закрывающая задача |
|---|---|---|
| Два пользователя, два проекта, подмена внешнего ID | Ни одна попытка не читает чужой context/artifact и не использует чужой credential | FB-002, FB-003, FB-005 |
| Дубликат входящего сообщения и crash после inbox | Один turn/start intent и один run; повтор виден как duplicate | FB-003, FB-005 |
| Две реплики до завершения первой | Документированный порядок без смешения истории и ответов | FB-003 |
| Crash между terminal transition/outbox и отправкой | Событие восстанавливается; результат доступен по status | FB-004 |
| Внешний успех перед local ack / 429 / timeout | Дедупликация или unknown/reconciliation; нет слепого повтора записи | FB-004, FB-008, FB-010 |
| Отзыв пользователя, policy, подключения или emergency deny | Новое действие блокируется; активный процесс останавливается по контракту | FB-002, FB-004, FB-006, FB-008 |
| Неподдержанная модель/tools/schema и исчерпанный budget | Blocked/failed/partial с причиной; секреты не раскрываются, hidden fallback отсутствует | CLD-002–CLD-005, USE-002 |
| Code/STDIO без executor либо попытка escape | Fail-closed, аудит, отсутствие host/cross-project доступа | FB-006, FB-008 |
| Тесты завершились ошибкой или patch конфликтует с base | Результат не маркируется успешным developer outcome | FB-007 |
| Restore/rollback и cleanup TTL | Старые manifests читаются, доступ не расширяется, временные процессы/данные удаляются по контракту | FB-009 |

Запланированные FB-карточки не являются отчётом о реализации; выполненные имеют ссылку на отдельное evidence. Закрытие требует перечисленного evidence; источник статуса существующих задач остаётся roadmap. Проверка самих Markdown-документов не заменяет acceptance реализации.
