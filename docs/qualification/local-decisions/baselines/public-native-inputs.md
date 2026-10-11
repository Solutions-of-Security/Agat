# GLiClass и NanoJev на полных публичных development-входах

11.10.2026. Следующий этап [плана](../../../local-decision-model-plan-2026-09-21.md)
сохраняет весь ранее закреплённый набор публичных обращений, включая cases,
которые не помещаются в context bound конкретной модели. Два opt-in CPU
baseline используют те же adapters, model/dependency pins и policy, что
[GLiClass](./gliclass.md) и [NanoJev](./nanojev.md). Их результаты на прежних
36 synthetic cases сохранены отдельно.

## Scope

[CLI](../../../../scripts/evaluate-public-native-baseline.py) принимает
external file SHA полного исходного context profile. Context проверяется
существующим validator: только development, whole original pool order,
labels/predictions до измерения=0, calibration/holdout inputs не используются,
translation/truncation=false, qualification/routing=false/not_assessed.
Historical context source hashes проверяются через Git до загрузки модели.
Отбор по eligibility прежнего decider не применяется: у каждого baseline
свой prospective tokenizer/context bound.

[Library](../../../../scripts/lib/decision_public_native_baseline.py) выполняет
каждый полный request в original/reversed order. В модели не передаются
метки, rationale или результаты другой модели. В report остаются raw native
scores, full token arrays, typed mappings, source/group bindings, все
context refusals, whole-request/native timing и три process resource samples.
Score/Boolean сохраняют typed values; order variants не становятся новыми
независимыми документами. Policy/temperature, weights и serving runtime
не меняются.

## Проверка

`verify` не импортирует model packages: external context/report bytes,
source snapshot/current source equality, pinned local weights и header,
полные ordered inputs, native mappings, typed policy replay, cached counts,
resource arithmetic и отсутствие human/quality/SLO/routing authority
проверяются заново. Полная tokenizer parity проверяется отдельно реальным
tokenizer; stdlib verifier проверяет структуру и bindings token inventory.
Prospective publication byte bound включает newline canonical JSON.

Нет accuracy, NLL/Brier/ECE по эталонам, classification qualification или
выбора модели для автоматических действий. Accepted/abstained и смена
argmax описывают лишь model behavior на unlabelled public sources. Такой
набор не доказывает representative Agat traffic или independent human
качество. Реальные review, calibration/holdout и owner/SLO остаются открытыми.

```bash
/private/tmp/agat-gliclass-20261011/bin/python -B scripts/evaluate-public-native-baseline.py measure \
  --candidate gliclass --model-dir docs/private/gliclass-model \
  --context-profile docs/private/context-profile.json --context-file-sha256 ACTUAL_CONTEXT_FILE_SHA256 \
  --output-dir docs/private/public-gliclass-measured
python3.13 -B scripts/evaluate-public-native-baseline.py verify \
  --candidate gliclass --model-dir docs/private/gliclass-model \
  --context-profile docs/private/context-profile.json --context-file-sha256 ACTUAL_CONTEXT_FILE_SHA256 \
  --report docs/private/public-gliclass-measured/report.json --report-file-sha256 ACTUAL_REPORT_FILE_SHA256 \
  --output-dir docs/private/public-gliclass-verified
```

