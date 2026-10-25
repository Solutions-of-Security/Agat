# Решение о версии отчёта и задача исправления

Backend-инкремент USE-004, 25 октября 2026. Решение относится к сохранённому
проверенному Markdown artifact завершённого run. Оно связывает artifact ID,
SHA-256, run/stage, process instance/version, hash frozen graph и квитанцию
проверенного JSON. Последующее редактирование процесса или Knowledge не меняет
этой связи. Принятие не выдаёт разрешения на MCP/tools и не запускает модель.

## Контракт API

- `GET /api/v1/artifacts/:id/decisions?beforeRevision=N` — текущая ревизия,
  история по 50 записей и `nextBeforeRevision` для следующей страницы.
- `POST /api/v1/artifacts/:id/decisions` — новое решение либо квитанция повтора.
- `GET /api/v1/report-rework-tasks/:id` — связанная ручная задача исправления.

Чтение доступно read-roles текущего проекта. Запись — только `admin/operator`.
Субъект берётся из проверенного auth context; поле actor в запросе запрещено.
В legacy local mode нужен admin token. Чужие artifact/task/history — 404.

```json
{
  "requestId": "11111111-1111-4111-8111-111111111111",
  "expectedSha256": "<SHA-256 сохранённого Markdown, 64 lowercase hex>",
  "expectedRevision": 0,
  "decision": "rework_requested",
  "reason": "Проверьте выбранный период и объясните ограничение расчёта."
}
```

`requestId` — UUID v4. `decision` допускает `accepted` или `rework_requested`.
Для исправления нужен непустой комментарий; предел — 4000 UTF-16 units и 16000
UTF-8 bytes. CRLF нормализуется, внешние пробелы убираются. Поля не дополняются
молча; неизвестные keys, coercion, управляющие символы и недопустимые значения
отклоняются. Комментарий — текст, а не исполняемая разметка.

Ответ `{ decision, reworkTask, created }`: 201 для новой записи, 200 для повтора.
Rework создаёт ровно одну задачу в той же transaction. Accept возвращает
`reworkTask: null`. Новый комментарий/решение — следующая запись, старые записи
не редактируются. Ревизии нумеруются для одного artifact, предел — 1000.

`expectedSha256` фиксирует файл, `expectedRevision` — прочитанную историю. Если
другой пользователь успел принять решение, ответ 409 требует прочитать новую
историю. Страница и текущая ревизия читаются одним SQL statement snapshot.
Идентификатор повтора связан с project и hash полного verified subject. Повтор
того же намерения возвращает прежние decision/task IDs даже после следующего
решения; попытка применить тот же ключ к другому файлу/содержимому — 409.

Перед первой записью проверяются bytes/целостность Artifact Store вне SQL write
locks. Затем под блокировками project → canonical artifact повторно проверяются
состояние, SHA и ревизия. Project lock защищает namespace request IDs, включая
конкурентные запросы к разным artifacts. Сохранение решения, task и audit atomic.
В PostgreSQL используется `FOR NO KEY UPDATE` на project и `FOR UPDATE` на
artifact; при SQLite действует `BEGIN IMMEDIATE`. Внешних вызовов под этими
write locks нет. Сбой проверки исходника не сохраняет частичного решения.

## История и права хранения

Schema **35**, contract `agat-report-decisions-v35`, добавляет
`business_report_decisions` и `business_report_rework_tasks`, unique constraints
на artifact/revision, project/actor/request и decision/task. Все новые FK имеют
подходящий leading index. Migration/admission остаются отдельной Job;
PostgreSQL runtime DDL не выполняет.

В SQLite triggers запрещают UPDATE и прямой DELETE живой истории/задачи; cascade
purge owning run/artifact/project разрешён. В PostgreSQL system и tenant runtime
имеют для этих двух таблиц только SELECT/INSERT. Startup требует именно эти
права и отклоняет случайную выдачу UPDATE/DELETE. Для остальных application
tables сохраняется прежний DML contract; запреты TRUNCATE/REFERENCES/TRIGGER и
schema creation сохраняются. Это защита application history; владелец БД и
контролируемая migration остаются доверенной административной границей.

File retention, удаляющий bytes без удаления artifact metadata, не стирает
решения. История читается, source помечается недоступным, новые решения
отклоняются. Физический owning purge удаляет связанные записи по FK. Backup и
offline migration включают обе таблицы; event retention не удаляет историю.

`business.report.decision.recorded` содержит IDs, hashes, revision, decision,
process version, actor hash и task ID. Комментарий, JSON/Markdown content,
имена документов и source URLs в event не попадают. Комментарий хранится в
project-scoped ledger и выдаётся только авторизованным читателям проекта.
`binding.inputSha256` — hash JSON, поданного Markdown renderer, то есть
проверенного результата; это не hash входного задания процесса.

## Задача исправления и остаток USE-004

Task имеет собственный ID, запросившего субъекта, комментарий и frozen binding
к исходному результату. `execution: manual`; автоматического child run или
переноса большого отчёта в prompt здесь нет. Только задача последнего решения
имеет `state: open`; предыдущая получает вычисляемый `superseded`, при этом
запись и исходный комментарий сохраняются. Новый accept не удаляет старую task.
Task source availability отражает storage metadata и SHA; download отдельно
проверяет фактическую целостность bytes.

Этот backend-инкремент не закрывает USE-004 целиком. Следующие этапы — UI
«Принять / Нужны исправления», доступная карточка связанной задачи с исходным
отчётом и историей, затем перенос подходящего примера в Golden eval. Запуск
исправления должен явно учитывать context/budget/policy gates и связывать новый
результат с task; наличие metadata task не считается выполненным исправлением.
Human acceptance не объявляет автоматически проверенными факты и не заменяет
модельную квалификацию, usability trial или реальный пилот.

## Проверка и основания

Семь SQLite/API/OIDC tests проверяют exact bindings, retry, supersession,
пагинацию/пустую страницу, malformed input, corrupt/deleted source, immutable
history, schema 34 layout upgrade и persistence после restart, отсутствие новых run/model/tool effects и запрет записи для
viewer/auditor/designer. Полный coordinator replay после восстановления ноутбука:
389 passed, 28 штатных opt-in skips; typechecks и все builds прошли.

Реальные две PostgreSQL replicas проверяют четыре повтора (201/200), конкурирующие
намерения (201/409), один request ID на разных artifacts, tenant RLS, grants и
owning purge. SQLite → PostgreSQL apply/verify и rehearsal включают decision,
task, binding и DOCX bytes. CI/merge фиксируются в
[журнале реализации](./implementation-progress-2026-10-25.md).

Основания: [AWS — safe idempotent retries](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/),
[HTTP preconditions и lost updates](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1.1),
[PostgreSQL 17 locks](https://www.postgresql.org/docs/17/explicit-locking.html#LOCKING-ROWS),
[FK и индексы](https://www.postgresql.org/docs/17/ddl-constraints.html#DDL-CONSTRAINTS-FK).
`expectedRevision` — application precondition в JSON; заголовок `If-Match`
не заявляется реализованным. History остаётся в существующей authoritative БД:
[отдельный event-sourcing store](https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing)
для этого ограниченного workflow не вводится.
