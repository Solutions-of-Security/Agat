# DOCX/PDF сохранённого отчёта — USE-004

25 октября 2026. В результате завершённого запуска доступны «Скачать DOCX» и
«Скачать PDF», если сохранён Markdown с квитанцией `business-report-markdown.v1`.
Это работает и при `resultDestination: history`: явный artifact node пакета
сохраняет файл независимо от дополнительных agent artifacts. Исходный Markdown
и его обычное скачивание сохраняются.

## Одно содержание и точная версия

Экспорт читает проверенные bytes исходного артефакта из Artifact Store, сверяет
SHA-256 и строит одно дерево `business-report-office.v1`. Оба формата используют
это дерево, включая формулы, ограничения, исходные цитаты и предупреждения о
границах проверки. `documentSha256` одинаков для DOCX/PDF одного источника и
origin. В конце документа указаны ID и SHA-256 исходного Markdown, профиль,
SHA-256 конфигурации renderer и адрес АГАТ. Этот hash конфигурации закрепляет
версии библиотек, fonts/license и лимиты; это не hash исходного кода.

Свежие retrieval, текущие документы Knowledge и вызовы модели не нужны. Изменение
или удаление текущего Knowledge не меняет уже сохранённый отчёт. Если исходный
артефакт удалён либо перестал быть ready во время генерации, сохранение экспорта
отклоняется. Принятие содержания человеком здесь не выполняется и не выдаёт
разрешение на инструменты.

Новые Markdown artifacts сохраняют rendering receipt на своей строке. Для
прежнего файла квитанция восстанавливается только при совпадении SHA-256 bytes,
completed artifact stage, frozen graph config и исходного
`business.report.rendered` event. Более 200 event candidates либо неоднозначный
input hash закрывают такой путь. При первом успешном экспорте историческая
квитанция сохраняется на artifact row и больше не зависит от event retention.
Отсутствие квитанции оставляет доступным исходный файл.

## API и хранение

`GET /api/v1/runs/:id/artifacts` возвращает `{ artifacts: [...] }`: metadata,
rendering receipt и `sourceArtifactId`, без binary body. Чужой проект — 404.

`POST /api/v1/artifacts/:id/export` принимает только:

```json
{
  "format": "pdf",
  "expectedSha256": "<64 lowercase hex symbols from the canonical artifact>",
  "origin": "https://agat.example"
}
```

Ответ `{ artifact, created }`: 201 при создании, 200 при повторе. Download
использует существующий `/artifacts/:id/download`, авторизацию и integrity checks.
Нужна read-role с доступом к проекту; legacy local mode требует admin token.
Экспорт — создание представления уже доступных данных, поэтому отдельная
designer/operator роль не требуется.

Origin допускает только HTTP(S), без credentials, path, query или fragment;
его host должен совпадать с HTTP Host текущей установки. Reverse proxy должен
сохранять публичный Host. От origin не выполняется HTTP-запрос. Разрешённые ссылки
ведут только на `/#knowledge?...` этой установки; активный HTML, картинки и
произвольные внешние ссылки не исполняются. Другой origin получает отдельный
cache key, чтобы ранее созданный файл не перенаправил пользователя в чужую среду.

Identity производного файла включает canonical artifact ID/SHA-256, format,
origin и renderer hash. После bounded rendering исходная строка блокируется и
перепроверяется; unique export key и row lock оставляют один сохранённый файл
при конкурентных запросах разных coordinator replicas. Повтор сверяет доступность
и integrity существующего файла. Его удаление не превращается в скрытую замену
под прежним ID. Audit `business.report.exported` содержит IDs/hashes/format/size
и субъекта без тела отчёта.

DOCX/PDF проходят через обычный Artifact Store: filesystem, PostgreSQL BYTEA
или versioned S3; действуют его download, retention и legal-hold contracts.
Schema **34**, contract `agat-business-report-office-v34`, добавляет
`rendering_json`, `source_artifact_id`, `export_key` и partial unique index.
Self-reference сохраняется при offline cutover. Мигратор вставляет parent раньше
производного файла, а digest считает в canonical primary-key order после той же
filesystem → BYTEA трансформации, что применяет при импорте. PostgreSQL runtime
остаётся DDL-free; используется штатная migration/admission Job.

## Рендеринг и пределы

