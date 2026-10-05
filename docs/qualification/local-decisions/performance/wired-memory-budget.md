# Явный wired-memory budget в runtime 0.12.3

05.10.2026 MSK. После [failed совместного soak 0.12.2](./resident-shared-soak-failure-0.12.2.md)
добавлен opt-in `--wired-limit-mib`. Он задаёт per-process residency budget
MLX перед загрузкой модели. Это отдельный serving profile; исправление
прежнего timeout и полный совместный gate пока не подтверждены.

## Контракт настройки

Флаг доступен в `profile`, `score`, `evaluate`, `serve`, в генераторе
LaunchAgent и recovery/launchd probes. При отсутствии флага setter не
вызывается и wired override отсутствует в model identity. Явный `0`
отключает wiring и записывает `allocatorWiredLimitBytes: 0`; положительное
значение записывается в bytes. Explicit zero и upstream default имеют разные
fingerprints. Cache limit остаётся отдельной настройкой.

Тип и диапазон **0–65536 MiB** проверяются до manifest/model loading.
Explicit настройка требует macOS 15+, Metal и корректных device budgets.
Requested bytes должны быть строго меньше total memory и не больше
`max_recommended_working_set_size`. Отказ ОС/API или недостаточный бюджет
останавливают startup до загрузки весов. System-wide sysctl не меняется.
Настройка применяется только в собственном model process до evaluation;
при изоляции это inference child, а не HTTP parent.

Wired limit не ограничивает общий расход памяти или live tensors и не
резервирует всю указанную память заранее. Он задаёт, сколько MLX memory
может оставаться resident. Начальный опыт использует **4096 MiB**, не
автоматическое присвоение всего системного recommended budget: рядом
должен работать Qwen3:8b. Достаточность для всего разрешённого контекста
2048 или иной нагрузки отдельно не доказана.

Например, для нового отдельного profile export:

```bash
python -m decision_runtime profile \
  --manifest /absolute/path/decider-2b.json \
  --policy docs/qualification/local-decisions/policy.shadow.v1.json \
  --max-tokens 2048 --cache-limit-mib 128 --wired-limit-mib 4096 \
  --inference-timeout-ms 5000 --output docs/private/new-wired-profile.json
```

Output должен быть новым. Runtime version/source SHA и wired bytes входят
в полный execution fingerprint; прежняя calibration/qualification к нему
автоматически не переносится. Launchd probe отклоняет wired profile без
совпадающего explicit флага до запуска job.

## Настоящий короткий pilot

Из source commit `d9af6039274c0623664999bec80fc020dfb8fd12` экспортированы
два реальных профиля: [default 0.12.3](./profiles/runtime-0.12.3.json) и
[wired 4096 MiB](./profiles/runtime-0.12.3-wired-4096.json). Старый resident
decider сначала остановлен через проверенный собственный launchd label;
его три PID завершились. Default export и wired process работали по очереди,
одновременно второго decider не было. Prometheus продолжал работать.

Wired pilot выполнил **30/30** HTTP решений: два прохода по прежним 15
development inputs, 16 `ok`, 14 `abstain`, zero generated tokens. Все
выходные signatures и input token counts точно совпали с сохранённым
baseline 0.12.2. Веса, tokenizer, prompt, policy, max input/cache и
серверный deadline **5000 мс** сохранены. Wall p50/p95/max —
**174,480 / 269,040 / 270,083 мс**. Первый запрос процесса включён; cold
start всей машины не утверждается. Primary/Qwen в этом pilot не запускался.

После опыта шесть candidate PID завершены, resident 0.12.2 восстановлен с
прежним bundle/profile; Prometheus PID не изменился. Независимый read-only
verifier заново сверил sources, seals, exact journal, порядок 30 calls,
полные результаты/профили, baseline signatures/tokens, свежий scrape,
ownership и отсутствие всех девяти остановленных PID.
[Public allowlist summary](./evidence/2026-10-05/wired-memory-native-pilot-0.12.3/result-summary.json)
сохраняет aggregates/fingerprints; исходные наблюдения и outputs private.

Guard tests проверяют malformed values, OS/device capacity, API отказ до
load, default/zero behavior и применение до model loading. CLI tests
проверяют все четыре inference commands и реальный spawn handshake;
frozen-profile test отклоняет согласованную смену wired metadata в scores.
Service tests проверяют plist round trip и точное совпадение profile/флага.
Финальные native проверки прошли: 135 runtime tests (три прежних opt-in
skips), 578 repository Python tests (четыре прежних opt-in skips), 12 Node
tests, 2279 локальных ссылок в 251 Markdown-файле и process catalog. Первый
docs-check до profile export отказал на семи bindings tests из-за отсутствия
нового profile file; genuine export исправил эту зависимость, повтор прошёл.

Следующий этап — recipe для нового resident профиля, короткий paired
Qwen/decider опыт и затем новый заранее закреплённый 7200-секундный soak.
Пока постоянные jobs используют 0.12.2. Matching development outputs не
доказывают correctness, calibration или причинность прежнего timeout;
routing выключен, qualification — `not_assessed`.

## Основание решения

[MLX API](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.set_wired_limit.html)
документирует default 0, macOS 15+, physical/system bounds и возврат
предыдущего limit. [Официальный generation context MLX-LM 0.31.3](https://github.com/ml-explore/mlx-lm/blob/v0.31.3/mlx_lm/generate.py)
применяет wired limit и синхронизирует execution перед восстановлением.
Наш direct-logits scorer этот context не вызывает; новый setter применяется
один раз при startup. Документация MLX относится к 0.32.3; API дополнительно
проверен в установленном 0.32.2, сначала в пустом отдельном процессе,
затем в описанном model pilot. Источники проверены 05.10.2026 MSK.
