# Продолжение реализации плана — 25 октября 2026

Основа последовательности — `docs/roadmap.md`, включая локальную актуализацию
от 17 октября: документы → проверяемый отчёт → решение человека → повторная задача.
Неподтверждённые владельцы, pilot cohorts, бизнес-данные и реальные trials не
заменяются синтетическими доказательствами.

## Поставленные этапы

| Этап | Результат | PR / merge в main |
| --- | --- | --- |
| SP-002 / RUN-103, выходной контракт | Strict schema, расчёты, run/project-scoped источники, hashes/bytes артефактов, остановка до downstream effects | [#225](https://github.com/Solutions-of-Security/Agat/pull/225), `4dcffdd2a6c7fa6c784a0e50063f2fc4ace047a8` |
| SP-002, вход и effective prompt | Input до создания run; subprocess/replay/test-node; схема в pinned prompt и manifest | [#226](https://github.com/Solutions-of-Security/Agat/pull/226), `44aa72fd02c46feca4877508586fe89fbe00bac3` |
| USE-003, таблицы | CSV/XLSX values-only, original bytes, точные ячейки и cell-aware chunks, preview/retry/provenance | [#227](https://github.com/Solutions-of-Security/Agat/pull/227), `ddc9af9b1fcc05ced887d74546f2b65c2d30097a` |
| USE-004 / RUN-103, Markdown artifact | Детерминированный отчёт из JSON, повторная проверка evidence после approval wait, точные скачиваемые bytes | [#228](https://github.com/Solutions-of-Security/Agat/pull/228), `cb4cefe17acf5667608b9c4ea506fc9029b9a7e4` |
| USE-004, чтение результата | Безопасный форматированный вид, точные source links, raw/download fallback при сбое renderer | [#229](https://github.com/Solutions-of-Security/Agat/pull/229), `a6122d39c594bb17eb4fc0cec70bb8ad4682d99f` |
| SP-001/SP-005, backend v2 | Свои коллекции, versioned manifest, exact history, diff, install/activate/rollback и v1 compatibility | [#230](https://github.com/Solutions-of-Security/Agat/pull/230), `3d69bf5826fe2d03789e08ec864d66d10c289c48` |
| SP-001/SP-005, UI v2 | Release/sources/diff/history, update/rollback и структурированный вопрос/период | [#231](https://github.com/Solutions-of-Security/Agat/pull/231), `3ad622ea61302445415af919419bc4f8bebbb67a` |
| USE-003 / UX-206, проверка извлечения | Scope/gaps PDF/DOCX, exact review до индексации и model lease | [#232](https://github.com/Solutions-of-Security/Agat/pull/232), `0281c5e4288e3014dcbb157e9eeabf4b93fb1d0b` |
| USE-003, dedupe | Original bytes identity одной коллекции, сохранение review/vectors, parser busy 503 | [#233](https://github.com/Solutions-of-Security/Agat/pull/233), `e9b1dad304a1100cb3ad125728f5dddfb2f157de` |
| USE-004, DOCX/PDF | Одно проверенное содержание, frozen source/renderer identity, retry-safe files и UI download | [#234](https://github.com/Solutions-of-Security/Agat/pull/234), `43b0cf79d078d9c0da790ad7d3d21312c465ebd8` |
| USE-004, backend решения | Append-only history, exact source binding, retry/revision и связанная ручная задача | [#236](https://github.com/Solutions-of-Security/Agat/pull/236), `a8f3738757c4a585b0b902e69fcecfd5f8c06124` |
| USE-004, интерфейс решения | Явное принятие/rework, безопасный retry, история и ручная передача задачи | [#237](https://github.com/Solutions-of-Security/Agat/pull/237), `536b7357d9026a2892af60e3d83b7a9ce5845657` |
| USE-004, backend Golden candidate | Exact report/decision/agent/input/output provenance, blank reference, development-only preparation | [#238](https://github.com/Solutions-of-Security/Agat/pull/238), `3105c4b6b3af43e704feec7c12597c20a46836d4` |
| USE-004, versioned Golden import | Human reference/rubric, atomic v1/lineage/event, durable actor-bound retry | [#239](https://github.com/Solutions-of-Security/Agat/pull/239), `64a5f239c7fc8078cc8ede7d8c6e183a16f468a9` |

Каждый merge выше выполнен после успешного обязательного `required` gate,
всех verify jobs и CodeQL. Историческая ошибка Linux browser fixture в #227
исправлена переносимым временным каталогом; исходный неуспешный прогон не
считается PASS.

Для [форматированного вида результата](./readable-report-view.md) выполнены 66 web
tests, 19 Temporal/process tests, восемь desktop/mobile browser checks, typechecks
и сборки. CodeQL обнаружил неточное сравнение hostname в network assertion теста;
оно исправлено разбором URL, четыре затронутых UI checks повторно прошли.
Изменение типов ScheduleSpec сохраняет runtime values.

## Проверки versioned pack

Поставленный [PR #230](https://github.com/Solutions-of-Security/Agat/pull/230) добавил
[versioned report pack](./versioned-report-pack.md): v2 на коллекциях проекта,
неизменную v1, exact manifests/history, install/diff/activate/rollback и schema v31.
Пройдены 101 целевой тест, typechecks всех workspaces, coordinator build,
docs links/catalog; настоящие PostgreSQL 17 и две coordinator replicas проверили
четыре конкурентных install и tenant RLS. SQLite → PostgreSQL import/verify и
rehearsal rollback прошли. Гонка HTTP 201, найденная реальным concurrent тестом,
исправлена определением `created` под project lock. Полный docs replay с сетью
пройден: 1532 Python tests (4 штатных skips), Node checker tests, links и catalog.
Первоначальный sandbox-прогон с ошибками сети не считается PASS.

Полный CI HA нашёл оставленный fixture `ready` marker после tenant RLS assertions:
cleanup пытался закрыть глобальную replica под tenant scope. Cleanup переведён в
system scope; concurrent pack test вместе со следующим schema-upgrade test прошли
на PostgreSQL. Production migration gate не ослаблялся. Полный локальный HA suite:
114 tests passed. Повторный полный CI, все verify jobs и CodeQL прошли до merge.

[PR #231](https://github.com/Solutions-of-Security/Agat/pull/231), ветка
`codex/report-pack-installer-v2`, добавляет UI следующего этапа: выбор
release/коллекций, exact diff, install/update/rollback/history, форму вопроса и
периода. 68 web tests, все workspace typechecks и production builds прошли.
Оба Chromium UI пути 1440×1100 и 390×844 прошли на настоящем coordinator:
v1 compatibility → binary CSV → v2 → update/rollback → structured request →
approval → Markdown → keyboard source highlight → reload bindings. Screenshots
в системном temp просмотрены; console warnings/errors и overlay отсутствуют.
Browser plugin not available — использован Playwright. Общая browser-регрессия:
30 desktop/mobile checks прошли. После retarget на main повторный полный CI,
обязательный required gate и CodeQL прошли до merge. Установка и synthetic worker не означают
квалификацию модели или пилота.

## Текущий этап USE-003 / UX-206

[Проверка извлечённого текста](./document-extraction-review.md) добавляет
schema v32, отчёт scope/gaps PDF/DOCX, явное решение до индексации и model lease,
exact hashes + уникальную revision, idempotent review под row lock и сохранение
ограничений в provenance, worker context и Markdown. Старые файлы без отчёта
остаются неизвестными до повторного разбора. CSV/XLSX сохраняют values-only path.

Локально прошли 370 coordinator tests (28 отдельных opt-in checks пропущены),
70 web tests, 170 worker tests (3 штатных skips), 32 desktop/mobile browser checks,
две проверки PostgreSQL HA, offline apply/verify + rehearsal rollback, typechecks
и production build. PostgreSQL barrier test проверяет конкуренцию review/reindex
до изменения jobs/chunks: допустимы новая ревизия без job или отказ stale reindex
с сохранением принятой ревизии. Повторная проверка исправила общий document lock;
embedding lease отдельно проверяет разрешённый scope. Скриншоты просмотрены; страница PDF без текста дополнительно
сверена через Poppler. UI показывает ограничения, checkbox выключен, причина
обязательна, download bytes совпадают, reindex сбрасывает решение. Нет console
warnings/errors и overlay. Финальные CI результаты фиксируются перед
merge; синтетический материал не считается бизнес-приёмкой или квалификацией.

Полный локальный HA replay: 114 checks passed. После заключительной правки
порядка collection → job → document отдельно прошли пять PostgreSQL checks,
включая принудительную конкуренцию с embedding completion. Полный docs replay:
1532 Python tests (4 штатных skips), Node tests, local links и catalog passed.
Поставка — [PR #232](https://github.com/Solutions-of-Security/Agat/pull/232),
CI на `850f37f468616889fee70257228f19ac1c4eee6e` выявил wildcard fixture worker,
влиявший на последующие retrieval tests. Отказ воспроизведён; модель fixture
ограничена и worker выключается при cleanup. Пять последовательных PostgreSQL
checks с budget/lease/isolation passed. Новый head
`55e69df1de70467dccfe0899988c242093641c79` успешно прошёл required, все verify jobs и CodeQL. Merge #232 — `0281c5e4288e3014dcbb157e9eeabf4b93fb1d0b`.

## Поставленная дедупликация USE-003

Ветка `codex/knowledge-file-dedupe` реализует
[повторную загрузку исходных bytes](./knowledge-file-deduplication.md):
имя не меняет file identity одной коллекции, canonical source/review/vectors
сохраняются; MIME и явно несовместимый source URI отклоняются. Schema 33 добавляет
non-unique index и сохраняет прежние копии. Parser busy становится временным
503 и не записывает failed document или новую extraction revision.

Локально прошли 374 coordinator tests (28 opt-in skips), затем все пять
dedupe tests, включая добавленный reindex saturation case; 70 web tests,
32 desktop/mobile browser checks, все workspace typechecks/builds, пять
последовательных HA checks и две offline migration checks. Настоящие две coordinator replicas
вернули один 201 и три 200 с одним document/job/queued event. Скриншоты desktop
и mobile просмотрены: notice показывает сохранённое имя, review сохраняется,
переполнения по ширине, console warnings/errors и overlay нет. Browser plugin
недоступен, использован Playwright. Links/catalog прошли. Final head `80a18b4154e2534d9b4938be8b13b0d967aacbf2` прошёл required, все verify jobs и CodeQL; merge #233 — `e9b1dad304a1100cb3ad125728f5dddfb2f157de`, после #232.

## Поставленный этап USE-004: DOCX/PDF

Ветка `codex/business-report-office-export` добавляет [экспорт сохранённого проверенного отчёта](./business-report-office-export.md), schema 34, exact source/renderer identity и download из результата запуска. Оба формата используют одно дерево без model calls. Legacy receipt восстанавливается только по точному stage/graph/event; при отсутствии доказательств доступен исходный файл.

Проверены 381 coordinator tests / 28 opt-in skips, 70 web tests, 32 browser checks, typechecks/builds, четыре concurrent requests двух PostgreSQL replicas и offline apply/verify/rehearsal. Перепроверка выявила и исправила PDF footer с лишними страницами и неверную pre-transform checksum сверку self-referenced artifacts при migration. На финальных samples просмотрены все 19 страниц (DOCX 2/8, PDF 2/7); exact source URI, все 25 table IDs и 50 source lines подтверждены. [QA manifest](./qualification/internal-report/office-export-2026-10-25.json) фиксирует bytes/hashes/pages. Настоящий desktop/mobile UI скачивает оба формата и повтор использует тот же artifact.

Локальный Docker image дошёл до export layers, затем storage Docker Desktop отказал с input/output и read-only filesystem; на host осталось 244 МиБ. S3 replay прерван средой. Эти попытки не считаются PASS. В required CI добавлен production image/renderer smoke; полный S3 gate уже входит в integration matrix. Final head `7cb293c5c16e681e4310d8bc8f5e7c2880f21727` прошёл required, все verify jobs (включая production image/renderer и полный S3) и CodeQL. Merge #234 — `43b0cf79d078d9c0da790ad7d3d21312c465ebd8`.

## Поставленный backend USE-004: решение о результате

Ветка `codex/report-result-decisions` от merge #234 добавляет [append-only решение и связанную ручную задачу](./report-result-decisions.md): exact artifact SHA/run/process version, actor-bound request ID, optimistic revision, atomic decision/task/audit, metadata-only event и права admin/operator. История переживает file/event retention; physical owning purge удаляет её согласованно. SQLite guards и PostgreSQL SELECT/INSERT-only grants защищают от изменения записей. Source bytes проверяются до SQL locks; project → artifact сериализует общий namespace request IDs даже между разными файлами.

Выключение ноутбука прервало сборку/регрессию при заполненном диске; незакоммиченные исходники сохранены. Старые неуспешные прогоны не считаются PASS. После восстановления: 389 coordinator tests passed / 28 штатных opt-in skips, семь final API/OIDC/retention/upgrade tests, все typechecks/builds. Две настоящие PostgreSQL replicas проверили retry 201/200, competing intent 201/409, общий request ID разных artifacts, tenant RLS, запреты правки и owning purge; отдельный startup probe отвергает выдачу UPDATE runtime или tenant role. Offline apply/verify/rehearsal переносит decision/task/binding и DOCX bytes.

Проверка выявила прежнее требование CRUD на каждой runtime table, несовместимое с append-only ledger. Для двух новых таблиц введён явный строгий SELECT/INSERT contract с запретом UPDATE/DELETE; остальные DML и все DDL запреты сохранены. Индекс FK run_id имеет ведущий run_id. Head `b327a6f8346ff5f0071810e548ad606836b34709` прошёл все 15 checks: required, verify jobs и CodeQL. Merge #236 — `a8f3738757c4a585b0b902e69fcecfd5f8c06124`.

## Поставленный интерфейс USE-004: решение и ручная передача

Ветка `codex/report-result-review-ui` добавляет явный выбор «Принять / Нужны исправления», обязательный комментарий к исправлению, историю и карточку задачи в результате completed run. Чтение доступно ролям проекта; запись — admin/operator. Retry после неизвестного ответа повторяет тот же UUID и неизменный payload; 409 закрывает форму и перечитывает историю. Source SHA и frozen revision проверяются сервером. Отдельный deferred chunk и boundary сохраняют чтение/экспорт при сбое панели.

Карточка ручной задачи показывает исходный файл, комментарий, версию процесса и состояние open/superseded; скачивание сверено по SHA-256. Передача копирует текст с exact source binding; при отказе clipboard открывается выделенный текст для ручного копирования. Созданная задача не объявляется выполненным исправлением, новый run не запускается.

Локально прошли 75 web tests, typechecks и production builds всех workspaces, docs links/catalog и 32 общих Chromium checks. Настоящий coordinator + Chromium 1440×1100 и 390×844 прошли install/CSV/report/approval/export → 503 history/retry → lost response после server commit → тот же decision/task ID → manual handoff/download → stale 409 → reload/superseded history → все 52 записи через cursor. Run/stages/approval и число model completions не изменяются. Отдельный cold context проверил missing chunk fallback и PDF download. Browser plugin not available — использован repo Playwright. Console warnings/errors обычного пути отсутствуют; ожидаемые 503/409/net::ERR_FAILED относятся только к внедрённым запросам, missing chunk и предупреждение service-worker блокировки изолированы. Все 11 screenshots панели/задачи desktop/mobile просмотрены в `/private/tmp/agat-stage12-ui`; временные изображения не включены в Git. Первые прогоны с преждевременной проверкой RAF focus restore и сброшенным после reload formatted view исправлены и не считаются PASS. Фактический CI/merge приведён ниже.

Обязательный test job первого CI #237 прошёл assertions, но docs replay завершился ошибкой tearDownClass: временный `sources/.git` оказался непустым во время удаления. Только для ActiveDeadlineOfflineTest отключены автоматические Git maintenance/gc, которые могут пережить subprocess и продолжить запись в fixture. Строгий cleanup сохранён; ignore_errors/retry masking не вводились. Все восемь checks модуля, включая настоящий model-free coordinator/worker deadline HTTP flow, прошли. Повторный полный локальный docs replay: 1532 Python tests, 4 штатных skips, Node checkers и links/catalog прошли. Исправленный head `009bf61fe9daae615244dd879fed6e812a4a9930` прошёл все 15 checks required/verify/CodeQL до merge #237; исходный отказ не считается PASS. Основание изоляции: [Git maintenance](https://git-scm.com/docs/git-maintenance), [Git gc](https://git-scm.com/docs/git-gc).

## Поставленный backend USE-004: подготовка Golden candidate

Ветка `codex/report-eval-candidate` добавляет [read-only development candidate](./report-eval-handoff.md) с exact artifact/decision/process/agent/input/output hashes. Pinned schema и однозначный JSON-producing agent сверяются с renderer receipt и сохранённым human decision. Reference остаётся пустым, observed JSON хранится отдельно; current corpus fingerprint не объявляется историческим retrieval snapshot. Packet не даёт holdout eligibility, qualification или разрешения process replay. Авторизация соответствует созданию datasets (admin/designer), ответ no-store. Новых таблиц, datasets, events, runs и model calls подготовка не создаёт.

Семь целевых SQLite/API/OIDC tests прошли, включая graph drift, ambiguous context, missing snapshot, corrupt source, 10000-chunk bound, роли и foreign project. Полный coordinator replay: 396 passed / 28 штатных opt-in skips; typecheck/build coordinator прошли. Две настоящие PostgreSQL replicas вернули одинаковый candidate, source/tenant binding и пустой reference; прежние concurrent decision/grants/purge assertions также прошли. UI передачи и фиксация versioned dataset остаются следующими этапами. CI/PR/merge будут зафиксированы после поставки.

Head #238 `196a1e9da6edd78ca941e4252f2bfec0ef1e82c5` прошёл все 15 required/verify/CodeQL checks. Merge — `3105c4b6b3af43e704feec7c12597c20a46836d4`.

## Поставленный backend USE-004: фиксация Golden-примера

Ветка `codex/report-eval-dataset-import` добавляет отдельный явный import endpoint: typed reference + human rubric/review, exact candidate SHA, dataset/v1 и metadata-only lineage/event. Повтор связан с полным verified subject/project/request ID, использует deterministic UUIDv8 и возвращает исходную версию после нового решения или source purge. Изменённое намерение — 409. Live source проверяется до записи и под общим project → artifact lock; весь Artifact Store I/O и fingerprint work происходят до write locks. Lineage хранится в существующем version JSON и входит в content SHA, schema 35 не меняется. Input/reference копируются в Golden dataset с отдельным сроком хранения; original-run purge не стирает явную копию. Импорт не запускает модель/процесс/experiment.

14 целевых candidate/import API/OIDC tests прошли. Полный coordinator replay: 403 passed / 28 штатных opt-in skips. Отдельный interop replay подтвердил exact stage input в существующем Golden executor после явного создания experiment; первый fixture пытался создать второй agent registry, исправлен отдельным frozen prompt и повторно прошёл. Две настоящие PostgreSQL replicas проверили четыре imports с 201/200, один dataset/event, changed-intent 409, tenant 404 и durable receipt после source-run purge. Offline apply/verify/rehearsal перенёс dataset/v1, lineage JSON и content SHA; все три integration checks прошли. Typechecks и production builds всех workspaces, docs links/catalog прошли. Поставка backend в #239 приведена ниже; UI описан следующим этапом.

Head #239 `078669b1adedfd40105b089049dbc04f483449a6` прошёл все 15 required/verify/CodeQL checks. Merge — `64a5f239c7fc8078cc8ede7d8c6e183a16f468a9`.

## Поставленный интерфейс USE-004: UI Golden handoff

Ветка `codex/report-eval-handoff-ui` соединяет результат отчёта с versioned Golden dataset: admin/designer видят явный CTA, exact packet binding/digests проверяются до формы, reference изначально пустой, rubric/review задаёт человек. Контекст можно перечитать после 409 с сохранением текста; неизвестный POST outcome замораживает body/UUID даже после close/reopen окна. Success открывает exact dataset/v1 с lineage, reload и обратной ссылкой на run; query datasetVersion=1 сохраняет исходную версию и после появления v2. Код формы загружается отдельно; inner boundary сохраняет историю/чтение/экспорт при missing chunk. Ошибки API получают machine code для recovery; сообщения reference validation переведены на понятный текст. Общий focus trap учитывает summary.

Локально прошли 79 web tests, 14 candidate/import API/OIDC checks, typechecks/builds всех workspaces и 32 общих Chromium checks. Настоящий coordinator + desktop/mobile Chromium прошли полный install/CSV/report/approval/office path, решения/ручную задачу, invalid reference, stale context/refresh, explicit rubric/review, lost response после commit, close/reopen и exact retry, один dataset/event, lineage/reload/source link; model completions/run/stages/approval не изменяются, experiment не создаётся. Визуальный replay выявил уход Close и ошибки за границу mobile viewport: header вынесен из scroll body, error авто-прокручивается, критерии многострочные; повторный flow и bounding-box assertions прошли. Все 13 Golden screenshots в `/private/tmp/agat-stage15-ui` просмотрены. Browser plugin not available; repo Playwright. Обычный путь без console warnings/errors; внедрённые HTTP/chunk ошибки изолированы. Черновик/pending через полную перезагрузку/смену отчёта/проекта не восстанавливается; это остаётся в UX-210. CI/PR/merge фиксируются после поставки.

Head #240 `0d5a16d7cccddc1b8742bb6771b686fb827473b4` прошёл все 15 required/verify/CodeQL checks. Merge — `785a963f97d00b25cc9ffa9df58cd2405d1e30b5`.

## Текущий этап UX-306: контракт событий

Ветка `codex/product-usage-contract` фиксирует [metadata-only v1](./product-usage-events.md): семь checkpoint kinds, task identity через retry/replay, раздельные client observations/server facts, project/cohort/sample/version и явные null. Нормализатор отвергает произвольные поля и не отражает значения в errors. Дедупликация проверяет event ID, semantic fact, неизменность task/run/artifact bindings и decision revision; конфликт не превращается в «последняя запись победила». Participant — HMAC project-scoped индивидуального verified subject, shared token остаётся unknown. Новых endpoints, storage/DDL, tracking, metrics claims и collector export пока нет; instrumentation и расчёт — следующие этапы.

Локально прошли 413 coordinator tests / 28 штатных opt-in skips, включая десять новых contract tests и row/byte limits; typechecks всех workspaces, production build coordinator, docs links/catalog и diff whitespace check. При перепроверке убрано приведение enum values к строке: произвольный объект не может пройти через toString. Результаты относятся к контракту, не к реальному пилоту. Remote CI и merge этого этапа ещё не выполнялись.

## Границы и следующий остаток

- USE-003: extraction review и dedupe поставлены; квалификация на пользовательских данных остаётся отдельным gate.
- USE-004: backend/UI решений, DOCX/PDF, candidate, versioned import и UI Golden handoff в main; квалифицировать полный путь на разрешённых пользовательских данных.
- Подготовка UX-306/USE-008/EVAL-101: metadata-only события, разрешённый corpus,
  human owner/baseline, отдельные development/holdout и настоящие повторные trials.
- RUN-101 и SAFE-101 остаются обязательными применимыми gates перед расширением
  model/tool/memory/cloud scope. Кодовый schema/arithmetic validator не доказывает
  фактическую истинность, готовность production или успешный пилот.

## Рабочая копия

Исходный каталог пользователя содержит cloud-backed файлы macOS и локальные
изменения; Git status и часть чтений там зависают. Для этого выполнения создана
отдельная копия актуальной удалённой main в `/private/tmp/agat-plan-20261025`.
Commit/push/PR/merge и тесты выполняются в ней. Исходный каталог
`/Users/mdavliatshin/Documents/Workspace/Agat` не перезаписывался, чужие изменения
не включались в commits. Авторитет поставки — проверенные merge commits в GitHub.
Следующим проходам нужно явно использовать эту рабочую копию и перепроверять main.
