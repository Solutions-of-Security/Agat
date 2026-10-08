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
