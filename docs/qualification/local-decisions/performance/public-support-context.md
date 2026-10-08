# Контекст новых публичных development-вопросов

[Profiler](../../../../scripts/profile-public-support-context.py) готовит
следующую capacity-проверку на полных текстах
[публичного корпуса](../source-review/public-support/README.md). Предыдущие
arrival-rate измерения использовали два synthetic prompt lengths. Новый
этап определяет реальные длины замороженного development split без gold,
предсказаний или inference. Calibration/holdout не передаются tokenizer.

CLI требует отдельно закреплённый SHA import receipt, acquisition и exact
profile file SHA. Он повторно читает pinned API/model snapshots, проверяет
historical acquisition/import Git bytes и восстанавливает весь pool, source
bindings, readiness и два исходных blank-review. Каждый output должен
совпасть с pinned bytes и реконструированным содержимым. Незавершённая пагинация,
неподдерживаемая атрибуция, изменённый input/seed или заполненные оригинальные
blank файлы не принимаются как прежний corpus. Результат reconstruction
проверяется снова после tokenization.

Выбор — весь development split с прежним seed
`agat-source-review-2026-09-26-v1` и original pool order. Нативная операция
ограничена 1–60 development cases; превышение даёт failure без подвыборки.
Это сохраняет будущую длительность одного fixed-arrival окна до 120 s при
0.5/с. Дополнительные окна потребуют отдельного протокола.

Локальный verified model manifest должен соответствовать repository/revision,
artifact/tokenizer SHA и профилю runtime 0.12.3 с wired 4096 MiB, cache 128 MiB,
max input 2048 tokens. В отдельном child загружается только tokenizer через
`local_files_only=True`, `trust_remote_code=False`, offline/telemetry-disabled
environment; это следует
[Transformers offline guidance](https://huggingface.co/docs/transformers/main/en/installation#offline-mode).
Проверяются все 34 package pins, Python 3.13.12 / arm64. Код inference и веса
не загружаются для вычисления logits; manifest hashes читаются для проверки
артефакта. Никакие файлы resident, registry или модели не заменяются.

Token count включает обе отдельно кодируемые части прежнего runtime prompt
wrapper и все descriptions вариантов. Проверяются однозначные single-token
letter continuations и обратное восстановление текста. Text/order/options
сохраняются полностью. Каждый длинный запрос остаётся в inventory с
`contextEligible=false`, `contextExclusionReason=context_too_long`: source
не режется и не дополняется до synthetic длины.

Sealed private `context-profile.json` связывает model/profile/source/receipt
pins, каждый input SHA, part counts, eligible/ineligible counts и proposed
schedule 0.5/s / 1 slot / caller 10000 ms / threshold 5000 ms. Это только
план для следующего измерения. Как в
[constant arrival rate](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/),
будущий schedule должен сохранять fixed offsets независимо от времени ответа;
длинные/непринятые запросы остаются в учёте. Такой запуск здесь не выполнен.

```bash
shasum -a 256 docs/private/public-support-review-20261008/import.json
python3 scripts/profile-public-support-context.py \
  --import-receipt docs/private/public-support-review-20261008/import.json \
  --import-file-sha256 REPLACE_WITH_ACTUAL_64_HEX_SHA \
  --acquisition docs/private/public-support-acquisition-20261008/acquisition.json \
  --profile docs/qualification/local-decisions/performance/profiles/runtime-0.12.3-wired-4096.json \
  --profile-file-sha256 786902a051c862a805197adfb7def29f4482df914f828a61768e64a07bca0e6e \
  --manifest /ABSOLUTE/PATH/TO/VERIFIED/decider-2b.json \
  --runtime-python /ABSOLUTE/PATH/TO/PINNED/venv/bin/python \
  --output-dir docs/private/public-context-new
```

Output создаётся только после всех checks, в новом `docs/private` каталоге
0700, файл 0600; предыдущий output не перезаписывается. Tokenizer timeout —
60 s. Source/manifest/profile/input drift даёт exit 1 без usable output;
успех — 0. `referenceLabels`, `predictions`, `modelCalls = 0`; independence,
customer population и SLO не устанавливаются, routing false / not_assessed.
Нативная версия tokenizer здесь измеряет length/admission, а не смысл
заявки, incident truth или качество классификации.

Семь targeted regressions проверяют полный development selection, запрет
omission/reorder/rebinding, токены/continuations, profile/resource binding,
raw snapshot/import reconstruction, historical source checks, scope/bounds
и private CLI output. Совместно с source acquisition/import — 16 pass.

## Native результат 08.10 MSK

Из commit `5ac6504` выполнена новая local tokenization всего development split:
**49 cases / 44 groups**, 46 cases в пределах 2048 tokens и три `context_too_long`.
Полный диапазон — 188–9253 tokens. Ни один input не усечён; calibration/holdout
requests, reference labels, predictions и model calls — 0.

Независимый audit повторил grouping/input hashes и tokenization своим prompt
wrapper: **257 checks pass**. Он сверил полный ordered inventory, raw profile,
import/model/source pins и private modes. Первый failed PTY harness относится
к прежнему review этапу и сюда не переносится как product failure.
Full docs check: 813 Python tests / 4 expected optional skips, 12 Node,
links и process catalog — pass. Последующее чтение `/metrics` подтвердило
resident counters computed 1 / rejected 0 / failed 0 без новых inference.

[Публичная сводка](./public-support-context-summary.json) содержит counts и
file/seal pins без исходных вопросов. Private `context-profile.json` сохраняет
все 49 полных inputs, включая три длинных. Следующий шаг — отдельный owned
HTTP capacity/rejection опыт на этом inventory; заранее известные lengths
не доказывают ни фактическую latency, ни правильность классификации.
