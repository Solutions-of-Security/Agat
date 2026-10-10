# Source-bound latency accounting: paired workflow и A/A control

Статус: accounting harness подготовлен; sealed analysis ещё не выполнен.
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
