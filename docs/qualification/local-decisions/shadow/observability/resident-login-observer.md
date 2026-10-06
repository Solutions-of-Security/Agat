# Однократный collector после login

07.10.2026 MSK. [Приёмка boot/login](./resident-boot-login.md) требует
настоящего нового OS event и сохранённого baseline. Collector подготавливает
постоянный snapshot вне временного checkout, регистрируется отдельным user
LaunchAgent и при каждом login один раз проверяет boot и GUI session.
**Фактический boot/login wired resident 0.12.3 остаётся открытым.**
Routing выключен, qualification — `not_assessed`.

## Постоянный snapshot

[Builder](../../../../../scripts/prepare-decision-session-observer.py)
принимает resident seal и независимо закреплённый baseline file SHA.
До записи проверяются package/model/dependencies, прежние registration,
профиль, OS identity и свежий scrape. Три новых builder/collector/manager
source files должны совпасть со своим committed Git revision.

Новый пакет создаётся только в отдельном прямом каталоге
`Library/Application Support/Agat/decision-shadow/session-observers`.
Он содержит mode 0600 baseline, collector, sealed deployment, plist и
private logs; внешний каталог имеет mode 0700. Исходный Git commit baseline
копируется через локальный shallow fetch в собственный detached checkout.
`git fsck` и отсутствие alternates подтверждают собственные Git objects.
Collector сверяет 35 source hashes, исторические Git bytes, HEAD, чистый
checkout и локальное размещение Git metadata/worktree. Удаление исходного
временного repository не лишает snapshot commit или исходников.

Snapshot закреплён именно на measurement source baseline. Новый builder
не заменяет исходные evidence files и не пересоздаёт baseline при установке.
Смена release/registration/source bindings требует отдельного нового
baseline до следующего реального event.

## Job и результат

[Collector](../../../../../scripts/observe-decision-resident-session.py)
использует Python действующего resident venv и frozen readonly CLI.
Сначала проверяется `--event boot`, затем `--event login`; каждой проверке
выделены прежние 90 секунд ожидания готовности и общий subprocess timeout
240 секунд. Он читает metadata, health, metrics и зависимости, без
diagnostic inference, restart или изменения resident jobs. Offline
environment и loopback bindings сохраняются.

