# Нативное восстановление launchd на runtime 0.12.2

04.10.2026. **Временный LaunchAgent прошёл полный цикл отказа и автоматического
restart на реальных весах decider-2b.** После SIGKILL собственного inference
ребёнка runtime вышел с кодом 75; launchd запустил новый процесс, загрузил
тот же профиль и вернул идентичное решение. Job удалён, собственных PID
не осталось. Маршрутизация выключена, qualification — `not_assessed`.

## Условия опыта

Измеренный committed harness — `4fcc73f6d6f1db6a3031ebd87e41c20b5924cd76`.
Зафиксированы SHA четырёх исходников probe и helpers. До scoring и fault
injection проверен полный [профиль 0.12.2](../performance/profiles/runtime-0.12.2.json),
SHA `81663153638983bd72afd5a31d8871f38c7db064e28a37636a36b780cf0b6cef`:
2048 input tokens, cache 128 МиБ, isolated spawn и deadline 5000 мс,
прежняя policy, T=1 без fitted calibration. Implementation SHA остаётся
`58cbac3c4cd93a79f516d6ea252bd9ca3a0a572db9b7d2a8a7ff36e257987915`.

Python 3.13.12, checkout и resident weights расположены вне Documents,
в отдельном временном рабочем каталоге. Это устраняет зависимость данного
опыта от прежнего cloud-only checkout и Documents/TCC; разрешения macOS
не менялись. Успех не подтверждает доступ launchd к прежнему расположению.

Сгенерирован уникальный job текущей GUI-сессии: foreground runtime,
`RunAtLoad=true`, `KeepAlive.SuccessfulExit=false`, throttle 30 секунд,
loopback на выделенном ephemeral порту. Plist проверен нативным `plutil`.
Модель соответствует [foreground LaunchAgent Apple](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html).
Постоянный LaunchAgent и login item этим опытом не устанавливаются.

## Результат и независимая перепроверка

| Наблюдение | Результат |
|---|---:|
| Первая readiness, включая загрузку | 4661,032 мс |
| SIGKILL ребёнка → остановка первого runtime и детей | 236,960 мс |
| SIGKILL → readiness нового процесса через launchd | 29882,792 мс |
| Первый / второй scoring, HTTP | 262,638 / 199,406 мс |
| Полный launcher, включая cleanup | 35177,122 мс |
| Native starts / scoring calls | 2 / 2 |
| Собственные процессы успешного опыта | 6, все остановлены |

`launchctl` записал `last exit code = 75: EX_TEMPFAIL`, число запусков
увеличилось с одного до двух. Retirement log содержит ровно одно событие
`decision.backend_retired`, exit 75, SIGKILL exit -9 и SHA ожидаемого профиля.
Статус, причина, typed value, selected option и полное распределение logits
и probabilities совпали до и после restart. Повторного inference для
неудавшегося запроса не было; fault injection выполнялся между запросами.

Отдельная проверка после launcher пересчитала seals, SHA исходников текущего
и измеренного commit, retained files, plist, runtime fingerprint, оба
request/result binding и вероятности из logits. Повторно проверены отсутствие
регистраций всех трёх jobs и отсутствие всех **15** наблюдавшихся PID.
[Публичная сводка](./evidence/2026-10-04/native-launchd-0.12.2/result-summary.json)
сохраняет counts, timing, проверки и SHA приватных отчётов. Plist, host paths,
PID, job labels, inputs и process logs остаются в игнорируемом `docs/private`.

Два предыдущих failed reports сохранены. Первый probe не разбирал символический
suffix exit code; второй увидел restart и корректный ответ, но проверил
регистрацию сразу после `bootout`, до её удаления. Эти результаты не
переписаны в pass. Harness теперь сохраняет raw native state и ограниченно
ожидает публикацию exit code и завершение удаления. [Протокол и команда](./service-recovery.md)
описывают profile admission, committed source binding, приватную диагностику,
SIGTERM cleanup и 17 новых fixture tests. Формат exit code также
показан в [диагностике Apple DTS](https://developer.apple.com/forums/thread/791996).

Окончательный `docs:check` прошёл: 12 Node checks, 483 Python tests
(четыре opt-in skips), каталог 13 process templates. После добавления отчёта
отдельно проверены 2190 локальных ссылок. 20 целевых launchd/service tests
прошли; runtime implementation и serving profile не менялись.

## Следующий этап

Этот gate подтверждает один restart в текущей GUI-сессии. Boot/login,
crash loop, постоянная установка и производственный recovery SLO не проверены.
Измеренная задержка включает throttle и загрузку модели на общей машине.
Прежний внеплановый exit 75 при совместной работе моделей этим управляемым
SIGKILL не объясняется.

Далее — подключить нативный scraper к реальному endpoint, проверить scrape,
счётчики, недоступность при recovery и восстановление наблюдения. Независимые
бизнес-данные, человеческие reviews, калибровка, holdout и согласование SLO
остаются открытыми; ограниченная маршрутизация требует qualification.
