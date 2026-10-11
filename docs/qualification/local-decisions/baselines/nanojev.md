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

## Actual native development run

CPU FP32/eager reference выполнил все 36 прежних authored synthetic cases
в двух порядках: 72 forwards, 192 полных candidate paths, 22706 processed
tokens, 89–212 tokens на path, backend errors=0. Это 3 зависимые template
groups: 24 Choice и 12 Boolean, без native Score benchmark cases.

| Порядок | Совпало с synthetic labels | Accepted | Accepted errors | Abstained |
|---|---:|---:|---:|---:|
| Original | 13/36 | 12/36 | 6/12 | 24/36 |
| Reversed | 13/36 | 12/36 | 6/12 | 24/36 |

По families original/reversed: evidence 4/12 и 4/12; classification 3/12
и 3/12; clarification 6/12 и 6/12. Все 12 Boolean были приняты как typed
`False`; wire value и policy независимо проверены. Ни один из 24 Choice
не принят. Argmax изменился в 0/36 cases, включая 0/24 Choice. Небольшие
FP32 различия logits/probabilities при перестановке сохранены; bit-identical
distributions не заявляются. Boolean reversal нормализован по значению.

NLL original/reversed — 1.3026917266 / 1.3026917365; Brier —
0.7855709529 / 0.7855709579; ECE10 — 0.2551964003 / 0.2551964032;
macro-F1 — 0.1740740741 в обоих порядках. Эти величины относятся к authored
labels; ни temperature, ни thresholds не подбирались. Ошибка среди принятых
synthetic решений — 50%, что оставляет этот profile diagnostic_only.

Все четыре planned кандидата теперь имеют whole-inventory development
evidence; stdlib audit заново проверил каждый старый report по тому же
dataset/hash, raw logits и typed policy:

| Кандидат | Original matches | Reversed matches | Changed argmax |
|---|---:|---:|---:|
| decider 2B, MLX | 31/36 | 30/36 | 1/36 |
| Qwen Base, MLX | 18/36 | 28/36 | 10/36 |
| GLiClass Edge v3, CPU FP32 | 11/36 | 15/36 | 22/36 |
| NanoJev, CPU FP32/eager reference | 13/36 | 13/36 | 0/36 |

Это descriptive comparison 36 cases / 3 groups, без representative human
quality, frozen calibration или independent holdout. Candidate weights,
architectures и execution profiles различаются; latency comparison не
контролировался, победитель для routing не назначен.

## Время и process memory

Model-load block — 19671.449 ms. Полный backend phase, включая imports и
проверки artifacts, — 28629.021–28629.031 ms; все 72 scoring —
29221.419–29221.429 ms. Native forward p50/p95 — 409.685 / 535.440 ms;
whole-request original — 410.309 / 540.619 ms, reversed — 410.893 / 536.841 ms.
Download не включён в resource profile. Значения описывают один run текущего
Mac и текущую нагрузку; принятый latency SLO не установлен.

Max RSS до backend — 49364992 bytes (47.078125 MiB); после load и после
scoring — 5258067968 bytes (5014.484375 MiB, около 4.897 GiB). Пик возник при
load и не вырос при scoring. Это whole-process high-water mark, включающий
одновременные временные buffers/weights; model-only allocation не измерена.
Load + scoring wall interval — 57850.445–57850.456 ms, user/system CPU —
61.250621 / 5.009400 s. Все three raw snapshots и arithmetic сохранены.

## Перепроверка

Source feature `c7cda24896f8310f7f9d581c871592f5c8a33a59`; own fixture Git
`42d2abc302d702390634392cdb49ade7e9bd2ead`; 146 measured source files.
Header подтвердил 322 FP32 tensors и полный payload 2385001992 bytes;
strict state_dict load не оставил missing/unexpected keys. Report file
SHA-256 `bdf686bd83edf09be5ae78c67f7f7ef85b4f944c1fd17da7ca06c60fab6d92ec`,
sealed report `be1fdcff6112824ea586757eef8f9afe515b7f24b2a9f4d429f9de219eff4dd6`.
Независимый stdlib audit — 4909 checks. Separate direct Tokenizers JSON
replay подтвердил все 192 полных arrays; model calls=0. Standard verifier
проверяет структуру/bindings token inventory; полная BPE parity проверена
отдельным tokenizer replay.

