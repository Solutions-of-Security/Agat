# Resident deployment для постоянного локального наблюдения

04.10.2026. Следующий шаг после [реального native scrape/recovery](./native-prometheus.md)
— подготовить стабильное resident расположение runtime, модели, зависимостей
и scraper вне Documents и временного каталога. Подготовка создаёт новый
bundle и приватный manifest; сервисы регистрируются отдельно после проверки
пакета. Routing остаётся выключенным, qualification — `not_assessed`.

## Подготовка и границы владения

[Builder](../../../../../scripts/prepare-decision-resident-deployment.py)
принимает только новый прямой release directory в
`~/Library/Application Support/Agat/decision-shadow/releases/`. Существующее
содержимое, symlink и защищённые Documents/Desktop/Downloads отвергаются.
Расположение следует [назначению Application Support у Apple](https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/FileSystemProgrammingGuide/FileSystemOverview/FileSystemOverview.html).
Пользовательский исходный checkout и его незакоммиченные изменения не используются
как источник; измеритель работает с отдельным committed checkout.

Python [не считает venv переносимым](https://docs.python.org/3.13/library/venv.html).
Поэтому создаётся новый venv в конечном расположении на проверенном
Python 3.13.12 arm64. Все 34 exact dependency pins устанавливаются офлайн
из полного wheelhouse через hash lock и `--no-deps`; `pip check` и фактический
набор installed versions сверяются. После установки выполняется настоящий
малый MLX/Metal calculation. Пакеты и serving recipe не обновляются.

До чтения manifest/wheels/weights проверяется отсутствие cloud-only flags.
Resident checkpoint копируется как обычные файлы и сверяется с прежним
artifact SHA. Manifest меняет только локальное расположение snapshot;
repository, revision, tokenizer и model bytes сохраняются. Runtime package
копируется byte-for-byte, полный profile остаётся 0.12.2. Prometheus/promtool
копируются только при совпадении [release pin](./prometheus-3.13.4.json).

Bundle содержит конфигурацию и два foreground LaunchAgents:

| Job | Поведение |
|---|---|
| `org.agat.decision-shadow` | Loopback `127.0.0.1:8766`, 2048 tokens, cache 128 МиБ, isolated deadline 5000 мс, exit 75 при отказе, offline model loading |
| `org.agat.decision-prometheus` | Loopback web `127.0.0.1:9095`, scrape/evaluation 15 с, timeout 5 с, собственный TSDB, retention 7 дней / 256 МБ |

Оба jobs имеют `RunAtLoad`, `KeepAlive.SuccessfulExit=false`, throttle 30 с,
exit timeout 30 с и собственные logs. Promtool проверяет generated config
и прежние alert unit tests; firing thresholds не меняются. Alertmanager,
remote write и внешние получатели не добавляются.

Builder требует committed source bytes и сохраняет их SHA, source commit,
wheel hashes, copied/generated file hashes, installed dependencies,
полный profile и configs в sealed manifest. Top-level release directory
имеет режим 0700, manifests/configs/plists/logs — 0600. Runtime/Prometheus
не запускаются подготовкой. При отказе остаются failed report и новый
частичный release; существующий release не перезаписывается и автоматически
не удаляется.

```bash
# Wheelhouse заранее содержит ровно все закреплённые wheels для Python 3.13 arm64.
# Сначала commit builder и его входов; output и release directory ещё не существуют.
.venv/decision/bin/python scripts/prepare-decision-resident-deployment.py \
  --destination "$HOME/Library/Application Support/Agat/decision-shadow/releases/<new-release>" \
  --python .venv/decision/bin/python \
  --manifest .local-models/decisions/decider-2b.json \
  --wheelhouse /absolute/path/pinned-wheels \
  --prometheus /absolute/path/prometheus-3.13.4.darwin-arm64/prometheus \
  --promtool /absolute/path/prometheus-3.13.4.darwin-arm64/promtool \
  --output docs/private/new-run/resident-preparation.json
```

## Проверка до постоянной регистрации

Нативный probe принимает `--service-root <release>/runtime` вместе с новым
venv, manifest и policy из bundle. Он создаёт только временный уникальный
job, сохраняет профиль до scoring/fault injection и после restart, проверяет
точное решение и удаляет временную регистрацию. Полный expected profile
и persistent private evidence обязательны при подключении scraper.

После реального package gate и CI постоянная регистрация отдельно проверяет
отсутствие обоих заданных job labels и plist files, доступность выбранных
loopback ports, SHA bundle/configs и соответствие полного health profile.
Копирование в `~/Library/LaunchAgents` создаёт только новые собственные
files; чужая служба не заменяется и не останавливается. После bootstrap
нужно увидеть именно подготовленный job, реальный scrape и counters.

Остановка собственной регистрации выполняется `launchctl bootout
gui/<uid>/<label>` для каждого из этих двух labels; отсутствие регистрации
и owned PID проверяется отдельно. Удаление собственного plist прекращает
автозапуск следующей GUI-сессии. Bundle и приватные evidence сохраняются
для диагностики; model bytes и пользовательские данные остановка не удаляет.

Base Homebrew Python остаётся внешней управляемой зависимостью: его обновление
или удаление требует новой проверки окружения. Boot/login на этом Mac не
объявляется проверенным без фактического опыта. Постоянный scraper даёт
локальное состояние alerts; владелец реакций и production SLO требуют
согласования. Предметная qualification, independent reviews, calibration,
holdout и ограниченная маршрутизация остаются отдельными gates.
