# Resident deployment для постоянного локального наблюдения

04.10.2026. **Resident bundle подготовлен и прошёл реальный native gate**
после [проверки настоящего scraper](./native-prometheus.md). Runtime, модель,
свежий venv и scraper находятся в Application Support, вне Documents и
временного каталога. Постоянная регистрация служб — следующий отдельный
этап после CI. Routing остаётся выключенным, qualification — `not_assessed`.

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

## Фактическая проверка resident пакета

Измеренный commit — `aa06d1333269d1fe5198878ef60e9db642f9cd37`, поверх
main `96c5490c8e61e0b71d1caf8e93edb00f11ab8733` после scraper gate.
Подготовка закрепила **27 source files**, **36 copied files**, **9 generated
configs/plists**, 34 wheels и 34 installed dependency versions. Fresh venv,
offline hash install, `pip check`, MLX calculation и оба promtool checks
завершились с exit 0. Manifest и model files проверены после копирования.

| Наблюдение package gate | Результат |
|---|---:|
| Native starts / scored calls | 2 / 2 |
| SIGKILL → exit 75 | 421,623 мс |
| SIGKILL → новый ready runtime | 20426,738 мс |
| Полный launcher, включая scraper и cleanup | 48708,555 мс |
| Metric snapshots / series в каждом | 4 / 46 |
| API queries / query-range points (шаг 1 с) | 55 / 26 |
| Собственные PID | 7, все остановлены |

Временный LaunchAgent действительно загрузил package из Application Support
через новый venv. До scoring и после restart получен прежний полный профиль
0.12.2 с SHA `81663153638983bd72afd5a31d8871f38c7db064e28a37636a36b780cf0b6cef`.
Оба результата, logits/probabilities и token counts совпали с предыдущим
native baseline. Source relocation не создаёт новый serving recipe.

Scraper подтвердил counters 0 → 1 → 0 → 1, новый server start time,
`up` 1 → 0 → 1 и pending/cleared endpoint alert. Query-range содержит
26 пересэмплированных points, включая 20 down points; это не число scrapes.
Рабочие persistent plists пока не регистрировались: gate использовал
уникальный временный label и private evidence directory.

Отдельная проверка пересчитала bundle/seals, все copied/generated/source
SHA и исходники измеренного commit, model artifact, pinned binaries/archive,
request/result bindings, retirement event и raw Prometheus evidence. Она
повторно подтвердила отсутствие временного job и семи PID. [Публичная сводка](../evidence/2026-10-04/resident-package-0.12.2/result-summary.json)
сохраняет только counts, SHA и исходы. Home paths, command output, model
manifest locations и API bodies находятся в игнорируемом `docs/private`.

30 целевых builder/launchd/service tests прошли. Полный `docs:check`:
12 Node checks, 506 Python tests (четыре opt-in skips), 13 process templates,
2201 локальная ссылка до добавления итогового отчёта. Девять новых tests
проверяют exact pins, wheel metadata/hash lock, cloud-only admission,
ownership/path boundaries, loopback config и service-root/profile gating.

## Постоянная регистрация после CI

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
