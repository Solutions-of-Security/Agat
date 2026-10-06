# Bounded crash-loop wired resident

07.10.2026 MSK. Следующий эксплуатационный gate проверяет три управляемых
отказа собственного inference child, четыре автоматических поколения
launchd и 30 секунд стабильной работы последнего процесса. Native gate
пока не заявляется пройденным; fixtures проверяют admission и verifier.
Routing выключен, qualification — `not_assessed`.

## Границы и конфигурация

`scripts/check-decision-crash-loop.py` принимает действующий sealed resident
bundle и его независимо закреплённый seal. До регистрации проверяются
package/model bytes, committed measurement sources, точные dependency pins,
ownership двух постоянных jobs и свежий scrape. Runtime конфигурация
копируется из действующего bundle: меняются только временные label, port и
log paths. Model, Python, offline environment, policy, deadline и wired
limit сохраняются. Постоянные user jobs продолжают работу.

Временный job получает уникальный label текущей GUI session и loopback
port; нативный `plutil` проверяет plist. Используются прежние
`RunAtLoad=true`, `KeepAlive.SuccessfulExit=false`, throttle 30 секунд.
По [документации Apple](https://raw.githubusercontent.com/apple-oss-distributions/launchd/main/man/launchd.plist.5)
throttle ограничивает частоту запусков; он не устанавливает число попыток.
Три fault injections — конечный бюджет этого опыта. Production circuit
breaker и согласованный recovery SLO этим результатом не устанавливаются.

Каждое поколение должно иметь ровно один inference child и resource tracker.
После одного явного diagnostic call проверяются 46 raw Prometheus series:
computed 0 → 1, rejected/failed 0, readiness 1 и in-progress 0. Перед SIGKILL
повторно сверяются parent, дети и launchd state. Сигнал получает только
inference child этого временного job. Runtime самостоятельно завершается
с exit 75; harness не запускает замену вручную.

## Приёмка

| Наблюдение | Условие |
|---|---|
| Текущий exit | Все три owned процесса отсутствуют, `runs` соответствует поколению, PID в native state отсутствует, exit 75 |
| Повторный старт | Ровно 1 → 2 → 3 → 4, новые PID, прежний полный profile |
| Throttle | Native process birth intervals ≥ 29 секунд при throttle 30; `ps lstart` имеет секундную точность |
| Метрики | Новый server start относительно непосредственно предыдущего поколения, свежий target, counters 0 → 1 |
| Отказ scraper | Реальный down target с `lastError`, `up` 1 → 0 → 1 для каждого цикла |
| Alert | Raw endpoint alert pending, затем отсутствует после recovery |
| Решение | Точное совпадение typed outcome, distribution и tokens; request/profile/probabilities перепроверены |
| Логи | Ровно три retirement events: ожидаемые child PID, exit 75, SIGKILL -9, profile |
| Стабильное окно | ≥ 30 секунд, промежутки observations ≤ 5 секунд, прежние PID/generation/counters |
| Cleanup | Временный label отсутствует, все наблюдавшиеся owned PID и scraper остановлены |
| Постоянный resident | Прежние registration/plists/model/package/environment/OS session/PID и counters, готовность и свежий scrape |

Прежний `last exit code = 75` у живого процесса не принимается за новый exit.
Пропущенный ранний старт делает process inventory неполным и не позволяет
заявить успешную очистку. Raw vectors/targets/alerts/history проверяются
повторно; pass flags не заменяют их. Query-range points обозначают
evaluations, а не число независимых scrapes. Pending alert не является
firing: прежний `for: 2m` сохраняется, как описывает
[Prometheus](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/).

## Запуск и evidence

```bash
python3 scripts/check-decision-crash-loop.py \
  --bundle "$RESIDENT_ROOT" \
  --expected-seal "$BUNDLE_SEAL" \
  --request docs/qualification/local-decisions/request.example.json \
  --evidence-dir docs/private/crash-loop-new-run
```

Output — новый `crash-loop.json`, seal и mode 0600; каталог mode 0700.
Предыдущий каталог не переиспользуется. Report сохраняет runs, raw
metrics/targets/alerts, native state files, retirement events, source hashes,
до/после resident observations и отдельные measurement/cleanup failures.
SIGINT/SIGTERM переводят запуск в cleanup; второй сигнал не обрывает
очистку. Все raw paths, PID, labels, запросы и OS identifiers остаются
в игнорируемом `docs/private`.

27 целевых tests проверяют четыре поколения и конечный fault budget,
throttle, stale exit/metrics, bindings/tokens, чужие/пропущенные PID,
raw alerts вместо pass flags, разрывы stable window, signal cleanup,
изменение постоянного resident и source drift. Эти fixtures не доказывают
настоящий crash-loop на этом Mac. Реальный boot/login, owner/SLO,
независимые human reviews/calibration/holdout остаются отдельными gates.

Первый полный docs check выявил гонку в существующем Temporal signal fixture:
`ready.json` мог наблюдаться до окончания записи. Публикация переведена на
atomic rename; пять реальных signal scenarios затем прошли. Остаточная
owned fixture group остановлена и отсутствие всех трёх PID подтверждено;
failed log и cleanup evidence сохранены приватно. Runtime/Workflow Commands
не менялись.

После исправления полный `docs:check` прошёл: 655 Python tests, четыре
explicit Docker opt-in skips, 12 Node checks, каталог и 2360 локальных
ссылок в 262 Markdown файлах. Native measurement следует этому committed
checkpoint; фактический результат будет сохранён отдельно.
