# Проверка настоящего native scraper при восстановлении runtime

04.10.2026. **Настоящий native scrape/recovery опыт прошёл** на профиле 0.12.2.
Opt-in режим [нативного launchd probe](../service-recovery.md):
настоящий Prometheus опрашивает принадлежащий этому опыту runtime на loopback,
сохраняет запросы HTTP API, counters и состояние alert до отказа и после
автоматического restart. Ниже — закреплённые условия и фактический результат
с отдельной перепроверкой.

## Закреплённый scraper

Выбран [Prometheus 3.13.4](https://github.com/prometheus/prometheus/releases/tag/v3.13.4),
официальный patch release 02.10.2026 для Darwin/arm64. По [LTS policy](https://prometheus.io/docs/introduction/release-cycle/)
ветка 3.13 поддерживается до 31.07.2027; прежняя 3.5 завершила поддержку
в июле 2026. Версия выбрана по текущим release metadata, без изменения MLX
dependencies, модели или serving profile.

[Release pin](./prometheus-3.13.4.json) содержит URL и SHA256 официального
архива и извлечённых `prometheus`/`promtool`. Архив скачивается по HTTPS,
его SHA сравнивается с release metadata, extraction использует безопасный
tar filter. Probe до запуска проверяет executable bytes, версию и платформу,
а затем конфигурацию и прежние alert unit tests через тот же `promtool`.
SHA проверяет целостность содержимого и не является цифровой подписью.

Prometheus 3 [нормализует числовые `le` labels](https://prometheus.io/docs/prometheus/latest/migration/),
например `1` → `1.0`. Verifier проверяет числовые границы histogram,
сохраняя исходные labels в приватном artifact. Прежние cumulative buckets
и счётчики не меняются. Exporter уже отдаёт `text/plain;version=0.0.4`,
поэтому fallback для отсутствующего Content-Type не включается.

## Контролируемая последовательность

1. Создать новый каталог в игнорируемом `docs/private`, проверить committed
   harness и полный expected профиль 0.12.2 до scoring/fault injection.
2. Запустить owned Prometheus на выделенном `127.0.0.1` порту, с собственным
   TSDB, retention 2 часа / 128 МБ. Scrape/evaluation — 2 секунды, timeout —
   1 секунда для короткого диагностического опыта. Существующие alert пороги
   сохраняются; постоянный пример продолжает использовать 15 секунд.
3. Дождаться настоящего scrape: `up=1`, readiness=1, 46 рядов, нулевые
   исходные counters. После одного решения — computed count=1, rejected=failed=0.
4. SIGKILL только собственного inference ребёнка. Подтвердить exit 75,
   `up=0`, target health=down и pending `AgatDecisionEndpointUnavailable`.
5. Дождаться нового launchd процесса с тем же полным profile. Проверить
   новый server start time и counters=0; после второго решения — computed=1.
   Alert должен исчезнуть; сохранённая `up` history должна содержать 1 → 0 → 1.
6. Удалить только временный job, отдельно проверить все runtime PID,
   завершить owned scraper и убедиться в исчезновении его PID. При ошибке
   или SIGTERM сохранять failed report и диагностику.

[HTTP API Prometheus](https://prometheus.io/docs/prometheus/latest/querying/api/)
используется для instant queries, target status, alerts и range history.
Пустая серия не означает нулевой счётчик или здоровый endpoint. Новый scrape
после restart проверяется по server start time; прежние сохранённые counters
не принимаются за новое состояние. Чужие targets, request/document labels,
нечисловые samples, дубли, нарушение cumulative buckets или counts отвергаются.

```bash
# Сначала commit исходников и release pin; новый evidence directory ещё не существует.
.venv/decision/bin/python scripts/check-decision-launchd.py \
  --python .venv/decision/bin/python \
  --manifest .local-models/decisions/decider-2b.json \
  --policy docs/qualification/local-decisions/policy.shadow.v1.json \
  --request docs/qualification/local-decisions/request.example.json \
  --expected-profile docs/qualification/local-decisions/performance/profiles/runtime-0.12.2.json \
  --inference-timeout-ms 5000 \
  --prometheus /absolute/path/prometheus-3.13.4.darwin-arm64/prometheus \
  --promtool /absolute/path/prometheus-3.13.4.darwin-arm64/promtool \
  --evidence-dir docs/private/new-run/native-monitoring \
  --output docs/private/new-run/native-monitoring/result.json
```

## Проверенный результат

Измеренный harness — `ff7b2c1e610b71f799951b7daa880b59e3e29a84`, поверх
main `62812e193db07bfa5dec130163245f830b134830` после native gate.
Восемь committed files связывают probe, helpers, release pin и alert rules/tests.
Runtime implementation и [профиль 0.12.2](../../performance/profiles/runtime-0.12.2.json)
с SHA `81663153638983bd72afd5a31d8871f38c7db064e28a37636a36b780cf0b6cef`
сохранились до/после restart. Binary bytes совпали с закреплённым архивом.

| Наблюдение | Результат |
|---|---:|
| Полный launcher, включая scraper startup и cleanup | 50485,467 мс |
| SIGKILL ребёнка → runtime exit 75 | 512,825 мс |
| SIGKILL → новый ready runtime | 24423,959 мс |
| Snapshots / series в каждом | 4 / 46 |
| Computed counters до/после первого scoring, затем после restart/scoring | 0 → 1 → 0 → 1 |
| Rejected / failed counters | 0 / 0 в обоих runtime |
| Сохранённые HTTP API queries | 47 |
| `up` query_range points, шаг 1 секунда / down points | 30 / 24 |

Query-range points являются пересэмплированной history, а не числом
независимых scrapes. Scraper непосредственно записал target health=down
с transport error; endpoint alert стал pending и исчез после recovery.
Server start time изменился, счётчики относятся к новому процессу.
В обоих стартах один диагностический input получил одинаковый typed result
и полное распределение. Prometheus завершился штатно с exit 0.

Отдельная проверка пересчитала sealed report, committed source/binary/archive
SHA, retained files, plist, оба request/result binding и retirement event.
Из сохранённых API-ответов заново извлечены counters/reset, `up` sequence,
target error и pending/cleared alert. Временная регистрация отсутствует,
все **семь** собственных PID остановлены. [Публичная сводка](../evidence/2026-10-04/native-prometheus-0.12.2/result-summary.json)
сохраняет только counts, timing, SHA и проверки. API bodies, targets, PID,
logs и TSDB остаются в игнорируемом `docs/private`.

34 целевых tests прошли; полный `docs:check` — 12 Node checks,
497 Python tests (четыре opt-in skips), 13 process templates. Pinned promtool
проверил generated config и прежние пять групп alert tests. Нормализация
`le` подтверждена реальным Prometheus 3.13.4 и regression tests.

## Следующий этап и границы

Этот опыт ограничен одной текущей GUI-сессией, одним диагностическим input
и временным scraper. Production SLO, correctness, confidence calibration,
boot/login и crash loop из этих counters не выводятся. Live pending alert
и его снятие до прежних двух минут не доказывают firing; его временные
условия проверяются отдельными прежними promtool unit tests. Постоянное
[Resident bundle](./resident-deployment.md) подготовлен и проверен реальным
native gate; его постоянная регистрация и владелец реакций остаются
следующим этапом после CI. Routing выключен,
qualification — `not_assessed`.
