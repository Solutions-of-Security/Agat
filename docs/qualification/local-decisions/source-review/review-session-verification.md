# Проверка сессионных квитанций review

`scripts/verify-decision-review-session.py` проверяет три файла с внешними SHA:
исходный blank/partial review, сохранённый review и sealed session v2.
Проверка пересчитывает pool/input fingerprints, сохраняет порядок заданий,
сверяет владельца, seed, SHA-связи и net counts. Поддерживается только v2;
историческая v1 не содержит отдельного подтверждения submit и отклоняется.

`completeReviewArtifact=true` требует status completed, endReason submitted,
submissionConfirmed=true, отсутствия пустых labels и reviewedAt=finishedAt.
Полный черновик после quit остаётся partial. Failed receipt с сохранённым
checkpoint может пройти проверку артефактов, но completeReviewArtifact остаётся
false. Для раннего сбоя нулевые counters квитанции являются placeholders;
verifier возвращает независимо рассчитанные counts и reportedCountsReconciled=false.

Дубликаты JSON keys отклоняются существующим строгим parser. Это устраняет
неоднозначное толкование повторных полей: [RFC 8259, objects](https://www.rfc-editor.org/rfc/rfc8259.html#section-4)
рекомендует уникальные имена; [Python JSON](https://docs.python.org/3/library/json.html#repeated-names-within-an-object)
описывает стандартное принятие последнего значения и возможность object_pairs_hook.
Времена должны быть UTC и идти в порядке startedAt ≤ reviewedAt ≤ finishedAt.

SHA-связи подтверждают bytes и согласованность артефактов. Личность, реальное
участие людей, независимость и предметная квалификация reviewer остаются false.
`reviewSourcesRevalidated=false`: sourceCommit/sourceFiles предыдущей сессии
проверяются как структурные declarations; verifier не сверяет их с Git snapshot.
[Отдельная source-проверка CLI](./blind-terminal-source-integrity.md) сохраняет
свои proofs. Архив текущего verifier отдельно закрепляет 15 его исходников.

## Использование

```bash
python3 scripts/verify-decision-review-session.py \
  --session docs/private/reviewer-a/session.json \
  --session-file-sha256 <session-sha256> \
  --input-review docs/private/package/review.first.blank.json \
  --input-review-file-sha256 <input-sha256> \
  --output-review docs/private/reviewer-a/review.json \
  --output-review-file-sha256 <output-sha256> \
  --output-dir docs/private/reviewer-a-verified
```

Три SHA нужно взять из закреплённого evidence inventory. Output directory
должен быть новым и находиться в docs/private. Некорректные входы отклоняются
до создания output; существующая квитанция не перезаписывается. Exit 0 означает
проверенную согласованность файлов; пригодность завершённого review отражена
в completeReviewArtifact, а предметные human gates проверяются отдельно.

## Реальное выполнение и регрессии

[Сводка](./review-session-verification-summary.json), source
`083f06d27f0d98386e1211ffd60ee4143a92f39f`. Новый CLI проверил семь исходных
квитанций предыдущих PTY-прогонов: один публичный просмотр без меток,
синтетические исправление/clear/submit/resume и обе source-integrity сессии.
Единственный completed artifact относится к прежней синтетической отправке;
новых review labels или human reviews этот прогон не создал.

Девять controls отклонены: ложный completed, новый ответ при исходно полном
черновике, human authority, boolean count, изменённые owner и seed, raw input
mismatch, duplicate JSON и отсутствующая session после SIGKILL. Контрольные
копии и исходные артефакты сохранены. Все 16 owned verifier PID отсутствуют.
Protected resident: два capture по 27 checks, восемь snapshot fields неизменны.

Независимый stdlib/Git auditor восстановил все семь verification JSON целиком
и проверил причины девяти отказов: 498 checks / 6028 JSON keys. Целевые Python
тесты 55; полный прогон 1248, четыре пропуска, 545.925 s. Model calls=0,
49 публичных development-заданий остаются пустыми, qualification=not_assessed.

[Immutable archive и проверка копии](./review-session-verification-archive-summary.json)
позволяют повторить независимый аудит исходных квитанций. При восстановлении
CLI и модели повторно не запускаются.
