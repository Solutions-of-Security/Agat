# Finalization с проверкой исходных квитанций

[Finalizer](../../../../scripts/finalize-decision-reviews.py) связывает готовый
dataset с двумя явно отправленными review и, при разногласиях, с completed
[adjudication session](./adjudication-session-verification.md).
[Offline verifier](../../../../scripts/verify-decision-review-finalization.py)
заново восстанавливает оба выходных файла из тех же внешне закреплённых входов.
Он не принимает один SHA dataset как доказательство его происхождения.

Для каждого review передаются session, фактический initial review и saved
review с их SHA-256. Принимаются terminal v2 и импорт русской browser form.
Comparison тоже закрепляется внешним SHA; его содержимое заново вычисляется
по двум исходным review. Canonical comparison проверяет типы чисел и boolean,
порядок, counts и обе оценки с обоснованиями.

Если разногласия есть, нужны все шесть adjudication inputs с SHA. Сначала
проверяется completed receipt и отдельный submit. Затем original handoff
восстанавливается из фактической source pair: packet, blank и case notes
должны побайтно совпасть с ним. Валидная adjudication от другой пары,
полный draft после quit и failed receipt не проходят finalization.
Если разногласий нет, лишняя adjudication отвергается.

Согласованные случаи сохраняют обе исходные записи review; спорные — ещё
и запись adjudicator. Original case order, полные input/provenance и group-level
split seed сохраняются существующим `finalize_reviews`. Dataset и sealed
`finalization.json` записываются в новый каталог под `docs/private` с mode
0700/0600. Dataset сохраняется первым; ошибка второй записи не создаёт
completed finalization receipt. Существующий каталог не перезаписывается.

```bash
python3 scripts/finalize-decision-reviews.py \
  --first-session docs/private/first/session.json --first-session-file-sha256 <sha256> \
  --first-input-review docs/private/first/initial.json --first-input-review-file-sha256 <sha256> \
  --first-output-review docs/private/first/review.json --first-output-review-file-sha256 <sha256> \
  --second-session docs/private/second/session.json --second-session-file-sha256 <sha256> \
  --second-input-review docs/private/second/initial.json --second-input-review-file-sha256 <sha256> \
  --second-output-review docs/private/second/review.json --second-output-review-file-sha256 <sha256> \
  --comparison docs/private/pair/comparison.json --comparison-file-sha256 <sha256> \
  --output-dir docs/private/finalized
```

Для спорной пары к этой команде добавляются `--adjudication-session`,
`--adjudication-input-review`, `--adjudication-output-review`,
`--adjudication-packet`, `--adjudication-blank-review` и
`--adjudication-case-notes`; у каждого есть соответствующий
`--adjudication-<name>-file-sha256`. `--input-review` каждой сессии означает
фактический исходный checkpoint, включая draft перед resume.

Для replay используется тот же набор source arguments и другой output-dir:

```bash
python3 scripts/verify-decision-review-finalization.py \
  <source arguments из команды finalization> \
  --finalization docs/private/finalized/finalization.json --finalization-file-sha256 <sha256> \
  --dataset docs/private/finalized/dataset.json --dataset-file-sha256 <sha256> \
  --output-dir docs/private/finalized-replay
```

Replay сравнивает точные canonical bytes обоих outputs. Изменение label,
review record, provenance, split, порядка или authority flags отвергается даже
после пересчёта внешних SHA и seal. Расчёт deterministic; модели и терминальные
сессии повторно не запускаются.

`sourceReviewsRevalidated=true` означает проверку submitted artifacts и bytes,
а не аутентификацию участников. `handoffReconstructedFromSourceReviews=true`
относится к заново восстановленному handoff; вложенная квитанция прежней session
сохраняет свой исходный false flag. `labelSource=expert-reviewed` — существующий
формат dataset, из него не выводится expertise. Identity, независимость,
human execution и qualification остаются непроверенными. Finalization не
включает routing, не выбирает calibration thresholds и не измеряет accuracy.

## Проверка 11.10.2026

17 новых и 87 связанных Python tests прошли, включая совместимость browser
import с terminal review, отказ при incomplete submit, другой source pair,
переименованных scalar types, изменённом dataset и ошибке второй записи.
Первый mutation test присвоил уже существующий split и потому не менял fixture;
после исправления контроль всегда выбирает другой split. Production guards
не ослаблялись.

Из `9e8099e` настоящий CLI один раз финализировал исходную synthetic pair
предыдущих этапов: три cases, два agreed / один adjudicated. Standard-library
probe независимо восстановил весь dataset, provenance, review records и
group split, выполнив 40 checks. Original offline replay прошёл; четыре controls
отклонились без output directory. Новые human review, model calls и реальные
reference labels — 0.

[Summary](./review-finalization-summary.json) закрепляет plan/result и outputs.
[Archive metadata](./review-finalization-archive-summary.json) сохраняет 53 files /
89350 bytes и 23 code sources. CRC, every-entry SHA/size и копия в исходном
workspace проверены. Standalone verifier, восстановленный из этой копии,
получил byte-identical verification без моделей или новых review sessions.

Общий локальный прогон предыдущего этапа: 1337 tests / четыре optional skips
и один error в старом paired timing fixture — wall duration 361 ms против
monotonic 623.513 ms. Изолированный повтор этого сценария прошёл без изменения
кода. Причина расхождения не установлена; исходный полный лог сохранён, PASS
ему не присвоен. Полный GitHub CI test job предыдущего этапа прошёл отдельно.

Следующий предметный этап — получение двух реальных независимых review,
разбор их фактических разногласий и development-сравнение на закреплённых
ответах. Техническая проверка работает на synthetic fixtures; публичным
development обращениям агент метки не назначает.
