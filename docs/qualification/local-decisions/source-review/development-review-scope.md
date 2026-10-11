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

[Review validator](../../../../scripts/lib/decision_review_development_scope.py)
проверяет новую submitted-review форму. Нужны declared consistent
consensus/adjudication records, annotation, только development splits и
отсутствие inferred authority. Original pool восстанавливается целиком;
каждый case заново проходит прежний strict consensus/adjudication finalizer.
Даже resealed dataset с calibration/holdout/train split или human/routing claim
отклоняется. Проверка записей использует прежний finalizer; значения
reviewer ID и timestamps остаются декларациями, а не аутентификацией людей.

Standalone scoped dataset нельзя передать в обычную runtime evaluation,
freeze или validate experiment plan. Прежний runtime validator отвергает
такой dataset до загрузки весов. Метрики доступны через
[source-bound native diagnostic](./review-native-diagnostics.md), который
проверяет scope и все source reviews перед comparison с сохранённым native
history; новых model calls нет. SHA и seal проверяют целостность;
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
inventory validation, refusal до model construction, resealed split/authority mutations,
consensus record consistency, idempotent scoping, legacy byte replay,
v1/v2 bundles и сохранение mixed splits. Все тестовые метки synthetic;
реальные independent reviews для 49 исходных cases остаются необходимыми.
Routing=false; qualification=not_assessed.

Первый полный прогон выявил ошибку архитектурной границы: изменения offline
review helpers внутри `decision_runtime` изменяли SHA всего serving package
и расходились с закреплёнными native profiles 0.12.3. Guard вынесен в
`scripts/lib`; все runtime files восстановлены побайтно. Model/profile artifacts
не менялись, implementation verifier не ослаблен. Failed log сохранён.
