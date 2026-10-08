# Терминальная независимая разметка

[CLI](../../../../scripts/review-decision-pool.py) открывает закреплённый SHA
blank или partial `agat.decision.review.v1` в интерактивном терминале. Он
устраняет необходимость редактировать pool/input fingerprints вручную.
Рецензент видит исходный текст, атрибуцию, вопрос и все разрешённые варианты.
Family/group IDs, seed, splits, чужие метки и модельные предсказания интерфейс
не показывает. Сам review JSON по прежнему содержит pool/seed: интерфейсная
слепота не является разграничением доступа к файлу или доказательством
независимости рецензента.

Выбор вводится номером варианта; обоснование обязательно, подтверждение —
отдельным `y`. Пустой ввод не выбирает ответ. `n` возвращает к выбору,
`:skip` на шаге выбора оставляет метку пустой, `:quit` завершает сессию.
Пропуск отличается от разрешённого abstain-варианта: abstain — явно выбранная
метка с обоснованием. Boolean и Score показывают закреплённое значение каждого
варианта. Порядок, текст, варианты и семантические fingerprints не меняются.

## Основание решения

Изучен реальный manual annotation workflow
[Prodigy](https://prodi.gy/docs/text-classification): выбор одной взаимоисключающей
категории и отдельный review конфликтов совместимы с существующей схемой Agat.
Для нашего независимого review не подключаются model suggestions или active
learning. Существующий групповой split/seed остаётся замороженным; изменение
задачи или отбор «удобных» вопросов этим инструментом не выполняется.

Недоверенные вопросы могут содержать управляющие последовательности терминала.
[MITRE CWE-150](https://cwe.mitre.org/data/definitions/150.html) описывает
подмену вывода/курсорных позиций через ANSI escapes.
Renderer оставляет LF, tab и символы, удовлетворяющие
[Python str.isprintable](https://docs.python.org/3/library/stdtypes.html#str.isprintable);
ESC, C1, CR, backspace, bidi controls и остальные непечатаемые символы
показываются как буквальные escape-последовательности. Это преобразование
только отображения: исходные строки и fingerprint в review сохраняются.
Скрипт не исполняет команды, не открывает ссылки и не обращается к модели/сети.

## Сохранение и продолжение

Вход: обычный файл с отдельным SHA-256, до 32 MiB / 1000 cases. До первого
вопроса проверяются schema/pool SHA, полный упорядоченный inventory, input SHA,
разрешённые варианты и ownership ранее заполненных ответов. Чужой reviewer ID,
уже submitted review, неполная пара метка/обоснование и drift источников
отказывают. Ввод и вывод должны быть TTY; pipe не используется для bulk labels.

Output — новый каталог внутри `docs/private`, 0700, файлы — 0600. После каждого
`y` создаётся полный `review.json` через exclusive temporary file, fsync,
atomic replace и directory fsync. Замена ограничена checkpoint собственной
сессии; входной пакет не изменяется. EOF/Ctrl-C/`:quit` сохраняют ранее
подтверждённые ответы, незавершённый текущий ответ не принимается. После
crash последний целый checkpoint можно продолжить в новом каталоге; это
включает последний ответ, сохранённый до прерывания submission stamp.

`reviewedAt` устанавливается по фактическим UTC-часам только при заполнении
всех заданий. Partial review сохраняет `reviewedAt = null` и exit 2; completed
даёт exit 0, failure — 1. Sealed `session.json` закрепляет source commit/file
SHA, входной и выходной file SHA, число прежних/новых/оставшихся ответов и
причину завершения. Failed receipt сохраняется при ошибке после создания
сессии. Checkpoint без успешного receipt нельзя считать завершённой проверкой.

```bash
shasum -a 256 docs/private/public-support-review-20261008/review.first.blank.json
python3 scripts/review-decision-pool.py \
  --review docs/private/public-support-review-20261008/review.first.blank.json \
  --review-file-sha256 REPLACE_WITH_ACTUAL_64_HEX_SHA \
  --reviewer-id YOUR_OWN_REVIEWER_ID \
  --output-dir docs/private/your-first-review-session
```

Для продолжения передайте `review.json` предыдущей partial session, его
фактический SHA, тот же reviewer ID и новый output directory. Второй
рецензент начинает с собственного неизменённого blank, независимо от первого.
Не показывайте ему первый review/receipt с ответами. Два completed файла
передаются прежнему `python3 -m decision_runtime finalize-review`; разногласия
по прежнему требуют третьего рецензента. Команда не назначает людей и не
отправляет им сообщения.

Reviewer ID и TTY **не аутентифицируют человека**. Receipt оставляет
`reviewerIdentityVerified`, `humanExecutionVerified`, `independentReviewVerified`
и `expertQualificationsVerified = false`; команда должна подтвердить реальное
выполнение, квалификацию и независимость участников отдельно. Completed session
не создаёт consensus, calibration, holdout или qualification автоматически.
Model calls — 0, routing false, qualification not_assessed.

## Проверка реализации

11 новых проверок покрывают повреждённый pool/input binding, чужую/незавершённую
разметку, скрытые splits, реальные terminal-control символы, обязательное
подтверждение, skip/EOF/interrupt, длинный paste, частичное продолжение,
atomic write failure, source drift и совместимость с `finalize-review`.
Совместно с существующими calibration/review checks: 31 pass.
Все размеченные тестовые ответы — явно synthetic fixtures; они не используются
как human gold для реальных публичных вопросов.
