# Локальный исполнитель типизированных решений

Статус на 07.10.2026: экспериментальный runtime `0.12.3` по [плану локальной модели](./local-decision-model-plan-2026-09-21.md): локальное исполнение, инструменты калибровки, development-сравнение и [shadow-интеграция с worker/coordinator](./qualification/local-decisions/shadow/README.md). Код расположен в [decision_runtime](../decision_runtime/__main__.py). Профили Choice/Boolean/Score включаются явно в API процесса, leases и [редакторе shadow-проверки](./qualification/local-decisions/shadow/editor.md); наблюдения доступны в технических деталях запуска. Автоматическая маршрутизация не включена. [Постоянный resident переключён на 0.12.3 с wired budget 4096 МиБ](./qualification/local-decisions/shadow/observability/resident-wired-rollout-0.12.3.md); два собственных jobs работают, install/stop/reinstall и native audit прошли.

Добавлены [экспертная разметка, калибровка и отдельная проверка holdout](./qualification/local-decisions/calibration/README.md). Реальные экспертные данные пока не предоставлены; инженерный прогон завершился `not_qualified`, обученная температура не назначена default.

В `0.3.0` добавлены [экспорт утверждённой ассистентом разметки и предметное development-сравнение](./qualification/local-decisions/development/README.md). На 15 development-примерах decider выбрал 14 правильных меток, Qwen Base — 7; обратный порядок выявил ошибочное принятое решение decider. Девять отложенных случаев не оценивались. Результат — `diagnostic_only`, без квалификации и включения маршрутизации.

Следующий [генеративный baseline Qwen3 8B](./qualification/local-decisions/baselines/README.md) на тех же входах дал 9/15 меток в исходном порядке и 8/15 в обратном. Его JSON-ответы сравниваются по меткам; текстовая уверенность не подменяет вероятности logits.

[HTTP-прогон под ограниченной нагрузкой](./qualification/local-decisions/performance/README.md) на runtime `0.4.0` дал p95 382.487 мс на 60 последовательных development-запросах. При конкурентности 2 и 4 появились 50% и 75% `busy`; выполненных решений осталось около четырёх в секунду. Это локальный диагностический опыт, не производственный SLO.

В [отдельных ресурсных опытах](./qualification/local-decisions/performance/resources.md) измерены загрузка, первый запрос и MLX active/cache. Ограничение свободного cache до 128–512 МиБ существенно сократило удерживаемые буферы при совпадении ответов на проверенных development-входах. В `0.6.0` такая настройка доступна оператору и входит в закреплённый профиль.

В `0.8.0` команда `profile` и [план эксперимента v2](./qualification/local-decisions/calibration/frozen-profile.md) закрепляют полный профиль до измерений; calibration и holdout проверяются на совпадение профиля и корректную хронологию. Старый план v1 больше не может пройти qualification. В `0.9.0` [preflight CLI](./qualification/local-decisions/calibration/preflight.md) отвергает несовместимый запуск calibration/holdout до первого inference.

В `0.10.0` [выход отказавшего сервиса](./qualification/local-decisions/shadow/service-recovery.md) с кодом 75 позволяет внешнему менеджеру запустить его заново. Реальный foreground-опыт проверил смерть MLX при простое, восстановление с тем же профилем и полный HTTP 504 перед выходом. Генератор launchd готов; нативный опыт остановился на запросе разрешения macOS к Documents до старта runtime, поэтому автоматический restart на этом хосте ещё не подтверждён.

[Десятиминутный HTTP-прогон](./qualification/local-decisions/performance/endurance.md) на `0.9.0` выполнил 3086 запросов без ошибок и изменений решений; p95 289.099 мс. Это продолжительная локальная диагностика, не производственный SLO.

[Реальный трёхэтапный workflow](./qualification/local-decisions/performance/workflow.md) на `0.10.0` завершил 12 процессов, 36 primary-вызовов Qwen3 8B и 18 shadow-решений, включая перекрытие HTTP-вызовов при двух leases. Предыдущий прогон сохранил primary после server timeout. Все итоговые primary-тексты этих опытов содержали ошибки арифметики; сохранность ответа и качество отчёта проверяются отдельно.

