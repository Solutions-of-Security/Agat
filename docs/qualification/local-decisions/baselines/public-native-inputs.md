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
Native observation и переносимый архив будут записаны после завершённых
прогонов на committed source.
