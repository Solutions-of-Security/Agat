# ADR-019: Whole-cell residency-aware region-loss DR

- Статус: принят
- Дата: 2026-08-31
- Решение: schema v24
- Связано: [ADR-017 Fleet HA-cell](./adr-017-fleet-ha-cell.md), [ADR-018 S3 authority](./adr-018-s3-artifact-authority.md), [Region-loss DR](./region-loss-dr.md)

## Контекст

Fleet HA-cell уже имела multi-replica coordinator, managed PostgreSQL restore/failover gates и versioned S3 authority. Однако запуск восстановленного snapshot в другом регионе оставался ручным: region/residency config не доказывал единственного writer, PostgreSQL/S3/Temporal recovery points могли расходиться, а возврат старого primary создавал split-brain. Нужен исполняемый переход при потере региона без обещания application-level active-active replication.

## Решение

Выбран whole-cell active/passive transition только внутри заранее разрешённого residency domain. Внешний control plane сначала полностью fence source. Затем два distinct approver подтверждают HMAC-sealed evidence из PostgreSQL restore, version-preserving S3 replica и Temporal recovery. Snapshot-bound plan активируется отдельной suspended break-glass Job одной serializable PostgreSQL transaction.

Schema v24 хранит одну active activation и monotonic cell `write_epoch`. Runtime role имеет read-only admission, tenant role не видит DR control tables. Target coordinator требует exact activation ID/epoch/region/residency/S3 bucket; stale source не может пройти startup после следующей активации. Failback является новым reverse transition и снова увеличивает epoch.

## Рассмотренные варианты

| Вариант | Решение |
|---|---|
| application active-active между регионами | отклонён: конфликт PostgreSQL/Temporal/object side effects, split-brain и неясная residency authority |
| автоматический failover по health check | отклонён: health не доказывает fencing source и целостность трёх recovery systems |
| перенос отдельных projects | отклонён: global state/outboxes/releases и shared Temporal namespace нарушают атомарную границу |
| ручное редактирование region/bucket rows | отклонён: нет snapshot binding, monotonic epoch, approvals и idempotent audit |
| DNS-only переключение на restored DB | отклонён: stale source credential/runtime сохраняет write capability |
| fenced whole-cell activation с sealed evidence | выбран: одна authority, измеримый RPO/RTO/skew и fail-closed startup |

## Последствия

Положительные: residency policy исполняема; source fencing и four-eyes обязательны; restored state меняется атомарно; worker credentials/leases/outboxes безопасно переводятся; stale runtime блокируется exact epoch; failback имеет тот же доказуемый процесс.

Отрицательные: автоматического regional failover нет; outage продолжается, пока источник нельзя надежно fence; нужен независимый provider adapter/evidence key и восстановление Temporal; whole-cell recovery может быть медленнее project-level routing; migration credential временно доступен break-glass Job.

## Инварианты пересмотра

ADR пересматривается, если все authoritative systems получают единый consensus/fencing token, появляется доказуемая per-project database/Temporal/object boundary либо provider не сохраняет exact S3 version IDs. Изменение не может убирать single-writer guarantee, residency allowlist, monotonic epoch, exact snapshot binding, two-person approval и auditable failback.
