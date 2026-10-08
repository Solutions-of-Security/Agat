# Offline replay public workflow crash/recovery v3

09.10.2026 MSK. Продолжение [in-flight crash/recovery](./public-workflow-inflight-recovery.md):
общий [verifier CLI](../../../../scripts/verify-public-support-workflow.py)
теперь принимает v3 рядом с прежними v1/v2. Выбор зависит от версии
закреплённого launch plan. Неизвестные версии и смешение plan/result/recipe
отклоняются. Исходные свидетельства не исполняются как Python-код.

Независимые SHA-256 исходного context, plan и result остаются обязательными.
Проверка перечитывает historical Git contributors, журнал всех instances,
durable caller returns, crash/recovery barriers, ownership, active HTTP
snapshot и отдельные counter epochs. Прерванный handler сохраняет
`unknown_after_crash`; его не добавляют к завершённым model calls.

CLI фиксирует собственный commit, SHA contributors и версию Python.
Изменение исходников или consumed artifacts отвергается до публикации
отчёта. Результат создаётся исключительно в новом каталоге `docs/private/`
с правами 0700, JSON — 0600; существующие результаты не перезаписываются.
`reportedCleanupComplete` означает проверенную историческую запись,
`liveCleanupVerified=false` сохраняется. Offline replay не обращается к
модели, сети, текущим PID и не отправляет сигналы процессам.

Пример запуска из корня проекта (значения pins берутся из отдельно
сохранённого original receipt):

```sh
python3 -B scripts/verify-public-support-workflow.py \
  --evidence-dir docs/private/public-workflow-recovery-native-20261008 \
  --context-profile docs/private/public-context-native-20261008/context-profile.json \
  --context-profile-file-sha256 CONTEXT_SHA256 \
  --plan-file-sha256 PLAN_SHA256 \
  --result-file-sha256 RESULT_SHA256 \
  --output-dir docs/private/public-workflow-recovery-replay-20261009
```

Проверка digest входов и contributors согласуется с
[SLSA provenance](https://slsa.dev/spec/v1.2/provenance), но этот локальный
диагностический report не объявляется SLSA attestation. Размеры JSON
ограничены до parsing согласно рекомендации
[Python JSON](https://docs.python.org/3/library/json.html).

Шесть новых [regression tests](../../../../scripts/test/test_decision_public_workflow_recovery_verification.py)
проверяют actual temporary Git, запуск настоящего CLI, полный source
inventory, private exclusive publication, raw pin до dispatch,
неизвестные/смешанные версии, source/artifact drift и отказ до обработки
при public/symlink output. Прежние v1/v2/v3 проверки сохраняются.

Native offline replay и полная регрессия выполняются на committed
исходниках; их результаты будут закреплены отдельно. Human labels,
owners, customer SLO, calibration/holdout и actual boot/login не возникают
из проверки свидетельств. Маршрутизация остаётся выключенной.
