# Preflight после остановки resident служб

05.10.2026 MSK. Реальный первый install/status/stop прошёл, но полный rollout
остановился на повторном preflight с `Address already in use`. Failed report
сохранён. TCP regression воспроизвёл тот же отказ после закрытия соединения;
preflight исправлен для немедленного повторного запуска POSIX listener.
После этого failed опыта jobs отсутствовали. [Полный повтор](./resident-rollout.md)
позднее прошёл и оставил два jobs работающими. Routing выключен, qualification —
`not_assessed`.

## Реальный частичный rollout

Измеренный commit — `720f15218f5aee93bd931254d854761a6ab8aa68`, main после
[manager gate](./resident-deployment.md). Точные profile/model/package SHA
проверялись перед каждой командой. Первый bootstrap установил только два
подготовленных user LaunchAgents, получил прежний health profile 0.12.2,
один правильный свежий scrape target и прирост computed counter после
диагностического решения. `status` подтвердил readiness без нового inference.

| Команда | Исход | Полная длительность, мс |
|---|---|---:|
| Первый check | verified | 5498,240 |
| Первый install | ready | 47735,439 |
| Первый status | ready | 5357,752 |
| stop | stopped | 5446,192 |
| Повторный check | failed, address in use | 5038,262 |

Длительность команд включает повторное чтение/проверку model/package bytes;
это не latency отдельного inference. Диагностическое решение, distribution
и token counts совпали с resident native baseline. Stop подтвердил отсутствие
обоих jobs и завершение четырёх собственных PID; собственные plists удалены,
registration marker архивирован. Bundle, модель и TSDB сохранены.

Независимый verifier сверил seals, все пять report/log hashes, исходники
исторического measured commit, health profile и результат. Он повторно
проверил отсутствие PID/jobs/plists. Исход — `verified_partial_failure`,
что подтверждает сохранённый отказ, а не успешный полный rollout. [Публичная
сводка](../evidence/2026-10-04/service-port-reuse-0.12.2/result-summary.json)
содержит counts и SHA; raw logs, paths, PID и ответы остаются в `docs/private`.

## Исправление проверки порта

Старый probe делал обычный TCP bind. Runtime использует `ThreadingHTTPServer`,
чей HTTP listener допускает address reuse; обычный probe мог отвергнуть
адрес после успешного shutdown из-за оставшегося connection в `TIME_WAIT`.
Последующая попытка снять исходное состояние sockets уже не нашла записей,
поэтому его точное состояние в момент первого отказа не объявляется
наблюдённым. Отдельный настоящий TCP fixture воспроизвёл механизм.

Теперь [manager](../../../../../scripts/manage-decision-resident-deployment.py)
выставляет только `SO_REUSEADDR`, делает bind на точный loopback address
и кратко проверяет listen. `SO_REUSEPORT` не включается. Активный listener
по-прежнему отвергается. Проверки labels, plist paths, package SHA,
ownership, source binding и readiness сохраняются. Использование address
reuse для `TIME_WAIT` соответствует [Python socket documentation](https://docs.python.org/3.13/library/socket.html#socket.create_server).

Новый regression открывает настоящий TCP listener: preflight должен
отклонить его, затем принять тот же address после закрытия accepted
connection и listener. До исправления второй шаг воспроизводил
`OSError: [Errno 48] Address already in use`; после исправления весь test
проходит. 52 целевых lifecycle/builder/launchd/service tests и полный набор
12 Node / 528 Python checks (четыре opt-in skips), process catalog и локальные
ссылки прошли.

На committed fix `b8cb31b8c08de93ec5e9a5914b047b5809ee2895` реальный read-only
preflight снова завершился `verified`. Отдельный verifier пересчитал 33
committed sources и 45 package files, resident модель, seals и проверил
свободные ports и отсутствие jobs/plists. Serving runtime/profile не менялся.
Следующий этап после CI — новый полный install/status/stop/reinstall protocol
с сохранением предыдущего failed report.
