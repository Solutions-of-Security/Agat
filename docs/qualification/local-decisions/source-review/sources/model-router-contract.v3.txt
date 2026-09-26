# Model Router и hardware benchmarks

АГАТ 0.6 маршрутизирует agent stages по фактическим возможностям локальных моделей и состоянию вычислительных узлов. Router сохраняет outbound-only pull-модель: coordinator не открывает model endpoint и не инициирует соединение с worker.

## Поток выдачи lease

1. Worker регистрирует RAM/VRAM, runtimes и `modelProfiles`.
2. Heartbeat обновляет загрузку CPU/RAM/GPU, батарею, температуру, VRAM и мощность.
3. При каждом poll coordinator строит общий список свежих online workers со свободными слотами.
4. Для первого доступного queued stage применяются model pin, runtime и hard constraints policy.
5. Оставшиеся пары `node + model` ранжируются по стратегии.
6. Lease получает только worker, занявший первое место. Остальные workers не могут забрать зарезервированный stage.
7. Решение сохраняется в `stages.routing_json`, worker snapshot, event `routing.selected`, run DTO и execution manifest.

Если попытка завершилась ошибкой или lease истёк, следующая выдача исключает прежнюю пару `node + model`, пока существует другая совместимая альтернатива. При единственном доступном варианте retry остаётся на нём.

## Профили моделей

Для Ollama worker в режиме `AGAT_MODEL_DISCOVERY=auto` один раз при старте читает:

