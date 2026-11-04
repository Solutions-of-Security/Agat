# Timeout инспекции процессов в полном wired soak

06.10.2026 MSK. **Исходный 7200-секундный gate 0.12.3 завершился failed:
collector не получил metadata процессов за две секунды.** Все сохранённые
668 model calls вычислены; это неполный прогон, не принятый полный gate.

После [двух коротких повторов](./wired-package-shared-load-0.12.3.md)
новый sealed wired-4096 package снова запущен через собственный временный
LaunchAgent. Измеренный commit —
`7c302d54d0fd266b583b8de37897bd19abb9848b`; profile SHA —
`776fba7fa1244e13292ac3605a53a6d6c6cf557901c8d863cab9489d6d2f47ce`.
Модель, max input 2048, cache 128 МиБ, server deadline **5000 мс**,
HTTP timeout 10000 мс и прежние 15 development inputs закреплены.
Primary Qwen3 8B: прежний digest, Ollama 0.35.1, context 8192,
predict 128, seed/temperature 0, think=false; один loaded model и parallel
slot. Bytes нативного Ollama runner совпали до/после опыта.

| Наблюдение | Исходный результат |
|---|---:|
| План / измеренное время без warmup | 7200 с / 291,894189 с |
| Полные блоки / остановленный блок | 2 / 3-й |
| Measured / отдельные warmup calls | 656 / 12 |
| Успешные / failed calls, включая warmup | 668 / 0 |
| Фактические HTTP-overlapping pairs | 88 |
| Exact pair journal / успешные observations | 484 / 132 |
| Временные PID, отсутствие которых проверено | 6 |

В третьем блоке после 28 overlapping pairs вызов `/bin/ps`, читающий
PID/PPID/command двух candidate children, превысил `timeout=2` и выбросил
`TimeoutExpired`. Observer отменил benchmark, который сохранил уже
завершённые calls и partial block; outer protocol имеет `status=failed`.
Согласно [Python subprocess](https://docs.python.org/3.13/library/subprocess.html#subprocess.TimeoutExpired),
это истечение ожидания дочерней команды. В этом случае команда — `ps`;
исключение не является свидетельством timeout inference. Причина задержки
metadata на этом хосте не установлена.

Полный offline verifier отказал на исходном launcher с причиной
`Launcher is incomplete or failed`. Duration/plan не изменены; cancelled
prefix не превращён в успешный gate. Отдельный audit сверил seals,
committed sources, все inputs/outcomes, phase prefixes, timing/overlap,
token bounds/distributions и journal. Decision signatures и полный profile
стабильны в сохранённых rows/observations; retirement events отсутствуют.
Сохранённые Prometheus samples имеют нулевые failed/rejected counters.
Final complete counter snapshot не получен, поэтому полный counter delta
не заявляется. Observations не доказывают непрерывную стабильность между
ними. Primary residence records сохранили digest/context; API size и
size_vram в этом опыте — 6 528 046 202 bytes, без причинных выводов из
отличия от предыдущего короткого запуска.

Cleanup завершён: временный label/PID отсутствуют, старый resident 0.12.2
восстановлен с точным profile и fresh scrape; прежний Prometheus PID
сохранился. Новый package остаётся незарегистрированным. Исходный driver,
protocol, rows, logs, verifier и measured Git source сохранены в private
архиве; CRC, каждый file SHA/size и неизменность источника перепроверены.
[Public allowlist summary](./evidence/2026-10-06/wired-soak-collector-failure-0.12.3/result-summary.json)
содержит проверенные counts/bindings и исходный failed статус.

`child_processes` теперь принимает explicit integer `timeout_s` от 1 до
30 секунд на каждую из двух metadata команд. Прежний default 2 секунды
сохранён; новый полный controller может закрепить 10 секунд в новом plan.
Timeout/query failure по-прежнему прерывают проверку, а ownership/role
guards действуют при любом budget. В tests реально задержанная на 2,25 с
команда отказала с default и вернула проверяемые metadata при explicit
10 с; дополнительно проверены invalid budgets до запуска команд,
propagation отказов и отказ на чужой parent/неизвестный child. Serving
deadline/profile этим tooling изменением не меняются.

Целевой recovery набор — 6/6; новый opt-in contract воспроизведён на
исходном helper до изменения. Полный `docs:check` прошёл: 589 Python tests
(четыре platform skips), 12 Node tests, 2307 local link targets и generated
process catalog. Нативный прогон и delayed subprocess использовали
Python 3.13.12; локальный Node 25.8.0, обязательный CI использует Node 24.

Следующий этап — новый полный опыт с отдельно закреплённым inspection
budget и новым immutable evidence. Qualification, calibration, owner/SLO
и boot/login acceptance остаются открытыми; routing выключен,
`not_assessed`. Постоянный rollout 0.12.3 этим этапом не выполнен.
