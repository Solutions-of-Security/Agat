# 0.5 decision arrivals/с при работе primary

07.10.2026 MSK. **Два native repeats дали 72/72 computed decisions без
drops, busy и measurement errors. На длинном active input — 12/12,
caller p95/max 1110.362 мс.** Более редкое расписание получило ограниченные
технические данные для planning envelope [draft owner/SLO](../shadow/observability/resident-service-acceptance.md).
Это не утверждение customer SLO или качества модели.

## Метод

[Launcher](../../../../scripts/run-decision-arrival-rate.py) закрепил commit
`08bec883a85913419154ac97f576dedb7d58341a` и 53 contributors перед inference.
Новый параметр `--decision-rate 0.5` требует оба primary paths и создаёт
план/result/phases v3 с явным `decisionRatePerSecond`. Defaults v1/v2
сохраняют прежнюю семантику. Verifier проверяет schema, rate и каждую
запланированную строку; override rate в legacy plan отклоняется.

По сравнению с [1 decision arrival/с](./arrival-primary-4096.md) изменён
только decision schedule: шесть arrivals за 12-секундное окно, offsets
0/2/4/6/8/10 секунд, один client slot, lag limit 100 мс. Очередь, retry
и catch-up отсутствуют, dropped arrivals остаются в denominator.
Метод основан на [constant arrival rate](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/).

Exact inputs 256/2048 tokens, runtime profile 0.12.3, wired 4096 МиБ,
cache 128 МиБ, max input 2048, deadline 5000 мс и caller timeout 10000 мс
сохранены. Primary — тот же pinned Ollama 0.35.1 / Qwen3:8b, context
8192, 128 decode tokens, scheduled rate 0.5/с и один client slot.
Qwen загружен во всех conditions; primary calls только в active.
Для каждого input следуют idle-before / active / idle-after.

## Два повтора

Все quantiles независимо пересчитаны по pooled computed rows. При 12
observations nearest-rank p95 совпадает с maximum; это краткий опыт.

| Input tokens | Condition | Computed / scheduled | Caller p50 / p95 / max, мс |
|---:|---|---:|---:|
| 256 | idle before | 12/12 | 164.025 / 191.509 / 191.509 |
| 256 | active | 12/12 | 217.734 / 237.235 / 237.235 |
| 256 | idle after | 12/12 | 161.300 / 256.634 / 256.634 |
| 2048 | idle before | 12/12 | 860.044 / 920.434 / 920.434 |
| 2048 | active | 12/12 | 1097.690 / 1110.362 / 1110.362 |
| 2048 | idle after | 12/12 | 857.469 / 889.090 / 889.090 |

Все 72 scheduled decisions вычислены в пределах diagnostic threshold
5000 мс; восемь decision warmups отдельно. Runtime journal/counters
подтвердили 80 POST computed с учётом warmups. Maximum dispatch lag —
8.667 мс. Primary вернул 10/24 planned calls; 14 capacity drops учтены,
два warmups отдельно. Каждый returned primary call декодировал 128 tokens;
wall p50 3186.645 мс, p95/max 5308.129 мс. Достигнутый throughput primary
не равен его scheduled rate. Проверены 24 actual HTTP overlap pairs.

На прежнем 1/s расписании long active condition дала 12/24; здесь — 12/12.
Audit сверил exact inputs, primary configuration и runtime settings
между протоколами. Computed outputs/distributions/tokens совпали с прежним
опытом. Порядок фаз фиксирован, фоновые задачи машины не контролировались,
permanent resident работал параллельно. Поэтому сравнение показывает
ограниченный observed capacity result без причинного вывода или гарантии
на другой workload, burst, hardware, context/decode budget.

## Проверка и сохранение

25 targeted tests, 759 Python docs tests (4 optional skips), 12 Node
checks и process catalog прошли. Прежние native v1 и v2 evidence прошли
новый [verifier](../../../../scripts/verify-decision-arrival-rate.py).
Первый новый unit run выявил ошибку fixture-interval; исправленная версия
измеряет фактические интервалы двух потоков, исходный log сохранён.

Independent audit прошёл 10 checks. Все 12 recorded temporary PID
отсутствуют; четыре permanent resident observations сохранили process
births/bindings, 34 pins, 35 protected sources, fresh scrape и counters
1/0/0 без дополнительных inference. Сохранены 18 phase-boundary
health/memory samples; peak memory и causal effect не измерены.

[Public allowlist summary](./evidence/2026-10-07/arrival-primary-half-4096/result-summary.json)
содержит counts, timings и provenance. Private ZIP: 57 files, два measured
Git states, 81,867,689 bytes, SHA-256
`f734091c2300b887857614dbef910d6517cfc7a8cd656b51aeb9dea253bda788`.
CRC, SHA/size каждого файла и идентичная копия в исходном workspace
проверены. Сохранены обе новые серии, comparison inputs/receipts прежнего
опыта, audit и test logs; последующая публикация и CI сюда не включены.

0.5 decision arrivals/с / один active call предлагаются для обсуждения
пилота с этим resource/input envelope. Owners, реальный customer flow,
actual boot/login, два независимых human reviews, calibration и holdout
остаются открытыми. Routing выключен; qualification `not_assessed`.
