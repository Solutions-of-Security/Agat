# Native baseline на опубликованных русских ответах TERRa

Протокол был зафиксирован до native-измерения. Источник — [проверенный импорт](README.md) всех 307 опубликованных development-случаев TERRa: 153 entailment, 154 not_entailment, 282 нормализованные группы посылок. Исходные premise/hypothesis и ответы сохраняются без перевода или редактирования. Majority reference для этого корпуса — всегда not_entailment, 154/307; это диагностический ориентир этого набора.

[Описание задачи издателя](https://russiansuperglue.com/tasks/task_info/TERRa), [правила использования development](https://russiansuperglue.com/faq/) и [статья](https://aclanthology.org/2020.emnlp-main.381/) определяют бинарное textual entailment. Этот публичный корпус не объявляется неизвестным pretraining-данным checkpoint. Контаминация не оценена. Авторы исходных меток не считаются двумя локальными независимыми reviewers Агат.

## Зафиксированные кандидаты и входы

[CLI](../../../../scripts/evaluate-terra-baseline.py) перед загрузкой модели проверяет исходные byte pins ZIP/LICENSE, весь импортированный corpus, committed source closure и прежние immutable веса/tokenizer. Измеряется каждый случай в исходном и обратном порядке вариантов. Эталонный ответ остаётся за пределами request; он не передаётся scorer. Ни один случай не удаляется по токенизации другой модели. Слишком длинные входы сохраняются в знаменателе и отказе собственной модели.

| Кандидат | Тот же native backend | Зафиксированный профиль |
|---|---|---|
| GLiClass edge v3.0 | Uni-encoder, CPU float32/eager | 1024 токена, Torch threads 2/1 |
| NanoJev | Опубликованные trained decision heads, CPU float32 | 1024 токена на полный candidate path, Torch threads 2/1 |
| decider-2b | Runtime 0.12.3 MlxBackend.score, decision-tuned | 2048 токенов, cache 128 MiB, wired 4096 MiB |
| Qwen3.5-2B-Base | Runtime 0.12.3 MlxBackend.score, backbone baseline | Тот же MLX scoring protocol и профиль |

Новые модели не скачиваются. Pins и locked stack наследуются из [CPU baseline](../baselines/public-native-inputs.md) и [MLX baseline](../baselines/public-mlx-inputs.md). Существующий scoring graph, веса и production runtime не меняются. Temperature=1 и policy 0.8/0.1 фиксированы; подгонки threshold, обучения или calibration на этих ответах нет. Данный этап проверяет Choice textual entailment; Boolean/Score проверены в interface-тестах и требуют своего реального корпуса.

## Метрики и воспроизведение

Для каждого порядка отдельно рассчитываются accuracy по всем 307 попыткам, macro-F1, per-class errors, NLL, multiclass Brier, 10-bin ECE/reliability, coverage и selective risk среди принятых решений. Accuracy отражает argmax и включает abstain; принятые ошибки и abstain раскрываются отдельно. NLL/Brier/ECE/F1 вычисляются по успешно оценённым случаям, а context/backend errors остаются в общем знаменателе. Оба порядка связаны; 614 вариантов не выдаются за 614 независимых примеров. Группы общей посылки также зависимы.

Raw native logits, полные token arrays, input hashes, порядок кандидатов и результаты policy сохраняются в закрытом отчёте. `verify` повторно строит corpus из publisher ZIP/LICENSE, сверяет request/эталон/группу каждого случая, восстанавливает вероятности из raw logits и все cached метрики без импорта MLX/Torch/Transformers и без model forward. Проверяется committed source identity. SHA защищает целостность относительно предоставленного pin; он не является независимой подписью автора измерения.

Для native-процесса сохраняются три RUSAGE_SELF-снимка, load interval и отдельные native forward intervals; MLX имеет два снимка allocator. Process high-water RSS включает Python и библиотеки; MLX peak имеет другую область измерения. На одном Mac с разными CPU/MLX-профилями это наблюдения ресурсов/latency, а не согласованный SLO, controlled speed comparison или доказательство вместимости. Cache/wired settings не объявляются лимитом полной памяти процесса.

Пример последовательного запуска для CPU (пути и внешние SHA — реальные проверенные локальные inputs):

```sh
python scripts/evaluate-terra-baseline.py measure --candidate gliclass \
  --model-dir docs/private/gliclass-model \
  --dataset docs/private/inputs/dataset.json --dataset-file-sha256 DATASET_SHA \
  --archive docs/private/inputs/TERRa.zip --archive-file-sha256 ARCHIVE_SHA \
  --license docs/private/inputs/LICENSE --license-file-sha256 LICENSE_SHA \
  --output-dir docs/private/gliclass-measured
```

Для MLX используются `--manifest` и обязательный `--manifest-file-sha256` вместо CPU directory. Для stdlib replay: команда `verify`, те же pinned inputs, `--report` и `--report-file-sha256`, новый private output directory. Corpus bounded 4 MiB, complete report bounded 32 MiB включая завершающий newline; при отказе до публикации output не создаётся.

## Открытые gates

Внешняя диагностическая точность не открывает human review, representative Agat traffic, calibration, независимый holdout, владельцев риска и SLO. `classificationAccuracyMeasured=true` относится к опубликованным внешним references; `classificationAccuracyQualified=false`, routing disabled и qualification=not_assessed сохраняются. Результаты ниже получены на закреплённом протоколе и проверены в восстановленной копии.

## Фактический native результат 11.10.2026

[Aggregate evidence](./evidence/2026-10-11/terra-native-summary.json) связывает четыре отчёта с feature commit `4e8b09ee57fc7e4e9342d9b7e708e20e4c6f9c15`, отдельным fixture commit `61288adf2204c884c475c6b557bbb4d566fefd83`, 166 source files и исходным corpus file SHA `9b31000641df590f84e8b152319c5747ad31a33ca280c88ce354c43cad383771`. Все **307 случаев × 2 порядка × 4 модели = 2456 настоящих native forwards** завершены; context/backend errors — 0. Четыре process измерения запускались последовательно. Новых model downloads — 0. Native profiles и resident runtime 0.12.3 сохранены.

Здесь и далее пара значений означает **исходный / обратный порядок**, каждый со своим знаменателем 307. Accuracy включает выбранный argmax при abstain.

| Кандидат | Правильный argmax | Accuracy | Принято | Ошибок среди принятых | Abstain | Изменён argmax при перестановке |
|---|---:|---:|---:|---:|---:|---:|
| decider-2b | 267 / 269 | 86.97% / 87.62% | 267 / 265 | 21 / 21 | 40 / 42 | 8/307 |
| gliclass | 157 / 150 | 51.14% / 48.86% | 246 / 255 | 122 / 130 | 61 / 52 | 23/307 |
| nanojev | 153 / 153 | 49.84% / 49.84% | 2 / 2 | 2 / 2 | 305 / 305 | 0/307 |
| qwen3.5-2b-base | 265 / 271 | 86.32% / 88.27% | 184 / 175 | 9 / 9 | 123 / 132 | 30/307 |

| Кандидат | Macro-F1 | NLL | Multiclass Brier | ECE10 | Selective risk |
|---|---:|---:|---:|---:|---:|
| decider-2b | 0.868837 / 0.875841 | 0.353316 / 0.323596 | 0.194701 / 0.189168 | 0.065468 / 0.055080 | 7.87% / 7.92% |
| gliclass | 0.440924 / 0.399950 | 2.373067 / 2.635777 | 0.867107 / 0.891692 | 0.406239 / 0.435297 | 49.59% / 50.98% |
| nanojev | 0.332609 / 0.332609 | 0.778276 / 0.778276 | 0.577112 / 0.577112 | 0.197971 / 0.197971 | 100.00% / 100.00% |
| qwen3.5-2b-base | 0.863121 / 0.882185 | 0.345884 / 0.346687 | 0.205840 / 0.206301 | 0.063347 / 0.086414 | 4.89% / 5.14% |

Always-not_entailment reference дал бы **154/307 = 50.16%**. GLiClass находится рядом с этим ориентиром и часто принимает неверные ответы с высокой уверенностью. NanoJev во всех случаях выбирал entailment, не распознав not_entailment; оба принятых решения ошибочны. Это результаты данной модели и заранее закреплённого rendering на этой задаче.

decider/Qwen Base существенно лучше этого corpus reference, но ошибаются и среди принятых: **21/267 и 21/265** у decider, **9/184 и 9/175** у Base. Base принимает меньшую долю запросов. Число смен argmax — 8 у decider и 30 у Base. Модели нельзя квалифицировать по одной accuracy; данные не показывают универсального преимущества decision tuning. Различия с предыдущими публичными support-входами одновременно затрагивают task, язык, длину и число вариантов, поэтому причинный эффект языка из них не выводится. Threshold/temperature по этим ответам не подгонялись.

## Наблюдения ресурсов

| Кандидат | Model load, ms | Native forward p50/p95, ms | Whole-process max RSS, bytes | MLX allocator peak, bytes |
|---|---:|---:|---:|---:|
| decider-2b | 6204.440 | 65.655 / 90.935 | 3798548480 | 4087039356 |
| gliclass | 6422.514 | 19.299 / 30.132 | 599506944 | — |
| nanojev | 9930.682 | 330.952 / 509.603 | 5340168192 | — |
| qwen3.5-2b-base | 5613.685 | 65.163 / 90.896 | 3386736640 | 4100595996 |

RUSAGE_SELF сохраняет три исходных cumulative snapshot и syscall brackets. В MLX дополнительно сохранены active/cache/peak после load/scoring. Полный регрессионный набор выполнялся параллельно с измерениями; этот Mac и разные CPU/MLX profiles не дают controlled speed comparison. Данные не являются SLO, пределом total memory или основанием выбора production backend. Полные recorded токены — GLiClass 128818, NanoJev 167916 с повторяемым prefix на каждом candidate path, decider/Base по 70756; ни один вход не усекался.

## Повторная проверка и сохранение

12 новых focused tests и 55 связанных — PASS. **1494 scripts tests PASS / 4 skip / 588.910 s**, log SHA `f074022c568c4a130e9f07f6429d10de98e6ed76ef4354e010711dae10ead612`; **18 Node tests PASS**. 20 serving source files не изменились.

Отдельный stdlib script без project/model imports выполнил **72726 checks**: все publisher rows/references, groups, request/render hashes, порядок, полные tokens, raw logits → softmax → fixed policy, accuracy/F1/NLL/Brier/ECE/reliability, accepted errors, обе Git histories и RUSAGE/MLX scopes. Сами cached aggregates не используются как эталон. Actual pinned tokenizer replay сверил **6754 массива**: GLiClass 614, NanoJev 1228, MLX 2456 на каждый checkpoint, а также 2456 декодированных letter continuations. Новых model forwards в этих проверках нет.

Actual copied CLI отверг **66 подмен** и подтвердил **4 positive replays**: reference/text/order/group/split, partial corpus/native inventory, probabilities/logits/policy, source bytes, cached metrics/resources, fake human/qualification и wrong/missing external pins. Resealed mutations действительно изменялись и проверялись семантически. Refused outputs отсутствуют; все 70 probe PIDs, 10 основных owned PIDs и пять original-copy audit/verifier PIDs завершены. Protected resident state, 4 PID, 20 package files и 8 source hashes совпадают до/после измерений.

[Archive receipt](./evidence/2026-10-11/terra-native-archive-summary.json): один успешный ZIP `docs/private/terra-native-baselines-evidence-20261011.zip`, **108616017 bytes / 2040 entries**, SHA `fa8b0880201410c9961fc0218bf2b75e5f70ddd0f1bb302ee25ebbef9e865e98`. Он содержит measured sources и собственную Git history, полные source ZIP/LICENSE/corpus, native logs/tokens/references/results, tests/refusals и историю неудачных подготовок. Каждый CRC/SHA/size и закрытый restored inventory 2039 файлов перепроверен.

**Этот evidence ZIP не содержит весов.** Для remeasure и copied production CLI replay нужны сохранённые standalone bundles: `public-native-baselines-20261011.zip` (2164356018 bytes, SHA `850f3e52418a04842e9e9efa8c4652098552ad391fddaa63c36b8a033a093313`) и `public-mlx-baselines-20261011.zip` (6653677419 bytes, SHA `51486b5596b86553712fba5d70159d3afb210f7f7bfe0ee816c68adfa66fd7c9`). Bundle hashes, все 32 cache-file bytes/pins и member locations описаны в archive manifest; model bytes не дублируются ещё раз. Независимая stdlib arithmetic проверка source/logit/reference evidence работает без этих весов.

После восстановления в original workspace arithmetic audit повторил 72726 checks без model files. Затем добавлены только 32 отдельно проверенных prerequisite model files через hardlink; для MLX созданы новые rebased manifests с новым явным external SHA. Все четыре настоящих copied production CLI повторили отчёты **byte-for-byte, 0 model imports/forwards**. Report hashes не менялись.

История сохраняет private-helper preflight failure до model start (manifest metadata field) и отказ проверки первой evidence ZIP (активный журнал дописывался после inventory snapshot). Повтор исправил только сборку helper/archive; dataset, serving/scoring implementation, веса и native measurement не менялись. Неудачная ZIP сохранена отдельно и не считается успешно проверенной копией.

Следующий data gate — настоящий отдельный русский corpus для Boolean/yes-no, затем применимый предметный calibration/holdout и реальные independent human reviews Агат. Score по числовым бизнес-данным требует своего эталона. Этот внешний diagnostic не закрывает перечисленные gates.
