# Управляемая остановка Temporal model launcher

06.10.2026 MSK. **CLI Temporal/RAG launcher обрабатывает SIGTERM и SIGINT
через cooperative cancellation, освобождает собственные process groups
и сохраняет failed experiment report.** Это подготовка следующего
[wired native gate](./temporal-wired-profile.md); inference и Workflow
Commands этим изменением не меняются.

До исправления реальные OS-сигналы завершали launcher с `-15` и `-2`
без failed report. SIGTERM также обходил штатный teardown. Regression
fixtures используют настоящий launcher, process inventory, process groups
и isolated child; ответы моделей и БД являются fixtures, GPU не запускается.
Первоначальные ошибки тестовой изоляции сохранены отдельно и не принимаются
за signal regression.

Handler сохраняет первый сигнал и возвращается. Launcher проверяет его
после сохранения Popen handle/ownership, при readiness и warmup, между
transport launches и во время ожидания workload. Отмена записывается как
`ExperimentInterrupted`, пропускает обычный failed-response drain и
переходит к teardown. Повторный сигнал во время cleanup не прерывает его
и не заменяет исходную причину. Прежние SIGTERM/SIGINT handlers
восстанавливаются при выходе из CLI, включая preflight exception.

Выбор соответствует
[рекомендации Python](https://docs.python.org/3.13/library/signal.html#note-on-signal-handlers-and-exceptions):
exception из signal handler может возникнуть между произвольными bytecode
операциями, включая acquisition/registration ресурса. Наш handler не бросает
exception; остановка происходит только в явных checkpoints.

Проверены четыре OS-сценария: SIGTERM при warmup, SIGINT при активном
workload, сигнал между spawn и регистрацией handle, повторный сигнал
во время cleanup. Все собственные наблюдавшиеся PID отсутствуют после
report; unload, original stop reason и пустые cleanup errors перепроверены.
Дополнительный check подтверждает восстановление прежних handlers при
return и exception. Все пять новых checks прошли; целевой Temporal набор —
100 tests, четыре skips для явно включаемых Docker fixtures.

Остановка ждёт текущую операцию до следующего checkpoint с прежними
HTTP/command budgets. Немедленное прекращение GPU computation или новый
shutdown SLO не заявляются. Profile, deadline inference 5000 мс,
cache 128 МиБ, input limit 2048 и workload budget 600 с сохранены.
Interrupted launcher остаётся failed и отвергается полным verifier.
Предметная qualification и owner/SLO открыты; routing выключен.

Полный повтор `docs:check` с Python 3.13.12 / Node 24.14.0 прошёл:
603 Python tests (четыре skips), 12 Node tests, 2314 local link targets
и generated catalog. Первый полный check и отдельный repeat failed
на прежнем idle-timing fixture; его исходники и ожидания сохранены
без изменений, оба failed logs retained. Успешный повтор не устанавливает
причину этих timing failures.
