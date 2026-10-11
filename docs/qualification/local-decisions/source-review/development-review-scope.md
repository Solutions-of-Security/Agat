# Назначение finalized development labels

11.10.2026 MSK. [Finalization](./review-finalization.md) v2 переносит ограничение
development в самостоятельный dataset. Прежний v1 сохранял development splits,
но не имел явного usage marker. Новый receipt отличает `developmentOnly=true`
от mixed group-split review; ни метки, ни исходное распределение групп не меняются.

Если каждый case относится к development, dataset получает seal,
`usage=development-only`, `developmentLabelKind=submitted-review` и явные
false flags для routing, qualified routing, human execution, reviewer identity,
independence, expertise и classification accuracy. Original annotation,
source requests, порядок, метки, rationale и все review records сохраняются.
Mixed calibration/holdout pool сохраняет прежние assignments и dataset bytes.

[Dataset validator](../../../../decision_runtime/evaluation.py) различает
старые assistant-reviewed development data и новую submitted-review форму.
Для второй нужны declared consistent consensus/adjudication records,
annotation, только development splits и отсутствие inferred authority.
Даже resealed dataset с calibration/holdout/train split или human/routing claim
отклоняется. Проверка записей общая с прежним qualification gate; значения
reviewer ID и timestamps остаются декларациями, а не аутентификацией людей.

Standalone development dataset нельзя передать в freeze или validate experiment
plan. Evaluation preflight отвергает calibration/holdout до загрузки весов;
CLI также проверяет наличие запрошенного split до backend construction.
Обычная development evaluation доступна. SHA и seal проверяют целостность;
переписывание данных вне проверенного source bundle не создаёт происхождение.

Это решение сохраняет независимый итоговый holdout. В
[scikit-learn data leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage)
test data исключаются из решений о модели; источник прочитан 11.10.2026.
Маркер назначения — инженерная защита от случайного повторного использования
данных, уже раскрытых для development, а не замена предметной разметки.

## Совместимость

Новые commands формируют `agat.decision.review-finalization.v2`.
Verifier читает версию из pinned receipt и для v1 восстанавливает именно
прежние bytes. Старые v1 datasets, bundles и saved native diagnostics
проверяются по своему исходному формату. Они не переписываются задним числом.
Несогласованная смена receipt version или удаление нового usage с пересчётом
file pins не совпадает с полным source replay.

## Проверка

12 новых / 99 related Python tests прошли: standalone copy/load/development
evaluation, refusal до model construction, resealed split/authority mutations,
consensus record consistency, idempotent scoping, legacy byte replay,
v1/v2 bundles и сохранение mixed splits. Все тестовые метки synthetic;
реальные independent reviews для 49 исходных cases остаются необходимыми.
Routing=false; qualification=not_assessed.