[RAG-workflow на 0.12.0](./qualification/local-decisions/performance/rag-workflow.md): 12 процессов, 44 embeddings, 36 primary-вызовов, 18 shadow-решений; два полных источника на каждом этапе и подтверждённое завершение собственных процессов. В 6/12 итогов неверные вычисления. Это проверка локальной цепочки, без qualification, Temporal или внешних бизнес-коннекторов.

[Парный повтор с одинаковыми входами primary](./qualification/local-decisions/performance/rag-paired.md) дал одинаковые наборы запросов/ответов во всех четырёх блоках, верные расчёты в 12 повторных итогах и 18 успешных shadow-ответов. Runtime и его профиль сохранены; overhead и качество на новых задачах этим опытом не квалифицируются.

[Обычный offline-батч 2/4](./qualification/local-decisions/performance/batching.md) проверен на 180 измеряемых строках: метки сохранились, но вероятности изменились до 3.93 процентного пункта. Расхождение возникает уже в backbone. Serving остаётся одиночным; прежняя калибровка на этот прототип не переносится.

[Общий prefix cache](./qualification/local-decisions/performance/prefix-cache.md) также остаётся offline: повторное использование состояния прошло 72 проверки изоляции, но разделение state/question изменило вероятности до 3.48 процентного пункта. Ускорение трёх вопросов с общим state измерено с учётом prefill; для этого вычислительного пути нужны собственные calibration/eval.

В `0.11.0` [отмена при уходе клиента](./qualification/local-decisions/shadow/cancellation.md) проверена на реальном MLX и сквозном worker/coordinator: timeout 100 мс остановил inference до серверного предела 10 секунд, сохранил primary-маршрут и позволил явно восстановить тот же профиль. Обычный HTTP half-close без opt-in сохраняет ответ.

В `0.12.0` добавлены [операционные метрики и примеры alerts](./qualification/local-decisions/shadow/observability/README.md): `/metrics`, readiness, фиксированные счётчики исходов и раздельная latency вычислений/отказов. Реальный MLX, официальный parser и promtool проверены; постоянный мониторинг ещё не подключён.

В `0.12.1` [причина retirement](./qualification/local-decisions/performance/decision-exit-diagnostics.md) записывается в stderr после завершения HTTP-обработчиков и cleanup backend. Timeout, cancellation, смерть ребёнка и явный restart проверены на настоящих весах; новый fingerprint требует отдельной применимой qualification.

В `0.12.2` [исправлено освобождение HTTP admission](./qualification/local-decisions/performance/http-admission-release.md): после завершения вычисления и cancellation watcher слот освобождается до записи ответа. Следующий последовательный запрос больше не получает `busy` из-за незавершённого response writer; настоящая конкуренция во время inference по-прежнему отвергается. Новый профиль закреплён отдельно; [полный двухчасовой повтор](./qualification/local-decisions/performance/continuous-soak-0.12.2.md) и [Temporal/PostgreSQL/RAG gate](./qualification/local-decisions/performance/temporal-runtime-0.12.2.md) прошли.

Отдельный offline [baseline по заголовкам заявок](./qualification/local-decisions/baselines/rules.md) сравнили на тех же 15 development-входах: 2 правильные метки, один принятый ответ. Покрытие недостаточно; правила не подключены к serving.

В `0.12.3` добавлен [явный per-process wired-memory budget](./qualification/local-decisions/performance/wired-memory-budget.md) с проверкой macOS/device bounds до загрузки модели. Default не вызывает setter; opt-in меняет serving fingerprint. Настоящий 30-call pilot с 4096 MiB сохранил решения/tokens прежнего baseline и deadline 5000 мс, затем восстановил resident 0.12.2. [Полный совместный Qwen/decider gate](./qualification/local-decisions/performance/wired-shared-soak-7200-0.12.3.md), [реальный Temporal/PostgreSQL/RAG](./qualification/local-decisions/performance/temporal-wired-runtime-0.12.3.md) и [постоянный rollout](./qualification/local-decisions/shadow/observability/resident-wired-rollout-0.12.3.md) прошли на том же профиле. Следующие gates — boot/login, bounded crash-loop и owner/SLO; причинность прежнего timeout и предметная qualification остаются открытыми.

