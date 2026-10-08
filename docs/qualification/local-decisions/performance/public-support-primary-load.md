# Public development inventory при активном primary

[Launcher](../../../../scripts/run-public-support-load.py) принимает paired
`--primary-binaries` и `--primary-models`. Explicit plan/result/phase v2
закрепляют всю прежнюю development выборку, pinned primary и `primary_active`.
Default v1 сохраняет прежнюю семантику; его historical native evidence
продолжает проходить [offline verifier](./public-support-load-verification.md).

Каждый schedule содержит все 49 arrivals на общей monotonic origin,
offsets 0/2/…/96 s, rate 0.5/s, один slot, max lag 100 ms, без retry/queue/
catch-up. Decider сохраняет 2048-token / wired 4096 MiB / 5000-ms inference
deadline и caller timeout 10000 ms. Все три длинных запроса отправляются
целиком для context rejection. Primary использует прежние verified Ollama
0.35.1 / Qwen3:8b, context 8192, максимум 128 decode tokens, timeout 30 s,
точный teaching prompt/settings/blob/release SHA из предшествующего
[synthetic mixed protocol](./arrival-primary-half-4096.md).

Один primary warmup и два decider warmup отделены от scheduled denominator.
Private journals сохраняют каждую scheduled строку обоих исполнителей,
включая capacity drops. Phase elapsed включает drain primary. Read-only
`/api/ps` фиксирует model digest/context до warmup, после него и после load.
Сам primary получает отдельный owned server/runner; teardown выгружает только
его модель, останавливает owned processes и проверяет полный PID inventory.

V2 verifier повторяет all source/receipt/response/metrics checks и дополнительно
проверяет primary schema, release/request/settings, полный journal, typed
responses, single slot и фактическое пересечение primary/decider HTTP-интервалов.
`primary_active` требует хотя бы один returned primary и overlap pair.
Пересечение HTTP calls не измеряет точное пересечение GPU kernels.
Primary drops остаются в собственном denominator; scheduled rate не выдаётся
за attained throughput. Неконтролируемый фон и фиксированный порядок опытов
не дают причинной оценки overhead относительно прежнего отдельного run.

Все labels остаются пустыми. Classification accuracy, customer SLO и
representative traffic не измерены, routing false / not_assessed. Resident
получает только read-only проверки; actual boot/login event остаётся внешним gate.

Девять новых regression tests проверяют 1–60 bounds, общий clock двух настоящих
потоков, capacity/cancellation journals, response budgets, реальный temporary
Git replay v2 и rehashed corruptions, early paired-path guard и v1 compatibility.
Targeted набор вместе с прежними probes/verifier/primary tests — 42 pass.

## Native результат 08.10 MSK

Из `bc1fe25` измерен весь inventory: **49 scheduled**, 48 admitted,
**45 computed** (30 ok, 15 abstain), три context rejection, **один
client_capacity drop**. Computed caller p50 **388.117 мс**, p95 **1126.021 мс**,
max **2284.683 мс**; все computed уложились в 5000 мс. Полный good denominator
**45/49 = 0.9183673**, заранее context-eligible denominator **45/46 = 0.9782609**.
Это наблюдаемый capacity result; отсутствие retries сохраняет потерянный arrival.

Первый input, занимавший slot 2284.683 мс, перекрыл следующий arrival на offset
28000 мс. Его полный case/input binding остаётся в journal с `client_capacity`,
без выдуманного latency. Standalone run того же inventory имел 49 admitted /
46 computed. Фиксированный порядок и неконтролируемый фон ограничивают
сравнение; перенос прежних synthetic результатов на весь реальный public
inventory как гарантии 0.5/s оказался необоснованным.

Primary вернул **25/49**, **24 capacity drops** явно учтены; один warmup отдельно.
Каждый returned call завершил 128 decode tokens, суммарно 3200. Wall p50
**3385.137 мс**, p95 **3893.306 мс**, max **3975.580 мс**. Подтверждены **49
HTTP overlap pairs**, без утверждения GPU kernel overlap или production RPS.

Source-bound verifier — **pass / exact**, independent audit — **307 checks**.
Все **48** сопоставимых typed responses, включая context rejection, имеют
точно прежние signatures/distributions/выборы без duration; это повторяемость
вычислений, не доказательство правильности. Два decider warmup отдельно;
physical HTTP counters включают 50 handlers: 47 computed и три rejected.
Fresh owned PID census и resident source/process/counter preservation
проверены после teardown; actual boot/login не наблюдался.

Full docs check: **842 Python / 4 optional skips, 12 Node**, links/catalog pass.
[Allowlisted summary](./public-support-primary-load-summary.json) сохраняет
полные denominators, primary pins, timings, drop и raw evidence bindings.
Planning envelope 0.5/s остаётся draft. Следующий инженерный вопрос —
отдельно закреплённая более редкая decision arrival rate для этого resource
envelope; менять исходный результат или ограничивать тексты ради успешной
доли нельзя. Owners/real permitted workflow, human labels/calibration/holdout
и actual boot/login остаются внешними gates.
