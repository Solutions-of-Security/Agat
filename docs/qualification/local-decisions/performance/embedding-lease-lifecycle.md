# Владение embedding lease и атомарность индекса

Дата: **27.09.2026**. После [maintenance stage lease](./lease-maintenance-locks.md) независимо проверен lifecycle `knowledge_embedding_jobs`: продление, успешная партия, ошибка и фоновое освобождение.

## Воспроизведение до изменения

Baseline `1e92917c5cd8c0123a2d4859e82566842f9b6f61` допускал истёкшую аренду, пока её статус оставался `running`. Renewal мог оживить её; complete/fail не блокировали строку задания при чтении владельца. Последующие UPDATE по job id могли затронуть уже переназначенную попытку. Maintenance отдельно изменял job и document, используя предварительно прочитанный список.

| Сценарий на настоящем coordinator/PostgreSQL | Baseline | Исправленное поведение |
|---|---|---|
| Lease истёк во время ожидания job lock | Renewal 204; complete/fail 200 | Renewal 404; complete/fail 400; индекс и события не записаны |
| Владелец заменён до освобождения job lock | Complete сохраняет векторы, fail сбрасывает новый lease | Все три старых запроса отклонены; новый владелец завершает индексацию |
| Срок своевременно продлён другой транзакцией | Запросы допустимы | Renewal 204 и complete/fail 200; новый срок прочитан после lock |
| INSERT события complete/fail задержан до expiry | Изменения и событие остаются | Векторы, document, job и событие откатываются вместе |
| Maintenance видит старый expiry при незавершённом renewal | Продление стирается, failures становится 1 | Строка пропускается; lease и failures=0 сохранены |
| Второй coordinator обслуживает очередь, пока complete держит job до expiry | Job `completed`, document ошибочно `pending`, вектор записан | Поздний SQL-only complete откатывается, health второй реплики отвечает; новая попытка создаёт согласованный ready-индекс |
| UPDATE document отклонён после изменения job | Половина перехода остаётся | SQL-переход откатывается; следующий maintenance выполняет один retry либо terminal failure |

Тесты наблюдают SQL-lock в `pg_stat_activity` до expiry и освобождают его после заданной границы. Во всех отрицательных случаях проверяются сохранённые векторы и события, а не только HTTP-код. Смена владельца моделируется отдельной транзакцией; восстановление проходит через обычные store API. Контрольные случаи timely renewal сохраняют прежнее допустимое поведение.

## Решение

Renewal, complete и fail читают текущую строку под `FOR UPDATE` в существующей транзакции, после чего сверяют корректную конечную дату со свежим временем. Renewal вычисляет новый срок после ожидания lock. Общая функция проверки даты используется и прежним допуском terminal stage; его граничные тесты включены в регрессию.

Complete и fail выполняют дополнительную проверку времени после последнего event INSERT, перед запросом COMMIT. Это операции SQL-состояния: позднее сохранение можно откатить целиком, включая векторы и счётчики. Здесь нет новых файловых/S3 side effects. HTTP-форма и действующие коды отказа сохранены.

Maintenance выбирает expired jobs через `FOR UPDATE SKIP LOCKED` и сохраняет job/document в одной транзакции. SQLite использует тот же transaction wrapper с `BEGIN IMMEDIATE`. Уже открытая транзакция переиспользуется. Порядок retry и предел `max_failures` не изменены.

Выбор основан на [семантике Read Committed](https://www.postgresql.org/docs/17/transaction-iso.html) и [применении SKIP LOCKED для обработчиков очереди](https://www.postgresql.org/docs/17/sql-select.html#SQL-FOR-UPDATE-SHARE). Проверка параметра времени до ожидания не доказывает действительность lease после lock. [CURRENT_TIMESTAMP PostgreSQL](https://www.postgresql.org/docs/17/functions-datetime.html#FUNCTIONS-DATETIME-CURRENT) также фиксируется на начале транзакции; он не заменяет свежую проверку после ожидания.

## Проверка и ограничения

```sh
node --import tsx --test apps/coordinator/test/embedding-lease-lifecycle.test.ts \
  apps/coordinator/test/knowledge.test.ts \
  apps/coordinator/test/knowledge-retrieval-validation.test.ts \
  apps/coordinator/test/stage-lease-admission.test.ts
bash scripts/test-fleet-ha-postgres.sh \
  --test-name-pattern='embedding (renew|complete|fail) (respects|rolls back)|embedding maintenance'
npm run test:coordinator
bash scripts/test-fleet-ha-postgres.sh
npm run typecheck
npm run docs:check
```

До изменения **12/12** новых SQLite-тестов провалились. PostgreSQL: первая группа — **7 ожидаемых FAIL и 4 проходящих контроля**, maintenance — **2 ожидаемых FAIL**. После изменения целевые **31/31**, PostgreSQL **13/13**, полный coordinator **278/278** и полный PostgreSQL **53/53**; typecheck, docs-check и architecture audit — pass. SQLite отдельно проверяет точную границу `now == expiresAt`, повреждённую дату, истечение после настоящего SELECT/INSERT и откат при отказе обновления document. [Полные логи, итог и SHA](./evidence/2026-09-27/embedding-lease-lifecycle/checks.json) позволяют проверить результат.

Первый полный PostgreSQL-прогон выявил дефект изоляции стенда: завершённые embedding-fixtures оставляли workers с пустым списком primary-моделей онлайн, и действующий compatibility fallback router выбирал их для последующих тестов. Все 13 новых случаев прошли, 15 последующих сценариев не получили ожидаемый lease. Cleanup теперь помечает собственный fixture pool offline; production routing сохранён. Первый повтор дал 52 PASS и один сбой старого теста квот. Ранний budget-fixture оставлял незавершённый run: за удлинившийся прогон его lease успевал истечь и попадал в общую очередь. Budget-fixture теперь отменяет собственный run в cleanup; quota-fixture явно проверяет id полученной задачи перед проверкой лимита. Оба неудачных полных лога сохранены; полный набор повторён после исправления изоляции.

Векторы синтетические `[1, 0]`; новая модель и новые embeddings из реального корпуса не вычислялись. Проверяется протокол сохранения и владения, без оценки retrieval recall, качества MLX или производственного SLO. Проверка срока заканчивается до запроса COMMIT; физическая фиксация, потеря ответа и скачки часов не получают новых гарантий. Health проверен при конкретной row-lock конкуренции на одной БД с несколькими процессами coordinator, без отказа PostgreSQL primary.

Следующий gate — проверить аналогичный SQL-lock интервал **stage renewal**: прежний `renewLease` имеет условие по expiry в UPDATE, но время передаётся до возможного ожидания строки. Дополнительно остаётся отдельная конкуренция разных embedding jobs одной коллекции при выборе размерности индекса; текущая блокировка одного job сама по себе её не квалифицирует.
