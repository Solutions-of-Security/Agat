# Потерянное подтверждение embedding COMMIT

Дата: **27.09.2026**. Продолжение [HTTP-профиля embedding](./embedding-http.md). Проверяется сохранность индекса и восстановление после успешного SQL COMMIT, ответ которого не получил coordinator.

## Протокол и решение

Test-only TCP proxy на `127.0.0.1` выбирает точный `application_name` обычного собранного coordinator и транзакцию после `UPDATE knowledge_chunks SET embedding_model`. Proxy передаёт COMMIT PostgreSQL 17.6, удерживает ответ и сообщает тесту о завершении только после настоящего `CommandComplete(COMMIT)`. Независимое соединение проверяет сохранённые chunks, job, document и событие до разрыва или timeout. Credentials и SQL bodies не сохраняются; fixture закрывает только собственные процессы, соединения и контейнер.

По [контракту PostgreSQL COMMIT](https://www.postgresql.org/docs/17/sql-commit.html) изменения уже видны другим транзакциям; потеря подтверждения не доказывает rollback. [RFC 9110, §9.2.2](https://www.rfc-editor.org/rfc/rfc9110.html#name-idempotent-methods) требует оснований для автоматического повтора неидемпотентного запроса. Поэтому проверяется отсутствие повторной записи и продолжение через актуальное состояние job, а не через безусловный replay POST.

Три сценария используют реальный HTTP endpoint completion:

| Сценарий | Сохранено до fault | Ожидаемое продолжение |
|---|---|---|
| Терминальная партия, разрыв после COMMIT | Один вектор, completed job, ready document, одно ready-событие | HTTP 503 с неизвестным исходом; новый poll не выдаёт готовый документ |
| Частичная партия, разрыв после COMMIT | 32 из 33 векторов, pending job, indexing document, одно batch-событие | После restart новый worker получает только последний chunk с новым lease |
| Терминальная партия, удержанный ответ | Один вектор и одно ready-событие | Клиентский query timeout даёт HTTP 503; pool заменяет соединение, health и maintenance продолжаются |

Для третьего сценария существующий `AGAT_POSTGRES_STATEMENT_TIMEOUT_MS=1000` задаёт как PostgreSQL `statement_timeout`, так и клиентский `pg` `query_timeout`; connect timeout — 500 мс. [Документация pg.Client](https://node-postgres.com/apis/client) различает эти два ограничения. Ответ ожидается не дольше 2500 мс после независимой проверки COMMIT. Штатные default timeout не меняются; этот предел теста не является производственным SLO.

## Состояние после отказа

Одинаково проверяются точные chunk IDs и JSON-векторы, model/dimensions, embedded timestamps, число chunks, job owner/lease/expiry, failures, ошибки документа и точные event IDs/payloads. Сразу после HTTP 503 и после restart старые complete/fail получают 400, renew — 404; снимок сохранённого состояния не меняется. Проверка `/fail` моделирует сообщение worker об ошибке получения ответа, которое не должно ухудшить уже сохранённый документ.

Ожидается реальный maintenance heartbeat main после замены соединения и после restart, затем повторяется сравнение снимков. У частичного документа новый worker получает только chunk №33. Старые complete/fail/renew повторяются уже при новом владельце и не меняют его lease. Успешное завершение остатка сохраняет первые 32 вектора и их timestamps без изменений и добавляет ровно одно ready-событие. Четыре completion-события для трёх сценариев не сопровождаются retrying/failed-событиями.

Runtime-изменение не потребовалось: существующая обработка `AGAT_COMMIT_UNKNOWN` уже сохраняет неизвестный исход при неудачном cleanup ROLLBACK и заменяет повреждённое соединение. Расширены общий ограниченный proxy и PostgreSQL integration regressions; перед новым BEGIN/ROLLBACK proxy сбрасывает признак целевой записи, чтобы fault не переходил в другую транзакцию.

Первый вариант теста удержанного ответа ошибочно ожидал закрытия всего SQL-моста и health 503. Фактически раньше срабатывает `pg.query_timeout`: completion возвращает корректный 503, а health остаётся 200 на новом соединении. Исправлено только это ожидание fixture; исходный неудачный лог сохраняется отдельно и не считается runtime-дефектом или успешной проверкой.

## Верификация и границы

Целевые **6/6** COMMIT-сценариев прошли, включая три embedding и три прежних retrieval. Удержанный ответ дал 503 через **1030 мс**, разрыв — через **59/53 мс** от запуска запроса. Начальный полный PostgreSQL-набор до добавления withhold-контроля — **64/64**; окончательный полный набор — **65/65**. Typecheck всех workspaces и строгая отдельная проверка proxy — pass. [Логи и SHA](./evidence/2026-09-27/embedding-commit-acknowledgement/checks.json) сохраняют ранний успешный прогон и ошибочное ожидание fixture отдельно от финальных проверок.

```sh
bash scripts/test-fleet-ha-postgres.sh --test-name-pattern='committed embedding batch after lost COMMIT|COMMIT acknowledgement|uncertain sync COMMIT'
bash scripts/test-fleet-ha-postgres.sh
npm run typecheck
npm run docs:check
```

Это управляемый loopback fault после известного тесту COMMIT и ограниченные snapshot-проверки, а не универсальная гарантия exactly-once, модельная qualification, managed-PostgreSQL failover или доказательство rollback после любого timeout. Python worker и inference в этих трёх сценариях не запускаются. Следующий gate — фактический worker после потерянного ответа completion: освобождение renewal thread и слота, отсутствие автоматического повтора завершённой партии и продолжение следующей lease через обычный клиент.