## Что реализовано

- Локальное исполнение открытых весов Qwen3.5 через MLX на Apple Silicon.
- `choice`, `boolean`, `score`: сеть оценивает разрешённые варианты, код формирует JSON. Генерации текста ответа нет.
- Явные `ok / abstain / error`; недостаточность данных, низкая вероятность и сбой различаются.
- CLI и HTTP API на `127.0.0.1`, ограничение входа и один одновременно исполняемый запрос.
- Фиксация revision весов, SHA-256 файлов модели и tokenizer, версии prompt/runtime/dependencies, fingerprint входа и policy.
- Воспроизводимый eval: accuracy, macro-F1, NLL, Brier, ECE, покрытие, ошибка среди принятых решений, latency и чувствительность к порядку вариантов.
- `prepare-development` и `compare-development`: проверка approved-разметки и исходного seed, исключение отложенных данных, пересчёт сравнения по logits, семейные матрицы ошибок и разбор проблемных случаев.
- Shadow leases для Choice/Boolean/Score, закреплённый профиль, независимая проверка coordinator, ограниченный loopback-вызов, сохранение наблюдения и его повторное использование после retry/restart/safe replay.

Runtime не обучает веса, не вызывает внешний LLM API и не выполняет действий в бизнес-системах. Его исход `ok` означает прохождение экспериментального порога, а не подтверждённую правильность или разрешение на автоматическое действие.

```mermaid
flowchart LR
    A[Состояние, вопрос, варианты] --> B[Проверка контракта и длины]
    B --> C[Локальный Qwen3.5: один forward]
    C --> D[Оценки токенов A–J]
    D --> E[Softmax по разрешённым вариантам]
    E --> F[Порог и abstain: обычный код]
    F --> G[JSON и eval; режим shadow]
```

## Установка и загрузка моделей

Проверяемая конфигурация: macOS arm64, Python 3.13, Apple Silicon с Metal. MLX устанавливается в отдельное окружение; обычным workers и CI-тестам он не нужен. Ориентир только для весов двух BF16-моделей — несколько гигабайт на модель; для установки нужен дополнительный объём диска и память runtime.

Команды выполняются из корня репозитория:

```bash
uv venv --python 3.13 .venv/decision
uv pip install --python .venv/decision/bin/python -r decision_runtime/requirements-mlx.txt
.venv/decision/bin/python -m decision_runtime download decider-2b
.venv/decision/bin/python -m decision_runtime download qwen3.5-2b-base
```

Полный lock зависимостей для этой платформы: [requirements-mlx.txt](../decision_runtime/requirements-mlx.txt); входная зависимость — [requirements-mlx.in](../decision_runtime/requirements-mlx.in). `uv pip freeze` зафиксировал установленное окружение. При обновлении зависимостей повторяются проверки и измерения.

[Каталог моделей](../decision_runtime/models.json) закрепляет точные Hugging Face commits:

