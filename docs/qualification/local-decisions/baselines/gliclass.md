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
native измерение выполнено ниже; memory/representative qualification ещё
требуют отдельного этапа.

## Фактическое измерение, 11.10.2026

[Публичная машинная сводка](./evidence/2026-10-11/gliclass-summary.json)
содержит SHA private native report, source/fixture commits и scope flags.

Весь исходный [development.v1](../development.v1.json): 36 authored synthetic
случаев, **три общих task-template groups**, 24 Choice / 12 Boolean. Это те же
input bytes и synthetic labels, на которых раньше измерены два Qwen scorer.
Score-контракт покрыт синтетическим adapter test; native Score на этих 36
случаях не измерялся. Original + reversed дают 72 зависимых forward-вызова,
200–267 полных токенов, без truncation/backend errors.

| Кандидат | Совпадение с synthetic labels, original | reversed | Смена argmax при перестановке |
|---|---:|---:|---:|
| decider-2b, прежний MLX run | 31/36 | 30/36 | 1/36 |
| Qwen3.5 2B Base, прежний MLX run | 18/36 | 28/36 | 10/36 |
| GLiClass Edge v3, CPU FP32 | 11/36 | 15/36 | 22/36 |

При прежнем policy original accepted 3/36, errors среди принятых 0/3;
reversed accepted 2/36, errors **2/2**. Whole-attempt coverage: 8.33% / 5.56%.
Family original/reversed matches: evidence 3/12 и 6/12, classification 0/12
и 5/12, clarification 8/12 и 4/12. Низкое покрытие и чувствительность к порядку
не обосновывают замену основного scorer этим профилем. Обобщение на другие
GLiClass checkpoint или реальные источники из этих данных не следует.

Model load 6492.902 ms; original total request p50/p95 21.290/97.377 ms,
reversed 20.861/84.040 ms; native forward pooled p50/p95 20.604/82.993 ms.
Это описательные CPU-измерения одной последовательности без отдельного warmup.
Первый запрос и зависимые permutations сохранены отдельно; latency разных
исторических runtimes не сравнивается как выигрыш. Process memory не измерена
в этом run. Temperature/threshold/split/labels/weights не менялись после вывода.

Независимый stdlib audit сделал 3086 checks и повторно вычислил softmax,
typed Boolean, NLL, Brier, macro F1, ECE, coverage, argmax и прежние Qwen
counts из исходных logits. Два stdlib production replay дали byte-identical
native report, modelCalls=0. Реальный pinned tokenizer отдельно перепроверил
все 72 полных token-ID массива без model calls. Ещё 11 production CLI refusals отвергли чужие
splits, SHA, rehashed authority/summary/typed Boolean/clipping и лишний model
file до output; 12 записанных CLI PID этой проверки отсутствуют.
Protected resident: прежние четыре PID, 20 runtime source files и все восемь
защищённых полей совпали до/после; 27 checks в каждом capture.

Первоначальные два неуспешных запуска сохранены: неверный bytes→JSON переход
CLI и неприведённый `BatchEncoding` контейнер. Оба исправлены и покрыты тестом
entrypoint/native container; они не используются как успешное inference.
Первоначальный sandbox-related test log и отменённый дублирующий docs:check
также сохраняются отдельно. Native run выполнен из собственного fixture Git,
с копиями committed измеряемых исходников; fake model tests не смешиваются
с 72 фактическими forward-вызовами.

12 adapter tests, полный scripts suite 1417 (4 optional skips), runtime 135
(3 optional skips) прошли. 18 Node documentation tests — PASS; links и process
catalog проверены отдельно. Прерванный дублирующий Python прогон через
`docs:check` не заявлен успешным.

Финальный [archive receipt](./evidence/2026-10-11/gliclass-archive-summary.json):
`docs/private/gliclass-baseline-20261011-v2.zip` в исходном workspace,
126761271 bytes, SHA256
`b7480eebf5a61e855f84915b6524ee7209599babcbd4b5614c3ac10362290dbb`.
1097 ZIP entries восстанавливают 1114 logical files; 18 повторных model files
хранятся один раз и восстанавливаются из той же проверенной копии. CRC, каждый
SHA/size, closed inventory и actual copied production CLI replay — PASS,
modelCalls=0, private native report byte-identical. Завершённые 1417/135 logs
в финальном archive совпали с оригиналом побайтно. Первая archive-копия с
progress snapshot runtime log сохранена отдельно; её log не используется
как подтверждение полного runtime suite.
