# Явный transport в профиле RAG-измерений

Дата: **28.09.2026**. Подготовка повторного измерения после [HTTP isolation](./knowledge-http-cancellation.md) и [проверки restart coordinator](./coordinator-completion-recovery.md).

`run-decision-rag-workflow.py` и `benchmark-decision-workflow.ts` принимают `--embedding-transport isolated|session`; default диагностического launcher — `isolated`. Оба плана сохраняют выбранное значение до нагрузки. Harness передаёт его Python worker через CLI, поэтому унаследованный `AGAT_EMBEDDING_TRANSPORT` не меняет зафиксированный профиль опыта. Другие вызывающие `workflowPhase` сохраняют прежнее поведение, если не передают этот option.

Workflow plan теперь сохраняет SHA `embedding_http.py`, `embedding_transport.py`, `telemetry.py` и `web_tools.py` вместе с прежними исходниками. Это включает общий helper для embedding, primary и knowledge HTTP. Independent verifier отвергает разные или отсутствующие с одной стороны значения transport, неизвестные режимы и отсутствие SHA двух transport modules. Фактические файлы затем проверяются существующим контролем frozen sources.

Старые планы, в которых transport не записан ни launcher, ни workflow, получают `legacy_unspecified`. Им не приписывается текущий default. Формат расширен совместимыми полями; production worker defaults, model inference и routing не изменены.

Проверки:

- **5 workflow/RAG tests** с настоящим Python worker. В тесте намеренно задан неверный inherited transport: явный `isolated` завершает нагрузку, явный `session` доходит до проверяемого отказа на неверных embedding vectors.
- **5 verifier tests**: оба режима, старые планы, несовпадающие/односторонние значения, неверные типы и отсутствующие/некорректные SHA.
- **293 coordinator tests**, **26 gated skip**; strict TypeScript — pass.
- **12 Node + 323 Python tests** документации, каталог процессов и локальные ссылки — pass.

[SHA и журналы проверок](./evidence/2026-09-28/rag-http-isolation/profile-checks.json) фиксируют проверенную реализацию. Эти тесты проверяют протокол опыта; результаты повторного прогона реальных моделей ещё должны быть записаны отдельно.

Первый CI остановился в отдельном Artifact Store job: после restart MinIO вышел с `Unable to initialize console server: Specified port is already in use`. Тестовый launcher теперь задаёт внутренний console port `9001`, как существующий Kubernetes deployment; новый host port не публикуется. [MinIO документирует](https://min.io/docs/minio/kubernetes/upstream/administration/minio-console.html) случайный console port по умолчанию и `--console-address` для явного назначения. Владелец конфликтовавшего порта из исходного лога неизвестен. Повторный полный S3-сценарий с migration, cache, outbox, version deletion и restart прошёл: [лог](./evidence/2026-09-28/rag-http-isolation/artifact-store-console.log), [исходная ошибка и SHA исправления](./evidence/2026-09-28/rag-http-isolation/artifact-store-console-checks.json).

```bash
python scripts/run-decision-rag-workflow.py \
  --manifest .local-models/decisions/decider-2b.json \
  --policy docs/qualification/local-decisions/policy.shadow.v1.json \
  --fixture docs/qualification/local-decisions/performance/rag-workflow.fixture.json \
  --design paired-rag --embedding-transport isolated \
  --evidence-dir docs/qualification/local-decisions/performance/evidence/new-run
```

Python для launcher должен иметь закреплённый MLX runtime; `python3` в `PATH` используется для обычного worker. Запуск требует доступных локальных файлов моделей и нового пути evidence. Реальный контроль качества, независимые документы и production SLO не следуют из проверок harness.
