# Приёмка resident после boot/login

07.10.2026 MSK. [Постоянный wired resident 0.12.3](./resident-wired-rollout-0.12.3.md)
работает в текущем GUI domain. Новая [CLI](../../../../../scripts/check-decision-resident-session.py)
сохраняет исходный baseline и проверяет фактическую смену boot/login.
Готовность после bootstrap или reinstall остаётся отдельным наблюдением.
Фактический reboot/login этого release пока не выполнен.

## Что фиксируется

`capture` проверяет sealed bundle/model, installed plist ownership и mode
0600, точный health profile и свежий Prometheus scrape. Дополнительно
проверены Python 3.13.12 arm64, все 34 dependency pins resident package и
`pip check`; dependency inspection не загружает MLX модель. Сохраняются
registration file SHA/seal, оба native service states, четыре owned процесса
с UTC-временем начала, committed source hashes и OS session identity.

OS identity включает private host fingerprint, `kern.bootsessionuuid`,
`kern.boottime` и общий GUI audit session обоих jobs. `SessionGetInfo`
подтверждает graphic access именно этого session ID. Native форматы и
API проверены на текущем Mac; [Apple API documentation](https://developer.apple.com/documentation/security/sessiongetinfo(_:_:_:)?language=objc)
и установленный SDK определяют типы аргументов и session attributes.

`verify` требует baseline file SHA, закреплённый отдельно от содержимого
файла. Повторный расчёт внутреннего seal после изменения baseline не
позволяет обойти этот pin. Bundle/profile, registration bytes, plists,
environment и measurement source bytes должны сохранить идентичность.
Исторические inputs перепроверяются через исходный Git commit.

| Исход | Значение | Exit code |
|---|---|---:|
| `baseline_recorded` | Готовая исходная установка и её OS identity сохранены | 0 |
| `awaiting_event` | Установка готова, требуемая смена boot/login ещё не наблюдалась | 2 |
| `verified` | Требуемый event наблюдался, процессы начались после baseline, profile/ownership/pins и свежий scrape проверены | 0 |
| `failed` | Нарушена привязка, chronology, готовность или проверка зависимостей; сохранён failed receipt | 1 |

Для `--event boot` нужен новый boot UUID и boot time после baseline. Новый
GUI login в том же boot не закрывает boot gate. Для `--event login` нужна
новая пара boot UUID / GUI session ID. После reboot числовые SID/PID могут
повториться: проверяются новый boot и времена начала процессов; отсутствие
старого числового PID не используется как доказательство. Простая смена PID
в том же GUI session возвращает `awaiting_event`.

## Использование

Команды читают собственные services, health, metrics и package metadata.
Они не bootstrap/bootout jobs и не отправляют diagnostic inference. Output
создаётся только новым файлом внутри `docs/private`, mode 0600. Default
ожидание готовности — 90 с; `--wait-s` допускает 0–180 с.

```bash
python3 scripts/check-decision-resident-session.py capture \
  --bundle "$HOME/Library/Application Support/Agat/decision-shadow/releases/<release>" \
  --expected-seal <bundle-seal> \
  --output docs/private/session-baseline.json

# Сохранить выведенный file SHA отдельно. После фактического boot/login:
python3 scripts/check-decision-resident-session.py verify \
  --bundle "$HOME/Library/Application Support/Agat/decision-shadow/releases/<release>" \
  --expected-seal <same-bundle-seal> \
  --baseline docs/private/session-baseline.json \
  --baseline-sha256 <pinned-baseline-file-sha> \
  --event boot \
  --output docs/private/session-after-boot.json
```

Для login без reboot применяется `--event login`. Baseline и его pin должны
сохраняться в постоянном workspace; временный checkout не считается местом
хранения до следующего boot. После смены package, registration или source
bytes нужно создать новый baseline до соответствующего следующего event.
Readonly проверка не назначает temperature, qualification или routing.

Согласно [Apple launchd guide](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html),
user agents загружаются при login и получают SIGTERM при logout. Эта
CLI проверяет готовность после наблюдаемого event; она не доказывает
многосуточную доступность, причину прежнего timeout или production SLO.
Bounded crash-loop и независимые human reviews/calibration/holdout остаются
отдельными gates.

## Проверки tooling

25 целевых tests проходят: настоящий новый event в fixtures, прежняя сессия,
повторная выдача SID/PID, ложная chronology, другой host/UID, scalar aliases,
изменённые registration/profile/plists/environment, старые process births,
неверный baseline SHA, rehashed corruption, exclusive private outputs и
сохранение failed observations. Dependency tests проверяют точные pins,
normalization collisions, Python/platform drift и `pip check` failure.
Fixture event не объявляется настоящим reboot/login этого Mac.

Полный `docs:check` прошёл: 628 Python tests, четыре explicit Docker opt-in
skips, 12 Node checks, process catalog и локальные ссылки. После commit
`7ed2770bd9eb37be81de2b95918c1069e462fc8c` настоящий `capture` вернул
`baseline_recorded`: 35 committed inputs, 34 dependencies, четыре owned
процесса и свежий scrape проверены. Немедленный `verify --event boot`
вернул **`awaiting_event`, exit 2**: boot UUID и GUI session не изменились.

Отдельный native audit подтвердил seals/source bytes, неизменные
package/profile/registration, прежний PID set, настоящие boot/GUI fields,
готовность и raw counters: computed 1, rejected 0, failed 0. Девять checks
прошли. [Публичная сводка](../evidence/2026-10-07/resident-session-gate/result-summary.json)
закрепляет baseline file SHA
`582cb7b1a0c4e6fc77406dfaa15cdfe9b3050cd8e5389de1045a08ac47bbe67e`.
Baseline сохранён отдельным mode 0600 файлом в постоянном workspace;
raw evidence и measured source сохранены в проверенном private архиве.
**Boot/login gate остаётся открытым** до реального нового event.