Production CLI: 17 refusals / 1 byte-identical stdlib replay, inference0 и
без partial outputs. Scope, external byte pins, typed mapping, native
Boolean zero, EOS, fake summary/PID/authority, GPU profile, incomplete
state_dict identity и extra model file отвергнуты. Все 18 owned PID
завершились. Основные measure/capture/replay — 4 owned PID,
все завершены; 4 protected resident PID / 20 source files / 8 полей сохранены.
14 new adapter tests + 20 related GLiClass/resource tests — PASS;
full scripts suite — 1439 tests / 4 skips / 524.970 s PASS.
Один regression проверяет exact byte bound файла с завершающим newline:
отказ происходит до публикации output. Writer сохраняет compact canonical
JSON и один newline; этот дополнительный byte теперь входит в предел.

Первый run на `7c205bfdaa6334d058eb34ca21ac6acd352d12cb` сохранён отдельно.
Повтор на текущем source подтвердил bit-identical все 72 native logits/token
paths и typed results, кроме duration; независимый comparison — 144 pairs.
Между original/reversed внутри одного run остаются небольшие FP32 различия
Choice, описанные выше. Повтор не увеличивает число cases/groups и не
измеряет стоимость модели при контролируемой нагрузке.

Дополнительно отдельная **typed-contract-only** authored fixture:
3 cases / 1 shared document group, 6 real native forwards / 10 paths для
Choice/Boolean/Score, backend errors=0, byte-identical stdlib replay.
Boolean вернул typed `True`; Score — `abstain`, numeric value `null`.
Эта smoke-fixture подтверждает загрузку heads/shape/mapping, не качество
ответов. Accepted numeric Score mean проверен unit fixtures; фактический
Score smoke не выдан за такое доказательство. Его SHA-256
`6eb6c25b046cd4aa8510fcd0c5fe6b35268758dd2a274bf79de7e4f3898e0fcc`.
Обучение, новые human references, calibration и promotion не выполнены;
authority/routing=false/not_assessed.

## Переносимый архив

[Aggregate summary](./evidence/2026-10-11/nanojev-summary.json) содержит
whole-inventory metrics и process resource arithmetic без исходных текстов.
[Archive receipt](./evidence/2026-10-11/nanojev-archive-summary.json) связывает
текущий report и завершённый full-suite log SHA-256
`f541439c0e8cdf586d0eacc26c11e6bfe266a9877e11149ad174b1576bfadc54`.

Private `docs/private/nanojev-baseline-20261011-v2.zip` в исходном workspace:
1958090546 bytes, SHA-256
`2a99134b51e95d6082ef935115c1c6dca956b40402ccd00884a57a60e0fe9e90`;
870 ZIP entries / 908 restored logical files. Сохранены оба native runs,
их own Git snapshots, pinned source/tokenizer/dependency metadata, оба
typed-contract smoke, negative CLI probes, independent audits и оба
завершённых full-suite logs. Одинаковые model files записаны один раз,
включая полные bytes weights; старый завершённый v1 archive также сохранён.

Обе ZIP copies проверены по каждому CRC/SHA/size и закрытой inventory.
Actual restore находится в `docs/private/nanojev-baseline-original-copy-20261011-v2`.
При восстановлении bytes модели повторно сверены с архивом и hard-linked
из прежней проверенной копии; самостоятельный ZIP содержит их целиком.
CLI из новой restored fixture подтвердил byte-identical report/model0;
owned verifier PID завершён. Proof сохранён под
`docs/private/nanojev-baseline-original-copy-proof-v2.json`.
