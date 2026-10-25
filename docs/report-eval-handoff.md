# Передача отчёта в подготовку Golden eval

Backend-контракт подготавливает **development candidate**, связанный с точным
отчётом и решением человека. Dataset, experiment, model call, tool approval и
новый run при этом не создаются. Schema 35 не меняется. UI передачи и фиксация
проверенного примера в versioned dataset — следующие этапы.

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

Это подготовка исторически связанного материала, не атомарная публикация
dataset: новое решение после выдачи не изменит уже выданный packet. Фиксация
примера должна снова проверять исходную связь и сохранять lineage версии.
Повторный просмотр/корректировка development примера исключают его использование
как независимого holdout без отдельного протокола.

## Проверки и основания

Целевые SQLite/API/OIDC tests проверяют frozen bindings и hashes, точный JSON
агента вместо Markdown, blank reference для обоих решений, повтор, отсутствие
run/stage/artifact/event/dataset/experiment writes, 401/403/404/409, no-store,
corrupt bytes, missing snapshot, graph drift, неоднозначные stages и лимит
10000 chunks. Репличный PostgreSQL replay проверяет одинаковый packet на двух
coordinators и tenant isolation. Итоговые результаты фиксируются в
[журнале реализации](./implementation-progress-2026-10-25.md).

Основания дизайна: [MLflow dataset lineage/digest/source](https://mlflow.org/docs/latest/ml/dataset/)
для отслеживания происхождения; [Google: разделение development и test sets](https://developers.google.com/machine-learning/crash-course/overfitting/dividing-datasets)
для независимой оценки. Это применение принципов к текущему контракту Агата;
MLflow не добавляется в зависимости, synthetic checks не считаются human review
или испытанием реального пилота.
