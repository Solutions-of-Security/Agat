# DaNetQA development: исходные русские Boolean references

11.10.2026. [DaNetQA](https://russiansuperglue.com/tasks/task_info/DaNetQA)
добавляет внешний yes/no benchmark к [плану](../../../local-decision-model-plan-2026-09-21.md).
[Авторы корпуса](https://arxiv.org/abs/2010.02605v2) описывают вопросы к
фрагментам русской Википедии с ответами, собранными через Toloka. Это
опубликованные corpus references; локальных независимых human reviews Агат
из них не выводится.

## Источник и фиксированное преобразование

[Source descriptor](danetqa-source.json) закрепляет publisher dataset commit
`53cb9d7f38b34c308a2eea3f4797f9edc7947d8b`. `DaNetQA.zip`: 1293761 bytes,
SHA `b5b4bcfe17e1eb16aa13a7aab4ca088871e27b0851468e9a07b9b528bb42fb96`.
Читается только `DaNetQA/val.jsonl`: 1108765 bytes,
SHA `92d983265e7900e33454f70c5f1f088e2d9b180858edb4fcc8f7defab816dc3e`.
Train/test members не читаются. [Publisher FAQ](https://russiansuperglue.com/faq/)
и оригинальный MIT LICENSE закреплены отдельно: Git commit
`1aff0918f226252eb304bdcdadf9eef5926cc924`, 1075 bytes,
SHA `e69f12e8fa47f0568071cd6f6a472e01dcde4c183ecb89dfea86932bd8fe4107`.
Remote dataset loader не запускается.

Импортируются все **821 rows**, исходные Boolean references:
**409 false / 412 true**. До inference закреплено преобразование:

- `state` — полный исходный `passage`, `question` — исходный вопрос.
- `kind=boolean`; options в порядке `false` / `true`, descriptions «Нет.» /
  «Да.», values строго Boolean `false` / `true`.
- `expectedOptionId` и исходный `publishedBooleanReference` находятся вне
  model request вместе с publisher index, source provenance и group.
  Request ID связывает результат с исходной строкой и не содержит ответ.
- NFKC/whitespace/case normalization используется только для группировки:
  **779 passage groups**. Actual input text и порядок rows не меняются.

Сохранены обе дополнительные повторные строки: source IDs `[14,185]` с
false и `[446,552]` с true. Всего 819 разных normalized passage/question
пар, 2 дополнительных повтора, 0 конфликтующих references. Это зависимые
строки; их нельзя объявлять независимыми наблюдениями. Будущие original /
reversed option orders также зависимы и должны иметь отдельные метрики.
Никаких translation, truncation, model-dependent filtering, переписывания
references, calibration/holdout или обучения на этих данных не выполняется.

Корпус имеет `external-development-only` и `labelSource=published-corpus`.
Human/routing/accuracy qualification=false, localIndependentHumanReviews=0.
Наличие public corpus в training data checkpoint пока не исследовано;
он не объявляется unseen или representative трафиком Агат. Импорт имеет
0 model calls и не измеряет accuracy. Numeric Score требует собственного
предметного reference набора.

## Воспроизведение и проверки

[CLI](../../../../scripts/import-danetqa-development.py) использует stdlib.
`acquire` явно получает только два byte-pinned artifacts; `import` и
`verify` работают offline. Complete source closure должен совпадать с
committed bytes. Новые outputs создаются только под `docs/private`,
directory 0700 / files 0600 / O_EXCL; лимит 4 MiB включает final newline.

```bash
python3.13 -I -B scripts/import-danetqa-development.py acquire \
  --output-dir docs/private/danetqa-acquired
python3.13 -I -B scripts/import-danetqa-development.py import \
  --archive docs/private/danetqa-acquired/DaNetQA.zip \
  --archive-file-sha256 b5b4bcfe17e1eb16aa13a7aab4ca088871e27b0851468e9a07b9b528bb42fb96 \
  --license docs/private/danetqa-acquired/LICENSE \
  --license-file-sha256 e69f12e8fa47f0568071cd6f6a472e01dcde4c183ecb89dfea86932bd8fe4107 \
  --output-dir docs/private/danetqa-imported
python3.13 -I -B scripts/import-danetqa-development.py verify \
  --archive docs/private/danetqa-acquired/DaNetQA.zip \
  --archive-file-sha256 b5b4bcfe17e1eb16aa13a7aab4ca088871e27b0851468e9a07b9b528bb42fb96 \
  --license docs/private/danetqa-acquired/LICENSE \
  --license-file-sha256 e69f12e8fa47f0568071cd6f6a472e01dcde4c183ecb89dfea86932bd8fe4107 \
  --dataset docs/private/danetqa-imported/dataset.json \
  --dataset-file-sha256 ACTUAL_DATASET_FILE_SHA256 \
  --output-dir docs/private/danetqa-verified
```

Verify пересобирает все 821 requests/references/groups из оригинальных
pinned ZIP/license bytes и сравнивает canonical bytes, включая типы.
Равенство Python `False == 0` не допускает замену Boolean числом в JSON.
Resealing изменённого dataset не заменяет source pin и полный original
rebuild. Публичный evaluation adapter требует оригинальные corpus bytes.

[12 regression tests](../../../../scripts/test/test_decision_danetqa_corpus.py)
проверяют typed false/true, сохранение duplicates, конфликтующие ответы,
immutable source и bounds, resealed references/authority, isolated actual
CLI без model packages, byte-identical replay и final-newline bound.
Synthetic unit fixtures явно не заявлены как настоящий corpus/reviews.

## Завершённый committed import

Source feature `aba5345d6d686eaa90fe11f350aededb42d537a2`, source fixture
`ce0e2e6aece51626680a9d21476b9dbe5499b275`: **150 source files** сверены с
current bytes и обеими Git histories. Actual copied CLI import и verify
сохранили **1886246-byte dataset** побайтово. File SHA:
`61b7157491adc95d3cfdd47f65e9d1b39ae0c666e74a10cc4a30cf8c7dbf80ba`; canonical artifact SHA:
`8bd4dfdacac6bb83be318e94834ecc5f95525acb499bf69b9a84bfe9573e8e4b`.

Независимый stdlib audit выполнил **7865 checks** original source rows,
полных requests, typed references, source bindings, row order, групп,
зависимых duplicates, source bytes и qualification. **35 actual CLI
refusals + 1 positive replay**: подмена false/true числом, изменённые gold,
state/question/options/group, partial/reordered corpus, удалённый duplicate,
fake human/routing/accuracy authority, wrong/missing source pins. Все 36
probe PIDs и оба import/replay PIDs завершены, refused outputs отсутствуют.
**0 model packages imported / 0 model calls**.

**1506 scripts tests PASS / 4 skip / 518.658 s**, включая 12 новых;
37 связанных tests и 18 Node tests PASS. 20 serving source files
не менялись. [Aggregate evidence](evidence/2026-10-11/danetqa-import-summary.json)
фиксирует импорт; inference и accuracy в этом этапе не выполнялись.

[Archive receipt](evidence/2026-10-11/danetqa-import-archive-summary.json):
один standalone ZIP `docs/private/danetqa-development-import-20261011.zip`
в original workspace, **65007004 bytes / 1301 entries**, SHA
`3877af83441600f7315eb5731f7bee03d354dcab524b50d1008fd561740c1236`. Он содержит полный source/corpus/license,
source fixture с own Git, dataset, результаты checks и draft history.
CRC/SHA/size каждого entry и closed inventory проверены;
1300 файлов восстановлены в
`docs/private/danetqa-development-original-copy-20261011`. Actual copied
production CLI этой original копии воспроизвёл dataset byte-for-byte
без model imports/calls; owned PID завершён.

Первая draft unit попытка имела неполный synthetic license fixture и
была исправлена до committed import. Первая real experiment попытка
получила ошибочно записанный ожидаемый commit; guard остановил её до
создания dataset. Повтор использовал exact Git SHA. Обе failed logs
сохранены в архиве; они не объявляются успешными runs.

Следующий этап — native scoring четырёх pinned candidates на этих
Boolean-входах в обоих порядках вариантов. Qualification Агат требует
собственных representative данных, настоящих независимых human reviews,
calibration/holdout и согласованных владельцев/SLO.
