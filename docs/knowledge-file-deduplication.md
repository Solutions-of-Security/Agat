# Повторная загрузка и переименование файлов

USE-003, 25 октября 2026. File upload использует SHA-256 **исходных bytes**
в пределах одной коллекции проекта. Повторная загрузка PDF/DOCX/CSV/XLSX
под другим именем возвращает существующий документ. Повторные chunks,
embedding job и событие постановки в очередь не создаются.

Первый upload возвращает HTTP `201` и `upload.disposition: created`.
Повторный — HTTP `200` и `upload.disposition: existing`. Верхнеуровневая форма
document DTO сохраняется; `upload.requestedName` показывает имя запроса.
UI открывает canonical document и сообщает его сохранённое имя.

## Идентичность и намерение

- Ключ — collection + server-computed original SHA-256. Имя не входит в ключ;
  одинаковый извлечённый текст недостаточен для reuse.
- Другая коллекция или проект — отдельный scope. Права проверяются до поиска
  совпадения и parser; hashes другого проекта не раскрываются.
- MIME должен совпасть с сохранённым. Явно указанный непустой `sourceUri`
  должен совпасть; несовместимый запрос отклоняется. Пустой URI означает
  отсутствие нового указания и сохраняет уже записанный источник.
- Canonical name, source URI, bytes, hashes, chunks, vectors, timestamps,
  extraction revision и квитанция не переписываются. Reuse не подтверждает
  неизвестный/ошибочный разбор и не объявляет индекс готовым.
- **Переиндексировать** — отдельная операция: запускает текущий parser,
  меняет revision и сбрасывает применимое [подтверждение текста](./document-extraction-review.md),
  даже при совпадении bytes.
- Текстовый ingest сохраняет прежний контракт. Семантическая дедупликация,
  объединение разных источников и автоматическое удаление старых копий
  не входят в этот этап.

Старые копии сохраняются вместе с историей и ссылками runs. Для нового upload
выбирается самая ранняя запись по `created_at, id`. Готовность модели или статус
проверки не меняют выбор. Автоматическое удаление исторических копий недопустимо.

## Конкуренция и ошибки

Поиск совпадения выполняется перед parser и повторяется после асинхронного
разбора в транзакции под `FOR NO KEY UPDATE` коллекции. Несколько replicas,
одновременно получившие ещё неизвестные bytes, создают одну запись.
Сохраняется порядок блокировок collection → existing job → document.

Занятость двух parser slots возвращает `KnowledgeParserBusyError` / HTTP `503`.
Она не считается ошибкой формата и не сохраняет failed document. При reindex
сохраняется предыдущий текст и его решение. После освобождения parser запрос
можно повторить. Настоящая ошибка разбора сохраняется; повторная загрузка
открывает тот же failed document с доступным оригиналом.

Schema **33**, contract `agat-knowledge-file-dedupe-v33`, добавляет non-unique
partial index по `(collection_id, original_sha256)`. Он допускает существующие
копии. PostgreSQL обновляется отдельной [migration/admission Job](./postgresql-migration-job-runtime-role.md);
[offline migration](./sqlite-postgresql-migration.md) сверяет все сохранённые rows.

## Основание и проверки

Scope и обработка несовместимых параметров следуют принципу явного намерения
из [Amazon Builders' Library](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/):
одинаковые payload не всегда означают одну операцию. Здесь reuse ограничен
file upload одной коллекции; reindex и другие коллекции имеют отдельный смысл.
Сериализация использует [row locks PostgreSQL 17](https://www.postgresql.org/docs/17/explicit-locking.html).

Проверки покрывают сохранение готовых embeddings/review и provenance,
переименование, concurrent cold uploads, parser saturation, несовместимые
metadata, одинаковый текст при разных bytes, другие проекты/коллекции,
failed/legacy states и сохранение старых копий. HTTP различает 201/200;
PostgreSQL проверяет четыре запроса через две настоящие coordinator replicas.
Desktop/mobile UI проверяет notice, прежнее имя и ID, одну запись в коллекции,
неизменную квитанцию и последующий явный reindex. Синтетические fixtures
не являются бизнес-квалификацией.
