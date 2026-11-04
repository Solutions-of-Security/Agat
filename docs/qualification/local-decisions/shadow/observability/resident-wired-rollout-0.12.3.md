# Постоянный wired resident 0.12.3

07.10.2026 MSK. **Два собственных user LaunchAgents установлены и работают
на runtime 0.12.3 с wired budget 4096 МиБ.** Полный цикл
install/status/stop/reinstall завершился и прошёл отдельный native audit.
Routing выключен, qualification — `not_assessed`.

До переключения прошли [package/recovery gate](../../performance/resident-wired-package-0.12.3.md),
[7200-секундный совместный soak](../../performance/wired-shared-soak-7200-0.12.3.md)
и [реальный Temporal/PostgreSQL/RAG](../../performance/temporal-wired-runtime-0.12.3.md).
Measured commit — `b7d462dda285bde0df444dd0f86be579eaf39906`, main после
[PR #135](https://github.com/Solutions-of-Security/Agat/pull/135) и 15 успешных
CI checks. Runtime и management code в этом этапе не менялись.

## Переключение и полный цикл

Перед остановкой 0.12.2 проверены его bundle/model bytes, registration
ownership и package verification из сохранённого private архива. File SHA
этой verification совпал с [опубликованным package evidence](../evidence/2026-10-04/resident-package-0.12.2/result-summary.json).
Все 15 package checks допускают повторную установку прежнего release.
Та же проверка выполнена для нового wired package; существующие архивы
регистраций сохранены. Ошибок rollout не было, восстановление 0.12.2
не потребовалось.

Каждая команда [manager](../../../../../scripts/manage-decision-resident-deployment.py)
перепроверила seal, resident модель, полный профиль и committed inputs.
Длительности включают чтение package/model bytes и ожидание scrape;
это не inference latency.

| Команда | Исход | Полная длительность, мс |
|---|---|---:|
| old-status | ready | 5452,419 |
| old-stop | stopped | 5818,638 |
| check-1 | verified | 5488,038 |
| install-1 | ready | 32884,817 |
| status-1 | ready | 6281,519 |
| stop-1 | stopped | 6551,134 |
| check-2 | verified | 5016,836 |
| install-2 | ready | 43302,172 |
| status-2 | ready | 6225,663 |

Оба installs получили точный profile SHA
`776fba7fa1244e13292ac3605a53a6d6c6cf557901c8d863cab9489d6d2f47ce`.
Distributions, typed outcomes и token counts обоих диагностических ответов
совпали с native wired baseline: 117 input tokens, 0 generated tokens.
Computed counter каждого нового процесса прошёл 0 → 1. Target был `up`,
без scrape error, с `lastScrape` после начала соответствующего install;
сохранённый TSDB не подменил свежую наблюдаемую готовность.

Остановка прежнего release и первого нового цикла завершила все восемь
наблюдавшихся PID. После второго install два jobs и четыре новых PID
работают; их набор сохранился при отдельном audit. Два прежних stopped
markers сохранили SHA; добавлены архивы прежней активной регистрации и
первого нового цикла. Старый package/model и новый package/model не
удалялись и не менялись.

## Проверка результата

Отдельный auditor пересчитал seals и file SHA protocol, девяти receipts,
command logs и восьми log snapshots. Проверены 34 различных committed
inputs, 41 различный copied/generated file каждого package, model bytes,
published package bindings, полный typed result, raw counters и scrape
timestamps. Installed plists имеют UID текущего владельца, mode 0600,
точные bytes/configs и ожидаемые пути в `launchctl print`. Итог —
**16 checks, все `true`; `verified`.**

[Публичная сводка](../evidence/2026-10-07/resident-rollout-0.12.3/result-summary.json)
содержит только counts, timings, публичные source hashes и исходы. Raw
requests/results, UID/PID, local paths, registration markers, API bodies,
измеренный Git source и исходы обеих попыток audit сохранены в `docs/private`.
Private архив проверен по CRC и SHA/size каждого включённого файла.

Локальные проверки прошли: 61 существующий manager/builder/launchd/service
test, 12 Node checks, 2352 локальных link targets в 260 Markdown files и
process catalog. Полный required CI выполняется отдельно перед merge этапа.

## Постоянная конфигурация

| Служба | Проверенная конфигурация |
|---|---|
| `org.agat.decision-shadow` | Foreground, loopback `127.0.0.1:8766`, offline decider-2b, input 2048 tokens, cache 128 МиБ, wired 4096 МиБ, isolated deadline 5000 мс, exit 75 при backend отказе |
| `org.agat.decision-prometheus` | Foreground, loopback `127.0.0.1:9095`, scrape/evaluation 15 с, timeout 5 с, private TSDB, retention flags 7 дней / 256 МиБ |

Оба owned plists находятся в user `Library/LaunchAgents`; `RunAtLoad`,
`KeepAlive.SuccessfulExit=false`, throttle 30 с и exit timeout 30 с
сохранены. Bundle seal нового release —
`2f2faa7cbce6bdf5df20c86f5d2109cf8ac8fe58277b3a99c526bd215be1c5c2`.
[Manager instructions](./resident-deployment.md) применяются к новому bundle:
`status` читает готовность без inference, `stop` завершает только свои jobs
и архивирует registration marker. `check/install` требуют соответствующую
private package verification и новый output. Для возврата на 0.12.2 сначала
выполняется owned stop нового release, затем check/install прежнего с его
сохранённой verification; оба релиза используют те же labels/ports.

[Apple launchd guide](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)
описывает user agents в GUI login session. Текущий bootstrap/reinstall
не объявляется фактическим boot/login. Оба jobs останавливались вместе,
поэтому непрерывное наблюдение downtime в этом цикле не проверено;
[отдельный package gate](../../performance/resident-wired-package-0.12.3.md)
сохраняет доказательство настоящих down/up и pending/cleared alert.

Следующие gates — boot/login, bounded crash-loop, owner/SLO и независимые
предметные данные с human reviews, calibration и holdout. Два development
результата подтверждают преемственность вычисления; correctness и пригодность
к автоматической маршрутизации этим rollout не квалифицированы.
