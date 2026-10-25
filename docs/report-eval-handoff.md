# Передача отчёта в подготовку Golden eval

Backend-контракт подготавливает **development candidate**, связанный с точным
отчётом и решением человека. Подготовка не создаёт dataset, experiment, model
call, tool approval или run. Отдельная явная операция сохраняет проверенный
reference/rubric в первой версии dataset с lineage и durable retry receipt.
Schema 35 не меняется. UI этих операций — следующий этап.

## API и условия

`POST /api/v1/artifacts/:id/eval-candidate` доступен admin/designer текущего
проекта с проверенной авторизацией. Эти роли совпадают с созданием Golden
datasets. Ответ — JSON с `Cache-Control: no-store`. Подготовка read-only;
повтор не требует request ID и не добавляет audit/event записи с содержанием.

```json
{
  "expectedSha256": "<64 lowercase hex исходного Markdown>",
  "expectedRevision": 1
}
```

Ревизия должна быть сохранённой текущей human decision (1–1000). Неизвестные
поля, coercion, отсутствующий SHA/revision отклоняются. Чужой artifact — 404;
несовпадение версии/решения/контекста — 409; ошибка проверки bytes — 503. Сначала
нужно перечитать историю, затем явно повторить подготовку. API не записывает
решение вместо оператора. Действуют [условия canonical report](./report-result-decisions.md).

## Содержание и происхождение

Profile `business-report-eval-candidate.v1` содержит:

- `purpose: development`, `holdoutEligible: false`, `referenceReviewRequired: true`;
- source: project/artifact/SHA, run/report stage, frozen process instance/version/
  graph SHA, decision ID/revision/kind/comment;
- точный вход однозначного предшествующего агентного этапа, чей output совпадает
  с JSON на входе Markdown renderer; output hash и schema hash сверены с receipt;
- source agent/stage, hashes prompt/definition, requested model, runtime/profile;
- `example` с input и collection IDs; referenceOutput пустой, must/must-not фразы
  не выводятся из сгенерированного ответа;
- исходный проверенный JSON отдельно в `observedOutput`, его SHA и input SHA;
- `currentKnowledgeSnapshot`: IDs и fingerprint текущего корпуса, без копии
  документов/chunks. Это текущий снимок, а не восстановленный исторический RAG;
- `contentSha256`: SHA-256 серверного `JSON.stringify` объекта без этого поля.

У первого agent stage без отдельного stage_input используется run input — те же
правила, по которым вход был выдан worker. Агент выбирается по exact JSON bytes,
позиции перед report stage и pinned business-report contract. Если совпадений
несколько, нет snapshot/контракта, snapshot миграционный либо сохранённый graph
не совпадает с решением, автоматическая подготовка отклоняется. Исходный файл
остаётся доступен для ручной подготовки. Содержимое текущего agent definition
не подставляется вместо frozen snapshot; runtime config/secrets не включаются.

Оба вида решения (`accepted`, `rework_requested`) сохраняются как provenance.
Даже принятому результату не назначается reference автоматически. Для rework
его observedOutput и комментарий позволяют подготовить контрпример; исправленный
reference задаёт человек. Принятие файла не доказывает истинность всех фактов.

## Границы воспроизведения

`replay: { automatic: false, scope: single_agent, context: requires_review }`
требует проверить целевого агента, prompt/schema, retrieval, разрешённый corpus
и human rubric. Golden eval сейчас исполняет отдельного агента: packet не
является разрешением повторить произвольный process с HTTP/MCP side effects.
Известная модель/profile не означает её квалификацию на этом сценарии.

Input и observedOutput ограничены 100000 UTF-16 units каждый, как примеры
существующего Golden dataset; превышение отклоняется без усечения. Canonical
artifact ограничен 800000 bytes. Fingerprint собирается для не более 1000
документов / 10000 chunks в выбранных коллекциях; больший corpus требует
отдельной подготовки. Artifact Store проверяет целостность фактических bytes
до выдачи. После проверки повторно читаются source и latest decision.

Это подготовка исторически связанного материала: новое решение после выдачи
не изменит уже выданный packet. Операция фиксации ниже снова проверяет исходную
связь и сохраняет lineage версии.
Повторный просмотр/корректировка development примера исключают его использование
как независимого holdout без отдельного протокола.

## Явная фиксация versioned dataset

`POST /api/v1/artifacts/:id/eval-dataset`, admin/designer, требует:

