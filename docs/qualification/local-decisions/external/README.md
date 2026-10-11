# Закреплённый русский TERRa development corpus

11.10.2026. Этот внешний корпус дополняет [план локальных decision models](../../../local-decision-model-plan-2026-09-21.md)
русскими опубликованными reference labels. Он проверяет, следует ли
утверждение из текста. Это самостоятельный внешний development benchmark;
данные документооборота Агат и локальные blind reviews из него не выводятся.

## Источник и prospective преобразование

[TERRa](https://russiansuperglue.com/tasks/task_info/TERRa) — часть
[RussianSuperGLUE](https://aclanthology.org/2020.emnlp-main.381/).
Авторы описывают тексты из новостей/литературных журналов и ручную разметку
с Toloka. [Publisher FAQ](https://russiansuperglue.com/faq/) объявляет MIT;
сохраняется оригинальный MIT LICENSE с immutable Git revision и byte SHA.
[Descriptor](./terra-source.json) закрепляет dataset revision
`53cb9d7f38b34c308a2eea3f4797f9edc7947d8b`, ZIP и development member
`TERRa/val.jsonl` по size/SHA. Удалённый dataset loading script не выполняется.

Импортируется **весь опубликованный development: 307 cases**,
153 `entailment` / 154 `not_entailment`. Train/test ZIP members не читаются,
calibration/holdout не создаются. Перевод, truncation и выбор примеров
по model outputs отсутствуют. До запусков моделей закреплено:

- `state` — точный исходный `premise`.
- `question` — «Можно ли вывести следующее утверждение из текста?» и
  точный исходный `hypothesis`.
- Choice options — «Утверждение следует из текста.» / «Утверждение не следует
  из текста.», IDs совпадают с publisher labels. Gold, index, группа и
  provenance находятся вне model request.
- Одинаковый `premise` после NFKC/whitespace/case normalization получает
  один group ID: **282 groups**. Нормализация не меняет actual input text.
  Порядок publisher rows и все 307 references сохраняются; conflicting
  normalized duplicate references отклоняются.

Это `external-development-only`, `labelSource: published-corpus`,
`localIndependentHumanReviews: 0`, human/routing qualification=false.
Published reference labels не заменяют два независимых локальных human
reviews. Зависимые группы и будущие original/reversed variants не считаются
дополнительными независимыми примерами. Корпус общедоступен; отсутствие
его в training data checkpoints пока не проверено.

## Инструмент и воспроизведение

[CLI](../../../../scripts/import-terra-development.py) работает стандартным
Python; acquire — отдельная явная команда, import/verify — offline.
Complete source closure должен быть committed и совпадать с рабочими bytes.
Outputs создаются только в новом `docs/private` каталоге, 0700 / files 0600,
O_EXCL. Unknown/duplicate keys, archive paths, symlinks, неверные byte pins,
лишние authority fields, изменённые references или denominator отклоняются.
Publication bound 4 MiB включает canonical final newline.

```bash
python3.13 -I -B scripts/import-terra-development.py acquire \
  --output-dir docs/private/terra-acquired
python3.13 -I -B scripts/import-terra-development.py import \
  --archive docs/private/terra-acquired/TERRa.zip \
  --archive-file-sha256 fc7320210b5b6f7087615f13558868de55f46d5e0e365d9d82968c66e6e0dba7 \
  --license docs/private/terra-acquired/LICENSE \
  --license-file-sha256 e69f12e8fa47f0568071cd6f6a472e01dcde4c183ecb89dfea86932bd8fe4107 \
  --output-dir docs/private/terra-imported
python3.13 -I -B scripts/import-terra-development.py verify \
  --archive docs/private/terra-acquired/TERRa.zip \
  --archive-file-sha256 fc7320210b5b6f7087615f13558868de55f46d5e0e365d9d82968c66e6e0dba7 \
  --license docs/private/terra-acquired/LICENSE \
  --license-file-sha256 e69f12e8fa47f0568071cd6f6a472e01dcde4c183ecb89dfea86932bd8fe4107 \
  --dataset docs/private/terra-imported/dataset.json \
  --dataset-file-sha256 ACTUAL_DATASET_FILE_SHA256 \
  --output-dir docs/private/terra-verified
```

Verify заново строит корпус из оригинальных pinned bytes и сравнивает
каждый request, reference, group, row order, denominator и authority.
Пересчитанный checksum изменённого dataset не заменяет исходный corpus pin.
Публичный adapter к прежним metrics требует оригинальные archive/license
bytes и эту проверку; bare self-seal не допускает references к evaluator.

[13 regression tests](../../../../scripts/test/test_decision_terra_corpus.py)
проверяют source/gold mutations после resealing, ZIP bounds/path/symlink,
полный denominator, original text/grouping, isolated actual CLI без model
packages, positive byte-identical replay, refused outputs и final-newline
bound. Synthetic unit fixtures не заявляются как реальные human labels.

## Committed import и независимая перепроверка

Source feature `91169b214d49de890399472c757b49bfc72d94a0`, отдельный source
fixture `b801c0d9a3636243e7ebe4f898c8b5edd2ff4fdd`: **148 source files**
закреплены current bytes и обеими Git histories. Actual copied CLI import
сохранил весь корпус; actual `verify` повторил **516166-byte dataset**
byte-for-byte. File SHA:
`9b31000641df590f84e8b152319c5747ad31a33ca280c88ce354c43cad383771`;
canonical artifact SHA:
`9dad46c252d520125446c760c9f57f5d60ce266e869d2fc88a6032ef0360b68d`.

Независимый stdlib script пересчитал **2924 checks**: все 307 source rows,
state/hypothesis/reference projections, ID/order/group bindings, raw row
hashes, request hashes, полный label denominator и authority, actual bytes
148 files и обе Git sources. **22 actual CLI refusals + 1 positive replay**
проверены на реальном корпусе: resealed changed gold/text/options/group,
partial/reordered data, calibration/holdout, fake reviewer/human/routing
authority и wrong/missing byte pins. Refused outputs отсутствуют, все
23 probe PIDs и оба import/replay PIDs завершены, **0 model imports/calls**.

**1482 scripts tests PASS / 4 skip / 515.775 s**, включая 13 новых.
20 serving source files не менялись.
[Aggregate evidence](./evidence/2026-10-11/terra-import-summary.json)
не заявляет accuracy: inference ещё не выполнялся.

[Archive receipt](./evidence/2026-10-11/terra-import-archive-summary.json):
один standalone ZIP `docs/private/terra-development-import-20261011.zip`
в original workspace, **47571825 bytes / 824 entries**, SHA
`b810884e66f05058bbbd88250ee1f3c4c9e35837c5e7ef184ceff44a522a166f`.
Он содержит полный original source ZIP/лицензию/publisher evidence, source
fixture с Git history, imported dataset, completed checks и draft history.
Каждый entry проверен по CRC/SHA/size; 823 files восстановлены в
`docs/private/terra-development-original-copy-20261011`. Actual copied
production CLI в этой original копии повторил dataset byte-for-byte,
0 model calls; owned verifier PID завершён.

Native scoring четырёх кандидатов и accuracy на этом корпусе — следующий
этап. Qualification Агат требует representative внутренних данных,
calibration/holdout, human reviews и SLO.
