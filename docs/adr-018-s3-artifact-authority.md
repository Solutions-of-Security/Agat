# ADR-018: PostgreSQL metadata authority и S3 payload authority

- Статус: принят
- Дата: 2026-08-31
- Решение: schema v23
- Связано: [Fleet HA-cell](./adr-017-fleet-ha-cell.md), [S3 Artifact Store](./s3-artifact-store-lifecycle.md)

## Контекст

PostgreSQL BYTEA сделал artifact download независимым от coordinator replica, но payload увеличивал database/WAL/PITR и связывал restore control plane со всем объёмом файлов. Нужен S3-compatible store без dual-write, потери project isolation или ложной distributed transaction.

## Решение

PostgreSQL остаётся authority существования, ownership, hash/size, retention, state и delete intent. S3 exact object version становится единственным authority bytes после `storage_backend='s3'`. Запись имеет порядок `conditional PUT → HEAD verify → DB metadata/audit commit`; удаление — `DB outbox claim → exact-version DELETE → DB tombstone`. Неопределённость после crash разрешается reconciliation и idempotent outbox, а не синхронным компенсирующим delete.

Актуальные object versions никогда не истекают по bucket lifecycle. Только PostgreSQL retention/legal hold может создать delete intent. Bucket lifecycle очищает incomplete multipart uploads и noncurrent versions. Project tenant не видит global outbox; cascade trigger переживает удаление parent metadata.

## Рассмотренные варианты

| Вариант | Причина решения |
|---|---|
| оставить BYTEA навсегда | просто, но неограниченно растут DB/WAL/PITR/RTO |
| filesystem/shared PVC | не переносим между replicas/regions, слабая integrity/durability boundary |
| DB+S3 dual-write с fallback | две authority и скрытая divergence; отклонено |
| presigned direct worker upload | расширяет trust boundary worker и усложняет commit protocol; отложено |
| удалить S3 object при DB rollback | timeout не доказывает delete; может удалить object конкурентного retry |
| content metadata в PostgreSQL + versioned S3 + reconciliation | выбран: явная authority, bounded uncertainty, provider portability |

## Последствия

Положительные: PostgreSQL backup больше не несёт новые payload bytes; download любой replica остаётся authenticated и hash-verified; exact versioning и outbox дают идемпотентное удаление; orphan window измерим и безопасно очищается после grace.

Отрицательные: artifact write/download зависят от S3 availability; отсутствует атомарность между системами; требуется отдельный bucket/IAM/KMS/Object Lock/replication lifecycle; reverse migration S3→BYTEA отсутствует. Эти ограничения считаются частью контракта и не маскируются fallback.

## Инварианты пересмотра

ADR пересматривается, если появляется доказуемый transactional object API, direct-upload становится необходим по размеру/latency или region-loss DR требует смены authority. Любое изменение обязано сохранить single writer/authority, project/residency boundary, checksum verification и auditable deletion.
