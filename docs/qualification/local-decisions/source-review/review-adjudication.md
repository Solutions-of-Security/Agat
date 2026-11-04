# Подготовка разрешения разногласий в review

[CLI](../../../../scripts/prepare-review-adjudication.py) создаёт private-пакет
для разбора разногласий между двумя отправленными review. Он повторно проверяет
обе тройки файлов (исходный review, сохранённый review, session v2) по external
SHA и полностью пересчитывает comparison. Изменённая и заново sealed сверка
отклоняется: сам seal не заменяет связь с исходными отзывами.

Один новый каталог под docs/private содержит packet.json,
adjudication.review.blank.json и case-notes.json. В notes сохранены исходные
задания, оба option ID и оба rationale; labels в blank содержат только
разногласия в исходном порядке. Полный pool и splitSeed остаются исходными
для совместимости с finalize_reviews. Все решения, adjudicatorId и reviewedAt
пусты, resolvedCases=0. Выходные bytes двух файлов закреплены в packet.

В [официальном описании consolidation AWS](https://docs.aws.amazon.com/sagemaker/latest/dg/sms-annotation-consolidation.html)
несколько аннотаций используются для получения одной итоговой метки. Здесь
выбран ручной разбор спорных случаев с сохранением исходных мнений: совпадение
двух option ID и различие строк reviewer ID сами по себе не доказывают истину,
независимость участников или предметную квалификацию.

## Проверенный сценарий

Исходный fixture — три ранее синтетически заполненных задания, два совпадения
и одно разногласие. Семь actual CLI invocations: два положительных (обычный
checkout и standalone из 17 файлов дают byte-identical outputs) и пять отказов
до создания output directory: неверный comparison SHA, изменённая resealed
сверка, duplicate JSON member, неверный saved-review SHA и полный draft,
завершённый без submit. Синтетические исходные labels не являются human gold.
Новые решения и model calls отсутствуют; sealed реальный holdout не открывался.

Восемь новых targeted tests и полный набор из 1298 Python tests прошли,
четыре optional skips. Native probe: 118 checks. Независимый stdlib auditor
пересобрал оба набора outputs, байты отрицательных controls и source bindings:
1491 checks / 1277 JSON keys. Protected resident: 27 checks, восемь неизменных
полей; восемь owned PID отсутствуют после завершения.

Первые две версии auditor остановились до создания proof: сначала проверяли
несуществующее поле submitted, затем ожидали ValueError для duplicate JSON,
хотя parser возвращает DecisionError. Обе версии и failure receipts сохранены.
Исправленный auditor и дополнительная проверка controls прошли без повторения
семи original CLI invocations.

## Ограничение и следующий шаг

Пакет готовит handoff; он не назначает adjudicator и не создаёт reference labels.
Нынешний generic blind terminal требует labels для всего pool и не принимает
этот disputed subset. Следующий инженерный шаг — отдельная adjudication session
с checkpoint, correction, explicit submit и проверяемой квитанцией, которая
сохранит полный pool и позволит третьему участнику заполнить только разногласия.
До реальных независимых review, разбора разногласий и назначения ответственных
предметное качество, SLO и routing остаются открытыми gates.

## Воспроизводимость

Source commit и все 17 source SHA, original plan/result pins и audit seal
сохранены в [aggregate summary](./review-adjudication-summary.json).
[Archive summary](./review-adjudication-archive-summary.json) закрепляет ZIP,
проверку каждого файла и независимое восстановление фактической копии
из исходного workspace. Private тексты, rationale и identities в публичный
отчёт не перенесены. Replay не запускает CLI или модели.

[Dedicated adjudication session](./adjudication-session.md) реализует следующий
инженерный шаг: disputed subset, correction/resume и separate explicit submit.
Реальные участники и предметное качество этим этапом не подтверждены.