Используются `markdown-it 15.0.3`, `docx 9.14.0`, `pdfkit 0.20.2`, `fontkit 2.0.4`.
Production runtime — Node.js, без LibreOffice/Python и внешних запросов. Отдельный
worker ограничен 256 МиБ V8 heap и 20 секундами; одновременно до двух renderer
на coordinator, избыток и временный сбой — 503 с возможностью повтора. Нет
сохранения частичного экспорта. Исходный Markdown — до 800000 UTF-8 bytes,
готовый файл — до 10 МиБ, PDF — до 100 страниц, таблица — до 501 строки и 8 колонок.
Artifact Store может иметь меньший собственный лимит. Неподдерживаемая разметка,
непомещающаяся строка таблицы или отсутствующий glyph дают явный отказ с доступом
к полному Markdown; содержание не усекается и символы не заменяются квадратиками.

A4, чёрные заголовки, native Word paragraphs/tables и повторяемая шапка таблиц.
В PDF таблицы измеряются до рисования, строки переходят целиком, footer не
добавляет пустых страниц. DOCX и PDF встраивают DejaVu Sans regular/bold из
[официального release 2.37](https://github.com/dejavu-fonts/dejavu-fonts/releases/tag/version_2_37).
Шрифты проверяются по SHA-256 при рендеринге; проверяется наличие каждого glyph.
Кириллица, стрелка и математический минус проверены. Emoji и другие glyphs вне
набора явно не поддерживаются. Font notice в неизменном виде хранится в
[DejaVuSans-LICENSE.txt](./licenses/DejaVuSans-LICENSE.txt), поставляется в image
и включается в DOCX custom property / PDF info.

| Asset | SHA-256 |
| --- | --- |
| DejaVuSans.ttf | `7da195a74c55bef988d0d48f9508bd5d849425c1770dba5d7bfc6ce9ed848954` |
| DejaVuSans-Bold.ttf | `e6476c1b80502924294eed40894c5b18e06c181444ca953e5334262df9c27724` |
| Font notice | `7a083b136e64d064794c3419751e5c7dd10d2f64c108fe5ba161eae5e5958a93` |

## Перепроверка

Короткий и длинный synthetic examples строятся реальным валидатором отчёта.
В обоих форматах сверяются содержание, source link, формулы, hashes и literal
HTML. Длинный пример содержит 25 длинных table IDs и 50 source lines. Его данные
не считаются модельной или бизнес-квалификацией.

DOCX открыт встроенным LibreOffice и отрендерен: 2 и 8 страниц; прямые PDF — 2
и 7 страниц. Все **19 страниц** просмотрены. Извлечение текста подтверждает все
25 IDs, 50 строк, отсутствие обрезки по границам страницы, фактические page
numbers и полный source URI, включая hash navigation, после DOCX → PDF. Hashes
файлов и page counts находятся в
[QA manifest](./qualification/internal-report/office-export-2026-10-25.json).
Скриншоты и generated examples остаются в системном temp, не в source tree.

SQLite/API проверяют exact download bytes, idempotent retry, чужой проект,
stale SHA, legacy receipt, удаление текущего Knowledge и source removal во время
rendering. PostgreSQL с двумя настоящими coordinator processes проверил четыре
конкурентных запроса: один 201, три 200, один DOCX и один PDF, общий document hash
и tenant RLS. Offline apply/verify и rehearsal rollback прошли, включая binary
DOCX и self-reference.

Настоящий UI на 1440×1100 и 390×844 проверяет оба download, повторное скачивание,
hash bytes и отсутствие новых model completions. Browser plugin недоступен,
использован репозиторный Playwright. Общая регрессия: 381 coordinator tests
passed / 28 opt-in skips, 70 web tests, 32 desktop/mobile browser checks,
workspace typechecks/builds. Финальные CI и контейнерные gates фиксируются в
[журнале реализации](./implementation-progress-2026-10-25.md).

Локальный production image дошёл до export layers после успешной сборки, затем
Docker Desktop сообщил input/output error и read-only filesystem. На host
осталось 244 МиБ свободного места. Локальный S3 replay также прерван средой и не
считается PASS. Проверка image + production renderer и полный S3 сценарий входят
в CI на отдельном runner. Глобальные контейнеры и настройки Docker не сбрасывались.
Основание для диагностики:
[Docker Desktop for Mac storage](https://docs.docker.com/desktop/troubleshoot-and-support/faqs/macfaqs/).

Контракт рендеринга опирается на
[PDFKit text/layout](https://pdfkit.org/docs/text.html),
[PDFKit fonts](https://pdfkit.org/docs/getting_started.html) и
[docx Packer](https://docx.js.org/api/classes/index.Packer.html).
Следующий остаток USE-004 — append-only «Принять / Нужны исправления», привязанный
к точному artifact/run/version, связанная задача исправления и передача примера
в Golden eval. Human owner, реальный corpus, usability trial и бизнес-пилот
остаются отдельными gates.
