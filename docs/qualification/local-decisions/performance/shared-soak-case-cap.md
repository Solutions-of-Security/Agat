# Согласованная граница числа development cases

05.10.2026 MSK. Перепроверка [shared soak](./shared-soak.md) выявила
несогласованность: controller и документация допускали до 30 cases, но
paired probe отвергал более 240 measured calls. При двух rounds это
допускало только 15 cases. Fixture из 16 cases проходил общий preflight,
затем блок отклонял его 256 calls до первого обращения к сервисам.

[Paired probe](../../../../scripts/lib/decision_shared_load.py) теперь
использует общие constants: максимум 30 cases, два rounds, восемь calls
на case/round, конечный максимум **480 measured calls на блок**.
[Controller](../../../../scripts/lib/decision_shared_soak.py) использует
ту же границу cases. Один round по-прежнему допускает до 240 calls;
закреплённый development корпус из 15 cases с двумя rounds — 240.

Число повторов не повышает concurrency: на каждой модели остаётся один
активный запрос, в overlapping фазе — два HTTP calls к разным моделям.
Budget блока остаётся до 600 секунд, warmup считается отдельно, первый
отказ останавливает нагрузку, retries отсутствуют. [Python ThreadPoolExecutor](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.ThreadPoolExecutor)
ограничивает одновременно исполняемые tasks числом workers; в этом probe
оно остаётся равным двум. Число HTTP workers не доказывает GPU overlap.

Regression воспроизвёл ошибку для 16 и 30 cases до fix. После исправления
оба полных paired fixtures завершились с точными counts: 256 и 480 measured
calls. Fixture из 31 cases отклоняется до health и создания primary.
Полный 30-case controller checkpoint/journal прошёл независимый verifier:
480 measured calls, четыре warmup calls, 60 overlapping pairs. Целевой
shared набор — 47 tests, PASS. Полный native `npm run docs:check` прошёл:
569 Python tests (четыре прежних opt-in skips), 12 Node tests, локальные
ссылки и process catalog.

Все новые inputs — синтетические unit fixtures. Реальные модельные
результаты прежних опытов относятся к закреплённым 15 development cases;
их evidence и budgets не меняются. Это исправление допустимости конечного
плана, без новых заявлений о business quality, throughput или SLO. Serving
profile 0.12.2, deadline, weights и routing не меняются.
