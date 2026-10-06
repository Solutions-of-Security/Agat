# Метрики локального decision runtime

26.09.2026. Runtime **0.12.0** экспортирует Prometheus-метрики через `GET /metrics` на прежнем loopback-порту. Реальный MLX-прогон, официальный парсер и проверки правил прошли. Конфигурация подготовлена для оператора; постоянный scraper, Alertmanager и получатели уведомлений не устанавливались. Автоматическая маршрутизация остаётся выключенной.

## Контракт экспорта

Экспорт имеет формат Prometheus text `0.0.4`, пять семейств и **46 рядов**, включая нулевые значения до первого запроса. Имена и значения labels заданы кодом. State, question, request ID, profile SHA, тексты исключений и пути к весам в labels не попадают. Runtime использует стандартную библиотеку Python; пакет `prometheus-client` нужен только независимой проверке сохранённого экспорта.

| Метрика | Семантика |
|---|---|
| `agat_decision_backend_ready` | `1`, если backend операционно доступен, в том числе когда занят; `0` после отказа/отмены. Это не проверка правильности ответа, свободного inference-слота или завершённого reap процесса |
| `agat_decision_requests_in_progress` | Число обрабатываемых `POST /v1/decisions`, включая чтение тела и ответ; при параллельных отклонениях может быть больше одного |
| `agat_decision_server_start_time_seconds` | Unix-время создания HTTP-сервера; счётчики сбрасываются при его пересоздании |
| `agat_decision_requests_total{outcome}` | Завершённые HTTP handlers по логическому исходу, даже если клиент уже закрыл соединение |
| `agat_decision_request_duration_seconds{class}` | Histogram длительности server handler: тело, inference/отказ, остановка при отмене и запись ответа; без установки соединения и записи observation в coordinator |

Histogram экспортируется стандартными `_bucket`, `_sum`, `_count`. Верхние границы: 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10 секунд и `+Inf`. Классы разделяют вычисления и быстрые отказы:

| Class | Outcome |
|---|---|
| `computed` | `ok`, `abstain` |
| `rejected` | `busy`, `invalid`, `profile_mismatch`, `context_rejected` |
| `failed` | `backend_error`, `timeout`, `cancelled`, `unavailable` |

`ok` означает принятие по текущим порогам; correctness здесь неизвестен. Серверный `timeout` включает deadline inference и timeout чтения тела. Клиентский timeout с opt-in закрытием соединения наблюдается сервером как `cancelled`; worker сохраняет собственный исход `timeout`. Метрики отражают разные точки наблюдения без подмены одной другой.

`GET /health` и `/metrics`, неизвестные пути и запросы, отклонённые HTTP-парсером до handler, не увеличивают счётчики решений. Экспорт возвращает HTTP 200 с `backend_ready=0`, пока HTTP-сервис жив; `/health` в этом состоянии возвращает 503. С `--exit-on-backend-unavailable` окно перед exit 75 короткое и может не попасть между scrapes. Недоступность самого endpoint проверяется стандартным `up` scraper, а не последним сохранённым значением readiness.

Host/Origin-защита и привязка к `127.0.0.1` действуют также на `/metrics`. Чтение экспорта не вызывает модель, не занимает inference-lock и не меняет профиль. При отсутствии подходящего `is_available` прямой backend сообщает операционную готовность по прежней семантике; это не активный пробный inference.

## Подключение и запросы

[Пример Prometheus](./prometheus.example.yml) опрашивает `127.0.0.1:8766` каждые 15 секунд с timeout 5 секунд. Scraper должен работать в том же сетевом пространстве: loopback контейнера не является loopback Mac. Endpoint не нужно публиковать в общую сеть. [Правила](./alerts.yml) подключаются относительно каталога конфигурации.

Пример p95 только для вычисленных запросов:

```promql
histogram_quantile(0.95,
  sum by(instance, le) (
    rate(agat_decision_request_duration_seconds_bucket{job="agat-decision",class="computed"}[5m])
  )
)
```

