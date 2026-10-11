# Сверка завершённых development reviews с native results

11.10.2026 MSK. [CLI](../../../../scripts/diagnose-finalized-decision-development.py)
связывает [finalized review bundle](./review-bundle.md) с полным сохранённым
[native option-order diagnostic](../performance/public-option-permutation-diagnostic.md).
Она не запускает inference. Реальные submitted reviews для 49 исходных
development cases пока не получены; вычисление на них требует готового bundle.

## Граница входов

Для bundle, original context, permutation context, native plan и result
задаются пять независимых file SHA. Сначала заново проверяются все source
reviews, comparison и adjudication. Finalized dataset должен принадлежать
точно тому pool, который восстанавливается из полного original context.
Другая pool с теми же case IDs не допускается.

Далее production native verifier проверяет historical committed sources,
полный immediate journal, все typed results и physical counters. Dataset
сохраняет исходный split seed и содержит каждый development case ровно один
раз в прежнем порядке, с теми же group, provenance и полным request. Partial
labels, другой порядок, изменённый источник, calibration/holdout cases и
неполный набор option orders не принимаются.

[Анализатор](../../../../scripts/lib/decision_review_native_diagnostics.py)
восстанавливает whole variant inventory из оригинальных запросов и заново
проверяет phase. Перед публикацией ещё раз проверяются bundle/native history
и consumed raw bytes. Output создаётся только после всех проверок под
`/docs/private`, directory mode 0700 / files 0600, без overwrite.

## Значение метрик

Каждый option order сравнивается с теми же submitted labels. Original case
остаётся единицей наблюдения: варианты одного входа не суммируются как
независимые примеры. Whole-context rejection остаётся в `caseCount` и общем
знаменателе; scored denominator показан отдельно для каждого order. Если
одна перестановка изменила context admission, отчёт сохраняет это различие.

Отчёт содержит argmax agreement со submitted label, accepted coverage,
accepted disagreements, macro-F1, NLL, multiclass Brier и ECE10 относительно
этих меток. Reliability bins явно называются `fractionMatchesSubmittedLabel`.
Per-case строки сохраняют order, status/reason, выбранный ID и probability
присланной метки; group census считает shared groups один раз. Abstained
disagreement не становится accepted error.

Переиспользованы существующие project metrics без изменения policy или
temperature. Multiclass Brier — сумма квадратов по всем allowed options,
без деления на два; NLL использует прежний project probability floor 1e-15.
[Scikit-learn model evaluation](https://scikit-learn.org/stable/modules/model_evaluation.html)
различает метрики категориального выбора и распределения вероятностей.
[Рекомендации по leakage](https://scikit-learn.org/stable/common_pitfalls.html)
поддерживают сохранение отдельного test/calibration data. Источники прочитаны
11.10.2026; scikit-learn dependency не добавляется.

Это comparison с присланными метками. Проверка файлов не аутентифицирует
людей и не подтверждает предметную правильность labels. В отчёте явно
`submittedLabelMetricsComputed=true`, `referenceLabelsVerified=0`, все
human/identity/expertise flags=false, `classificationAccuracyMeasured=false`.
Он не выдаёт квалифицированную accuracy, confidence bound, calibration,
holdout evaluation или routing approval. Qualification=`not_assessed`.

## Запуск и повторная проверка

Перед запуском sources должны быть committed. Обязательные аргументы:

- `--bundle` и `--bundle-file-sha256`;
- `--evidence-dir`, `--context-profile`, `--permutation-context`;
- `--context-profile-file-sha256`, `--permutation-context-file-sha256`,
  `--plan-file-sha256`, `--result-file-sha256`;
- новый `--output-dir` под `/docs/private`.

Первый запуск сохраняет `diagnostic.json` и отдельную `execution.json`
с committed implementation source pins. Для проверки saved diagnostic
добавляются оба `--diagnostic-report` / `--diagnostic-report-file-sha256`.
Все исходные reviews и native receipts снова проверяются; exact canonical
report восстанавливается целиком. Подмена чисел или authority даже вместе
с новыми file SHA и seal не проходит. Исторический код читается, не исполняется.

14 новых / 59 related Python tests прошли: полные synthetic measurements
на шести sourced inputs, 42 variants / seven orders, context rejection,
low-confidence abstention, shared groups, independent NLL/Brier arithmetic,
raw pin/source mutations, foreign pool, late artifact/source drift и private
saved-report replay. Тестовые метки явно synthetic; они не являются реальными
review исходных 49 случаев. Первый test fixture был отклонён из-за отсутствующего
обязательного declared entrypoint; fixture исправлен без ослабления verifier.
