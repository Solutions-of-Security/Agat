# GLiClass: закреплённый локальный development baseline

11.10.2026. Реализован отдельный opt-in CLI для кандидата GLiClass из
[плана](../../../local-decision-model-plan-2026-09-21.md). Все результаты имеют
`diagnostic_only`, `development-only`, `routingEnabled=false`,
`qualifiedForRouting=false`, `classificationAccuracyQualified=false` и
`qualification=not_assessed`. Serving runtime и профили 0.12.3 не изменены.

## Проспективный профиль

[Model card](https://huggingface.co/knowledgator/gliclass-edge-v3.0) описывает
32.7M / 131 MB Edge v3, указана лицензия Apache-2.0. Выбрана одна ревизия
`df03993a2ed98e5e4a0d2dd7efbbd105abe874cf`; SHA и размеры всех пяти inference
файлов закреплены в [каталоге](./gliclass-model.json). Маленькие файлы сверены с
Git blob из API этой ревизии, SHA весов — с LFS metadata; загрузчик затем
сверяет SHA256 фактических bytes. Это проверка целостности, не подпись автора.

Подготовлена отдельная среда Python 3.13 на macOS arm64. Все 41 wheel из
официального PyPI закреплены с hashes в
[requirements](./requirements-gliclass-macos-arm64.txt). GLiClass 0.1.20,
PyTorch 2.14.1, Transformers 5.19.0. Optional зависимости не добавлены в
основной runtime или CI.

CPU FP32, eager attention, один полный запрос за forward, 2 intra-op / 1 inter-op
thread, `eval()` + `inference_mode()`. Максимум — **1024 токена всего входа**,
установлен до измерения. Для uni-encoder сохранён training format:
`<<LABEL>>description … <<SEP>>question + kind + state`. Boolean/Score labels
дополнительно сохраняют исходное `value`, Choice сохраняет `abstain=true`.
ID вариантов связываются с исходным индексом; модель не получает gold,
annotation rationale или expectedOptionId. Полный state/question не переводится
и не сокращается.

[Официальный pipeline](https://github.com/Knowledgator/GLiClass) использует
`truncation=True`. Этот адаптер токенизирует с `truncation=False`, сохраняет весь
массив token IDs и отказывает до forward при >1024 токенах. Структурные
`<<LABEL>>`, `<<SEP>>`, `<<EXAMPLE>>` внутри исходных данных тоже вызывают
явный отказ; их скрытая очистка не проводится. Проверены число и расположение
candidate markers, точная форма `(1, candidate_count)` и конечность logits.

Используются native logits, затем существующий DecisionEngine выполняет один
softmax по всему разрешённому списку. Это согласуется с single-label режимом
[официальной документации](https://docs.knowledgator.com/docs/frameworks/gliclass/usage/).
Temperature 1, `minProbability=0.8`, `minMargin=0.1`; подбор порога/весов/языка
по увиденным результатам не проводится. Numeric Score — прежняя взвешенная
сумма по заданным значениям; Boolean `false` сохраняет тип.

## Проверка и повторение

[CLI](../../../../scripts/evaluate-gliclass-development.py),
[offline адаптер](../../../../scripts/lib/decision_gliclass_baseline.py),
[тесты](../../../../scripts/test/test_decision_gliclass_baseline.py).
`download` — единственная команда с сетью; `measure` использует только local
files и builtin ModernBert без remote Python. `verify` работает на стандартной
библиотеке: проверяет pinned weights, весь dataset, measured Git commit/source
files, 2 порядка, полный native call inventory, заново вычисляет typed results
и metrics из logits. Источники должны быть committed, проверяются до/после.
Отчёт и execution receipt пишутся только в новый `/docs/private` каталог,
права 0700/0600, без overwrite.

```bash
python3.13 -m venv /private/tmp/agat-gliclass-env
/private/tmp/agat-gliclass-env/bin/python -m pip install --require-hashes \
  --only-binary=:all: --index-url https://pypi.org/simple \
  -r docs/qualification/local-decisions/baselines/requirements-gliclass-macos-arm64.txt
python3.13 -B scripts/evaluate-gliclass-development.py download \
  --model-dir docs/private/gliclass-model
/private/tmp/agat-gliclass-env/bin/python -B scripts/evaluate-gliclass-development.py measure \
  --model-dir docs/private/gliclass-model \
  --dataset docs/qualification/local-decisions/development.v1.json \
  --dataset-file-sha256 ACTUAL_FILE_SHA256 --output-dir docs/private/gliclass-run
python3.13 -B scripts/evaluate-gliclass-development.py verify \
  --model-dir docs/private/gliclass-model \
  --dataset docs/qualification/local-decisions/development.v1.json \
  --dataset-file-sha256 ACTUAL_FILE_SHA256 --report docs/private/gliclass-run/report.json \
  --report-file-sha256 ACTUAL_REPORT_FILE_SHA256 --output-dir docs/private/gliclass-verified
```

Синтетические labels не являются independent human qualification. Обратный
порядок — зависимая диагностика тех же случаев. CPU FP32 latency этого этапа
не является контролируемым сравнением с предыдущими MLX/GPU/quantized runs.
Базовая model card не доказывает русскую предметную точность. Фактическое
native измерение и архив добавляются после проверки production CLI.
