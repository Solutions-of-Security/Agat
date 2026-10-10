# Сверка двух завершённых review

`scripts/compare-decision-reviews.py` принимает две тройки pinned-файлов:
input review, output review и session v2. Каждая тройка проходит
[проверку квитанции](./review-session-verification.md). Обе должны подтверждать
отдельный submit и completeReviewArtifact=true. Full draft после quit и
failed checkpoint отклоняются. Reviewer IDs должны различаться; полный pool,
порядок заданий и splitSeed должны совпасть.

Сверка считает совпадения option ID по cases и группам, сохраняет порядок
разногласий, оба option ID и rationale в private comparison.json. Разные
rationale при одинаковом option ID считаются совпадением выбора. Agreement
fraction — описательная доля совпавших cases; это не точность относительно
истины и не оценка независимости групп. Никакой expert-reviewed dataset или
reference label этот инструмент не создаёт.

Сохранение разногласий для разбора согласуется с [Google PAIR](https://pair.withgoogle.com/chapter/data-collection/),
где annotation tooling рассматривается вместе с expertise, возможностью
исправлений и неопределённостью разметки. Здесь две строки reviewer ID
подтверждают только различие IDs. Identity, human execution, независимость,
предметная квалификация и classification accuracy остаются false;
qualification=not_assessed и routingEnabled=false.

## Использование

```bash
python3 scripts/compare-decision-reviews.py \
  --first-session docs/private/reviewer-a/session.json \
  --first-session-file-sha256 <sha256> \
  --first-input-review docs/private/package/review.first.blank.json \
  --first-input-review-file-sha256 <sha256> \
  --first-output-review docs/private/reviewer-a/review.json \
  --first-output-review-file-sha256 <sha256> \
  --second-session docs/private/reviewer-b/session.json \
  --second-session-file-sha256 <sha256> \
  --second-input-review docs/private/package/review.second.blank.json \
  --second-input-review-file-sha256 <sha256> \
  --second-output-review docs/private/reviewer-b/review.json \
  --second-output-review-file-sha256 <sha256> \
  --output-dir docs/private/review-pair-compared
```

SHA берутся из закреплённого inventory. Output directory должен быть новым
под docs/private; отказ происходит до его создания. Исходные review и receipts
не переписываются. requiresAdjudication=true означает наличие разных выбранных
вариантов; разбор и предметная приёмка требуют реальных участников и критериев
владельца. Declared source files прошлых review остаются unrevalidated в
проверке квитанций, включая исторический registry из семи файлов.

## Реальная проверка механики

[Сводка](./review-pair-comparison-summary.json), source
`7fb92de951a5247bd88cb3607e7f2b94b8ae5f75`. Из прежней synthetic submit-квитанции
взяты три synthetic cases. Второй nominal reviewer получил новый blank пакет
с тем же pool и seed. Настоящий terminal CLI сохранил три ответа и выполнил
отдельный submit. Это запуск программного harness, а не review эксперта.

Настоящий comparison CLI показал два совпадения и одно разногласие в трёх
синтетических группах. Шесть controls отклонены: одинаковые reviewer IDs,
полный partial draft, полный failed draft, другой seed, другой внутренне
согласованный pool и изменённые raw input bytes. Никакой control не создал
comparison output. Один terminal child и семь comparison children завершены;
независимый ps подтвердил отсутствие всех восьми PID.

Auditor на stdlib и Git восстановил comparison и обе вложенные verification
квитанции целиком: 4037 checks / 3836 JSON keys. 66 целевых Python-тестов прошли;
полный прогон 1259, четыре пропуска, 513.002 s. Оба protected capture прошли
27 checks, восемь snapshot fields неизменны. Public development 49 labels
остаются пустыми, reference labels и real human reviews — ноль, model calls=0.

[Архив и actual original-copy replay](./review-pair-comparison-archive-summary.json)
сохраняют source snapshots, PTY transcript, исходную и вторую synthetic
квитанции, все controls, CLI logs и независимый auditor. Restored replay
повторяет аудит имеющихся артефактов без нового terminal или model execution.