Это приближение по buckets; рядом нужно показывать число вычислений за окно. Два вызова из функционального опыта не определяют p95 или SLO. Busy считается отдельно:

```promql
sum by(instance) (rate(agat_decision_requests_total{job="agat-decision",outcome="busy"}[5m]))
/
sum by(instance) (rate(agat_decision_requests_total{job="agat-decision"}[5m]))
```

При нулевом потоке доля не определена; её не следует изображать как подтверждённые 0% отказов. `rate`/`increase` учитывают сбросы счётчика. Из этих метрик нельзя вычислять accuracy, confidence calibration или стоимость правильного решения.

| Пример alert | Условие и задержка |
|---|---|
| `AgatDecisionEndpointUnavailable` | Scrape `up=0` в течение 2 минут |
| `AgatDecisionBackendUnavailable` | Scrape успешен, readiness `0` в течение 30 секунд |
| `AgatDecisionSustainedBusy` | Busy больше 20% на окне 5 минут при хотя бы 20 запросах; условие держится 2 минуты |

Пороги — начальные примеры, а не согласованные эксплуатационные SLO. При удалении scrape target серия `up=0` не появляется: управление списком целей и исчезновение самого Prometheus требуют отдельного мониторинга. Внешний manager должен ограничивать restart rate и проверять readiness после загрузки. При деградации shadow основной workflow сохраняет прежний маршрут.

## Проверка на реальном MLX

[План](../evidence/2026-09-26/metrics-plan.json) записан до inference: два прежних синтетических входа 512/2048 токенов, decider-2b, cache 128 МиБ, серверный deadline 5 секунд. Профиль — `4bd6e0de2bfde982d8d5fbdfc4d5e7ef36ccd1d8bb69356cd558a33934cb7a2a`, implementation SHA — `29d5e463a2f16d89a220ed00ccde17a0ecfce80e060234f599e135a7034eafa8`. Изменение исходников даёт новый профиль; прежняя калибровка автоматически не переносится.

[Прогон](../evidence/2026-09-26/metrics-result.json) завершён за **8.953 секунды**, включая загрузку и cleanup. Итог: `ok=2`, `invalid=1`, `profile_mismatch=1`, `busy=1`, `cancelled=1`, `unavailable=1`; остальные счётчики равны нулю. Histogram counts: computed 2, rejected 3, failed 2. Успешные HTTP-вызовы заняли 828.302 и 884.758 мс, busy — 0.625 мс. Busy не улучшает histogram вычислений.

Во время длинного вызова экспорт оставался доступен и показывал один активный handler. Worker timeout 100 мс отменил следующий inference; readiness перешёл в 0, health — в 503, новый запрос получил `backend_unavailable`. Дочерний процесс завершился с `inference_cancelled`, кодом -15. Независимая проверка подтвердила отсутствие обоих собственных PID.

[Измеритель](../../../../../scripts/check-decision-metrics.py) проверяет каждый полученный scrape официальным `prometheus-client 0.26.0`. Wheel проверен по SHA-256 `fa93d06737aa02bacd05794768508bb97d2fbee28cb3bca04eaae92f0ca953d6` и импортируется только измерителем. [Отдельный verifier](../../../../../scripts/verify-decision-metrics.py) повторно разобрал семь сохранённых wire-снимков, проверил точный набор labels, журнал запросов, cumulative buckets, границы сумм, хронологию, input/profile/code bindings и cleanup. [Результат](../evidence/2026-09-26/metrics-verification.json) — `verified`.

[Тесты правил](./alerts.test.yml) покрывают задержку срабатывания, недоступность backend при живом HTTP, подавление дублирующего alert при неуспешном scrape, краткое восстановление, малый поток, высокую долю busy, reset и abstain. `promtool 2.55.1` проверил пять групп тестов, синтаксис конфигурации и три правила в одноразовом контейнере без сети. Версия, image ID, hashes и вывод сохранены в [протоколе](../evidence/2026-09-26/metrics-promtool.json).

