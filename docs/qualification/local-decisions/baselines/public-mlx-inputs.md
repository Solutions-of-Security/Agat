# decider и Qwen Base на полных публичных development-входах

11.10.2026. Этот opt-in offline этап [плана](../../../local-decision-model-plan-2026-09-21.md)
дополняет [GLiClass/NanoJev на тех же источниках](./public-native-inputs.md).
Каждый checkpoint получает все исходные unlabelled development requests,
в original/reversed option order, включая over-context inputs.
Calibration/holdout не используются. Human references, representative
Agat/Russian quality, SLO и routing authority не выводятся из model outputs.

## Закреплённый профиль

[Publisher descriptor](./public-mlx-candidates.json) фиксирует все 13 файлов
двух прежних checkpoint: [decider](https://huggingface.co/Mapika/decider-2b/tree/b37f7e1ba3fbc9238004cf531fabbee2619973fd)
и [Qwen3.5-2B Base](https://huggingface.co/Qwen/Qwen3.5-2B-Base/tree/b1485b2fa6dfa1287294f269f5fb618e03d52d7c).
File SHA/bytes сверяются с immutable publisher metadata и actual local bytes;
manifest не может заменить этот inventory. Qwen Base model card описывает
[pre-trained-only weights](https://huggingface.co/Qwen/Qwen3.5-2B-Base/blob/b1485b2fa6dfa1287294f269f5fb618e03d52d7c/README.md),
готовая decision qualification из этого не следует.

Используется существующий **MlxBackend.score**, runtime 0.12.3, полный
state-first letter prompt, fresh backbone call без cross-request cache,
selected letter rows и float32 output logits. Weights/head/scoring graph
не меняются. Text backbone Qwen3.5; image inputs не вводятся.
Одинаковый prospective envelope: 2048 input tokens, allocator cache 128 MiB,
wired residency budget 4096 MiB, unquantized. Wired budget не ограничивает
общую live allocation. Policy прежняя: `0.8 / 0.1`, temperature=1 uncalibrated.
Whole input сохраняется при `context_too_long`; backbone/eval не вызываются.
Eligibility другого tokenizer/model не применяется.

Полный [MLX dependency lock](../../../../decision_runtime/requirements-mlx.txt)
проверяется до model factory: MLX 0.32.2 / mlx-lm 0.31.3, Apple Silicon,
Python 3.13. Diagnostic observer сохраняет фактические encode events двух
prompt parts/letter continuations, decode hashes, raw logits, backbone entry,
native/whole timing и resource samples. [MLX lazy evaluation](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.eval.html)
учтён: native observation заканчивается после исходного `mx.eval` и `tolist`,
а не после построения lazy graph. [Peak allocator memory](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.get_peak_memory.html)
записывается отдельно от whole-process `RUSAGE_SELF` RSS; это разные scopes.
Опыт не меняет installed resident service.

## Воспроизведение и закрытая проверка

[CLI](../../../../scripts/evaluate-public-mlx-baseline.py) не скачивает модели.
Пути и external byte SHA передаются явно; model snapshot должен находиться
на локальном диске. Frozen source/current equality, historical context Git,
complete input/group order и pinned artifact проверяются before/after run.
`verify` работает стандартным Python без model packages, пересчитывает policy
из native scores, complete denominator, refusals, timings/resources и false
authority. Actual tokenizer parity проверяется отдельно.

```bash
PINNED_MLX_PYTHON -B scripts/evaluate-public-mlx-baseline.py measure \
  --candidate qwen3.5-2b-base --manifest docs/private/qwen-base.json \
  --manifest-file-sha256 ACTUAL_MANIFEST_FILE_SHA256 \
  --context-profile docs/private/context-profile.json --context-file-sha256 ACTUAL_CONTEXT_FILE_SHA256 \
  --output-dir docs/private/public-qwen-base-measured
python3.13 -I -B scripts/evaluate-public-mlx-baseline.py verify \
  --candidate qwen3.5-2b-base --manifest docs/private/qwen-base.json \
  --manifest-file-sha256 ACTUAL_MANIFEST_FILE_SHA256 \
  --context-profile docs/private/context-profile.json --context-file-sha256 ACTUAL_CONTEXT_FILE_SHA256 \
  --report docs/private/public-qwen-base-measured/report.json --report-file-sha256 ACTUAL_REPORT_FILE_SHA256 \
  --output-dir docs/private/public-qwen-base-verified
```

Для decider — `--candidate decider-2b` и его отдельный manifest. Snapshot path
можно перенести с новым explicit manifest-file SHA, сохранив точные artifact
bytes. Manifest/source/context/report изменения или wrong scope отклоняются.
Файлы O_EXCL, private directories 0700 / reports 0600; publication byte bound
включает завершающий newline canonical JSON. Labels не добавляются, threshold
или temperature по этому опыту не подбираются.

[15 regression tests](../../../../scripts/test/test_decision_public_mlx_baseline.py)
исполняют actual scorer на synthetic numeric interface: full inventory,
over-context zero-backbone/eval, letter/decode contracts, ordered source
bindings, typed False / binary64 Score, raw policy/count replay, profiles,
stack/resource/memory/authority, isolated CLI copy и external byte pins.
Этот synthetic interface не служит evidence качества или реальных MLX
forwards. Native observations будут записаны отдельно после завершённых
прогонов на committed source и закрытой перепроверки.
