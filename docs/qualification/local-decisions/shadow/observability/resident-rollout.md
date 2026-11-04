# Постоянное локальное наблюдение: проверенный rollout

07.10.2026 MSK постоянные службы переключены на
[wired resident 0.12.3](./resident-wired-rollout-0.12.3.md).
Ниже сохранён исходный rollout 0.12.2 от 05.10.

05.10.2026 MSK. После [исправления TCP preflight](./resident-port-reuse.md)
полный install/status/stop/reinstall завершился и прошёл независимую проверку.
**Два собственных user LaunchAgents установлены и работают** из resident
Application Support bundle. Runtime остаётся 0.12.2, routing выключен,
qualification — `not_assessed`.

## Фактический цикл

Measured commit — `f14d8eaecf429e328d8bdf6bb1bcce935bfe65ae`, main после
PR #119 и 15 успешных CI checks. Все семь команд [manager](../../../../../scripts/manage-decision-resident-deployment.py)
выполнены на этом commit. Каждая перепроверила точный bundle seal, 45
copied/generated files, resident модель, полный profile и 33 committed sources.
Предыдущие failed reports и прежний registration archive сохранены.

| Команда | Исход | Полная длительность, мс |
|---|---|---:|
| check-1 | verified | 5896,359 |
| install-1 | ready | 46054,183 |
| status-1 | ready | 5647,603 |
| stop-1 | stopped | 5968,211 |
| check-2 | verified | 5990,690 |
| install-2 | ready | 27330,931 |
| status-2 | ready | 5162,345 |

Это длительности целых management commands, включая чтение model/package
bytes, source checks и ожидание scrape; они не являются inference latency.
Оба installs получили profile SHA
`81663153638983bd72afd5a31d8871f38c7db064e28a37636a36b780cf0b6cef`.
Оба диагностических результата, distributions и token counts точно совпали
с resident native baseline. Первое observed counter value каждого запуска
было 0; после scoring scraper показал 1. Старый TSDB не подменил новый
scrape: target health был up, `lastScrape` — после начала установки.

Stop удалил оба собственных jobs/plists и подтвердил завершение четырёх
наблюдённых PID. Повторный preflight сразу прошёл. После второго install
два jobs и четыре новых PID работают; первая PID set полностью отсутствует.
Новый stopped marker архивирован рядом с прежним. SHA прежнего archive
повторно сверён; bundle/model/TSDB не удалялись и не заменялись.

Независимый verifier пересчитал protocol/report/log seals и hashes,
исторические/current source bytes, bundle/model/profile, полный typed result,
raw counters и scrape timestamps. Он повторно проверил отсутствие первой
PID set, наличие новой, UID/mode/bytes installed plists и реальные plist paths
в `launchctl print`. Итог — `verified`. [Публичная сводка](../evidence/2026-10-04/resident-rollout-0.12.2/result-summary.json)
содержит только counts, SHA и исходы. Host paths, UID/PID, requests/results,
registration markers и raw API/log bodies остаются в `docs/private`.

## Рабочая конфигурация и управление

| Служба | Рабочая конфигурация |
|---|---|
| `org.agat.decision-shadow` | Foreground, loopback `127.0.0.1:8766`, offline decider-2b, input 2048 tokens, cache 128 МиБ, isolated deadline 5000 мс, exit 75 при backend отказе |
| `org.agat.decision-prometheus` | Foreground, loopback web `127.0.0.1:9095`, scrape/evaluation 15 с, timeout 5 с, private TSDB, retention flags 7 дней / 256 МиБ |

Оба plists находятся в user `Library/LaunchAgents`, принадлежат текущему
UID и имеют режим 0600. `RunAtLoad`, `KeepAlive.SuccessfulExit=false`,
throttle 30 с и exit timeout 30 с соответствуют проверенному bundle.
Точные runtime/model/dependency/binary pins сохранены в [package protocol](./resident-deployment.md).

`status` с bundle path, expected seal и новым private output читает ownership,
реальный supervisor state, health profile и scrape без inference.
`stop` с теми же обязательными аргументами выполняет owned bootout/cleanup,
удаляет свои installed plists и архивирует marker. `check` и `install`
дополнительно требуют private package verification. Новые outputs обязательны;
существующее evidence не перезаписывается. Синтаксис приведён в
[manager instructions](./resident-deployment.md).

После первого stop TSDB содержал два файла: 52 769 logical bytes / 53 248
allocated bytes. После нового reinstall — пять файлов: 88 195 / 90 112 bytes
в момент снимка; работающий TSDB продолжает изменяться. Это малый текущий
sample, а не проверка семидневного retention. [Prometheus storage documentation](https://prometheus.io/docs/prometheus/latest/storage/)
определяет retention size как policy удаления blocks: WAL/head и временная
compaction могут выходить за значение flag. 256 МиБ не объявляются жёсткой
квотой всего directory, семь дней не гарантируются независимо от size policy.

Alert rules и firing thresholds сохраняются. Этот rollout проверяет настоящий
scrape и counters; firing живого alert здесь не испытывался. Alertmanager,
remote write и внешние receivers отсутствуют. Оба jobs одновременно
останавливались в stop-1, поэтому этот цикл не доказывает непрерывное
наблюдение downtime. [Отдельный native recovery опыт](./native-prometheus.md)
уже подтвердил down/up и pending/cleared alert при живом scraper.

LaunchAgents работают в текущем GUI domain. Их последующий boot/login,
обновление внешнего Homebrew Python и многосуточное наблюдение не объявляются
проверенными. [Два bounded совместных прогона Qwen/decider](../../performance/resident-shared-load-0.12.2.md)
затем прошли 480 measured calls с supervisor/retirement/metrics evidence;
службы продолжают работать. Следующий gate — длительный совместный soak,
причина прежнего внепланового exit 75 остаётся открытой. Предметные данные,
independent human reviews, calibration/holdout,
владелец реакций и production SLO остаются открытыми.