`org.agat.decision-session-observer` имеет `RunAtLoad=true`,
`KeepAlive=false`, `ExitTimeOut=30` и `AbandonProcessGroup=false`.
Однократная заказанная пользователем приёмка использует `ProcessType=Standard`.
Первый native install с `Background` завершился Git inspection timeout
5 секунд до event collection, тогда как foreground observation прошёл.
Failed receipt и package сохранены, guarded rollback удалил новый job,
installed plist и registration без cleanup error. По текущему macOS man и
[официальному Apple source](https://raw.githubusercontent.com/apple-oss-distributions/launchd/main/man/launchd.plist.5)
`Background` предназначен для незапрошенной работы с resource limits;
`Standard` сохраняет обычную light resource policy. Повтор готовится в
новом immutable package. Git inspection и inference deadline не расширены;
причинность единственного timeout отдельно не доказывается.
Согласно [Apple launchd guide](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html),
user agents загружаются при login и завершаются при logout. Установленный
macOS `launchd.plist(5)` указывает, что при завершении job launchd очищает
его process group, если `AbandonProcessGroup` не включён.
Collector записывает один observation и завершается; самостоятельного
повторения после ошибки в этой GUI session нет.

| Сохранённый результат | Collector exit | Значение |
|---|---:|---|
| `recorded`, оба events `awaiting_event` / exit 2 | 0 | Текущая сессия проверена; реального нового event нет |
| `recorded`, login `verified` / exit 0, boot `awaiting_event` | 0 | Новый GUI login без reboot независимо подтверждён |
| `recorded`, оба events `verified` / exit 0 | 0 | Новый boot и GUI login независимо подтверждены |
| `failed` | 1 | Error, timeout, interruption или drift; failed observation сохранён |

Успешная запись pending observation не закрывает event gate. Два исходных
verification receipts сохраняют отдельно свои exit code, file SHA и seal.
Aggregate проверяет точный baseline pin, requested event и настоящие
boolean checks; fixture event или bootstrap не объявляются reboot/login.

Каждый запуск создаёт новый mode 0700 каталог в
`source/docs/private/session-events/<timestamp-random>/`, mode 0600
`boot.json`, `login.json`, logs и `observer-run.json`. Результаты сохраняются
эксклюзивно. Raw OS identifiers, PID, пути и source snapshot остаются
private. Public evidence содержит только разрешённые counts, hashes,
статусы и признаки фактического event.

## Управление

[Manager](../../../../../scripts/manage-decision-session-observer.py)
проверяет plist recipe, native `plutil`, package seal и source bindings.
`install` создаёт только отсутствующие собственные plist/registration,
bootstrap одного label и ждёт завершения collector и нового bound receipt.
`status` повторно проверяет ownership и исходные event files. `stop`
удаляет job и два неизменных owned files; package/evidence сохраняются.
Чужой label, изменённые bytes/inode/ctime или занятые paths отклоняются.
Failed install и SIGTERM/SIGINT выполняют guarded rollback; изменённые
чужие files сохраняются, cleanup failure отражается отдельно.

```bash
python3 scripts/prepare-decision-session-observer.py \
  --bundle "$RESIDENT_ROOT" --expected-resident-seal "$RESIDENT_SEAL" \
  --baseline "$SESSION_BASELINE" --baseline-sha256 "$BASELINE_SHA" \
  --destination "$OBSERVER_ROOT" \
  --output docs/private/observer-prepare-new.json

python3 scripts/manage-decision-session-observer.py install \
  --package "$OBSERVER_ROOT" --expected-seal "$OBSERVER_SEAL" \
  --output docs/private/observer-install-new.json
```

Для `check`, `status` и `stop` используется тот же manager с отдельным
новым output. Installed seal берётся из проверенного preparation receipt.
`status=registered` обозначает регистрацию observer, а допуск boot/login
читается отдельно из `observation.report.events`.

## Проверки

26 целевых tests проверяют pending/login-only receipts, противоречия
status/exit, private modes, symlinks, source/Git/baseline drift, чужой owner,
duplicate JSON и scalar aliases. Настоящий локальный Git snapshot остаётся
читаемым после перемещения исходного repository. Management scenarios
проверяют чужой label, ownership, changed files, failed bootstrap/collector,
signal rollback, чужой label в bootstrap race и запрет public output до
native mutation.
Native preparation/install/stop/reinstall проверяются после committed
checkpoint; fixtures сами не подтверждают event или native deployment.

Полный `docs:check` перед native policy correction прошёл: 681 Python tests, четыре
explicit Docker opt-in skips, 12 Node checks, 2371 локальная ссылка в
263 Markdown files и process catalog. Regression bootstrap race сначала
проверяет чужой path перед rollback bootout; чужой job остаётся работать,
ошибка cleanup сохраняется отдельно.

Owner/SLO и независимые human reviews/calibration/holdout остаются
отдельными gates. Collector не назначает владельцев и не включает routing.

## Native результат

После checkpoint `311dcab0f0527af8bacc3ca3c1337bf7bb79e205` новый Standard
package прошёл девять native steps: preparation, foreground, check,
install/status/stop, затем check/reinstall/status. Два install этого launchd job
завершились с `runs=1`, exit 0; observer остаётся зарегистрированным и
не имеет активного PID. При следующем login будет выполнен новый запуск.
Три Standard observations сохранили шесть raw event receipts:
**boot и login — `awaiting_event`, exit 2**. Изначальный foreground старого
Background package также сохранён отдельно.

Независимый audit прошёл 13 checks: оба committed builder histories,
35 frozen historical sources, собственные Git objects и native recipe,
inode/ctime/file pins, все raw receipts, install/stop/reinstall и отсутствие
завершённых observer/verifier processes. Постоянный resident сохранил
четыре прежних PID, package/model/profile/registration и 34 dependency
pins. Настоящий scrape подтверждает 46 series и counters computed 1 /
rejected 0 / failed 0 — дополнительных inference нет.

Первый отдельный audit отклонил равенство `kern.boottime`: calendar boot
time сдвинулся на 0,111569 с при прежних UUID и GUI session. В
[Apple XNU clock source](https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/osfmk/kern/clock.c)
`clock_set_calendar_microtime` корректирует и boot calendar time.
Audit сохраняет chronology и проверку event identity; точное равенство
этого calendar поля не требуется. Failed audit сохранён; corrected audit
перепроверил исходные receipts, source bindings при этом не менялись.
Отдельная regression проверяет положительную и отрицательную clock
adjustment в той же сессии; 13 session tests прошли. Первый audit и
первоначальные тестовые логи сохранены приватно.

Финальный полный `docs:check` прошёл: **682 Python tests**, четыре explicit
Docker opt-in skips, 12 Node checks, process catalog и локальные ссылки.
[Публичная сводка](../evidence/2026-10-07/resident-login-observer/result-summary.json)
содержит только status/count/source pins и признаки pending event.
Оба immutable packages, raw evidence, failed history, corrected audit,
финальные checks и оба measured Git sources сохранены в private ZIP.
CRC и SHA/size каждого file, а также копия в исходном workspace проверены.

**Реальный boot/login gate остаётся открытым.** Установка, successful exit
collector и fixture clock adjustment сами не доказывают новый OS event.