| Имя | Репозиторий | Revision | Назначение |
|---|---|---|---|
| `decider-2b` | [Mapika/decider-2b](https://huggingface.co/Mapika/decider-2b) | `b37f7e1ba3fbc9238004cf531fabbee2619973fd` | Дообученный внешний кандидат |
| `qwen3.5-2b-base` | [Qwen/Qwen3.5-2B-Base](https://huggingface.co/Qwen/Qwen3.5-2B-Base) | `b1485b2fa6dfa1287294f269f5fb618e03d52d7c` | Замороженная основа с тем же способом оценки |

Обе model cards указывают Apache-2.0. Загрузчик сохраняет README/LICENSE, если файл опубликован; Python-код из репозитория модели не скачивается. Это сравнение двух весовых checkpoints одним адаптером, а не воспроизведение всех оптимизаций SemIf или официального decider serving.

Модели и локальные manifests сохраняются в `.local-models/decisions/`, исключённом из Git. Другой каталог задаётся `download --store PATH`. `score`, `evaluate` и `serve` требуют manifest существующих локальных файлов: перед загрузкой проверяют SHA-256, включают offline-режим Hugging Face/Transformers и запрещают remote code. Установка и `download` требуют сети; inference — нет.

## Первый вызов

```bash
.venv/decision/bin/python -m decision_runtime score \
  --manifest .local-models/decisions/decider-2b.json \
  --input docs/qualification/local-decisions/request.example.json
```

[Полный пример запроса](./qualification/local-decisions/request.example.json):

```json
{
  "schemaVersion": "agat.decision.v1",
  "id": "evidence-example",
  "state": "Релиз опубликован 18 сентября.",
  "question": "Подтверждён ли факт публикации релиза?",
  "kind": "choice",
  "options": [
    {"id": "supported", "description": "Подтверждён"},
    {"id": "contradicted", "description": "Опровергнут"},
    {"id": "insufficient", "description": "Недостаточно данных", "abstain": true}
  ]
}
```

Разрешено 2–10 вариантов с уникальными ID и описаниями. `state` — непустая строка до 24 000 символов, `question` — до 2 000, описание варианта — до 1 000. По умолчанию общий предел — 2 048 токенов; `--max-tokens` допускает 64–4 096. Превышение возвращает `context_too_long`, контекст не обрезается молча.

| Kind | Обязательные свойства вариантов | Значение `value` при `ok` |
|---|---|---|
| `choice` | ID и описание, при необходимости `abstain: true` | ID выбранного варианта |
| `boolean` | Ровно два варианта, один с `value: true`, другой с `value: false` | Boolean, в том числе полноценный `false` |
| `score` | Различные конечные числовые `value` у всех уровней | Сумма `probability × value` по уровням |

`score` требует осмысленной числовой шкалы: расстояния между уровнями должны допускать усреднение. Для чисто порядковых категорий следует применять `choice`. В Boolean/Score недостаточность выражается пороговым abstain; если нужна отдельная семантическая категория «неизвестно», используйте `choice` с таким вариантом.

В `0.5.0` для межъязыковых Score-запросов добавлено поле `inputFingerprintVersion: "binary64-v1"`. [Пример](./qualification/local-decisions/request.score.example.json) проверяет числовой транспорт; арифметическую проверку комплекта в реальном процессе следует выполнять кодом. Числа остаются числами JSON, в диапазоне ±1 000 000. При hashing их заменяет точное binary64-представление; `-0` нормализуется в `0`. Запросы без нового поля сохраняют прежние input/schema fingerprints. Область калибровки включает версию fingerprint, поэтому артефакт для старой Score-схемы не применяется к новой автоматически. Полная [спецификация контракта](./qualification/local-decisions/shadow/score-contract.md).

В ответе всегда есть `schemaVersion`, `id`, `mode: shadow`, `model`, `policy`, `calibration`, `inputSha256`, `status`, `reason`, `selectedOptionId`, `value`, `distribution`. С `0.5.0` профиль и ответ включают `inputFingerprintVersions`; у нового Score-ответа также есть `inputFingerprintVersion`. У успешно вычисленного распределения дополнительно есть `selectedProbability`, `margin`, `inputTokens`, `generatedTokens: 0`, `durationMs`. На уровне протокола «успешно вычислено» может закончиться как `ok`, так и `abstain`.

| Status / reason | Смысл |
|---|---|
| `ok / accepted` | Победитель прошёл экспериментальные пороги |
| `abstain / abstain_option` | Выбран явный вариант недостаточности данных / «другое» |
| `abstain / below_threshold` | Вероятность или отрыв от второго варианта ниже порога |
| `error / invalid_request` | Некорректный контракт; модель не вызывалась |
| `error / context_too_long` | Вход превышает установленный предел токенов |
| `error / calibration_out_of_scope` | Вопрос или схема вариантов отсутствуют в области подключённой калибровки |
| `error / invalid_scores` | Backend вернул неверную размерность или нечисловые оценки |
| `error / backend_error` | Ошибка вычисления, без раскрытия внутреннего exception |

При `abstain` сохраняются предсказанная метка и распределение, но `value: null`. При `error` метка и значение `null`, распределение пустое. Нельзя проверять результат только на truthiness: сначала обрабатывается `status`, затем нужная метка/значение.

## Вероятности и policy

Вероятности — `softmax(logits / T)` по разрешённым A–J. Они условны на список вариантов; `selectedProbability` не равна проверенной вероятности истинности. Мы намеренно не называем это поле Jev `confidence`: статистики разных реализаций различаются.

Без артефакта калибровки температура — `1.0`, статус `uncalibrated`. В upstream decider опубликована своя температура `1.3`; этот эксперимент не переносит её как калибровку для Агат. С версии 0.2.0 поддерживается `--calibration PATH`: статус `fitted`, скалярная температура и привязка к модели/схеме. Метод и ограничения описаны в [отдельном руководстве](./qualification/local-decisions/calibration/README.md). Fine-tuning, квантование и shared-prefix cache пока не реализованы.

По умолчанию принимаются решения с `selectedProbability >= 0.8` и разницей между двумя лучшими вероятностями `>= 0.1`. Это исходные экспериментальные значения, не продуктовые нормативы. [Файл policy](./qualification/local-decisions/policy.shadow.v1.json) передаётся оператором через `--policy`. Его fingerprint входит в ответ. Клиентский payload и модель не могут менять пороги; неизвестные поля отвергаются.

Prompt `agat.state-first.letters.v1` следует state-first формату [decider prompt](https://github.com/Mapika/decider/blob/main/decider/prompt.py). Каждый вопрос вычисляется отдельно без общего KV/recurrent cache. Tokenizer проверяется на уникальные однотокенные метки и точное продолжение префикса. Адаптер MLX отображает `qwen3_5_text` на встроенную Qwen3.5-реализацию и вычисляет только нужные строки выходной матрицы для последней позиции. Общий backend не заявляется: иная архитектура/квантование отвергаются.

## HTTP API

```bash
.venv/decision/bin/python -m decision_runtime serve \
  --manifest .local-models/decisions/decider-2b.json \
  --policy docs/qualification/local-decisions/policy.shadow.v1.json \
  --port 8766
```

В другом терминале:

```bash
curl --fail-with-body http://127.0.0.1:8766/health
curl --fail-with-body http://127.0.0.1:8766/v1/decisions \
  -H 'Content-Type: application/json' \
  --data-binary @docs/qualification/local-decisions/request.example.json
```

`ok` и `abstain` возвращаются с HTTP 200; неверный payload — 400, длинный контекст или запрос вне области подключённой калибровки — 422, сбой backend — 500, занятый исполнитель — 503. Тело ограничено 128 КиБ, чтение сокета — 5 секундами. Операции выполняются последовательно; конкурентный запрос получает `busy` без скрытой очереди. Сервис не публикуется за пределами loopback, отклоняет Origin и посторонний Host, не пишет тела запросов в access log.

Самостоятельный endpoint не выполняет project authentication/ACL и не хранит результаты; другие локальные процессы имеют к нему доступ. Он не предназначен для сетевой публикации. В `0.4.0` `/health` также возвращает точные `profileJson` и `profileSha256`, а опциональный заголовок `X-Agat-Decision-Profile` проверяет профиль до inference. При worker-интеграции coordinator применяет права lease и сохраняет наблюдение. Deadline worker закрывает клиентское соединение. В `0.11.0` [явный opt-in отмены](./qualification/local-decisions/shadow/cancellation.md) останавливает изолированный inference при EOF/reset; прямой backend и старые клиенты сохраняют прежнее поведение. В `0.7.0` флаг `serve --inference-timeout-ms 2000` включает [отдельный вычислительный процесс](./qualification/local-decisions/shadow/isolation.md): серверный timeout останавливает его, health становится unavailable до перезапуска. Для остановки foreground-сервера — `Ctrl+C`.

### Память свободных буферов

Команды `serve`, `score` и `evaluate` принимают `--cache-limit-mib 128` (целое 0–4096 МиБ). Настройка ограничивает свободный allocator cache MLX после загрузки модели; `0` отключает его reuse, отсутствие аргумента оставляет настройку MLX. Она действует на процесс, в котором CLI держит одну модель. Лимит не ограничивает активные тензоры и не гарантирует общий расход RAM. Освобождение cache происходит при последующей allocation, поэтому snapshot может немного превышать заданное значение.

Значение записывается в `model.allocatorCacheLimitBytes` (`null` для настройки MLX по умолчанию). После смены лимита нужно заново получить профиль и опубликовать соответствующую конфигурацию процесса. Автоматического переноса прежней qualification нет. Числа 128–512 МиБ проверены только на коротком workload этого Mac; для длинного контекста и совместной работы моделей выполните [ресурсный прогон](./qualification/local-decisions/performance/resources.md).

## Проверки и результаты

```bash
npm run test:decision
.venv/decision/bin/python -m decision_runtime evaluate \
  --manifest .local-models/decisions/decider-2b.json \
  --dataset docs/qualification/local-decisions/development.v1.json \
  --split development --reverse-options \
  --output docs/qualification/local-decisions/evidence/my-decider-run.json
```

Для второго checkpoint измените manifest на `.local-models/decisions/qwen3.5-2b-base.json` и укажите новый output. Существующий отчёт не перезаписывается. `npm test` включает dependency-free тесты runtime, в том числе реальные loopback HTTP-запросы; GPU и скачивание модели не требуются.

Методика и измеренные результаты первого этапа сохранены в [паспорте эксперимента](./qualification/local-decisions/README.md). Его dataset содержит 36 синтетических development-примеров трёх общих шаблонов. Он не является human-reviewed gold или независимым holdout. Ошибки backend входят в знаменатель общего accuracy и coverage; условные метрики по вычисленным распределениям отдельно названы `Scored`. Перестановки не увеличивают число независимых примеров. Отчёт `evaluate` содержит `qualityGate: not_configured`: квалификация выполняется отдельной командой `qualify`, описанной в [руководстве второго этапа](./qualification/local-decisions/calibration/README.md).

## Следующий этап

Собрать разрешённые реальные примеры с экспертными метками и группировкой по документам/шаблонам; зафиксировать допустимые ошибки и нужное покрытие. Провести предметное испытание через готовые команды разметки, фиксации плана, калибровки и квалификации. При недостаточном качестве меток сравнить более сильные checkpoints или дообучение. Loader dataset запрещает перенос одной группы, исходного документа или одинакового нормализованного текста между splits; перефразы и близкие шаблоны требуют предметной проверки.

Для текущего корпуса v2 нужны новые независимые группы, calibration-примеры и запросы доступа. Development-разбор выделил случаи `agat-source-019` и `agat-source-021`: достаточность сведений и зависимость от порядка вариантов. Подробности и границы сравнения — в [протоколе третьего блока](./qualification/local-decisions/development/README.md).

Встраивание Choice/Boolean/Score в Агат выполнено через opt-in capability/profile worker, проверку coordinator и постоянный fallback на основной шаг. [Интеграционный протокол](./qualification/local-decisions/shadow/README.md) описывает deadline, retry/restart/safe replay, наследование ACL и реальные свидетельства. Предметная квалификация остаётся открытой. Изменение implementation SHA, включая версию `0.11.0`, параметров памяти и режима изоляции требует нового применимого fit/eval; калибровочные артефакты прежних реализаций не следует переносить автоматически.
