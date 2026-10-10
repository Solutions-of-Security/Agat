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

Следующий инженерный этап — связывание completed adjudication с исходной
проверенной парой review и finalization receipt. Реальные независимые review,
критерии владельца, calibration и holdout остаются предметными gates.
