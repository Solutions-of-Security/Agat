# Версии пакета внутреннего отчёта

Этап SP-001/SP-005 добавляет `internal-report` v2 для существующих коллекций
проекта, историю установок и отдельное переключение активной версии. Исторический
[пакет v1](./internal-report-pack.md) остаётся совместимым. Это поставка механизма;
EVAL-101/EVAL-103, реальное принятие отчёта и пилот остаются открытыми.

## Контракт release

`getInternalReportPack()` по умолчанию возвращает неизменённую v1. Её manifest
SHA-256 — `65cd83ca1606b39d7ab9206520304fd3ec416b00e0c0d3653dddf066847213a7`.
`getProcessPackReleases()` возвращает v2 и v1; каждое чтение создаёт отдельную копию.
Manifest v2 содержит `solution-pack.v1`, определения трёх ролей, граф с placeholders,
полные input/output schemas, tools/knowledge requirements, policy, execution
profile и обязательные evaluation/human acceptance gates. SHA покрывает всё это
содержимое. Secrets, scopes и содержимое пользовательских документов в manifest
не входят.

V2 сохраняет трёхролевой `single/tool_loop_v1` baseline с тремя итерациями на роль.
Он явно `unqualified`: выбор single/chain/team требует повторных EVAL-103 trials.
Установка сохраняет выбранную модель, ID коллекций и опубликованную версию
процесса. Определения агентов остаются обычными редактируемыми объектами проекта;
точный effective prompt/runtime закрепляет execution snapshot каждого запуска.
Редактирование агентов не восстанавливается переключением release и требует новой
квалификации. История установки описывает созданную конфигурацию, а не обещает
отменить пользовательские изменения.

## Документы и результат v2

1. Загрузить разрешённые документы через общий раздел **Знания**. Используется тот
   же API, parser, original bytes, provenance, preview и indexing, включая
   [CSV/XLSX](./tabular-knowledge-ingest.md).
2. Выбрать 1–20 разных существующих коллекций текущего проекта. V2 не создаёт
   учебную коллекцию и не копирует документы. Коллекции закрепляются как обязательные
   в опубликованном графе; embeddings/model/worker readiness проверяет общий USE-002.
3. Передать [report-request.v1](./business-input-contracts.md) с вопросом, периодом
   и требованиями. Невалидный input останавливается до создания run.
4. Проверяющий возвращает [business-report.v1](./business-output-contracts.md).
   Неверные числа/ссылки, invalid JSON и `insufficient` не проходят к согласованию
   или итоговому артефакту. Проверка арифметики не доказывает истинность операндов.
5. После решения человека сохраняется [проверяемый Markdown](./validated-report-markdown.md).
   Источники повторно проверяются после ожидания решения. Внешние MCP-инструменты
   запрещены, частичный старт не разрешён. До согласования итогового файла нет.

## API установки и переключения

Существующий UI установщика в этом backend-этапе продолжает показывать v1. UI выбора
release, документов, diff и структурированного запуска — следующий этап. API v2 уже
исполняется через тот же process runtime.

| Запрос | Поведение |
| --- | --- |
| `GET /api/v1/process-packs` | Совместимый `packs` с v1; новый `releases` с v2/v1 и установкой каждой версии; отдельный `activeInstallation` |
| `POST /api/v1/process-packs/internal-report/preflight` | `{version, manifestSha256, model, knowledgeCollectionIds?}`; read-only общий preflight, `activeInstallation` и `changes` с before/after |
| `POST /api/v1/process-packs/internal-report/install` | То же тело; первая установка release `201, created=true`, повтор `200, created=false`; решение о создании принимается под project lock |
| `GET /api/v1/process-packs/internal-report/history` | Exact manifests и записи установок проекта, отдельно активная запись |
| `POST /api/v1/process-packs/internal-report/activate` | `{version, manifestSha256, expectedActiveManifestSha256}`; CAS переключение на уже установленный release, общий preflight повторяется |

