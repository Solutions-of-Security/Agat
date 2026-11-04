# UX-306: контракт событий использования

Статус: инженерный контракт `agat.product-usage.v1` для первого сценария
`internal_report`. [Реализация](../apps/coordinator/src/product-usage.ts) проверяет
формат, связывание и дедупликацию ограниченного snapshot без I/O. Это основа
следующих этапов instrumentation и расчётов; ingestion endpoint, durable ledger,
UI tracking и измеренные результаты пилота этим этапом не поставлены.

Источник определений — [исследование использования](./product-usage-research-2026-09-08.md#что-измерять-в-пилоте)
и [UX-306](./roadmap.md). Completion, открытие и принятие — разные факты.
Наличие события не доказывает factual accuracy или qualification модели.

## Единица и контекст

`taskId` — непрозрачный UUID бизнес-задачи, постоянный через retry и replay.
Новая попытка получает новый `runId`, сохраняя `taskId`. Новый самостоятельный
запрос получает новый task ID. Просмотр и решение другого оператора сохраняют
участника исходной задачи; проверяющий не становится новым участником пилота.

Envelope содержит только:

| Поля | Правило |
|---|---|
| `schemaVersion`, `name`, `source` | Exact v1, фиксированный вид события и `client` / `server` |
| `eventId`, `projectId`, `taskId` | Event/task UUIDv4 или UUIDv8; project — существующий непрозрачный ID, до 100 ASCII букв/цифр/`_`/`-` |
| `participantKey` | Project-scoped HMAC-SHA256 verified индивидуального subject либо явный `null` |
| `scenario`, `sample`, `cohortId` | Сейчас `internal_report`; `real` / `test` / `demo` / `unclassified`; UUID когорты либо `null`, cohort разрешён только для `real` |
| `occurredAt`, `recordedAt` | Canonical UTC с миллисекундами; recordedAt назначает coordinator при первом сохранении |
| `definition` | Pack ID/version либо null; frozen process ID/version/graph SHA все вместе либо все null |
| `execution` | `local` / `cloud` / `mixed` / `unknown`; SHA frozen requested model set либо null |
| `data` | Только фиксированные поля из таблицы событий |

Pack ID сейчас `internal-report`, версии — positive safe integers. Process ID и
ID runs/artifacts/decisions — UUIDv4/v8. Digest — 64 lowercase hex.
`requestedModelSetSha256` обозначает запрошенный frozen набор; он не доказывает
фактическую модель, стоимость или внешний provider request. Метаданные execution
берутся из серверного snapshot. Новые сценарии/поля требуют версии контракта.

HMAC использует отдельный deployment secret не короче 32 bytes и namespace
schema/project. Secret не хранится в событии; его ротация меняет participant keys
и требует явной границы наблюдения. Псевдоним не является анонимизацией. Shared
admin/API token нельзя считать отдельным человеком: до индивидуальной
идентификации `participantKey=null`. `unclassified` — безопасное значение при
отсутствии классификации, `null` cohort не создаёт фиктивный пилот. Проверенный
owner/cohort и согласованный baseline назначаются отдельным процессом USE-008.

## События и авторитетность

| `name` | `source` | `data` и момент |
|---|---|---|
| `scenario_started` | client | `entryPoint: pack/process`; явное начало задачи до подготовки/очереди; один факт на task |
| `template_installed` | server | `manifestSha256`; успешная сохранённая установка exact версии, process binding обязателен |
| `readiness_blocked` | server | Непустые уникальные `categories` из preflight taxonomy; подтверждённая сервером проверка, сообщения не копируются |
| `run_submitted` | server | `runId`, `replayOfRunId` или null; сохранённая постановка в очередь с frozen process binding |
| `run_completed` | server | `runId`, terminal `status: completed/failed/cancelled`; успешный результат требует именно `completed` |
| `result_opened` | client | `runId`, `artifactId`; первое подтверждённое открытие выбранного результата, без утверждения о прочтении/пользе |
| `outcome_recorded` | server | `runId`, `artifactId`, `decisionId`, `revision`, `decision`; сохранённое явное решение человека |

Категории blockers: graph/runtime/model/team/knowledge/embeddings/tools/scopes/
version/trigger/capacity. `accepted` и `rework_requested` уже существуют в USE-004;
`rejected` зарезервирован для будущего явного отказа и не выводится из rework,
отмены или технической ошибки.

**Проверка формата не аутентифицирует источник.** Будущий ingest принимает от UI
только собственные наблюдения; project, participant/cohort, definition/execution,
recordedAt и server facts разрешает coordinator по проверенному контексту и
durable state. Поле `source=server` из HTTP body не даёт права записать completion
или решение. Client clock не используется как серверное время: для начала и
открытия граница измерения — `recordedAt`. Серверные timestamps берутся из
сохранённого факта; occurredAt не позже recordedAt. Передача событий из
существующего audit/OTel без проверки binding не предусмотрена.

## Дедупликация и конфликты

Snapshot ограничен 10000 входных записей и 8 MiB canonical JSON, включая retries.
Неизвестные поля на каждом уровне отвергаются; error содержит только fixed code,
не rejected значение. Нормализатор пересобирает объект, не оставляя ссылок на
переданные вложенные структуры. Категории сортируются.

Event ID уникален внутри project. Exact retry сохраняет body и occurredAt;
повторная доставка может иметь другой recordedAt, сохраняется первый receipt.
Изменённое намерение с тем же ID — `event_id_conflict`.

Дополнительные ключи фактов не дают повторной доставке с новым ID увеличить
число полезных результатов:

| Событие | Ключ внутри project |
|---|---|
| scenario_started | taskId |
| template_installed | taskId + packVersion + manifest SHA |
| readiness_blocked | eventId, отдельные реальные проверки допустимы |
| run_submitted / run_completed | kind + runId |
| result_opened | taskId + runId + artifactId |
| outcome_recorded | decisionId; дополнительно artifactId + revision уникальны |

Повтор client observation берёт первый recordedAt; при равном времени — меньший
event ID для детерминизма. Server fact сохраняет исходный occurredAt. Конфликт
payload/binding не скрывается выбором «последнего»: весь snapshot отвергается.
Один task не меняет participant/sample/cohort; один run не меняет task и frozen
process binding; artifact не меняет run. Replay с присутствующим parent обязан
оставаться в его task, даже при новой process version. Отсутствие parent в
ограниченном snapshot не доказывает корректность и учитывается следующим
расчётным этапом как missing evidence. Каждая ревизия решения остаётся отдельной.

## Метрики и оставшиеся этапы

Основные KPI сохраняют определения исследования: первый полезный результат,
принятие проверенных завершённых бизнес-задач и повтор за 7 дней. Время до первого
принятия — диагностическая метрика с незавершившими участниками; missing coverage
и затраты — обязательные ограничения интерпретации. Здесь нет новых targets:
реальных baseline и подтверждённых участников пока нет.

Следующие инженерные этапы:

1. Расчёт по одному контракту: task-level dedupe, cohort window, явный timezone,
   full 7-day observation, unfinished/unreviewed/rework/rejected и missing data.
   Нулевой знаменатель — N/A; отсутствие затрат и разбивки phase timings не ноль.
2. Durable server ledger и связь task → runs → artifacts → decisions с
   авторизацией/RLS/idempotency; atomic hooks не меняют выполнение run.
3. Consumer observations начала/открытия, восстановление UUID после retry,
   фильтр test/demo и явный отчёт о покрытии. Client не отправляет prompt/output.
4. USE-008 на разрешённых пользовательских данных: реальные две команды,
   владельцы и baseline. Synthetic tests не становятся результатами пилота.

OTel traces продолжают описывать длительные операции, события — точки жизненного
цикла. Dynamic task/run/event IDs не становятся именами событий или labels
операционных счётчиков. Контракт не включает collector export и не добавляет
внешний tracking endpoint.

## Проверка и основания

[Contract tests](../apps/coordinator/test/product-usage.test.ts) проверяют семь
видов, private-field rejection, отсутствие отражения secrets в errors, client/server
labels, invalid dates/versions, unknown participant, real/test boundary, lost-response
retry, semantic duplicates, changed intent, frozen bindings, replay, revisions,
cross-project namespace, keyed pseudonyms и row/byte limits.

Сверка 25.10.2026: [OpenTelemetry events](https://opentelemetry.io/docs/specs/semconv/general/events/)
описывает фиксированные имена, timestamps, поля и отличие checkpoint от span;
[handling sensitive data](https://opentelemetry.io/docs/security/handling-sensitive-data/)
обосновывает минимизацию и ограничения hashing. Применение этих принципов к
task-level контракту — инженерное решение Агата, а не готовая OTel product-funnel
семантика. Политика запрещённых payload полей задана локальным UX-306.
