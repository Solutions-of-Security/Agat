# Native baseline на опубликованных русских ответах TERRa

Протокол фиксируется до native-измерения. Источник — [проверенный импорт](README.md) всех 307 опубликованных development-случаев TERRa: 153 entailment, 154 not_entailment, 282 нормализованные группы посылок. Исходные premise/hypothesis и ответы сохраняются без перевода или редактирования. Majority reference для этого корпуса — всегда not_entailment, 154/307; это диагностический ориентир этого набора.

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

Внешняя диагностическая точность не открывает human review, representative Agat traffic, calibration, независимый holdout, владельцев риска и SLO. `classificationAccuracyMeasured=true` относится к опубликованным внешним references; `classificationAccuracyQualified=false`, routing disabled и qualification=not_assessed сохраняются. Результаты native-измерения и восстановленной копии будут добавлены после фактической проверки.