Для v1 `knowledgeCollectionIds` не передаются. Для v2 они обязательны; чужая или
удалённая коллекция не может быть установлена. Неизвестные параметры v2 отклоняются.
Установленная версия фиксирует модель и bindings: повтор с другой конфигурацией
не перезаписывает объекты. Настройку собственного процесса выполняют через обычный
редактор и новую публикацию.

В холодном проекте первая установка становится активной. При существующей активной
версии установка следующей создаёт отдельные ресурсы и историю, сохраняя активный
указатель. Для update затем вызывают `activate` с текущим digest; для rollback — тот
же endpoint с прежней версией. Изменившийся активный digest требует нового просмотра
diff. Повтор уже выполненного переключения идемпотентен. Runtime outage, отсутствующие
модели и незавершённые embeddings остаются видимыми blockers; переключение не
разрешает старт в обход preflight. Зависимости, блокирующие даже очередь, запрещают
переключение.

`changes` сравнивает bundled release definitions и настройки установки с активной
версией, включая роли, schemas, граф, tools/policy/profile/eval и source bindings.
Это не diff локальных пользовательских черновиков. Для новой учебной коллекции v1
ID ещё неизвестен до install, поэтому соответствующий `after` равен `null`;
для уже установленной цели возвращаются точные ID.

Install/activate требуют `admin`/`designer`; чтение — существующие READ_ROLES.
Они не запускают модель, не выдают права или credentials, не изменяют active instances,
прежние графы, drafts, run snapshots и результаты. Новая версия всегда запускается
с закреплёнными `processId/processVersion`, а не с произвольной последней публикацией.

## Хранение, миграция и recovery

Schema v31 добавляет `process_pack_history` с ключом `(project_id, pack_id, version)`.
Приложение только добавляет записи, не заменяет прежние manifests. При чтении истории
пересчитывается SHA содержимого manifest. `process_pack_installations` сохраняет
прежний ключ и служит активным указателем. Обе таблицы имеют PostgreSQL tenant RLS.

Старая v1 без строки истории видна read-only из прежней записи установки. Перед
изменением указателя она атомарно сохраняется в историю с исходным digest и IDs.
Роли/процесс/история создаются в одной transaction; конфликт имён или исчезнувшая
коллекция оставляют прежнее состояние. Повтор после restart возвращает прежние IDs.
PostgreSQL сериализует install/activate блокировкой строки проекта, включая запросы
разных coordinator replicas. События `process.pack.installed` и
`process.pack.activated` сохраняют версии/digests/resource IDs без документов.

Перед запуском нового PostgreSQL runtime нужно применить текущую
[отдельную schema/admission Job](./postgresql-migration-job-runtime-role.md).
Runtime остаётся DDL-free; Kubernetes init gate ожидает успешный marker v31.
SQLite обновляет schema существующим transactional initializer. Offline state
migrator переносит новую таблицу вместе с прежними process IDs/manifests.

История и compare-before-switch выбраны по практикам
[versioned rollout/rollback Kubernetes](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/).
Проверка exact digest и явных параметров опирается на
[SLSA verification guidance](https://slsa.dev/spec/v1.2/verifying-artifacts).
Это применение отдельных принципов, а не заявление SLSA qualification.

## Проверки этапа

- Целевая регрессия контракта, preflight, Markdown и v1/v2 проходит на настоящем
  SQLite/coordinator HTTP; worker outputs и embeddings в этих тестах синтетические.
- Изолированный PostgreSQL 17: четыре одновременных install через два настоящих
  coordinator-процесса создают один release и только один HTTP 201. Проверены
  повтор, update/rollback history и отрицательный tenant RLS.
- Реальный SQLite → PostgreSQL import/verify сохраняет manifest SHA и process ID;
  rehearsal rollback возвращает bootstrap baseline.
- Проверены atomic failure после создания ролей, legacy history без writes при GET,
  restart, stale activation, чужие/удалённые sources, invalid input/result,
  `insufficient`, approval и точные SHA скачиваемого Markdown.

Локальный технический PASS и удалённые CI gates фиксируются отдельно в
[журнале реализации](./implementation-progress-2026-10-25.md). Этот этап не закрывает
RUN-101, SAFE-101, полноценный SP-003 lifecycle, PDF/DOCX export или реальный пилот.
