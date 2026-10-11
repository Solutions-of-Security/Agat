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
forwards.

## Реальные измерения и перепроверка

На committed implementation `36851d4a7f06af80839b3094576f5a9aef2f52ee`
выполнены **196 dependent variants / 184 actual native forwards**: по 49
исходных requests и их reversed order для каждого checkpoint. Набор содержит
44 источниковые группы, преимущественно английские публичные support-входы,
включая русскоязычный источник. Это не representative Russian/Agat sample.
Неожиданных backend errors нет; по 3 over-context входа в каждом порядке
отклонены до backbone/eval, сохранив полный вход.

| Наблюдение | decider-2b | Qwen3.5-2B Base |
|---|---:|---:|
| Computed, original / reversed | 46 / 46 | 46 / 46 |
| Accepted by existing policy, original / reversed | 31 / 34 | 33 / 0 |
| Abstained after scoring, original / reversed | 15 / 12 | 13 / 46 |
| Argmax changed among both computed orders | 4/46 | 46/46 |
| Native forward p50 / p95, ms | 170.744 / 550.190 | 168.736 / 548.693 |
| Whole request original p50 / p95, ms | 171.858 / 554.222 | 168.394 / 553.531 |
| Whole request reversed p50 / p95, ms | 170.921 / 551.966 | 168.614 / 548.910 |
| Actual model load, ms | 5892.454 | 5702.884 |
| Whole-process high-water RSS, bytes | 3511582720 | 3539681280 |
| Process-global MLX allocator peak, bytes | 4475192924 | 4531778140 |

Accepted означает только прохождение прежней uncalibrated policy,
**не правильность ответа**. У Base проявилась сильная зависимость от option
order. Без reference labels это не accuracy comparison и не основание для
выбора production model. Исходные case predictions/logits остаются private,
чтобы не раскрывать их будущим независимым human reviewers.

Весь prompt: **73062 recorded / 44678 processed input tokens** на модель,
включая все context refusals; letter-continuation tokens учитываются
отдельно. Actual tokenizer replay совпал на **1312 token arrays**
(392 prompt-part arrays + 920 letter arrays) и **920 decoded continuations**,
без model imports/forwards. Для обоих checkpoint полные prompt token arrays
совпали; иной tokenizer не применялся как prefilter.

Resource phases и обе MLX memory samples сохранены в
[aggregate](./evidence/2026-10-11/public-mlx-summary.json). Final cache bytes
139324948/135134514 записаны как наблюдение; requested 128 MiB cache budget
не объявляется доказанным hard cap. RSS относится ко всему Python process,
allocator metrics — к MLX allocations, CPU time суммирует process threads
и не измеряет GPU compute. Native runs совпали по времени с первым полным
scripts test attempt; эти времена — observational, без controlled latency,
capacity или принятого SLO. Все measured requests — Choice; Boolean/Score
проверены здесь только regression contracts.

Отдельный standard-library audit пересчитал **7443 checks** из raw inputs,
token events, logits, ordered policy, resource samples, source/byte pins;
два actual `verify` повторили report bytes без model calls. **50 refusal
commands + 2 positive replays** перепроверены через actual isolated CLI,
все 52 owned PIDs завершены, refused outputs отсутствуют. Все 6 owned
native/capture/replay processes завершены; 4 прежних resident PIDs,
20 serving files и 8 resident fields сохранены.

**1469 scripts tests — PASS, 4 skip, 553.522 s**; 45 related tests, включая
15 новых, также PASS. Первый full attempt запущен с default sandbox:
loopback/process inspection были недоступны, 1324 tests завершились
13 failures / 122 errors. Этот failed log сохранён и не считается PASS;
повтор на том же source с требуемыми permissions завершился успешно.
Новый Qwen snapshot скачан явно с immutable revision вне cloud-backed
Documents; decider bytes повторно использованы. Все 13 файлов сверены с
publisher metadata и SHA/size. Два предыдущих preflight attempts сохранены
как неуспешные до model calls; original snapshots не исправлялись.

## Переносимый архив

[Archive receipt](./evidence/2026-10-11/public-mlx-archive-summary.json)
фиксирует одну standalone ZIP-копию в исходном workspace:
`docs/private/public-mlx-baselines-20261011.zip`. Она включает оба полных
model artifacts, measured 159-file source fixture с собственной Git history,
полные inputs/reports, failed/completed logs, независимые проверки и
publisher evidence. Каждый ZIP entry проверен по CRC, SHA и size.

Из архива восстановлен закрытый inventory в
`docs/private/public-mlx-baselines-original-copy-20261011`; verified model
bytes переиспользованы через hardlinks, чтобы не занимать второй объём
весов. При этом полные weights находятся и внутри самого ZIP. После
закрытия archived inventory созданы новые manifests с rebased local paths
и explicit file SHA. Actual copied production CLI в восстановленной копии
повторил оба отчёта byte-for-byte стандартным Python, **0 model calls**.
Original archived manifests и report bytes сохранены.

Human references=0; calibration/holdout не использовались;
qualification=`not_assessed`, routing=false. Реальные independent human
reviews и representative calibration/holdout остаются следующими
qualification prerequisites из плана.