Для NanoJev используются `--candidate nanojev` и отдельный pinned model
directory. Один native process загружает одну модель; thread profile
сохраняется прежним. Explicit `truncation=False` соответствует
[Transformers tokenizer API](https://huggingface.co/docs/transformers/main/en/pad_truncation);
число CPU threads фиксируется с учётом
[PyTorch threading guidance](https://docs.pytorch.org/docs/2.14/notes/cpu_threading_torchscript_inference.html),
оптимальная конкурентность этим опытом не устанавливается.

[15 regression tests](../../../../scripts/test/test_decision_public_native_baseline.py)
проверяют complete denominator без чужого context prefilter, no-label scope
до model factory, ordered bindings, over-context zero-forward, native logits
и EOS/semantic mapping, typed False/Score, resources, authority и actual CLI
byte pins/publication bound. Отдельный isolated source-copy smoke запускает
CLI стандартным Python с `-I`, без исходного checkout и model packages;
транзитивный `workers/local_decisions.py` входит в frozen inventory.

## Завершённый native run

Измерены **все 49 unlabelled English public support inputs / 44 dependency
groups**, original/reversed order: 196 variants двух кандидатов, 166 actual
forwards. Calibration/holdout не токенизировались. Policy остаётся
`minProbability=0.8`, `minMargin=0.1`, `T=1`; weights и tokenizer pins
совпадают с отдельными baseline. Каждый полный GLiClass input / каждый
NanoJev candidate path имеет prospective limit 1024 tokens. Полные inputs
сохранены и при превышении limit; forward не выполняется.

| Наблюдение | GLiClass | NanoJev |
|---|---:|---:|
| Вычислено, original / reversed | 41 / 41 | 42 / 42 |
| Context refusal, каждый порядок | 8 / 49 | 7 / 49 |
| Accepted, original / reversed | 0 / 1 | 0 / 0 |
| Abstained, original / reversed | 41 / 40 | 42 / 42 |
| Argmax changed среди обоих computed | 6 / 41 | 0 / 42 |
| Actual native forwards | 82 | 84 |
| Полные / processed token paths | 98 / 82 | 490 / 420 |
| Все recorded / processed tokens | 73346 / 32832 | 323080 / 135320 |
| Длина full token path, включая refused | 186–8845 | 107–9025 |
| Native forward p50 / p95, ms | 38.457 / 127.615 | 1275.943 / 4883.865 |
| Whole original p50 / p95, ms | 36.680 / 129.594 | 1015.234 / 4889.910 |
| Whole reversed p50 / p95, ms | 34.295 / 128.008 | 1000.551 / 4846.028 |

Unexpected backend errors=0; context admission не меняется от порядка.
Percentiles — nearest-rank, округление до 3 decimals. Whole timing включает
все 49 variants каждого порядка, в том числе быстрые context refusals;
native timing — только выполненные forwards. NanoJev token totals включают
повторённые state/question prefixes каждого candidate path, GLiClass —
единый label-set path. Это разные вычислительные профили.

Max RSS whole process: GLiClass before/load/end —
49561600 / 488898560 / **736755712 bytes**; NanoJev —
52248576 / **5269979136 / 5269979136 bytes**. Darwin unit multiplier=1.
Whole backend load interval: GLiClass 7131.780791–7131.794916 ms,
NanoJev 17103.778–17103.793791 ms. Scoring всех 98 variants:
4042.0775–4042.090667 / 140935.316042–140935.3265 ms.
Self CPU user/system за load+scoring:
8.589528/1.509132 s и 260.059369/4.500581 s; CPU суммирует threads.
RSS — high-water всего Python process, включая imports/tokenizer/allocator,
не отдельная model allocation. Full scripts suite выполнялся одновременно:
эти наблюдения не устанавливают controlled speedup, concurrency, SLO/capacity.

Оба runs на own measured Git `600a05cfd51e3fcea8a091779ffc60da5f309ef8`,
155 sources из feature `ed624fb40e9ca161c63138b9b9af5bfb63778807`.
External context-file SHA-256:
`c1c45eca0e0d11180dc060d292ab14480708ada261a00c1b79c57649f04dcf8c`.
GLiClass report-file SHA:
`1e5db5ada16d0dbdbc8d28d2ac345f7fadfed542bd9baf9caaa6e3a8b0350cc9`;
NanoJev:
`6fb6a2eefaf6841168367aa18a11f6af1618acfa59c02a8499b79b235fa7bc80`.
[Public aggregate](./evidence/2026-10-11/public-native-summary.json) содержит
counts/resources/source pins без текстов и отдельных predictions: будущий
blind review не получает модельные ответы. Accepted не означает correct.

## Закрытая перепроверка и переносимость

- 15 new / 49 related tests — PASS; full **1454 tests / 4 skips / 543.263 s** —
  PASS. Full log SHA-256
  `a60a334a20bc4341e1ade3cce444aa848215a911f77c754b59e7eb8ed88d6e44`.
- Independent stdlib audit — **6081 checks**, model0. Реальные tokenizer
  replay — **98 GLiClass / 490 NanoJev arrays**, все tokens byte-identical,
  включая over-context inputs; forward=0.
- Production CLI: **35 refusals / 2 positive replays**; 37 owned PID absent,
  refusal outputs отсутствуют, оба copied reports byte-identical, model0.
- Все 6 native/capture/stdlib replay PID завершены. 4 protected resident PID,
  20 serving sources и 8 resident fields остались прежними; downloads=0.

Первая isolated попытка завершилась до model imports/forwards: в source
inventory отсутствовал транзитивный `workers/local_decisions.py`. Read-only
source добавлен в inventory, отдельный actual CLI source-copy regression
закрывает этот случай. Первый full suite был прерван для исправления и
не считается PASS; завершённый corrected suite указан выше. Обе истории
сохранены, runtime/worker body не менялись.

Private archive в исходном workspace:
`docs/private/public-native-baselines-20261011.zip`, **2164356018 bytes**,
1230 stored ZIP entries / 1248 restored logical files, SHA-256
`850f3e52418a04842e9e9efa8c4652098552ad391fddaa63c36b8a033a093313`.
Включены оба полных model artifacts, historical/current own Git, context,
raw native reports, complete/interrupted logs и все проверки. Обе ZIP copies
закрыты CRC/SHA/size и exact inventory. Restore переиспользует только
проверенные existing model bytes; ZIP сам содержит все bytes.
Из восстановленного source tree оба production CLI запущены стандартным
Python с `-I`, model0, external file pins; reports byte-identical и оба PID
завершены. [Archive receipt](./evidence/2026-10-11/public-native-archive-summary.json).

Human references=0, accuracy/calibration/holdout/representative traffic/SLO
не подтверждены, qualification=`not_assessed`, routing=false. Реальные
независимые reviews и данные следующего этапа остаются необходимыми.
