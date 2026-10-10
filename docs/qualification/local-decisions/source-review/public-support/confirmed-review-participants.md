# Review: пользователь и независимый эксперт

10.10.2026 пользователь подтвердил состав: первый review выполняет он сам,
второй — выбранный им независимый эксперт. Подготовлено по 49 development
заданий для каждого; все решения пока пусты. Подтверждён состав будущих
участников. Фактическое выполнение, identity, квалификация и независимость
не подтверждены квитанциями или решениями.

Задания и рубрика описаны в [development review](./development-review.md).
Каждый участник начинает с собственного blank и работает самостоятельно.
До отправки второго review первый набор ответов ему не показывается.

## Удобная форма опроса

Для текущего пакета подготовлена [русская форма](./review-form-ru.md) с 49
заданиями. Откройте `review-form.html` в браузере, выберите участника и заполните
опрос. Прогресс сохраняется; черновик можно скачать и загрузить обратно.
После завершения приложите итоговый JSON к чату. Эксперту передайте отдельную
копию пустой формы. Прежний незавершённый `review.json` можно загрузить кнопкой
«Загрузить черновик».

Ниже сохранён дополнительный терминальный способ для исходного пакета.

## Первый участник

Для текущего checkout:

```bash
cd /private/tmp/agat-development-review-20261010
python3 scripts/review-decision-pool.py \
  --review docs/private/development-review-packet/review.first.blank.json \
  --review-file-sha256 1cb4d4369116c473d27e62d770b9024262e9d1bcad46b1a869bf21f38aa4f60c \
  --reviewer-id user-development-review \
  --output-dir docs/private/user-development-review-session
```

reviewer-id — предложенный declared ID этого участника. Выберите вариант по
исходному вопросу, введите обоснование и подтвердите y. :edit N исправляет
ответ, :clear N очищает после подтверждения. :skip оставляет задание пустым,
:quit сохраняет прогресс. После заполнения всех 49 заданий нужны :submit и y;
полный черновик после quit остаётся неотправленным.

## Второй участник

В собственном checkout с тем же исходным пакетом:

```bash
python3 scripts/review-decision-pool.py \
  --review docs/private/development-review-packet/review.second.blank.json \
  --review-file-sha256 1cb4d4369116c473d27e62d770b9024262e9d1bcad46b1a869bf21f38aa4f60c \
  --reviewer-id independent-expert-development-review \
  --output-dir docs/private/expert-development-review-session
```

Исходные bytes двух blank совпадают, а output directories и declared IDs
раздельны. Для resume передайте review.json своей предыдущей partial session,
его фактический SHA, тот же ID и новый output directory. Обе completed sessions
нужны вместе с первоначальными blank для [проверки квитанций](../review-session-verification.md)
и [сверки решений](../review-pair-comparison.md).

Локальный checkout закреплён на source commit
191319cd27247a877e32cba69e43c1ce7bcce5f1. Его 15 Python source files и четыре
исходных artifacts имеют read-only permissions и не изменяются следующими
этапами реализации. Для другого компьютера нужно восстановить те же source
bytes и blank SHA в отдельном checkout. Пакет не содержит calibration/holdout
заданий и модельных ответов.

При разногласиях создаётся [handoff](../review-adjudication.md) для третьего
участника с отдельным ID; его назначение пока не подтверждено. Решения двух
участников и технические квитанции сами по себе не создают доказанное качество
для процессов Агат. Reference labels=0, qualification=not_assessed, routing=false
до фактического завершения и проверки соответствующих этапов.
