# Завершение отказавшего сервиса и восстановление

26.09.2026. Runtime `0.10.0` добавляет `serve --exit-on-backend-unavailable`. При отказе изолированного MLX-процесса HTTP-сервис прекращает приём, завершает уже принятые обработчики и выходит с кодом **75**. Восстановление новым foreground-процессом проверено на реальных весах. Подготовлены генератор LaunchAgent и ограниченный тест launchd; нативный опыт на этом Mac остановился до старта runtime из-за запроса доступа macOS к Documents. Автоматический restart launchd **не подтверждён**.

## Поведение

Флаг требует `--inference-timeout-ms`. Без него сохраняется прежний режим: после отказа `/health` возвращает 503, новые решения отклоняются до явного перезапуска. Ошибки контракта и `context_too_long` не выводят здоровый backend из работы.

Смерть ребёнка проверяется в `service_actions` HTTP-сервера, включая простой; обычный интервал цикла — 500 мс. Отдельный короткий поток вызывает `shutdown`, а `server_close` ожидает обработчики. Это учитывает [контракт Python socketserver](https://docs.python.org/3.13/library/socketserver.html): `shutdown` должен вызываться вне потока `serve_forever`, а завершения обработчиков ждут при `daemon_threads=False`.

Результат не вычисляется повторно. При серверном deadline исходный клиент получает полный `504 / inference_timeout`, если его собственный бюджет ещё не истёк. При недоступности между запросами может закрыться соединение; worker сохраняет транспортный отказ и продолжает primary. Следующий запуск загружает модель заново; прежний lease или input автоматически не повторяется.

Остановка процесса не означает немедленного аппаратного прерывания GPU. Inference ограничен своим deadline, сокеты имеют пятиисекундный inactivity timeout. Это не абсолютный предел всего HTTP-обмена с намеренно медленным клиентом. Внешний менеджер должен ограничивать длительность завершения и следить за готовностью; бесконечного ожидания в эксплуатации быть не должно.

```bash
.venv/decision/bin/python -m decision_runtime serve \
  --manifest .local-models/decisions/decider-2b.json \
  --policy docs/qualification/local-decisions/policy.shadow.v1.json \
  --max-tokens 2048 --cache-limit-mib 128 \
  --inference-timeout-ms 2000 --exit-on-backend-unavailable --port 8766
```

Флаг управляет жизненным циклом сервиса, как порт; не меняет logits, policy или схему `inferenceExecution`. Реализация `0.10.0` имеет новый implementation SHA. Замороженные профили и fits прежних версий нельзя автоматически переносить. Профиль перезапущенного сервиса должен в точности совпадать с опубликованным профилем lease.

## LaunchAgent для macOS

[Генератор](../../../../scripts/render-decision-service.py) создаёт plist с абсолютными `ProgramArguments`, без shell. Путь virtualenv сохраняется даже при symlink на базовый Python: иначе интерпретатор может потерять установленные в окружении зависимости. Проверяются пути, диапазоны настроек и XML; существующий output не перезаписывается. Генератор сам не регистрирует и не запускает службу.

```bash
mkdir -p .local-models/decisions/logs
python3 scripts/render-decision-service.py \
  --manifest .local-models/decisions/decider-2b.json \
  --policy docs/qualification/local-decisions/policy.shadow.v1.json \
  --log-dir .local-models/decisions/logs \
  --output .local-models/decisions/org.agat.decision-shadow.plist
plutil -lint .local-models/decisions/org.agat.decision-shadow.plist
```

Настройки следуют модели [foreground LaunchAgent Apple](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html) и локальным `man launchd.plist` / `launchctl help`:

[Сгенерированный пример для этого checkout](./evidence/2026-09-26/launchd.example.plist) прошёл `plutil -lint`; для другого расположения его следует создать заново.

| Настройка | Значение и границы |
|---|---|
| `RunAtLoad` | Первый запуск при регистрации |
| `KeepAlive.SuccessfulExit` | `false`: перезапуск после ненулевого выхода, включая 75 |
| `ThrottleInterval` | 30 секунд, ограничение частоты запусков; не фиксированная пауза после каждой смерти |
| `ExitTimeOut` | 30 секунд до принудительного завершения при остановке job |
| `WorkingDirectory` | Корень данного checkout |
| stdout/stderr | Отдельные файлы в явно выбранном каталоге; ротация логов в шаблон не входит |

Это также перезапускает процесс после ошибки конфигурации. Throttle не заменяет ограничение числа ошибок, оповещение или readiness monitoring. Блокировка до инициализации Python не превращается в exit 75; бесконечно живой, но неготовый процесс сам `KeepAlive` не перезапустит.

Для выбранного оператором окружения с уже разрешённым доступом к файлам:

```bash
launchctl bootstrap "gui/$(id -u)" "$PWD/.local-models/decisions/org.agat.decision-shadow.plist"
launchctl print "gui/$(id -u)/org.agat.decision-shadow"
curl --fail-with-body http://127.0.0.1:8766/health
# Остановка и удаление именно этой регистрации:
launchctl bootout "gui/$(id -u)/org.agat.decision-shadow"
```

Команды не помещают plist в каталог автоматического входа. `SIGTERM` отдельному процессу не удаляет job и может привести к его перезапуску; для остановки используется `bootout`. MLX-адаптер рассчитан на Apple Silicon/Metal; Linux/systemd deployment этим шаблоном не заявляется.

## Проверенные результаты и ограничение хоста

[Foreground-опыт](./evidence/2026-09-26/service-recovery.json) использовал три собственных процесса, loopback и один диагностический Choice-вход:

| Сценарий | Наблюдение |
|---|---|
| SIGKILL вычислительному ребёнку между запросами | Без последующих HTTP-вызовов родитель вышел 75 за 554.965 мс; дети завершены |
| Явный запуск нового процесса на том же порту | Готовность за 4283.531 мс; профиль и распределение побитово совпали; новый запрос — HTTP 200 |
| Серверный deadline 100 мс | Полный HTTP 504 за 160.301 мс, затем exit 75; нет оставшихся детей |
| Завершение опыта | Все три родителя и их дочерние процессы остановлены |

Числа — единичные наблюдения, не SLO. Во время foreground-опыта выполнялся CPU-регрессионный набор; сравнение задержек старта/первого inference не является benchmark. Не измерялись reboot/login, многократные аварии и полный primary workflow.

[Нативный опыт](./evidence/2026-09-26/launchd-startup-not-ready.json) создал временный job с уникальным именем. Plist прошёл `plutil`; процесс Python появился, но за 90 секунд не открыл HTTP. Снимок стека показывает ожидание `getpath_readlines → fopen → open` при инициализации Python, до импорта runtime. Запись TCC `AUTHREQ_PROMPTING` для того же PID указывает на `kTCCServiceSystemPolicyDocumentsFolder`. [Apple документирует отдельное согласие на доступ к Documents](https://developer.apple.com/documentation/bundleresources/information-property-list/nsdocumentsfolderusagedescription).

Разрешения macOS не менялись. Временный job удалён через `bootout`; отсутствие job и PID проверено отдельно. После настройки доступа оператором этот опыт нужно повторить. Наличие рабочего foreground-сервиса не доказывает доступность тех же файлов из launchd.

Воспроизведение ограниченных опытов:

```bash
.venv/decision/bin/python scripts/check-decision-service-recovery.py \
  --manifest .local-models/decisions/decider-2b.json \
  --policy docs/qualification/local-decisions/policy.shadow.v1.json \
  --request docs/qualification/local-decisions/request.example.json \
  --output docs/qualification/local-decisions/shadow/evidence/new-run/service-recovery.json

# Регистрирует только временный job с новым именем; finally выполняет bootout.
.venv/decision/bin/python scripts/check-decision-launchd.py \
  --manifest .local-models/decisions/decider-2b.json \
  --policy docs/qualification/local-decisions/policy.shadow.v1.json \
  --request docs/qualification/local-decisions/request.example.json \
  --output docs/qualification/local-decisions/shadow/evidence/new-run/launchd-recovery.json
```

Опыты прекращаются при неподтверждённом результате; неготовый native startup не считается успешным restart. Предметная qualification остаётся открытой, автоматическая маршрутизация выключена.

## Проверка закреплённого профиля и сохранение диагностики

04.10.2026. Нативный probe теперь принимает `--expected-profile` и явный
`--inference-timeout-ms`. Ранее он всегда выбирал 2000 мс и мог проверить restart
другого профиля, чем опубликованный isolated-профиль с deadline 5000 мс.
При заданном expected profile параметры проверяются до регистрации job,
а полный `/health` профиль — до первого scoring и SIGKILL и после restart.
Несовпадение останавливает опыт; endpoint не получает диагностический input
при первоначальном несовпадении.

`--evidence-dir` создаёт новый каталог только внутри игнорируемого `docs/private`.
Plist и stdout/stderr сохраняются и при неготовом startup: каталог имеет режим
0700, файлы — 0600. Дети собственного LaunchAgent учитываются до HTTP readiness.
Неполный inventory или ошибка очистки сохраняются в failed report; они не
допускают отметку о подтверждённой остановке всех процессов. Ошибка `launchctl
print` также не означает отсутствия job: принимается только конкретный ответ
об отсутствии указанной службы.

Измеритель требует committed bytes собственных исходников и сохраняет commit,
source SHA и SHA оставшихся diagnostic files. Изменение harness во время опыта
отвергается. Эти SHA фиксируют содержимое, а не являются цифровой подписью.
Схема нового отчёта — `agat.decision.launchd-recovery.v2`; неуспешный опыт
возвращает ненулевой exit status и сохраняет отдельные исходы lifecycle/cleanup.

```bash
# Сначала commit изменённых исходников probe; каталог evidence ещё не существует.
.venv/decision/bin/python scripts/check-decision-launchd.py \
  --python .venv/decision/bin/python \
  --manifest .local-models/decisions/decider-2b.json \
  --policy docs/qualification/local-decisions/policy.shadow.v1.json \
  --request docs/qualification/local-decisions/request.example.json \
  --expected-profile docs/qualification/local-decisions/performance/profiles/runtime-0.12.2.json \
  --inference-timeout-ms 5000 \
  --evidence-dir docs/private/new-run/launchd \
  --output docs/private/new-run/launchd/launchd-recovery.json
```

SIGTERM во время lifecycle опыта вызывает bounded cleanup и сохраняет failed
report с диагностикой. Прежний signal handler восстанавливается после cleanup.

`launchctl print` может добавлять символическое имя к числовому exit code,
например `75: EX_TEMPFAIL`. Такой формат показан и в [диагностике Apple DTS](https://developer.apple.com/forums/thread/791996).
Parser принимает числовой код с необязательным символическим suffix и отвергает
посторонний текст. После исчезновения PID публикация exit code ожидается не
более пяти секунд; другой код или потеря регистрации завершают опыт ошибкой.
Исходный вывод launchctl сохраняется приватно в `failure-service-state.txt`.

Шестнадцать новых fixture tests проверяют границы профиля и source admission,
startup без HTTP, inventory, ошибки проверки регистрации, сохранение логов
и отказ при неподтверждённом cleanup, а также SIGTERM. Они не запускают MLX и не доказывают
нативный restart; для этого требуется отдельный реальный опыт.

[Независимая перепроверка](./evidence/2026-09-26/verification-service-recovery.json) пересчитала seals, профили и logits/вероятности, сверила текущий implementation SHA и отсутствие процессов/job. `npm test`: 204 coordinator, 52 web, 45 worker (1 skip), 97 runtime (3 skip), Temporal/replay и process pack — без ошибок. `docs:check`: 12 Node- и 62 Python-проверки, каталог процессов и локальные ссылки — без ошибок. Шесть lifecycle-тестов отдельно проверяют смерть в простое, сохранение ответа при одновременном shutdown, отсутствие retry и сохранение стандартного поведения без opt-in.