- [`GET /api/tags`](https://docs.ollama.com/api/tags) — размер, parameter size и quantization;
- [`POST /api/show`](https://docs.ollama.com/api-reference/show-model-details) — capabilities, native context window и parameter count.

OpenAI-compatible API не стандартизирует context window и hardware capabilities. Поэтому запрос к Ollama native API является best-effort: при несовместимом provider worker продолжает работу и публикует только явно заданные профили. Model calls для discovery не выполняются.

Явные значения задаются JSON-объектом или массивом и перекрывают обнаруженные поля:

```bash
export AGAT_MODEL_PROFILES_JSON='{
  "qwen3:8b": {
    "qualityScore": 84,
    "contextWindow": 65536,
    "capabilities": ["completion", "tools"],
    "parameterCount": 8200000000,
    "parameterSize": "8.2B",
    "quantization": "Q4_K_M"
  }
}'
```

Имя каждого профиля обязано точно совпадать с элементом `AGAT_WORKER_MODELS`. `qualityScore` — нормированная оценка 0–100 из доверенного eval-процесса оператора. Router не выдаёт субъективный score самостоятельно.

## Hardware и energy signals

| Переменная | Назначение |
|---|---|
| `AGAT_WORKER_VRAM_MB` | Общий VRAM узла в MiB |
| `AGAT_WORKER_VRAM_USED_MB` | Текущий занятый VRAM в MiB |
| `AGAT_WORKER_POWER_WATTS` | Наблюдаемая или оценочная мощность узла |
| `AGAT_WORKER_GPU_PERCENT` | Текущая загрузка GPU |
| `AGAT_WORKER_TEMPERATURE_C` | Температура ускорителя/системы |
| `AGAT_WORKER_BATTERY_PERCENT` | Заряд мобильного или edge-узла |
| `AGAT_WORKER_ON_BATTERY` | Признак питания от батареи |

Поля не подменяются нулями: отсутствие измерения означает `unknown`. Для production желательно публиковать их через hardware-specific agent или systemd environment generator.

## Пассивный benchmark

Synthetic prompt при старте не запускается. Worker измеряет wall time только завершённых OpenAI-compatible model calls и отправляет:

- input/output tokens из provider usage;
- суммарный `modelDurationMs`;
- `energyJoules`, если задана мощность.

Coordinator вычисляет:

```text
tokensPerSecond = outputTokens / modelDurationSeconds
joulesPer1kTokens = energyJoules / outputTokens × 1000
```

В SQLite хранится число samples, накопленные tokens/duration и EWMA: 70% предыдущего значения + 30% нового наблюдения. Для routing используется benchmark не старше 30 дней. Это соответствует модели usage-метрик Ollama, где token counts и durations являются измеряемыми полями ответа: [официальное описание usage](https://docs.ollama.com/api/usage).

Пассивный benchmark отражает реальную нагрузку, prompt length, tool loop и состояние железа. Он полезнее лабораторной заглушки для fleet scheduling, но не является воспроизводимым сравнением качества моделей.

## Policy

Policy глобальна для scheduler и редактируется в разделе **Модели** или через `PATCH /api/v1/settings/model-router`.

| Поле | Default | Поведение |
|---|---:|---|
| `enabled` | `true` | Включает глобальное ранжирование; при `false` сохраняется first-compatible poll |
| `strategy` | `balanced` | `balanced`, `performance` или `efficiency` |
| `minContextTokens` | `0` | Отбрасывает известный недостаточный context window |
| `minQualityScore` | `0` | Отбрасывает модель ниже доверенного quality score |
| `minBatteryPercent` | `30` | Не выдаёт stage разряженному узлу на батарее |
| `maxTemperatureC` | `85` | Не выдаёт stage перегретому узлу |
| `allowUnknownProfiles` | `true` | Разрешает rolling upgrade со старыми workers и помечает решение uncertainty |

Стратегии:

- `balanced` — минимальный parameter count/size среди моделей, прошедших constraints; затем throughput, quality и health;
- `performance` — максимальный свежий throughput; затем quality, health и footprint;
- `efficiency` — минимальный `joulesPer1kTokens`; затем footprint, health и throughput.

Model pin агента остаётся hard constraint. Если проект выдаёт lease-scoped MCP tools, модель с известным профилем без `tools/function-calling` исключается. Неполный профиль допускается только при `allowUnknownProfiles=true`.

Если `minContextTokens` или `minQualityScore` больше нуля, неизвестное значение допускается лишь в compatibility-режиме. Для строгого SLA выключите `allowUnknownProfiles` после обновления всего fleet.

## API и данные

`GET /api/v1/overview` содержит:

- `modelRouter.policy` и coverage counters;
- `nodes[].vramMb`, расширенные metrics;
- `nodes[].modelProfiles[].benchmark`.

`PATCH /api/v1/settings/model-router` требует роль `admin` и принимает частичный или полный policy object.

Worker registration/heartbeat принимает `vramMb` и `modelProfiles`. Completion metrics дополнены `modelDurationMs` и `energyJoules`.

Migration v10 добавляет:

- `nodes.model_profiles_json`, `nodes.vram_mb`;
- `stages.routing_json`;
- `model_benchmarks` с `ON DELETE CASCADE` от node;
- setting `model_router_policy`.

Старые workers продолжают получать lease благодаря `allowUnknownProfiles=true`. После обновления fleet дождитесь нескольких реальных запусков, проверьте coverage в разделе **Модели** и только затем включайте строгие context/quality thresholds.

## Проверка

```bash
npm run typecheck
npm test
npm run build
```

Минимальный smoke test:

1. Подключить два workers с разными моделями или железом.
2. Оставить модель агента пустой.
3. Выполнить по одному stage на каждом worker для накопления samples.
4. Выбрать `performance` и запустить следующий stage.
5. В run trace открыть `routing.selected` и сверить `selectedModel`, node, signals и reasons.

## Осознанные границы 0.6

- quality не синтезируется из latency или размера; источник — внешний/golden eval score;
- OpenAI-compatible providers без discovery требуют явного профиля;
- benchmark пока node-local и не нормализует prompt/output mix;
- стоимость для локального inference представлена energy, а не условной облачной ценой;
- router не заменяет будущие quotas, data residency и project-scoped task queues.
