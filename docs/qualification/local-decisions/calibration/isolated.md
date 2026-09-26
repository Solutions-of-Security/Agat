# Калибровка при изоляции inference

26.09.2026, runtime `0.7.1`. Параметр `--inference-timeout-ms` доступен в `score`, `evaluate` и `serve`. Это исправляет ограничение `0.7.0`, где изоляция была только у сервера: backend identity включает способ исполнения и deadline, поэтому fit из прямого `evaluate` не подходил такому серверу.

Во всех командах используйте одинаковые manifest, `--max-tokens`, `--cache-limit-mib` и `--inference-timeout-ms`. Не удаляйте поля identity из calibration artifact ради совместимости. После изменения параметров нужен новый применимый fit/eval; вопрос, описания и значения вариантов также определяют область калибровки.

## Выполненная проверка

На тех же **ранее использованных синтетических fixtures** выполнен полный технический путь. Это повтор инженерного smoke, не новый независимый holdout и не улучшение качества модели. Реальный source-review holdout не запускался.

1. Зафиксирован новый [план](./evidence/2026-09-26-isolated/experiment.json), с исходными policy/criteria.
2. Изолированный `evaluate` получил [12 calibration-результатов](./evidence/2026-09-26-isolated/calibration-scores.json), без ошибок backend.
3. Построен [fit](./evidence/2026-09-26-isolated/temperature.json): `T = 3.1929311809325567`, NLL `1.308588 → 0.750512`. Значения совпали с исходным синтетическим опытом.
4. После fit изолированный `evaluate` выполнил [12 синтетических holdout-запросов в двух порядках](./evidence/2026-09-26-isolated/holdout-scores.json). [Qualification](./evidence/2026-09-26-isolated/qualification.json) вернула **`not_qualified`**, exit code 2: нет экспертных данных, не выполнены риск, объём групп, coverage и accuracy. Маршрутизация выключена.
5. Artifact загружен в `serve` с тем же профилем. [Реальный worker/coordinator smoke](./evidence/2026-09-26-isolated/calibrated-shadow.json) сохранил `abstain / below_threshold`, вероятность 0.7138222866, 707.926 мс и `PRIMARY_BRANCH`.
6. [Вопрос вне схемы](./evidence/2026-09-26-isolated/out-of-scope.json) получил HTTP 422 / `calibration_out_of_scope`; процесс остался здоровым, health ready.

Использованы Apple M1 Max, 32 ГиБ, Python 3.13.12, MLX 0.32.2, mlx-lm 0.31.3, cache 128 МиБ, предел 2048 токенов и deadline 2000 мс. Основной генератор в worker smoke — явная fixture; реален MLX decision backend и путь записи observation. Временный сервер после проверки остановлен.

[Перепроверка](./evidence/2026-09-26-isolated/verification.json) пересчитала fit и qualification gates из raw logits, проверила seals, SHA файлов, совпадение полного identity у evaluate/fit/serve и сохранение primary. Runtime suite: 80 тестов, три необязательных MLX skip; реальные GPU-вызовы выполнены отдельно. Новый CLI-тест проходит `freeze → evaluate → calibrate → score/serve` на CPU fixture и проверяет отказ при смене deadline, включая освобождение дочерних процессов. Предыдущая полная workspace-регрессия, сборка и typecheck зафиксированы в [протоколе 0.7.0](../shadow/evidence/2026-09-26/verification-isolated.json); в `0.7.1` изменена доступность флага Python CLI.

## Воспроизведение

Используйте последовательность [freeze → evaluate → calibrate → qualify](./README.md). В текущем runtime сначала экспортируйте `profile` и используйте его в `freeze --runtime-profile`. К `profile`, обоим вызовам `evaluate` и последующему `score`/`serve` добавьте:

```bash
--max-tokens 2048 --cache-limit-mib 128 --inference-timeout-ms 2000
```

Получение raw calibration logits, например:

```bash
.venv/decision/bin/python -m decision_runtime evaluate \
  --manifest .local-models/decisions/decider-2b.json \
  --policy docs/qualification/local-decisions/policy.shadow.v1.json \
  --max-tokens 2048 --cache-limit-mib 128 --inference-timeout-ms 2000 \
  --dataset docs/qualification/local-decisions/calibration/smoke.v1.json \
  --split calibration \
  --plan docs/qualification/local-decisions/calibration/evidence/new-run/experiment.json \
  --output docs/qualification/local-decisions/calibration/evidence/new-run/calibration-scores.json
```

Dataset и план для предметной квалификации должны происходить из разрешённых независимых групп и человеческого review. Приведённый synthetic dataset предназначен для инженерной проверки. Настройки 2048/128/2000 — параметры этого опыта, не утверждённый SLO. При серверном timeout процесс останавливается и требует перезапуска; механизм описан в [протоколе изоляции](../shadow/isolation.md).
