# Native gate resident wired package 0.12.3

05.10.2026 MSK. **Новый пакет с wired budget 4096 МиБ прошёл native
launch/recovery gate и независимую проверку исходных evidence.** Постоянный
resident 0.12.2 восстановлен; новый release ещё не зарегистрирован.

[Подготовленный bundle](./resident-profile-selection.md) имеет seal
`2f2faa7cbce6bdf5df20c86f5d2109cf8ac8fe58277b3a99c526bd215be1c5c2`
и profile SHA
`776fba7fa1244e13292ac3605a53a6d6c6cf557901c8d863cab9489d6d2f47ce`.
Preparation commit — `9662f9d1597e06221e81ad35b46ca157b7b594b2`, native
harness commit — `4f9c53eae85aed01369ce97efe8155d341750714`. Все serving
source bytes совпали на обоих commits; между ними добавлена документация.

До опыта проверены прежние bundle/registration/plists и fresh scrape.
Остановлен только собственный resident decider, его три PID завершились.
Prometheus оставался запущенным. [Native probe](../../../../scripts/check-decision-launchd.py)
загрузил новый package через его отдельный venv и уникальный временный
LaunchAgent label, с explicit `--wired-limit-mib 4096` и deadline 5000 мс.

| Наблюдение | Результат |
|---|---:|
| Native starts / scored calls | 2 / 2 |
| SIGKILL собственного inference child → exit 75 | 427,302 мс |
| SIGKILL → новый ready runtime | 23719,925 мс |
| Полный native probe, включая scraper и cleanup | 53504,641 мс |
| Metric snapshots / decision series в каждом | 4 / 46 |
| API queries / query-range points | 51 / 29 |
| Временные PID, проверенные после cleanup | 7, все отсутствуют |

Оба старта вернули полный expected profile. Два решения совпали между
собой и с сохранённым native baseline 0.12.2 по статусу, причине, выбору,
значению, logits/probabilities и token counts. Это две проверки прежнего
diagnostic input; [30-call pilot](./wired-memory-budget.md) описан отдельно.

Настоящий временный scraper подтвердил counters 0 → 1 → 0 → 1, смену
server start time, `up` 1 → 0 → 1, down target и pending/cleared alert.
Query-range содержит 29 пересэмплированных points, из них 22 down; это
не число scrapes. Retirement event содержит exit 75 и child exit -9.

Независимая проверка заново пересчитала seals, historical/current source
SHA, bundle/model bytes, pinned archive/binaries, exact environment pins,
raw API counters/history/alerts, ответы и retained logs/plist. Полный
package verification содержит все 15 требуемых checks. Отдельный audit
перепроверил driver plan/protocol, отсутствие временного job и PID,
восстановление старого profile со свежим scrape и прежний Prometheus PID.
[Public allowlist summary](./evidence/2026-10-05/resident-package-0.12.3-wired-4096/result-summary.json)
сохраняет aggregates/fingerprints; raw paths, commands и API bodies private.

Serving code после предыдущих 584 Python / 12 Node checks не менялся;
документ и локальные ссылки проверены дополнительно.
[Короткая совместная Qwen/decider нагрузка](./wired-package-shared-load-0.12.3.md)
также прошла после исправления collector. Следующие gates — постоянный
rollout после CI и полный 7200-секундный shared soak. Boot/login,
crash-loop, owner/SLO и предметная qualification открыты. Recovery timing
не является production SLO; routing выключен, qualification `not_assessed`.
