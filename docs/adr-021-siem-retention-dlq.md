# ADR-021: acknowledged SIEM delivery с retained DLQ

- Статус: принят и реализован
- Дата: 2026-08-31
- Связано: [SIEM retention/DLQ](./siem-retention-dlq.md), [Fleet/HA 1.7](./fleet-ha-1.7.md), [ADR-017](./adr-017-fleet-ha-cell.md)

## Контекст

At-least-once exporter v1.7 считал любой HTTP 2xx доставкой и бесконечно возвращал failures в pending. Это не различало partial/empty sink acknowledgement, permanent payload/auth failures и временный outage. Poison event мог бесконечно занимать очередь, а operational retention и управляемого решения оператора не было.

## Решение

Sink обязан вернуть exact batch acknowledgement. Retryable failures получают bounded exponential retry; permanent 4xx или исчерпание attempts атомарно создают immutable redacted DLQ snapshot и переводят outbox в `dead`. Admin может replay или resolve только с reason и typed confirmation; auditor имеет project-scoped read-only доступ. Open DLQ retention никогда не удаляет автоматически. Delivered/replayed/resolved operational rows очищаются bounded maintenance, но authoritative `events` остаются неизменными.

Response body не читается; хранятся status/ack hash и redacted payload hash. Tenant DB role не получает privileges на global DLQ/retention tables.

## Последствия

Положительные:

- poison events видимы и не создают бесконечный retry storm;
- 2xx без доказуемого batch contract не теряет событие молча;
- DLQ replay сохраняет исходный idempotency key;
- открытый incident нельзя убрать одной настройкой retention;
- operational state можно очищать без удаления audit ledger.

Отрицательные:

- sink должен реализовать Agat ack header и dedupe;
- ack не даёт exactly-once при timeout после commit;
- admin resolve остаётся governance action и требует внешнего change/four-eyes workflow там, где это предписано policy;
- application retention не заменяет SIEM/legal archive policy.

## Отвергнутые варианты

- Любой 2xx = delivered: допускает silent partial acceptance.
- Infinite retry: создаёт starvation и скрывает permanent errors.
- Автоматически удалять open DLQ по TTL: уничтожает unresolved evidence.
- Копировать raw event/error body: расширяет секретный data surface.
- Автоматически replay после region-loss: может повторить permanent poison или нарушить sink change window.

## Проверка решения

Acceptance требует missing/mismatched ack failure, retry/permanent classification, max-attempt DLQ, payload redaction/hash, open retention deny, controlled replay/resolve, multi-replica claim и tenant privilege negative tests.
