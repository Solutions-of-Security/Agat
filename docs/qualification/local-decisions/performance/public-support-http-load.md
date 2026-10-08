# Полный public development inventory через локальный HTTP

Этот этап проверяет исполнимость и caller latency [закреплённого корпуса](./public-support-context.md)
на том же profile 0.12.3 / wired 4096 МиБ / 2048 tokens. Предметных эталонных
меток ещё нет: вычисленный выбор модели не становится правильным ответом.

## Закреплённый протокол

[Launcher](../../../../scripts/run-public-support-load.py) требует независимый
SHA raw `context-profile.json`, проверяет его seal, исторические Git sources,
все исходные bindings и точные зависимости native Python. Весь development
inventory содержит 49 вопросов в прежнем порядке из 44 групп; calibration и
holdout остаются вне модельных вызовов. Неполный inventory отклоняется.

До запуска сохраняется sealed plan с committed sources и profile/model pins.
Launcher создаёт свой runtime на отдельном случайном loopback port, читает
существующие verified weights offline и после измерений закрывает только
собственные процессы. Два явно учтённых warmup используют первый подходящий
вопрос; в scheduled denominator они не входят.

Фиксированные offsets — `0, 2, …, 96` секунд: 49 arrivals при 0.5/с в
номинальном окне 98 секунд, один client slot, caller timeout 10000 мс,
engineering threshold 5000 мс, max scheduler lag 100 мс. Завершение последнего
request определяет observed elapsed, а не гарантированные 98 секунд. Занятый
слот, опоздание и cancellation сохраняют строку с исходным case/input SHA.
Retry, ожидание свободного slot и догоняющий burst отсутствуют.

Все 49 полных запросов сохраняются в plan. Три входа длиннее 2048 токенов
передаются целиком для проверки `context_too_long`; truncation и translation
не применяются. Если эти arrivals пропущены scheduler, пропуски видны в общем
знаменателе; доказанного runtime rejection для них тогда нет. Computed ответ
требует совпадения runtime inputTokens с предшествующим точным token inventory.
Неподтверждённое поведение на over-limit input делает run failed.

Caller clock и boundary берутся из настоящего worker HTTP client. Summary
сохраняет все scheduled arrivals, computed/abstain/errors/drops, p50/p95/max
измеренных calls и отдельные computed timings. `goodPerScheduled` учитывает
ошибки и пропуски; быстрый отказ не увеличивает число успешных вычислений.
Сырые journal, runtime log, health/profile, metrics и owned PID inventory
остаются private, вместе с seals и file SHA.

Здесь не запускается собственный primary companion. Фоновая нагрузка машины
не контролируется; причинную оценку overhead или production capacity этот
протокол не даёт. Bootstrap/login не инициируются, resident service получает
только read-only проверки. Routing выключен, qualification `not_assessed`,
business SLO не принят.

## Обоснование и проверка

Open arrivals и явные dropped iterations согласуются с официальными
[constant arrival rate](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/)
и [dropped iterations](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/dropped-iterations/)
Grafana k6: расписание задаётся независимо от response time, нехватка исполнителя
не скрывается замедлением arrivals. Это ограниченный Python probe, а не k6 run.
Документы прочитаны 08.10.2026.

Семь новых regression tests проверяют whole-inventory bindings, строгие типы
расписания, короткие variable inputs, реальные блокировки client slot,
cancellation/lag, over-limit rejection и ранний отказ CLI до Popen. Новый
набор вместе с context и существующими arrival tests — 26 checks.

## Native результат 08.10 MSK

Из commit `a642578` выполнен отдельный owned runtime: **49/49 arrivals admitted**,
**46 computed** (31 `ok`, 15 `abstain`), три `context_too_long`; drops,
measurement errors и остальные runtime failures — 0. Два warmup сохранены
отдельно. Physical metrics подтверждают 51 POST handler: 48 computed и три
context rejection. Calibration/holdout calls и reference labels — 0.

Caller timings всех 46 computed: p50 **293.887 мс**, p95 **637.588 мс**,
max **719.935 мс**; все 46 уложились в engineering threshold 5000 мс.
Observed phase elapsed — 96439.040 мс; max dispatch lag — 46.920 мс.
Показатель по полному scheduled inventory — **46/49 = 0.9387755**; по заранее
выделенным context-eligible случаям — **46/46**. Эти два знаменателя нельзя
заменять друг другом или использовать для утверждения принятого SLO.
Abstain здесь означает корректно вычисленное распределение/отказ политики,
а не проверенный человеком правильный ответ или разрешённую маршрутизацию.

Независимая сверка прошла **701 checks**: seals/raw pins, historical/current
57 load sources, все 49 ordered journal rows, input/profile/token bindings,
softmax/policy semantics, nearest-rank quantiles, полные знаменатели,
metrics и отдельный warmup. Все три observed temporary PID отсутствуют.
Read-only resident recheck подтвердил прежние четыре PID, 35 protected
sources, profile/registration/plists и counters **1 computed / 0 rejected /
0 failed**. Actual boot/login event по-прежнему не наблюдался.

Полный docs check: **820 Python tests** / четыре expected optional skips,
**12 Node tests**, local links и process catalog — pass.
[Allowlisted summary](./public-support-http-load-summary.json) сохраняет
counts, timings и evidence pins; исходные вопросы/ответы остаются private.
[Проверенный evidence archive](./public-performance-archive-summary.json)
сохраняет raw API/import/context/load, independent audit, resident checks,
test logs и четыре Git states. CRC/SHA/size каждого файла повторно проверены
после новой копии в `docs/private` исходного workspace; предыдущие архивы
не заменяются.
Следующий инженерный шаг — повторяемая source-bound проверка такого evidence
и реальный public inventory при явно учтённом primary workload. Human review,
calibration/holdout и real permitted workflow/owners/SLO остаются внешними gates.
