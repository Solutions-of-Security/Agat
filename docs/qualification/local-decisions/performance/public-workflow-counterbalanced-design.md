# Prospective ABBA/BAAB replication: весь public inventory

Статус: **sealed prospective plan + source-bound replay + independent audit PASS**.
Native measurement ещё не выполнен.
Этот этап фиксирует дизайн до результатов. Model calls и measured workflows=0;
196 workflows / 196 primary scores / 98 decision scores — план будущего опыта.

## Почему следующий опыт имеет такой порядок

[Latency accounting](./public-workflow-latency-decomposition.md) показал,
что whole-workflow delta складывается из различающихся primary и post-primary
компонентов. [A/A control](./public-workflow-primary-repeat-control.md)
сохраняет primary variation даже без case shadow scores. Повторение одной
очередности не отделяет эту вариацию от эффекта порядка.

[NIST randomized blocks](https://www.itl.nist.gov/div898/handbook/pri/section3/pri332.htm)
рекомендует контролировать доступные nuisance factors блоками и рандомизировать
остальные. [Google Benchmark](https://google.github.io/benchmark/user_guide.html)
поддерживает random interleaving повторов для снижения влияния изменений
состояния системы. Эти источники обосновывают блоки и повторения; конкретная
формула ABBA/BAAB ниже является выбором этого протокола.

## Фиксация до измерения

Все 49 whole inputs / 44 groups остаются в исходном порядке, включая три
context-too-long inputs. Block — исходная соседняя пара; последний input
образует singleton. В одном периоде будущий driver создаёт до двух workflow
одного условия. Для каждого block идут четыре последовательных периода:

| Orientation | Period 0 | Period 1 | Period 2 | Period 3 |
|---|---|---|---|---|
| ABBA | control | shadow | shadow | control |
| BAAB | shadow | control | control | shadow |

У каждого input два control и два shadow наблюдения. 24 complete blocks
распределяются 12/12 по orientation; singleton получает отдельный seeded bit.
Domain-separated SHA-256 seed зависит только от фиксированного domain и SHA
context file; hash rank определяет выбор orientation. Это воспроизводимый
seeded порядок, не случайная выборка customer traffic. CLI не принимает seed,
condition order, отбор cases или результаты latency для выбора дизайна.

Всего запланировано 100 batches: 96 с двумя inputs и 4 singleton; 196 unique
route keys включают pair/period/index/condition, а replica 0/1 отличает два
наблюдения одного условия. Actual overlap, completion и native outcomes
должны быть проверены при будущем запуске; design не выдаёт их за измерения.

Contrast каждого case: mean двух shadow − mean двух control. Counts и средний
ordinal period у условий одинаковы, поэтому additive linear trend **по номеру
периода** алгебраически сокращается. Неравные длительности периодов не дают
того же свойства для тренда по elapsed wall time. Nonlinear drift, shared
prefill/cache carryover и зависимость 44 groups остаются ограничениями;
независимость 49 cases и causal overhead не предполагаются.

Остаются прежними целый worker input, condition-blind prompt, generation
ctx=32768/decode=128/temp=0/seed=0/think=false, pinned primary NUM_PARALLEL=2,
два worker slots, primary warmup=1 и decision warmups=2. Default cache policy
owned runner сохраняется. Different-output pairs и failed attempts должны
сохраняться; порядок не меняется после результатов ради удачного повторения.
Owners, human labels, holdout, customer SLO и routing qualification открыты.

## Инструменты и проверка

[Planner](../../../../scripts/plan-public-support-counterbalanced.py),
[source-bound replay](../../../../scripts/verify-public-support-counterbalanced-design.py)
и [регрессии](../../../../scripts/test/test_decision_public_counterbalance.py)
работают без model/network calls. Private sealed plan содержит full context,
source/whole-input pins, protocol, generation/settings, все blocks/batches/
routes и точные planned counts. Replay проверяет file SHA, seal, исторические
context/planner sources и пересчитывает полный порядок. Новый output directory
создаётся только в `/docs/private`; существующие receipts не перезаписываются.

## Sealed prospective evidence

[Aggregate summary](./public-workflow-counterbalanced-design-summary.json) фиксирует
plan file SHA `f72ba21086e9be0627d2848636e4b0e5a6e5bfd9b02446a377cfafcf503aa03b`,
source `9883a5e02a0fb60aad67fa93d650bfa98b6f1d1e`: 219 files / 40 context sources.
Полный sealed порядок находится в private plan. Source-bound replay PASS;
независимый stdlib audit 6110 checks / 5700 JSON keys проверил весь
порядок, исторические source bytes, generation/settings и planned denominator.
17 новых / 1154 Python tests (4 optional skips) прошли. Protected resident
27 checks и все 8 полей предыдущего snapshot неизменны. Model calls и measured
workflows=0; actual overlap, outcomes и причинный эффект этим этапом не установлены.