Восемь новых runtime-тестов проверяют конкурентные счётчики, границы buckets, отсутствие данных в labels, доступность экспорта при busy/отказе, исходы HTTP и Host/Origin guards. Полный `npm test`: 206 coordinator, 52 web, 45 worker (1 необязательный skip), 113 runtime (3 необязательных skip), Temporal/replay и process pack — pass. [Протокол регрессии](../evidence/2026-09-26/metrics-checks.json) фиксирует проверки и исходники.

## Воспроизведение

[Native Prometheus 3.13.4](./native-prometheus.md) прошёл 04.10 реальный
scrape/recovery опыт: loopback, полный профиль 0.12.2, counters/reset,
pending/cleared alert и owned cleanup. [Resident bundle](./resident-deployment.md)
подготовлен и прошёл отдельный native gate; manager прошёл ownership/cleanup
tests. [Первый install/status/stop](./resident-port-reuse.md) прошёл, повторный
preflight выявил и воспроизвёл ошибку address reuse; fix проверен.
[Полный повтор постоянного rollout](./resident-rollout.md) прошёл: два user
LaunchAgents работают из resident bundle, scrape и counters проверены.
07.10 MSK [постоянный wired rollout 0.12.3](./resident-wired-rollout-0.12.3.md)
переключил оба jobs на профиль с wired budget 4096 МиБ; два installs,
stop/reinstall и отдельный native audit прошли. Boot/login и согласование owner/SLO остаются открытыми.

[Bounded crash-loop gate](./resident-crash-loop.md) выполняется отдельным
временным job с тремя отказами, четырьмя поколениями и 30-секундным stable
окном. Native wired gate и отдельный audit прошли: 30/30/30 секунд между
стартами, точный baseline, все 13 временных PID/job очищены; постоянный
resident сохранил PID/profile/counters и свежий scrape.

[Приёмка boot/login](./resident-boot-login.md) имеет отдельный
readonly CLI: baseline file SHA, реальная OS/GUI session identity, времена
начала процессов, точный environment и прежняя registration привязка.
Требуемый event должен наблюдаться фактически; прежняя сессия возвращает
`awaiting_event` с exit 2.

[Однократный login collector](./resident-login-observer.md) подготавливает
frozen baseline source в Application Support и проверяет boot/login из
отдельного `RunAtLoad`, `KeepAlive=false` job. Pending observation остаётся
pending; snapshot и его Git objects не зависят от временного checkout.

```bash
.venv/decision/bin/python scripts/check-decision-metrics.py \
  --manifest .local-models/decisions/decider-2b.json \
  --policy docs/qualification/local-decisions/policy.shadow.v1.json \
  --source-plan docs/qualification/local-decisions/performance/evidence/2026-09-26/robustness-plan.json \
  --parser-wheel /absolute/path/prometheus_client-0.26.0-py3-none-any.whl \
  --plan-output docs/qualification/local-decisions/shadow/evidence/new-run/metrics-plan.json \
  --output docs/qualification/local-decisions/shadow/evidence/new-run/metrics-result.json

promtool check config docs/qualification/local-decisions/shadow/observability/prometheus.example.yml
promtool test rules docs/qualification/local-decisions/shadow/observability/alerts.test.yml
```

Артефакты создаются эксклюзивно. Проверка сохранённых результатов требует версии runtime и harness, записанной в плане. На целевом сервере нужно отдельно проверить выбранную версию Prometheus, подключить scraper и определить владельца реакций. [Нативное восстановление 0.12.2](../native-launchd-0.12.2.md) подтверждено 04.10 из resident checkout вне Documents; настоящий scraper и постоянная регистрация затем проверены на этом Mac. Владелец реакций и production SLO пока не согласованы.

Решения опираются на официальные рекомендации [instrumentation](https://prometheus.io/docs/practices/instrumentation/), [naming](https://prometheus.io/docs/practices/naming/), [формат экспорта](https://prometheus.io/docs/instrumenting/exposition_formats/), [Python parser](https://prometheus.github.io/client_python/parser/) и [unit testing rules](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/), проверенные 26.09.2026. Ограничение labels и раздельная latency отклонённых запросов выбраны по фактическому устройству этого runtime.
