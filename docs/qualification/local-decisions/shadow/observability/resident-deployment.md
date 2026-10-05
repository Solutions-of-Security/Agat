# Resident deployment для постоянного локального наблюдения

04.10.2026. **Resident bundle подготовлен и прошёл реальный native gate**
после [проверки настоящего scraper](./native-prometheus.md). Runtime, модель,
свежий venv и scraper находятся в Application Support, вне Documents и
временного каталога. [Постоянный rollout](./resident-rollout.md) прошёл
05.10 MSK; два jobs установлены и работают. [Manager](../../../../../scripts/manage-decision-resident-deployment.py)
прошёл тесты и preflight реального bundle. [Первый install/status/stop](./resident-port-reuse.md)
прошёл; повторный preflight выявил TCP address reuse issue, который исправлен
и проверен до успешного полного повторного rollout. Routing
выключен, qualification — `not_assessed`.

05.10 MSK [builder/manager дополнены выбором public profile](../../performance/resident-profile-selection.md).
Новый пакет 0.12.3 с wired budget 4096 МиБ подготовлен и прошёл
[отдельный native launch/recovery gate](../../performance/resident-wired-package-0.12.3.md).
Ниже сохранён фактический package gate 0.12.2.

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

### Управление собственными jobs

Manager предоставляет `check`, `install`, `status` и `stop`. Каждая команда
требует expected bundle seal, пересчитывает copied/generated files, модель
и implementation SHA, сверяет точный serving profile и два сгенерированных
plist с committed recipe. Исходники manager и его зависимостей должны
совпадать с HEAD; sealed private report сохраняет commit и SHA 33 sources.
Checksum подтверждает целостность артефакта, а не цифровую подпись.

`check` проверяет package verification с полным набором из 15 успешных checks,
связь с bundle/profile и прежним native baseline. Затем проверяет отсутствие
registration marker, обоих labels и установленных plists, а также свободные
loopback ports. Команда не создаёт jobs, не выполняет inference и не изменяет
release. `install` выполняет тот же preflight, создаёт только новые plists
с режимом 0600 и регистрирует их в текущем GUI domain.

Readiness требует реальных PID, одного inference child, полного health
profile, единственного правильного target и свежего успешного scrape.
Пустой query result до первого scrape и временный HTTP 503 ожидаются в
пределах 90 секунд; несовпадающий profile/target отклоняется. При установке
`lastScrape` должен быть после начала bootstrap. После одного диагностического
решения Prometheus обязан показать прирост computed counter от наблюдённого
значения. Старый TSDB sample не подтверждает новый запуск. Формат ответа
проверяется по [HTTP API Prometheus](https://prometheus.io/docs/prometheus/latest/querying/api/).

`status` проверяет владельца registration marker, byte-for-byte установленный
plist и путь plist в фактическом `launchctl print`. Он читает health/scrape,
не выполняя inference. `stop` использует те же ownership checks, но не зависит
от доступности HTTP, поэтому может остановить отказавший runtime. Он инвентаризует
собственные PID, выполняет bootout только двух своих labels и отдельно ждёт
исчезновения jobs/PID. Изменённый или заменённый plist не удаляется.
После успешной остановки marker архивируется; bundle/model/TSDB сохраняются,
повторный `check`/`install` возможен.

Bootstrap intent и ownership созданных файлов записываются до операции,
которая может прерваться. При неуспешной установке выполняется собственный
rollback, включая частично записанный файл с прежними device/inode/ctime и ожидаемым
префиксом; чужой или изменённый файл сохраняется. Неполная инвентаризация
процессов не может дать успешный cleanup report. Это реализует управление
foreground user agents согласно [Apple launchd guide](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html).

```bash
# Bundle уже подготовлен и package gate проверен; каждый output — новый.
python3 scripts/manage-decision-resident-deployment.py check \
  --bundle "$HOME/Library/Application Support/Agat/decision-shadow/releases/<release>" \
  --expected-seal <sha256-from-deployment.json> \
  --verification docs/private/<package-run>/verification.json \
  --output docs/private/<management-run>/preflight.json

# После check и CI: те же аргументы с install и новым output.
# status/stop требуют bundle, expected-seal и новый private output.
```

04.10 реальный `check` на commit `b370ec0e9d413de667d14c8880a3b4afffde4453`
поверх main `7b93ff4213f735085d68b9781ace408b35c0853c` завершился `verified`:
package gate, source bindings, bundle/model/profile, labels, plist paths и
порты проверены. Runtime и scraper не регистрировались. [Публичная сводка](../evidence/2026-10-04/service-management-0.12.2/result-summary.json)
содержит только явный набор counts, SHA и исходов; домашние пути и raw
process/API evidence остаются в `docs/private`.

21 новый fixture test проверяют неполный/чужой package gate, traversal,
занятые labels/ports, foreign plist path, первый и устаревший scrape,
числовой counter и новый прирост, inventory после ошибки, прерванный bootstrap,
полный цикл install/stop/reinstall, повторную выдачу inode, прерванный
unbuffered write и сохранение изменённого файла. Всего
51 целевой manager/builder/launchd/service tests проходят. Полный `docs:check`
прошёл на Node 24 / Python 3.13.12: 12 Node checks, 527 Python tests (четыре
opt-in skips), process catalog и локальные ссылки. Первый запуск в sandbox
сохранил отказы из-за запрета loopback/PID inspection; полный повтор с нужным
доступом прошёл. Независимый verifier повторно пересчитал 33 committed sources
и 45 bundle files, модель и seals, затем подтвердил отсутствие jobs/plists
и свободные порты. Реальные постоянные install/stop/reinstall ещё не
объявляются выполненными.

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

Linux CI обнаружил повторную выдачу прежнего inode после удаления файла.
Guard теперь проверяет device/inode и `st_ctime_ns`, а write выполняется без
буфера: при прерывании финальная identity читается через собственный file
descriptor. Собственный handle остаётся открытым до конца операции, поэтому
inode исходного файла не освобождается для повторной выдачи даже после
замены pathname. Два regression tests воспроизводят reused inode с одинаковыми
bytes и частичную запись. Исходный CI failure сохранён в `docs/private`;
после исправления preflight и независимая проверка выполнены заново.
