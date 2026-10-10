# Source-bound latency accounting: paired workflow и A/A control

Статус: **sealed accounting + replay + independent audit PASS**.
Новых model calls нет. Анализ использует raw receipts двух завершённых native
экспериментов: [paired real-primary](./public-workflow-paired-real-primary.md)
и [primary-only A/A](./public-workflow-primary-repeat-control.md).

## Фиксированные границы

В каждом dataset сохраняются все 49 inputs / 44 groups и 98 completed
workflow. Пары с разными primary outputs входят в полный denominator.
Source-bound native replay проверяет plan/result hashes, исторические
source snapshots, целый input, primary generation и durable output перед
вычислением accounting.

| Компонент | Начало → конец | Часы и смысл |
|---|---|---|
| Whole workflow | Instance creation → final trace response | Monotonic driver duration; включает polling и получение trace |
| До primary | Instance creation → adapter request received | Wall UTC; dispatch, worker setup и transport вместе |
| Primary proxy | Adapter request received → translated response prepared | Monotonic adapter duration; включает witness/forward/response preparation |
| Native forward | Forward started → translated response prepared | Wall UTC; не изолирует GPU kernel или чистый inference |
| После primary | Translated response prepared → final trace response | Wall UTC; native caller, completion и trace observation |
| Shadow relay | Decision adapter request received → native response prepared | Monotonic и wall; включает admission snapshots |
| Shadow caller | Worker local HTTP call | Собственный monotonic callerTiming из durable observation |

Primary reported total/load/prompt-eval/decode timings сохраняются отдельно:
наносекунды переводятся в миллисекунды. Их сумма не интерпретируется как
разложение adapter interval и не используется для вычисления queue/GPU time.

Для каждого workflow проверяются wall/monotonic boundaries с исходным
допуском 10 ms. При сложении wall-компонентов с primary monotonic duration
сохраняется явный clock closure residual, ограниченный 20 ms. Отрицательный
интервал завершает analysis ошибкой; отрицательный matched delta остаётся
в данных. Shadow tail также раскладывается по wall boundaries на до caller,
relay и после response, без наложения часов разных процессов.

## Сравнение и интерпретация

Для одного input вычисляется second − first по whole workflow и каждому
компоненту. Сумма delta компонентов и clock residual должна воспроизвести
whole-workflow delta. Mean складываются по этой тождественной формуле;
median компонентов не обязаны складываться в median whole delta.

Это описательное accounting одного host и двух фиксированных runs. Primary
вариация в A/A, caching, shared prefill и peer interference ограничивают
интерпретацию whole-workflow delta как стоимости shadow. Анализ не назначает
customer SLO, owners, labels, statistical significance или routing authority.

[NIST paired observations](https://www.itl.nist.gov/div898/handbook/prc/section3/prc311.htm)
определяет пары по одному объекту и анализирует их разности. Здесь 49
development cases не являются случайной выборкой customer traffic; вывод
о причинном overhead или переносимости на production не добавляется.

## Инструменты

[Analysis CLI](../../../../scripts/analyze-public-support-latency.py),
[replay CLI](../../../../scripts/verify-public-support-latency.py),
[accounting and negative tests](../../../../scripts/test/test_decision_public_latency_decomposition.py).
Raw inputs и response text остаются в private evidence. Public report
содержит только derived timings, hashes, source/protocol receipts.

## Измеренный ledger

| Mean second − first, ms | Paired shadow − control | A/A repeatB − repeatA |
|---|---:|---:|
| Whole workflow | 42.224 | -900.174 |
| До primary | -14.388 | 15.878 |
| Primary proxy | -938.854 | -919.227 |
| После primary | 995.469 | 3.367 |
| Clock residual | -0.004 | -0.192 |

Whole paired mean delta 42.224 ms
содержит post-primary delta 995.469 ms
и primary proxy delta -938.854 ms.
Поэтому небольшая разность whole mean не равна отсутствию затрат после primary.
В A/A primary proxy mean delta -919.227 ms
наблюдается при нулевых case shadow calls.

Для 49 known shadow callers actual caller timing: mean 990.437 ms,
median 141.389 ms, p95 2222.527 ms;
adapter relay mean 988.308 ms. Outcomes:
`{'abstain': 7, 'busy': 24, 'context_rejected': 2, 'ok': 16}`. Busy/context rejection/typed return сохранены;
это смесь исходов, не single-inference latency и не customer SLO.

Different primary output pairs сохранены целиком: paired original indices
`[4, 12, 36, 43]`, A/A indices `[12, 36, 43]`.
Ни одна из 49 пар не отфильтрована. Все individual phase intervals,
reported model timings, output hashes и matched delta closures находятся
в private ledger; [public summary](./public-workflow-latency-decomposition-summary.json)
содержит aggregate metadata.

Analysis source `3ab22f90b898f6dac59c39cf7f2addb8b58911ff`: 222 files; native
sources 209/218,
context 40 files. 238 raw native artifacts / 196 completed workflow проверены
по source/plan/result/file SHA. Offline replay PASS, новых model calls=0;
stdlib audit 5136 checks / 258476 JSON keys
пересчитал каждый component и aggregate независимо от приложения.
18 новых / 65 related / 1137 Python tests (4 optional skips),
12 Node docs tests прошли. Protected resident 27 checks после CPU tests и
analysis неизменен. Analysis не выполняет live teardown или inference.

[Immutable archive и actual original-copy replay receipt](./public-workflow-latency-decomposition-archive-summary.json)
сохраняют обе native inventories, context, analysis/verifier source snapshots,
все raw hashes и model-free reproduction. Causal overhead, owners/customer/SLO,
human labels/holdout остаются открытыми; routing=false / not_assessed.
