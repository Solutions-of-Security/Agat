# Интерактивная adjudication session

[CLI](../../../../scripts/adjudicate-decision-reviews.py) заполняет
[handoff разногласий](./review-adjudication.md) третьим declared reviewer ID.
Он проверяет external SHA packet, original blank, case notes и текущего
review, строгий формат handoff, его output pins, список разногласий и full pool.
Исходные reviewer IDs отклоняются. Сохраняются полный pool и splitSeed, а
редактируемые labels и показанные задания содержат только разногласия.
В интерфейсе видны оба предыдущих option ID и rationale; управляющие символы
исходного текста и обоснований показываются как literal escapes.

Сессия использует общую механику подтверждений, исправлений и checkpoint с
обычным review. :edit N меняет ответ, :clear N очищает его после y, :skip
оставляет текущее значение, :quit сохраняет draft. Полный draft после quit
остаётся без reviewedAt. Только отдельные :submit и y создают completed receipt
и временную метку. Новый каталог имеет mode 0700, review.json и session.json —
0600. Resume читает pinned draft и создаёт новый каталог.

Общий checkpoint записывает временный файл в том же каталоге, выполняет fsync,
os.replace и fsync каталога. В [официальной документации Python](https://docs.python.org/3.13/library/os.html)
успешный replace описан как атомарная операция; ошибки записи проверяются
отдельно. Здесь сбой оставляет предыдущие подтверждённые ответы и failed
receipt. Исходные bytes и committed sources перепроверяются перед сохранением.

## Использование

```bash
python3 scripts/adjudicate-decision-reviews.py \
  --packet docs/private/handoff/packet.json \
  --packet-file-sha256 <sha256> \
  --blank-review docs/private/handoff/adjudication.review.blank.json \
  --blank-review-file-sha256 <sha256> \
  --case-notes docs/private/handoff/case-notes.json \
  --case-notes-file-sha256 <sha256> \
  --review docs/private/handoff/adjudication.review.blank.json \
  --review-file-sha256 <sha256> \
  --reviewer-id <third-reviewer-id> \
  --output-dir docs/private/adjudicator-session
```

При resume --review указывает на предыдущий draft, остальные три источника
остаются первоначальными. Уже submitted review и draft другого ID отклоняются.
Session v1 связывает четыре input SHA, сохранённый output SHA, весь пул,
ordered disputed IDs, code commit и 17 source SHA. Net counts различают
существующие, новые, исправленные и очищенные ответы. Общая проверка receipt
поддерживает точный schema и отдельный validator adjudication subset;
обычные session v2 продолжают требовать labels всего pool.

## Проверка

18 original CLI invocations использовали только существующий синтетический
handoff из трёх cases с одним разногласием. Шесть PTY sessions: полный draft
после quit, correction, clearing, declined submit, explicit submit и standalone
draft. Пять partial и один completed synthetic receipt; четыре подтверждённые
операции выбора, одна очистка. Standalone из 17 sources с собственным Git epoch
дал byte-identical draft. Реального человека или gold этот fixture не представляет.

Двенадцать отказов не создали output directory: неверные четыре pins, исходный
reviewer ID, чужой owner прогресса, уже submitted review, nonterminal input,
claimed human authority, repinned notes без разногласия, duplicate JSON member
и изменённый исходный pool. 16 новых / 62 targeted / 1314 full Python tests
PASS, четыре optional skips. Probe 334 checks; независимый stdlib auditor
пересобрал все шесть checkpoint/receipt и байты controls: 2438 checks / 2120 keys.
Protected resident: 27 checks / восемь неизменных полей; 19 owned PID отсутствуют.

Первый полный test run: 1301 tests, один setUpClass error в synthetic parallel
primary cancellation fixture (13 class tests не запустились), четыре optional
skips. Fixture сохранил только тип ValueError; причина первого exception не
установлена. Изолированное повторение также отказало, затем диагностический
запуск всех 13 tests прошёл без изменений кода. Полный повторный набор прошёл;
первый лог, failure receipt и диагностический лог сохранены. Причина не выдаётся
за доказанную и guards не ослаблялись.

Первый private probe остановился до plan и CLI из-за неверного пути handoff.
Его source и failure receipt сохранены. V2 читает outputDirectory из previous
receipt и проверяет три исходных file SHA. Семь ранее выполненных handoff CLI
не повторялись; новый протокол выполнялся один раз.

## Границы доказательства

Different declared IDs не аутентифицируют участников. humanExecutionVerified,
reviewerIdentityVerified, independentReviewVerified и expertQualificationsVerified
остаются false. Сессия проверяет handoff bytes и согласованность его содержимого;
исходные два review заново не исполняются и не перепроверяются по их source files,
handoffSourcesRevalidated=false. Reference labels и dataset не создаются.
Finalization compatibility проверена только на unit synthetic fixture.

Следующий инженерный шаг реализован: [CLI проверки сохранённой adjudication
квитанции](./adjudication-session-verification.md) использует external pins всех
входов и явно разделяет partial, failed и completed.
Реальные независимые review, adjudicator и предметные gates остаются
неподтверждёнными. Новые model calls=0, qualification=not_assessed, routing=false.

Пользователь подтвердил состав будущих reviewers: он сам и независимый эксперт.
[Инструкция для двух участников](./public-support/confirmed-review-participants.md)
содержит отдельные команды с закреплёнными SHA и порядок resume/submit.
Состав подтверждён; завершённых человеческих review пока нет.

[Aggregate summary](./adjudication-session-summary.json) закрепляет source,
plan/result и audit. [Archive summary](./adjudication-session-archive-summary.json)
закрепляет каждый ZIP entry и восстановление копии из исходного workspace.
Replay не запускает terminal CLI, finalization или модели. Private case text,
rationale и identities не перенесены в публичный отчёт.
