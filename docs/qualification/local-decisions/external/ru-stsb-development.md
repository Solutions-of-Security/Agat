# RuSTSBenchmark development: исходные числовые Score references

Импорт закрепляет все **1336 development-пар** из [ai-forever/ru-stsbenchmark-sts](https://huggingface.co/datasets/ai-forever/ru-stsbenchmark-sts) на immutable revision `1ee9c2ca22b69ccfffa9df9f8f4ae40702c8a6e9`. **943 эталона дробные**, диапазон **0.0–5.0**. Исходные sentence1/sentence2 и все float ratings сохранены. Импорт не выполняет model inference и не заявляет измеренное качество.

[Описание MTEB](https://docs.mteb.org/overview/available_tasks/semantic-similarity/) называет источник переводом STSBenchmark на русский с проверкой/filtering издателя. [ruMTEB](https://arxiv.org/abs/2408.12503) описывает benchmark, [SemEval-2017](https://aclanthology.org/S17-2001/) — семантическую шкалу. Publisher processing не становится новой локальной независимой человеческой разметкой. Публичный corpus не объявляется неизвестным pretraining-данным checkpoint; контаминация не оценена.

## Закреплённые исходные файлы

[Source descriptor](./ru-stsb-source.json) закрывает inventory, size, SHA256 и Git blob SHA1. [CLI](../../../../scripts/import-ru-stsb-development.py) открывает только три явно byte-pinned файла:

| Источник | Bytes | SHA256 |
|---|---:|---|
| `data/sts_rumteb_dev.json` | 441982 | `9769640432908f3925d63070e4f3ca1f779bece4757d91d0558067a5115fe769` |
| Publisher `README.md` | 349 | `877171016b7e4224bf711093d940a9a76696ddf169e58d5df9c2f6bdbd308907` |
| Original CC BY-SA 4.0 legal code | 44855 | `d48546c6161cd2698445d184a859363a2436a6a3bcda655564730e3861e74a9e` |

Лицензия исходных данных — **CC-BY-SA-4.0**, согласно pinned publisher declaration. Полный [legal code](https://creativecommons.org/licenses/by-sa/4.0/) сохранён из официального Creative Commons repository на revision `7f5253347532fc0cb6cf51d78a7085d081fb4216`; derived dataset сохраняет source attribution, эту лицензию и описание framing/grouping modifications. Repo license для кода не подменяет лицензию данных.

При acquisition канонический license URL вернул HTTP 403; это сохранено в истории. Полный оригинальный legal code затем получен и проверен из immutable официального Git source. Повторная попытка записи существующего source payload отклонена O_EXCL, исходные bytes не менялись. Importer не скачивает/не парсит train/test files, не строит calibration/holdout. Во время source research publisher card показывала свой default train preview; он не используется для framing, fitting или оценки и не даёт основания заявлять blind/unseen data.

## Числовой контракт и зависимости

Каждый request имеет kind=score, два полных предложения в state, зафиксированный русский вопрос о сходстве смысла и шесть разрешённых шкальных значений **0.0, 1.0, 2.0, 3.0, 4.0, 5.0** с собственными краткими описаниями. Это шкала ответа модели. Исходный дробный `publishedScoreReference` хранится **вне request**; `expectedOptionId` не создаётся, reference не округляется в классификационную метку. Исходные строки не переводятся, не усекаются и не редактируются.

Нормализация NFKC/whitespace/casefold применяется только к dependency graph. Оба sentence roles образуют неориентированный граф, включая транзитивные связи: **2561 разных нормализованных предложений / 1242 connected components**. Pair order не создаёт новую независимую пару: **1333 уникальных unordered pairs / 3 дополнительных duplicate rows**, в **2 повторяющихся парах исходные scores различаются**. Все эти rows и их разные published ratings сохраняются; удаления, усреднения и выдуманной adjudication нет. Эти зависимости не принимаются за independent holdout.

Verifier повторно строит весь dataset из трёх оригинальных payloads. SHA seal означает целостность, а не право на человеческий review. Resealed changed score/state/group/order, int/bool aliases, invented class targets и qualification authority отклоняются.

## Actual import и перепроверка

Source feature `e035fb8d11d16bf1a77d4115087641bd48791ebd`, отдельный fixture `d49a7a4dff5fe9afd431f3fb2854b5e392188b5b`: **152 files** закреплены current bytes и обеими Git histories. Actual copied CLI import/replay сохранил **3010282-byte dataset** byte-for-byte, file SHA `0fe2c16548b39905dbeb389e48cc9623912fdfe8473fb56ef4a6bf69d0554a1c`, canonical artifact SHA `868e9686edbf09fc82bde2fe5a9347c6fc0fb7bbcc9016986596c13f04413d71`.

Независимый stdlib audit без project/model imports выполнил **12514 checks**: исходные continuous references, все source texts и indices, оба sentence roles и транзитивные groups отдельным BFS, 3 duplicate rows и 2 score conflicts, original file SHA/Git blob pins, request/source hashes, authority и обе Git histories.

Первая попытка private refusal helper завершилась после одного проверенного отказа из-за коллизии имён журналов; O_EXCL остановил второй запуск до создания child process. Оба payloads, log и helper сохранены. Исправленный helper v2 использует новый output directory и сохраняет PID/exit receipt после каждого вызова; source importer и original corpus не менялись.

**43 actual CLI refusals + 1 positive replay PASS**: rounded scores, float→int/bool aliases, invented class target, reference leakage into request, changed texts/options/dependencies/order/split, fake reviews/quality/routing, hidden conflicts, license changes и wrong/missing byte pins. Refused outputs отсутствуют; все 44 probe PIDs, два import/replay PIDs и original-copy verifier PID завершены. **0 model packages/imports/calls**.

**12 новых / 37 related tests PASS**; synthetic fixtures явно отмечены. Полный **1532-test scripts suite PASS / 4 skip / 540.387 s**, log SHA `b5b80c23a0cc971e38ec0260319c99d4cfc130cc7c5aea73b3e30bec41fe9350`; **18 Node tests PASS**. 20 serving source files не изменились.

[Aggregate evidence](./evidence/2026-10-11/ru-stsb-import-summary.json) сохраняет import scope. [Standalone archive receipt](./evidence/2026-10-11/ru-stsb-import-archive-summary.json): original-workspace ZIP `docs/private/ru-stsb-development-import-20261011.zip`, **59769824 bytes / 1437 entries**, SHA `e25bfa0d04604785a9022dfec138c9aa045374d7167d3f3a4d045d737e02875b`. Source, обе Git histories, оригинальные payloads/license, acquisition history, refusals и завершённые logs проверены по CRC/SHA/size. После closed inventory проверки actual восстановленный CLI снова сохранил dataset byte-for-byte, без model imports/calls.

Следующий этап — native Score comparison с этим целым frozen corpus: numeric weighted predictions, MAE/RMSE и Pearson/Spearman с tied ranks; оба option orders отдельно, accepted subset и coverage явно. Классификационные gold/F1/NLL/Brier по округлённым эталонам не создаются. Regression quality здесь ещё не измерено; human reviews, representative Agat traffic, calibration/holdout и owner/SLO остаются открытыми, routing disabled / qualification=not_assessed.
