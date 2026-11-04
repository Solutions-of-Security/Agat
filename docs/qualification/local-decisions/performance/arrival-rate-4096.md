# Fixed arrivals на wired 4096 МиБ

07.10.2026 MSK. **Два собственных native runtime прошли один закреплённый
план: 264 scheduled arrivals, 216 computed results, 24 client capacity
drops и 24 HTTP busy.** На 2048 токенах 1 arrival/с дал 24/24 результатов,
caller p95 874.733 мс; 2 arrivals/с дали 50% результатов. Это краткая
capacity-диагностика для [draft owner/SLO](../shadow/observability/resident-service-acceptance.md).

## Метод и границы

[Launcher](../../../../scripts/run-decision-arrival-rate.py) запускает отдельный
временный HTTP runtime с [профилем 0.12.3](./profiles/runtime-0.12.3-wired-4096.json):
wired 4096 МиБ, cache 128 МиБ, max input 2048, isolated deadline 5000 мс,
caller timeout 10000 мс. Permanent resident работает параллельно без
новых inference. Commit — `8fc0b82b4dfac3eee81438906581826975c3d67e`, 50 committed contributors,
34 точных dependency pins. Эти conditions не являются production workload.

Существующий [генератор контекста](../../../../scripts/lib/decision_context.py)
создал два authored synthetic inputs с padding, ровно 256 и 2048 токенов
с учётом runtime wrapper. Локальный tokenizer загружается без weights и
network; runtime независимо подтвердил длины без truncation и generated
tokens. Ожидаемые метки качества не используются. Каждый процесс записал
четыре отдельных cold/warm shape calls, затем восемь measured фаз.

В каждой фазе окно arrivals — 12 секунд, offsets `index / rate` по
monotonic clock, первый arrival в ноль. Следующий arrival не ждёт ответа;
очередь клиента отсутствует. Client slots — 1 или 2, runtime inference
slot — 1. Если client slot занят, сохраняется `client_capacity`; при
опоздании scheduler больше 100 мс — `scheduler_lag`. Catch-up burst и
автоматический retry отсутствуют. Завершение фазы ждёт уже отправленные
bounded calls, а не создаёт дополнительные arrivals.

Этот выбор опирается на [open versus closed models k6](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/)
и [constant arrival rate](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/).
[Dropped iterations](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/dropped-iterations/)
нужно учитывать отдельно: ограничение генератора или длительные ответы
могут уменьшить число отправленных запросов. Здесь каждый запланированный
arrival остаётся в denominator, включая неотправленные.

## Измерение двух повторов

Quantiles пересчитаны по pooled raw rows. Latency в таблице относится
только к computed `ok/abstain`; drops не имеют выдуманного HTTP timing,
а `busy` сохраняет настоящий короткий caller interval отдельно.

| Input tokens | Arrivals/с | Client slots | Computed / scheduled | Client drops | Busy returns | Computed caller p50 / p95 / max, мс |
|---:|---:|---:|---:|---:|---:|---:|
| 256 | 0.5 | 1 | 12/12 | 0 | 0 | 165.838 / 243.439 / 243.439 |
| 256 | 1 | 1 | 24/24 | 0 | 0 | 137.397 / 151.313 / 152.639 |
| 256 | 2 | 1 | 48/48 | 0 | 0 | 134.466 / 153.769 / 155.521 |
| 256 | 2 | 2 | 48/48 | 0 | 0 | 136.836 / 156.649 / 161.170 |
| 2048 | 0.5 | 1 | 12/12 | 0 | 0 | 878.606 / 905.380 / 905.380 |
| 2048 | 1 | 1 | 24/24 | 0 | 0 | 853.015 / 874.733 / 875.025 |
| 2048 | 2 | 1 | 24/48 | 24 | 0 | 856.080 / 867.362 / 871.207 |
| 2048 | 2 | 2 | 24/48 | 0 | 24 | 855.258 / 866.355 / 867.017 |

На длинном входе при двух slots общий caller p50 — 1.389 мс из-за быстрых
`busy`, тогда как computed p50 — 855.258 мс. Поэтому низкая latency по всем
HTTP returns не означает достаточной capacity. При одном slot 24/24
отправленных calls вычислены, но это 24/48 scheduled: good/admitted = 100%,
good/scheduled = 50%. Scheduler lag drops отсутствовали, максимальный
dispatch lag по всему опыту — 10.006 мс. Все 216 computed calls вернулись
в пределах выбранного diagnostic threshold 5000 мс.

Короткий вход справился с проверенными 2 arrivals/с; этот результат не
переносится на 2048 tokens. Planning envelope 1 arrival/с / 1 active call
получил ограниченные технические данные; burst tolerance, mixed/primary
load, длительное окно и реальный клиентский поток этим опытом не заданы.

## Перепроверка и сохранение

[Independent verifier](../../../../scripts/verify-decision-arrival-rate.py)
пересчитал 132 arrivals каждого процесса, fixed offsets, отсутствие
catch-up, bounded overlap, request/profile/tokens, timing, phase seals и
каждую journal row. Runtime counters совпали после каждого warmup/phase:
в сумме 248 POST outcomes, 224 computed с учётом 8 warmups и 24 busy.
Все distributions и typed outputs каждого exact input совпали между
warmup, нагрузочными фазами и двумя процессами.

Отдельный audit прошёл 8 checks: два measured source snapshots, immutable
controllers и receipts, pooled denominator, 35 baseline source files,
четыре resident observations и cleanup всех 8 recorded temporary PID.
Resident сохранил четыре process births/bindings, 34 dependencies,
fresh scrape и counters 1/0/0. Записаны 22 phase-boundary health, RSS и
system-memory samples; peaks и причинный эффект wired setter не измерены.

12 targeted tests и полный docs check прошли: 746 Python tests
(4 optional skips), 12 Node checks, process catalog. Первый unit run
выявил две ошибочные fixture expectations; его log сохранён отдельно.
Serving и 35 resident/session sources не менялись.

[Public allowlist summary](./evidence/2026-10-07/arrival-rate-4096/result-summary.json)
содержит counts, timings и source hashes. Private ZIP: 49 files,
40,979,571 bytes, SHA-256
`201f2ac970abcb50eebaf8b5f57045b4dc4dd4f0de7a17c0974bd0a06ceb77a1`.
CRC, SHA и size каждого файла и идентичная копия в исходном workspace
проверены. Архив содержит обе native series, raw memory/counters/journal,
audit, test logs и измеренный Git state; последующие docs/CI в этот архив
не включены.

## Повторение

Сначала нужен committed source, локальный verified manifest и runtime
Python с pinned requirements. Evidence создаётся только в новой ignored
директории под `docs/private`; launcher сам владеет временным runtime.

```bash
python3 scripts/run-decision-arrival-rate.py \
  --evidence-dir docs/private/arrival-rate-new-run \
  --runtime-python .venv/decision/bin/python \
  --manifest .local-models/decisions/decider-2b.json \
  --profile docs/qualification/local-decisions/performance/profiles/runtime-0.12.3-wired-4096.json
python3 scripts/verify-decision-arrival-rate.py \
  --evidence-dir docs/private/arrival-rate-new-run \
  --output docs/private/arrival-rate-new-run/verification.json
```

Actual boot/login, owner/SLO agreement, независимые human reviews,
calibration и holdout остаются открытыми. Repeated synthetic inputs не
образуют независимые случаи качества или customer population. Routing
выключен, qualification `not_assessed`.
