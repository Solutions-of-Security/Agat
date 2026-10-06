# Полный shared soak wired package 0.12.3

06.10.2026 MSK. **Исходный новый gate 7200 measured seconds завершён;
полный offline verifier и отдельный native audit прошли.** Старый resident
0.12.2 восстановлен. По указанию пользователя работа остановлена после
закрытия этого этапа; следующий native Temporal/RAG gate не запускался.

После [failed collector attempt](./wired-soak-collector-timeout-0.12.3.md)
проведён новый опыт в отдельной directory с исходным plan 7200 секунд /
128 blocks. Предыдущие failed plans, logs и partial results сохранены;
успешный prefix не выдаётся за полный gate. Controller и analysis commit —
`f1c1183eec9e1db280aa580343829d9f65e91609`; все 43 измеряемых source bindings сверены
с его Git archive. Во время опыта HEAD/source этого worktree не менялись.

[Sealed package](./resident-wired-package-0.12.3.md) запущен из отдельного
venv через собственный временный LaunchAgent. Profile SHA —
`776fba7fa1244e13292ac3605a53a6d6c6cf557901c8d863cab9489d6d2f47ce`; bundle seal — `2f2faa7cbce6bdf5df20c86f5d2109cf8ac8fe58277b3a99c526bd215be1c5c2`.
Wired budget 4096 МиБ, max input 2048, allocator cache 128 МиБ, server
inference deadline **5000 мс**, HTTP timeout 10000 мс сохранены. Native
inspection использовал отдельные 10 секунд на каждую metadata команду,
двухсекундную паузу и bounded observer join 60 секунд. Ошибка inspection
по-прежнему завершает gate; deadline модели не увеличивался.

Qwen3 8B использовал собственный Ollama 0.35.1, прежний digest
`500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`, context 8192, predict 128, seed/temperature 0,
think=false, один loaded model и parallel slot. Phase-boundary residence
records подтвердили digest/context; API size/size_vram —
6 528 046 202 bytes.
Bytes нативного runner совпали до/после и при независимой проверке.

| Наблюдение | Результат |
|---|---:|
| Исходный target / измеренная длительность | 7200 с / 7 226,882 с |
| Wall time benchmark, включая warmup/gaps | 7 331,948 с |
| Полные / time-budget blocks | 67 / 2 |
| Measured calls / отдельные warmup calls | 16 353 / 276 |
| Measured decision / primary calls | 8 166 / 8 187 |
| Failed calls, включая warmup | 0 |
| Фактически overlapping HTTP pairs | 2 040 |
| Exact journal records / native observations | 12 405 / 3 321 |
| Computed decision counter | 0 → 8 304 |
| Временные PID после cleanup | 6, все отсутствуют |

Measured duration — сумма block windows после последнего warmup, включая
phase-boundary API checks. Model load и warmup не входят в measured rows;
serialization/initial health создают дополнительные wall gaps. Контроллер
сохранил исходную длительность и остановился с `duration_complete`.
Все time-budget prefixes проверены по исходным rows и chronology; это разрешённая граница длительности, а не скрытый request failure.

| Фаза | Decision / primary calls | Decider HTTP wall p50 / p95 / max, мс | Primary HTTP wall p50 / p95 / max, мс |
|---|---:|---:|---:|
| Decision only before | 2 070 / 0 | 293,424 / 431,751 / 928,281 | — / — / — |
| Primary only before | 0 / 2 070 | — / — / — | 341,739 / 2 071,663 / 2 896,406 |
| Sequential pair | 2 046 / 2 046 | 338,725 / 800,630 / 3 220,066 | 328,992 / 1 832,755 / 3 193,821 |
| Overlapping pair | 2 040 / 2 040 | 495,079 / 799,453 / 2 070,584 | 606,747 / 2 279,988 / 3 344,754 |
| Primary only after | 0 / 2 031 | — / — / — | 343,326 / 1 966,727 / 2 870,658 |
| Decision only after | 2 010 / 0 | 289,636 / 519,263 / 3 013,681 | — / — / — |

Quantiles nearest-rank пересчитаны по pooled measured rows; warmup
отдельный. Все decision signatures/input tokens совпали между blocks и
с сохранённым baseline 0.12.2. Primary signatures также не менялись между
blocks; digest/options оставались одинаковыми. Это 15 повторяемых development inputs,
без новых независимых quality examples или qualification correctness.

Offline audit повторно сверил seals, committed inputs/profile/sources,
каждый block и exact journal, порядок phases/cases, timestamps/overlap,
counts, token bounds, distributions и исходные 7200 секунд. Public
[result summary](./evidence/2026-10-06/wired-shared-soak-7200-0.12.3/result-summary.json)
содержит timing/count allowlist и закреплённые analysis commit/SHA.

Отдельный native audit сверил bundle/model bytes, реальный launchd argv,
raw Prometheus vector и полный delta 0 → computed с отдельными warmup,
нулевые failed/rejected counters, стабильные sampled PID/children/profile
и server start time. Retirement/observer/cleanup errors отсутствуют.
Observation duration p50/p95/max — 161,910 / 398,961 / 2 523,286 мс;
observed interval p50/p95/max — 2 168,785 / 2 400,489 / 4 583,974 мс.
Минимальный sampled disk free — 3 058 184 192
bytes при исходном guard 2 ГиБ; raw system-memory observations сохранены.
[Hardware summary](./evidence/2026-10-06/wired-shared-soak-7200-0.12.3/hardware-summary.json)
публикует проверенные агрегаты без private paths/commands/API bodies.

Qwen выгружен, временный job и все owned PID отсутствуют. Старый resident
0.12.2 работает с прежним полным profile и свежим scrape; его исходные
registration/plists сохранены, Prometheus PID не менялся. Новый 0.12.3
release не зарегистрирован. Private evidence включает неизменённый plan,
protocol, rows/journal, logs, drivers/audits, baseline reference и полный
Git source archive; ZIP CRC, каждый file SHA/size и неизменность source
проверены перед сохранением в пользовательском `/docs/private`.

Во время опыта на том же хосте выполнялись независимые CPU checks и
process fixtures. Изменённый profile, условия хоста и последовательные
runs не позволяют приписать скорости или прежние timeouts одному setter.
Дискретные observations не доказывают непрерывную стабильность. Closed-loop
phases не задают production arrival rate; этот gate не является SLO.
Serving code данным evidence commit не меняется. Родительский полный
`docs:check` для launcher прошёл: 603 Python tests с четырьмя skips для явно
включаемых Docker fixtures и 12 Node checks; первоначальные failed checks сохранены
и разобраны в [соседнем отчёте](./temporal-launcher-cancellation.md).

Для текущего документационного commit с Node 24.14.0 / Python 3.13.12
прошли 12 Node checks, 2323 local link targets в 258 Markdown files,
проверка generated process catalog и `git diff --check`. Public summaries
дополнительно сверены с sealed private copies и проверены на отсутствие
private paths/host commands/request bodies.

Runtime остаётся shadow-only: routing выключен, `not_assessed`. Реальный
wired Temporal/PostgreSQL/RAG с fallback/recovery/native replay, permanent
rollout 0.12.3, boot/login acceptance, owner/SLO, независимая разметка,
calibration и holdout остаются открытыми. Новые этапы не запускались после
указания пользователя завершить текущий и остановиться.
