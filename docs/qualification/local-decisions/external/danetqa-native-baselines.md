# Native Boolean baseline на опубликованных русских ответах DaNetQA

Протокол зафиксирован до измерения. [Полный проверенный импорт](./danetqa-development.md) содержит 821 development-случай: 409 false / 412 true, 779 нормализованных passage groups. Все исходные passage/question и typed Boolean references сохранены. Два дополнительных duplicate rows остаются в знаменателе; общий passage, совпавшие пары и два порядка создают зависимости.

[Задача издателя](https://russiansuperglue.com/tasks/task_info/DaNetQA), [статья DaNetQA](https://arxiv.org/abs/2010.02605v2) и [правила development](https://russiansuperglue.com/faq/) задают yes/no question answering по passage. Публичные ответы не объявляются неизвестными pretraining-данными checkpoint; контаминация не оценена. Авторы исходных меток не считаются локальными независимыми reviewers Агат.

## Зафиксированный протокол

[CLI](../../../../scripts/evaluate-danetqa-baseline.py) проверяет исходные ZIP/LICENSE, все 821 request/reference, committed source closure и immutable model/tokenizer pins до загрузки backend. State и question сохраняют publisher text. Request имеет kind=boolean и варианты false/true с настоящими bool values и описаниями «Нет.»/«Да.». Эталон хранится за пределами request и не передаётся scorer.

| Кандидат | Сохранённый native backend | Профиль |
|---|---|---|
| GLiClass edge v3.0 | Uni-encoder, CPU float32/eager | 1024 токена, Torch threads 2/1 |
| NanoJev | Опубликованный trained Boolean head, CPU float32 | 1024 токена на полный единственный Boolean path, threads 2/1 |
| decider-2b | Runtime 0.12.3 MlxBackend.score, decision-tuned | 2048 токенов, cache 128 MiB, wired 4096 MiB |
| Qwen3.5-2B-Base | Runtime 0.12.3 MlxBackend.score, backbone baseline | Тот же MLX protocol и профиль |

Model pins и locked stacks наследуются из [CPU baseline](../baselines/public-native-inputs.md) и [MLX baseline](../baselines/public-mlx-inputs.md). GLiClass получает опубликованный renderer с kind/value; NanoJev сохраняет semantic false/true и logits [0,z], переставляемые в request order; MLX сохраняет исходные option-description prompts и letter logits без нового value suffix. **4926 source-backed render bindings** проверены до inference без model imports. Веса, scoring graph и serving runtime не изменены. Temperature=1, minProbability=0.8 и minMargin=0.1 закреплены заранее. Обучения, подгонки threshold, calibration, генерации и обрезки входов нет. Каждый checkpoint получает полный corpus, без чужого token prefilter.

## Измеренные результаты 11.10.2026

[Aggregate evidence](./evidence/2026-10-11/danetqa-native-summary.json) закрепляет source feature `3163edc00079b38bd08de00ceb8fddbb04904117`, отдельный fixture `c5e8c244aef0d671a8f302b6a63fbc4a9fa7d5c6`, **168 source files** и corpus file SHA `61b7157491adc95d3cfdd47f65e9d1b39ae0c666e74a10cc4a30cf8c7dbf80ba`. **821 × 2 порядка × 4 модели = 6568 настоящих native forwards** завершены, context/backend errors — 0, новых model downloads — 0. Четыре owned model processes выполнялись последовательно; resident runtime остался неизменным.

Пара ниже означает **исходный / обратный порядок**, каждый со знаменателем 821. Accuracy учитывает argmax при abstain; принятые решения и их ошибки показаны отдельно. NLL/Brier/ECE/F1 используют успешно вычисленные случаи; context/backend errors сохраняются в полном знаменателе. 1642 варианта не считаются 1642 независимыми примерами.

| Кандидат | Правильный argmax | Accuracy | Принято | Ошибок среди принятых | Abstain | Изменён argmax |
|---|---:|---:|---:|---:|---:|---:|
| decider-2b | 695 / 701 | 84.65% / 85.38% | 680 / 682 | 67 / 68 | 141 / 139 | 24/821 |
| gliclass | 420 / 398 | 51.16% / 48.48% | 468 / 192 | 219 / 98 | 353 / 629 | 548/821 |
| nanojev | 412 / 412 | 50.18% / 50.18% | 749 / 749 | 365 / 365 | 72 / 72 | 0/821 |
| qwen3.5-2b-base | 686 / 689 | 83.56% / 83.92% | 259 / 431 | 11 / 17 | 562 / 390 | 69/821 |

| Кандидат | Macro-F1 | NLL | Multiclass Brier | ECE10 | Selective risk |
|---|---:|---:|---:|---:|---:|
| decider-2b | 0.846428 / 0.853837 | 0.382771 / 0.388268 | 0.223450 / 0.226295 | 0.064622 / 0.066488 | 9.85% / 9.97% |
| gliclass | 0.404596 / 0.452270 | 1.038819 / 0.878507 | 0.687023 / 0.615500 | 0.293284 / 0.205030 | 46.79% / 51.04% |
| nanojev | 0.334144 / 0.334144 | 1.075188 / 1.075188 | 0.758645 / 0.758645 | 0.364633 / 0.364633 | 48.73% / 48.73% |
| qwen3.5-2b-base | 0.834220 / 0.839094 | 0.435092 / 0.373764 | 0.272281 / 0.229139 | 0.105104 / 0.064755 | 4.25% / 3.94% |

Always-true reference дал бы **412/821 = 50.18%**; это описательный ориентир данного корпуса. NanoJev во всех 821 случаях выбирал true в обоих порядках, включая 409 false references; 365/749 принятых решений неверны. GLiClass меняет argmax в 548 случаях и допускает 219/468 либо 98/192 принятых ошибок. Это фактические результаты закреплённых checkpoint/rendering на данной задаче.

Decider ошибается среди принятых в 67/680 и 68/682 случаях, Base — в 11/259 и 17/431; меньшее число ошибок Base сочетается с меньшим покрытием. Его coverage заметно зависит от порядка, даже когда общий argmax accuracy близок. Этот корпус не подтверждает универсального преимущества decision tuning. Различия с TERRa и внутренними входами одновременно затрагивают задачу, kind, язык, длину и rendering; причинный эффект языка или типа отсюда не выводится.

## Наблюдения ресурсов

| Кандидат | Model load, ms | Native forward p50/p95, ms | Whole-process max RSS, bytes | MLX allocator peak, bytes |
|---|---:|---:|---:|---:|
| decider-2b | 5687.610 | 116.287 / 167.706 | 3704864768 | 4150865370 |
| gliclass | 6511.515 | 32.751 / 55.498 | 742785024 | — |
| nanojev | 9957.035 | 368.471 / 532.982 | 5347131392 | — |
| qwen3.5-2b-base | 5623.034 | 116.502 / 165.811 | 3325575168 | 4164087338 |

RUSAGE_SELF сохраняет три cumulative snapshots и syscall brackets, исключая дочерние процессы. MLX отдельно сохраняет active/cache/peak после load/scoring; allocator peak и process RSS имеют разные scopes. Полный regression suite выполнялся параллельно; один Mac и разные CPU/MLX profiles дают наблюдения, а не controlled speed comparison или SLO. Cache/wired settings не являются total-memory cap. Recorded input tokens: GLiClass **642582**, NanoJev **472510**, Decider/Base **по 367222**, все целиком.

## Проверки и восстановление

**14 новых focused tests / 81 related tests PASS**; synthetic fixtures явно отделены от настоящих measurements. **1520 scripts tests PASS / 4 skip / 619.260 s**, log SHA `339d698d216ae7031a44b5d5e5d22f2b7f838015d2c8b012fbaec814dffb2d85`; **18 Node tests PASS**. 20 serving source files не изменились.

Независимый stdlib audit без project/model imports пересчитал **191294 checks**: исходные publisher rows/references и duplicate denominator, обе Git histories, render/input hashes, typed values, token inventories, raw logits → probabilities → fixed policy, accuracy/F1/NLL/Brier/ECE/reliability и resource scopes. Actual pinned tokenizers повторили **16420 arrays / 6568 letter decodes**: по 1642 arrays у GLiClass и Boolean NanoJev, по 6568 у MLX checkpoint. Новых model forwards — 0.

Actual copied CLI отверг **77 resealed подмен** и подтвердил **4 positive replays**: bool→0 aliases, gold/text/order/group/split, потеря duplicate row, partial native inventory, probabilities/logits/policy, metrics/resources/source, human/routing authority, wrong/missing pins. Refused outputs отсутствуют. Все 81 probe PIDs, 10 основных owned PIDs и пять original-copy verifier PIDs завершены; protected resident 4/20/8 и health ready сохранены.

[Archive receipt](./evidence/2026-10-11/danetqa-native-archive-summary.json): один ZIP `docs/private/danetqa-native-baselines-evidence-20261011.zip` в original workspace, **122587782 bytes / 1478 entries**, SHA `137607644728a72961c32d17727e0ffc794d4f33b066813b0cfd8f4a86d64a49`. Source, обе Git histories, полный publisher corpus/license, все logits/tokens, refusal mutations и завершённые logs проверены по CRC/SHA/size. В восстановленной копии независимый audit прошёл до добавления model prerequisites, затем четыре actual copied stdlib CLI воспроизвели report byte-for-byte, без model imports/forwards.

**Этот evidence ZIP требует двух отдельно сохранённых standalone weight bundles** для native remeasure и copied CLI verify: `public-native-baselines-20261011.zip`, 2164356018 bytes, SHA `850f3e52418a04842e9e9efa8c4652098552ad391fddaa63c36b8a033a093313`; `public-mlx-baselines-20261011.zip`, 6653677419 bytes, SHA `51486b5596b86553712fba5d70159d3afb210f7f7bfe0ee816c68adfa66fd7c9`. Полные SHA обоих bundles и все 32 cache files повторно проверены; веса повторно не скачиваются и не дублируются в новом ZIP. Самостоятельный арифметический audit не требует моделей.

`classificationAccuracyMeasured=true` относится только к этому внешнему corpus. Qualification, human labels, representative Agat traffic, calibration/holdout, owner/SLO и routing остаются неподтверждёнными; routing disabled. Следующий независимый этап — числовой Score corpus с исходными дробными эталонами и регрессионными метриками.
