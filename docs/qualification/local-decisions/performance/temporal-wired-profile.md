# Wired profile в Temporal/RAG launcher

06.10.2026 MSK. **Launcher и offline verifier теперь связывают explicit
wired budget с выбранным profile, initial startup и recovery.** Это
подготовка нового native Temporal/RAG gate; реальный опыт с wired runtime
ещё не выполнен.

После [native wired package gate](./resident-wired-package-0.12.3.md)
обнаружено, что `run-temporal-real-rag.py` допускал только default export:
wired metadata отклонялись, а командная строка restart не передавала budget.
Новый `--shadow-wired-limit-mib` требует оба runtime аргумента и matching
committed public `--shadow-profile`. В plan записывается
`decision.wiredLimitMiB`; initial startup и каждый recovery строят ту же
команду. Profile/config/plan mismatch отклоняется **до Popen**.

Без explicit option профиль сохраняет прежнюю identity без wired field.
Значение 0 передаёт `--wired-limit-mib 0` и требует profile с нулевым
`allocatorWiredLimitBytes`; 4096 — соответствующие 4294967296 bytes.
Boolean, отрицательные, fractional и выходящие за диапазон значения
отклоняются. Native OS/device guards прежнего
[runtime opt-in](./wired-memory-budget.md) выполняются до model load.
Deadline 5000 мс, max input 2048, cache 128 МиБ, policy и calibration
остаются закреплёнными условиями этого эксперимента.

Reference path канонизируется в public docs; private paths и symlink escape
отклоняются. Verifier независимо восстанавливает version/implementation
из архивированных runtime bytes и wired metadata из строго проверенного
plan option. Перезаписанные и заново hashed profile/plan не обходят binding.
Canonical comparison также сохраняет тип scalar: Python
[сравнивает bool с int](https://docs.python.org/3.13/library/stdtypes.html#numeric-types-int-float-complex),
но `false`, `0` и `0.0` имеют разные profile fingerprints. Byte budget
проверяется как integer до spawn; archived reference сверяется в canonical
JSON. Исторические default references продолжают проверяться по своим исходным
source, без добавления им нового wired budget.

[Temporal описывает replay](https://docs.temporal.io/workflows) как
восстановление Workflow по Event History; внешние API/model calls выполняют
Activities. Wired configuration остаётся в owned model process и pinned
experiment plan. Это изменение launcher/verifier; последовательность
Workflow Commands и worker routing не изменяются. Следующий native gate
должен снова подтвердить fallback, snapshots, model/worker recovery,
исходные histories и независимый replay при новом profile.

Семь новых regression scenarios воспроизведены до fix; перепроверка
выявила ещё два scalar-alias сценария, также воспроизведённые до исправления.
16 profile tests и полный целевой набор 58 tests прошли: explicit 4096 и zero fixture,
early CLI/startup rejection, одинаковые argv initial/restart, rehashed
corruption и совместимость исторического Temporal/RAG evidence. Zero
является unit fixture; native zero-budget опыт не заявляется.

После scalar fix полный `docs:check` прошёл: 598 Python tests (четыре
platform skips), 12 Node tests, 2311 local link targets и generated process
catalog. Локальные checks использовали Python 3.13.12 и Node 25.8.0;
обязательный CI использует Node 24.

Длительный shared gate измеряется в отдельном worktree с прежним frozen
source; новые launcher edits не входят в его runtime или protocol.
Предметная qualification, calibration, owner/SLO и boot/login acceptance
открыты; routing выключен, `not_assessed`. Этот этап не выполняет постоянный
rollout нового resident.
