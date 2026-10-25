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

Каждый merge выше выполнен после успешного обязательного `required` gate,
всех verify jobs и CodeQL. Историческая ошибка Linux browser fixture в #227
исправлена переносимым временным каталогом; исходный неуспешный прогон не
считается PASS.

Для [форматированного вида результата](./readable-report-view.md) выполнены 66 web
tests, 19 Temporal/process tests, восемь desktop/mobile browser checks, typechecks
и сборки. CodeQL обнаружил неточное сравнение hostname в network assertion теста;
оно исправлено разбором URL, четыре затронутых UI checks повторно прошли.
Изменение типов ScheduleSpec сохраняет runtime values.

## Следующий подготовленный этап

[PR #230](https://github.com/Solutions-of-Security/Agat/pull/230), ветка
`codex/versioned-report-pack`, добавляет
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
на PostgreSQL. Production migration gate не ослаблялся. Повторный полный CI
обязателен до merge. UI v2 реализуется отдельно следующим этапом.

## Границы и следующий остаток

- SP-001/SP-005: завершить поставку versioned pack и UI выбора release,
  пользовательских sources, diff, update/rollback и структурированного запуска;
  сохранить exact историю и v1 compatibility.
- USE-003: общий knowledge → pack path, completeness для частично извлекаемых
  документов, dedupe переименованных файлов и квалификация на пользовательских данных.
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
