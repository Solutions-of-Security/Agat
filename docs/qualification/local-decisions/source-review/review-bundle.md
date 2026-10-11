# Переносимый bundle завершённого review

11.10.2026. После [source-bound finalization](./review-finalization.md)
можно собрать все её inputs и outputs в отдельный immutable каталог.
Получателю достаточно одного external SHA файла `bundle.json`: verifier
сам находит файлы по фиксированным именам и заново проверяет обе submitted
сессии, comparison, adjudication handoff/session и finalized dataset.
Перенос каталога не требует исходных абсолютных путей или Git history.
Сам verifier должен быть доступен в установленной копии проекта.

## Упаковка и проверка

[Packager](../../../../scripts/package-decision-review-finalization.py)
принимает те же source/adjudication arguments, что
[finalization verifier](../../../../scripts/verify-decision-review-finalization.py),
включая `--finalization`, `--finalization-file-sha256`, `--dataset` и
`--dataset-file-sha256`. Output — новый каталог в `docs/private`.
Он сначала проверяет весь существующий finalization, затем копирует exact
raw bytes и пишет completion manifest последним. Исходные файлы не меняются.
При записи используется exclusive creation по
[Python os.open](https://docs.python.org/3.13/library/os.html#os.open);
SHA-256 реализован через [hashlib](https://docs.python.org/3.13/library/hashlib.html).

В каталоге девять JSON artifacts при полном согласии, пятнадцать — при
adjudication, и `bundle.json`. Fixed names: `first.session.json`,
`first.input-review.json`, `first.output-review.json`, аналогичные `second.*`,
`comparison.json`, `finalization.json`, `dataset.json`; для разногласий —
шесть `adjudication.*` inputs. Manifest schema —
`agat.decision.review-finalization-bundle.v1`; каждый файл имеет byte SHA и size.

После переноса используйте [одну CLI-команду](../../../../scripts/verify-decision-review-bundle.py):

```bash
python3 scripts/verify-decision-review-bundle.py \
  --bundle docs/private/received-review/bundle.json \
  --bundle-file-sha256 REPLACE_WITH_EXTERNAL_64_HEX_SHA \
  --output-dir docs/private/received-review-verified-new
```

SHA манифеста закрепляют отдельно перед переносом. Verifier не берёт внешний
pin из самого manifest. Имена файлов выбирает код: absolute paths, `..`,
дополнительные artifacts, symlink и отсутствующие файлы отвергаются.
Каталог храните immutable; outputs verification создаются отдельно.

Directory mode — 0700, files — 0600. Manifest ограничен 256 KiB, файл —
32 MiB, сумма artifacts — 128 MiB; существующие более узкие bounds сессий
дополнительно проверяются при replay. Size должен быть точным integer,
duplicate JSON keys и нечисловые значения не допускаются. Rehashed изменения
claims или source/receipt/dataset bindings дают отказ. Failed write не
публикует completion manifest, partial каталог нельзя выдавать за ready bundle.

## Границы результата и проверки

Bundle переносит конкретные review-решения и их происхождение. Он не
аутентифицирует человека и не доказывает независимость/квалификацию участников.
Все соответствующие flags, classification accuracy и routing остаются false;
qualification — `not_assessed`, model calls — 0. Formal `expert-reviewed`
labelSource не используется как доказательство профессиональной квалификации.

14 новых / 101 related Python tests прошли. Они проверяют полный и agreeing bundle, перенос после удаления
оригинальных source files, все file pins, rehashed claims, path escape,
symlink, missing/extra artifacts, byte limits, partial review, interrupted
write, private modes и отказ overwrite. Реальные human reviews этим не
создаются; дальнейший quality gate сохраняется.

Свежий CLI protocol из committed
`d005ff5698d0fb8cf93da37f6bdd0b12141601ba` упаковал прежнюю synthetic
source pair и finalized dataset: три cases, два agreed, один adjudicated,
15 artifacts. Dataset file SHA остался
`9e07af47891efe856db38fd1c9de8605aaac4ce7eceb592f64c27c84e6e361b2`.
119 independent byte/source checks, два positive CLI calls и пять отказов
до output прошли. Relocated replay из 24 sources без Git сохранил verification.
Проверка общего byte budget теперь выполняется до полного разбора inputs;
новый тест подтверждает отсутствие replay при превышении бюджета.

Первый related run включал неверное имя test module и завершился import error;
исправленный и финальный наборы прошли, прежний log сохранён. После изменения
порядка проверки бюджета исходный protocol повторён на новом commit;
manifest остался byte-identical предыдущему native package.
[Сводка](./review-bundle-summary.json) содержит реальные file pins и counts.
[Текущий archive](./review-bundle-archive-summary.json): 138 files / 211931 bytes.
Все entries и actual original-workspace copy проверены по CRC/SHA/size;
restored production CLI дал byte-identical verification без Git/model calls.
Первый архив с 137 files также сохранён отдельно как предыдущее evidence.
