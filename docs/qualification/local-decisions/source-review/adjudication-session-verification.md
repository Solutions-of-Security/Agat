# Проверка сохранённой adjudication session

[CLI](../../../../scripts/verify-decision-adjudication-session.py) проверяет
сохранённую квитанцию разрешения разногласий из
[терминальной сессии](./adjudication-session.md). Для каждого из шести файлов
нужен внешний SHA-256: session, входной review, выходной review, исходный packet,
blank review и case notes. Проверка выполняется до создания нового каталога
под `docs/private`; существующие файлы не перезаписываются.

Проверяются исходный полный pool и seed, ordered disputed subset, option IDs,
владелец прогресса, оба мнения и rationale в notes, output pins handoff, seal
квитанции, timestamps и восстановленные net counts. Частично заполненный или
полный черновик после quit остаётся `partial`. Failed checkpoint остаётся
`failed`, даже когда сохранены все ответы. Только completed receipt с отдельным
submit, всеми ответами и соответствующим reviewedAt получает
`completeAdjudicationArtifact=true`.

```bash
python3 scripts/verify-decision-adjudication-session.py \
  --session docs/private/adjudicator/session.json --session-file-sha256 <sha256> \
  --input-review docs/private/handoff/adjudication.review.blank.json --input-review-file-sha256 <sha256> \
  --output-review docs/private/adjudicator/review.json --output-review-file-sha256 <sha256> \
  --packet docs/private/handoff/packet.json --packet-file-sha256 <sha256> \
  --blank-review docs/private/handoff/adjudication.review.blank.json --blank-review-file-sha256 <sha256> \
  --case-notes docs/private/handoff/case-notes.json --case-notes-file-sha256 <sha256> \
  --output-dir docs/private/adjudication-verification
```

При resume `--input-review` указывает на фактический draft перед проверяемой
сессией; `--blank-review` остаётся первоначальным blank. Exit 0 означает
согласованность артефактов; статус самой сессии показан отдельно в stdout и
`verification.json`. Exit 1 означает отказ проверки.

JSON читается строгим общим parser: повторяющиеся поля и non-finite числа
отклоняются. Это важно, поскольку стандартный
[Python JSON decoder](https://docs.python.org/3.13/library/json.html)
по умолчанию принимает повторяющиеся имена и NaN. Размеры ограничены;
symlink inputs и неполный набор pinned файлов не принимаются.

Проверка не выполняет сессию, модели или finalization. Declared IDs и source SHA
не удостоверяют человека, независимость или экспертизу. Исходные две review
тройки здесь не перепроверяются, `handoffSourcesRevalidated=false` и
`reviewSourcesRevalidated=false`. Обе исходные оценки сохраняются: исследование
[причин разногласий в NLI](https://aclanthology.org/2022.tacl-1.78/)
показывает, что они могут отражать неоднозначность задания, а не только ошибки.
Qualification остаётся `not_assessed`, routing выключен, новые reference labels
не создаются.

## Проверка 11.10.2026

10 новых и 70 связанных Python tests прошли. Настоящий CLI из `e12f499`
проверил шесть оригинальных synthetic receipts предыдущего этапа: пять partial
и один completed. Отдельная копия из 17 файлов без Git дала byte-identical
результат; восемь refusal controls не создали output directory. Standard-library
probe независимо восстановил counts, pins, whole pool и статусы, выполнив
413 artifact checks. Новые terminal sessions, model calls и реальные labels — 0.

18 Node documentation tests, links и catalog прошли на Node 24. Первые Node
проверки отказали из-за отсутствующих зависимостей в новом checkout; использована
установленная копия с идентичным package-lock SHA. Первый общий Python run на
3.14 был прерван после sandbox socket errors; лог сохранён, полный повтор
использует CI-версию 3.13 и разрешённые локальные тестовые сокеты.

[Summary](./adjudication-session-verification-summary.json) связывает plan/result.
[Private archive metadata](./adjudication-session-verification-archive-summary.json)
сохраняет 79 files / 144109 bytes; CRC и SHA каждого entry проверены. Архив
скопирован в `docs/private` исходного workspace. Verifier, восстановленный из
этой копии, повторно дал byte-identical отчёт без terminal/model execution.
Тестовые логи в архиве отражают границу копирования; окончательный полный test
log сохраняется отдельно. Runtime сервисы этим этапом не вызываются.

Следующий инженерный этап — связывание completed adjudication с исходной
проверенной парой review и finalization receipt. Реальные независимые review,
критерии владельца, calibration и holdout остаются предметными gates.
