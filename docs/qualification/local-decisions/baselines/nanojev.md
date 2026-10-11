# NanoJev: закреплённый CPU reference baseline

11.10.2026. Для четвёртого кандидата из [плана](../../../local-decision-model-plan-2026-09-21.md)
добавлен отдельный opt-in development CLI. [Официальный checkpoint](https://huggingface.co/C-Tianyu/NanoJev)
`unified-games-v1` закреплён по immutable revision
`047b927b30882a1138fc504821b82ac145a4b81a`. Публичные игровые результаты
автора не квалифицируют задачи/русские документы Агат.

## Закреплённые артефакты

[Descriptor](./nanojev-model.json) фиксирует 12 inference/provenance files,
их bytes/SHA-256, источник `C-Tianyu/NanoJev` и base
`Qwen/Qwen3-0.6B@c1899de289a04d12100db370d81485cdf75e47ca`.
`best.safetensors`: 2385039280 bytes, SHA-256
`f68c47d66998231b86b7e91b4ed5e82ae23acf104c8b7cd6d165c3ac7b7ffe1b`.
Small official files проверены по metadata Git blob SHA-1 и сохранённому
SHA-256; tokenizer JSON pin взят из LFS metadata. Training/source metadata
прочитана до выполнения кода. Source LICENSE — MIT; объявленные лицензии
checkpoint и исходной Qwen модели учитываются отдельно.

[Hash-locked environment](./requirements-nanojev-macos-arm64.txt) сохраняет
уже проверенный isolated Python 3.13/macOS arm64 wheel environment. Native
backend использует Torch 2.14.1, Transformers 5.19.0, Safetensors 0.8.0,
NumPy 2.5.4 и Tokenizers 0.23.3. Это фактический локальный diagnostic stack;
опубликованный CUDA recipe использовал другие Torch/Transformers versions.
Дополнительные GLiClass packages из shared environment не импортируются
для native NanoJev inference. Глобальные Python packages не меняются.

`download` — единственная команда с доступом к Hub. Каждый файл проверяется
по внешнему committed descriptor, local inventory закрыта; manifest
публикуется последним, новый private каталог — 0700/0600. Нативные исходники
из pinned artifact используются только после проверки всего checkpoint.
Header safetensors разбирается stdlib: все tensors FP32, shapes/offsets
покрывают payload целиком без overlaps/gaps/trailing bytes. Нет pickle load.

## CPU-профиль и смысл вариантов

[Опубликованный predictor](https://github.com/TianyuCodings/NanoJev)
явно требует CUDA. Этот opt-in reference создаёт тот же builtin Qwen3 body
и pinned `DecisionModel`, загружает **все** weights через strict state_dict
и выполняет их на CPU: FP32, eager attention, два intra-op threads, один
interop thread, batch=1 complete question, no cache/prefix sharing,
inference mode. CUDA loader/fallback не подменяется; самостоятельный CPU
profile явно входит в model identity. GPU latency автора здесь не переносится.

State/question и candidate texts сохраняются полностью. Pinned
`prepare_examples` и независимый render заново создают все segmented token
paths с `add_special_tokens=False` и одним EOS. Prospective limit — 1024
tokens **на candidate path**; суммарное число processed tokens включает
повторяемый prefix всех paths. Over-context input сохраняется в audit и
отвергается до forward. Native tokenizer control syntax в исходных текстах
отвергается. Нет autoregressive decode или внешних model calls.

Choice сохраняет исходный порядок и ID/description; explicit abstain flag
остаётся и в policy, и в label description. Boolean использует единственный
native proposition path и logits `[0,z]` для semantic false/true. Raw typed
IDs Агат сопоставляются по значениям Boolean, поэтому `False` сохраняется.
Score native levels упорядочены по typed numeric value, их значения явно
добавлены к полному question; mean считается по исходным numeric values,
не по позиционному индексу. Reversal Boolean/Score нормализуется по
семантическим значениям; такую стабильность нельзя выдавать за независимый
тест чувствительности этих native heads к порядку.

Прежний `Policy()`/temperature=1 остаётся uncalibrated. DecisionEngine
сохраняет целое распределение и исходы ok/abstain/error. Score и Boolean
unit fixtures подтверждают контракт; это не native quality qualification.

## Измерение и повторная проверка

[CLI](../../../../scripts/evaluate-nanojev-development.py),
[library](../../../../scripts/lib/decision_nanojev_baseline.py),
[tests](../../../../scripts/test/test_decision_nanojev_baseline.py).
Измеряется вся исходная synthetic/assistant development inventory в двух
порядках. Calibration/holdout/human-authority inputs отвергаются до загрузки
модели. Report связывает actual committed source snapshot, внешние bytes
dataset/model pins, весь call inventory, raw native logits и typed mappings,
полные token paths, metrics/families/order и три whole-process RUSAGE_SELF
снимка. RSS — process high-water mark, не model-only allocation.

`verify` работает standard Python без model imports, требует external
report/dataset SHA, наличие measured source commit и byte-identical current
sources, заново проверяет logits/typed results, whole-attempt denominator,
policy, source bindings, cached metrics и raw resource arithmetic. Local
weights/header снова сверяются с внешними pins; self-rehashed manifest не
может заменить checkpoint. Authority/routing остаются false/not_assessed.

```bash
python3.13 -B scripts/evaluate-nanojev-development.py download --model-dir docs/private/nanojev-model
/private/tmp/agat-gliclass-20261011/bin/python -B scripts/evaluate-nanojev-development.py measure \
  --model-dir docs/private/nanojev-model --dataset docs/qualification/local-decisions/development.v1.json \
  --dataset-file-sha256 ACTUAL_DATASET_FILE_SHA256 --output-dir docs/private/nanojev-measured
python3.13 -B scripts/evaluate-nanojev-development.py verify \
  --model-dir docs/private/nanojev-model --dataset docs/qualification/local-decisions/development.v1.json \
  --dataset-file-sha256 ACTUAL_DATASET_FILE_SHA256 --report docs/private/nanojev-measured/report.json \
  --report-file-sha256 ACTUAL_REPORT_FILE_SHA256 --output-dir docs/private/nanojev-verified
```

Native results добавляются после фактического полного прогона и проверки
скопированного production verifier. Пока никакие quality/memory/latency
значения модели не выданы за измеренные, обучение/разметка не выполнены.
