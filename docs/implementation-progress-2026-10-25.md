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
`55e69df1de70467dccfe0899988c242093641c79` проходит полный CI перед merge.

## Подготовленная дедупликация USE-003

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
недоступен, использован Playwright. Links/catalog прошли. CI и merge этапа
будут записаны после завершения поставки предыдущего этапа. Дедупликация включает
исправление изоляции fixture из #232; её merge выполняется после merge #232.

## Границы и следующий остаток

- USE-003: завершить поставку проверки частичного извлечения и dedupe;
  квалификация на пользовательских данных остаётся отдельным gate.
- USE-004: DOCX/PDF из canonical content; append-only accept/rework на exact artifact/run.
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