- `requestId`: новый UUID v4, неизменный для retry одного намерения;
- `expectedSha256`, `expectedRevision`, `expectedCandidateSha256` из просмотренного
  packet — exact report/decision/context;
- `name` (1–120 units), необязательный `description` (до 2000), непустой
  `referenceOutput` (до 100000 UTF-16 units / 200000 UTF-8 bytes);
- `rubric`: 1–20 критериев существующего Golden contract;
- `reviewConfirmed: true`: явное подтверждение проверки reference, rubric,
  контекста и хранения копии примера в dataset проекта.

Input/collection IDs сервер берёт из свежего candidate. Поля actor, purpose,
input и произвольный source не принимаются. Проверяются schema, расчёты и
run-scoped sources reference; typed `insufficient` с ограничениями допустим как
ожидаемый отказ. Эти проверки и human attestation не объявляют факты доказанными.
Пустой reference, неверные источники/числа или отсутствующий review не записывают
dataset. Значимые внешние пробелы stage input сохраняются для Golden executor.

Операция atomic создаёт dataset/v1, example, rubric и один metadata-only event
`business.report.eval.dataset.created`. Ответ содержит `datasetId`,
`datasetVersion: 1`, `datasetContentSha256`, `candidateSha256`, `created`;
201 для создания, 200 для повтора, `Cache-Control: no-store`.
В event нет reference, rubric, комментария или названия dataset.

Namespace повтора включает project, полный verified subject и request UUID.
Dataset ID — детерминированный UUIDv8 из первых 128 bits SHA-256 канонического
JSON массива `[profile, project, subject, requestId]` с version/variant bits по
[RFC 9562 §5.8 / Appendix B.2](https://www.rfc-editor.org/rfc/rfc9562.html#section-5.8).
Project/RLS и проверка payload hash защищают доступ и intent. Такой receipt
помещается в существующий indexed dataset ID без новой lookup-таблицы.

Exact retry возвращает исходную v1 даже после следующего решения, новой версии
dataset или удаления source run; изменённый payload того же request ID — 409.
Новое намерение повторно проверяет живой source/candidate. Повторы после purge
исходника не создают его заново. Одинаковое название другого dataset — 409.
Все Artifact Store обращения, reference checks и corpus fingerprints выполняются
до короткой transaction. В ней блокировки project → artifact совпадают с
decision writer, затем повторно проверяются source SHA и latest decision.

Происхождение хранится в `eval_dataset_versions.knowledge_snapshot_json.reportImport`
и включается в version content SHA: source IDs/hashes/версия/решение, candidate
hash, actor hash, request hash, reference hash/validation, purpose development,
holdoutEligible false и attestation. Комментарий решения и runtime config не
дублируются. Неизменная v1 остаётся источником lineage в истории dataset;
последующие версии общего редактора не получают новое source attestation
автоматически. Подготовка experiment/prompt — отдельное явное действие.

Копия input/reference в Golden dataset хранится независимо от source run.
Удаление source не удаляет ранее явно сохранённый пример: к нему отдельно
применяются правила хранения Golden данных проекта. SQLite → PostgreSQL
сохраняет lineage JSON, reference и version SHA; новая DDL migration не нужна.

## Проверки и основания

Целевые SQLite/API/OIDC tests проверяют frozen bindings и hashes, точный JSON
агента вместо Markdown, blank reference для обоих решений, повтор, отсутствие
run/stage/artifact/event/dataset/experiment writes, 401/403/404/409, no-store,
corrupt bytes, missing snapshot, graph drift, неоднозначные stages и лимит
10000 chunks. Import tests дополнительно проверяют atomic rollback при отказе
example insert, intent/actor/project namespaces, durable retry после source
purge, typed reference и совместимость с явным Golden experiment. Репличный
PostgreSQL replay проверяет одинаковый packet и четыре concurrent imports
(201/200), один dataset/event, 409 и tenant isolation. Offline migration
переносит dataset/v1, lineage и reference SHA. Итоговые результаты фиксируются в
[журнале реализации](./implementation-progress-2026-10-25.md).

Основания дизайна: [MLflow dataset lineage/digest/source](https://mlflow.org/docs/latest/ml/dataset/)
для отслеживания происхождения; [Google: разделение development и test sets](https://developers.google.com/machine-learning/crash-course/overfitting/dividing-datasets)
для независимой оценки. Это применение принципов к текущему контракту Агата;
MLflow не добавляется в зависимости, synthetic checks не считаются human review
или испытанием реального пилота.
