# Проверяемые границы измерения парного workflow

11.10.2026 MSK. В полном локальном Python-прогоне старый paired fixture
сохранил wall duration 361 мс и monotonic duration 623.513 мс. Этот receipt
отклонён прежним правилом ±2 мс; отдельный повтор без изменений прошёл.
Причина исходного расхождения не установлена: в старом формате нет показаний
часов между последовательными вызовами. Старый failure log сохранён приватно,
его результат не заменяется последующим успешным повтором.

## Выбор и реализация

[W3C High Resolution Time](https://www.w3.org/TR/hr-time-3/) различает wall clock,
который может корректироваться, и monotonic clock для измерения длительности.
[Node 24 performance.now](https://nodejs.org/docs/latest-v24.x/api/perf_hooks.html#performancenow)
предоставляет миллисекунды относительно запуска процесса. Последовательные
вызовы Date/performance не имеют общей гарантии одновременности.

[Sampler](../../../../scripts/lib/decision_workflow_batch_clock.mts) сохраняет
монотонное показание перед `Date.now()` и после него. ISO timestamp строится
из фактически прочитанного wall time. Новый batch schema —
`agat.decision.paired-workflow-batch.v2`; `clockSamples.start/end` содержат
`wallAt`, `monotonicBeforeMs`, `monotonicAfterMs`.

Для start `(s0,s1)` и end `(e0,e1)`:

- `elapsedMs = round(e0-s1, 3)`: измеренный интервал между внутренними
  монотонными границами, точность сохранения 0.001 мс;
- `[e0-s1, e1-s0]`: интервал между фактическими wall-clock reads при
  согласованном ходе часов;
- `e1-s0 <= 20000`: исходный completion deadline включает обе выборки часов;
- wall duration должен попасть в этот интервал с прежним allowance 2 мс
  для миллисекундных timestamps. Неподтверждённое расхождение отклоняется.

[Verifier](../../../../scripts/lib/decision_workflow_batch_clock.py) отдельно
пересчитывает elapsed из raw samples с allowance 0.000501 мс для округления.
Подмена elapsed значением wall duration не проходит даже при широком sampling
interval. Проверяются finite numbers, отсутствие bool, точное соответствие
wall timestamps, последовательность всех monotonic brackets и отсутствие
перекрытия пар. Existing cross-process chronology, primary/lease/native
bindings и creation skew сохраняются.

[Driver](../../../../scripts/run-public-support-workflow.mts) использует
монотонный completion deadline для каждой пары и сохраняет явные sampling
данные. Offline evidence добавляет schema, факт проверки samples и их
максимальную ширину только для новых batches. Это проверка согласованности
артефактов; она не аутентифицирует системные часы или происхождение файла.

## Совместимость и проверки

Historical v8 batches без новых полей продолжают проверяться прежним правилом
±2 мс. Их reports не получают новых claims. Mixed/unknown schemas отвергаются;
старое расхождение 262.513 мс не становится допустимым без записанных samples.

[11 новых тестов](../../../../scripts/test/test_decision_workflow_batch_clock.py)
проверяют реальные Node clock reads с искусственной паузой внутри sampling,
подмену elapsed, неучтённый clock difference, deadline, rounding, malformed
samples, monotonic barrier и historical receipts. Related suite — 32 PASS,
включая actual model-free coordinator/worker с шестью исходными synthetic
inputs, тремя pairs и native HTTP busy. 18 Node documentation tests прошли.
Это synthetic integration evidence; model calls и новые real reference labels
равны нулю. Human review и qualification остаются открытыми.
