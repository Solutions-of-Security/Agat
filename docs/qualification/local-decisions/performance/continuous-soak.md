# Непрерывная нагрузка одного закреплённого runtime

Дата: 03.10.2026. Инженерный gate после [полного Temporal/RAG повтора](./cleanup-repeat.md): длительная доступность одного локального decider без автоматических restart. Instrumentation не меняет serving параметры, веса, policy или calibration; каждый опыт требует явный профиль измеренной версии. Качество решений и производственные SLO остаются `not_assessed`, автоматическая маршрутизация выключена.

04.10 [полный двухчасовой повтор на 0.12.2](./continuous-soak-0.12.2.md) завершён: восемь блоков, 25 433 измеряемых запроса, ошибок и изменений решений нет; независимый verifier и cleanup прошли. Исторические короткие проверки инструмента ниже сохраняют свои исходные версии и границы.

## План и границы

[Launcher](../../../../scripts/run-decision-soak.py) владеет одним HTTP runtime и его изолированным inference process. План по умолчанию: **7200 секунд измеряемой нагрузки**, восемь блоков по 900 секунд, concurrency 1, три отдельных warmup только перед первым блоком, timeout HTTP 10 секунд и inference 5 секунд. Длительность не включает startup, проверку checkpoint и warmup.

[Обычный endurance probe](../../../../scripts/benchmark-decision-endurance.py) сохраняет прежние пределы: до 1800 секунд и 10 000 запросов на один блок. Новый [контроллер](../../../../scripts/lib/decision_soak.py) объединяет до 32 блоков при общем плане до двух часов; runtime между блоками остаётся тем же. Последний запрос блока может завершиться после его временной границы. Сериализация отчёта и проверка `/health` создают измеряемые паузы; это closed-loop нагрузка, без гарантии постоянной интенсивности поступления запросов.

Используется только [approved development-набор](../development/evidence/2026-09-26/dataset.json), 15 Choice/Boolean случаев. Их повторение проверяет стабильность вычислений и доступность, но не создаёт независимые примеры качества. Holdout и calibration cases отклоняются до первого обращения к runtime. Primary generator, embeddings, coordinator, Temporal и бизнес-коннекторы в этот отдельный soak не входят.

План до запуска фиксирует Git commit и SHA исходников, dataset, полный профиль, policy, manifest без локального пути к snapshot, Python и точные версии MLX-зависимостей. Измеряемые исходники и входы должны быть committed; их изменённые или untracked версии отклоняются. Статус живого runtime должен совпасть с закреплённым профилем до warmup. Проверка resident checkpoint применяется до чтения весов и admission процессов.

## Наблюдения и остановка

Все сырые артефакты сохраняются в новой директории `docs/private`: план, приватные runtime/observation logs, отдельные sealed block reports и итоговый launcher report. Директория имеет режим 0700, журналы 0600. В техническом журнале находятся ID, fingerprints, статусы, latency и tokens; исходный текст и разметка в inference descriptor и journal не добавляются.

Каждый завершённый запрос сразу записывается в observation journal с flush. Блок сохраняется до начала следующего. При ошибке записи уже существующие журналы и предыдущие блоки остаются на месте; дальнейшая нагрузка прекращается. Flush не обещает сохранность при потере питания или физическом отказе storage.

Сигналы SIGINT/SIGTERM устанавливают флаг остановки. Уже идущий HTTP-вызов завершает свой ограниченный timeout; затем probe сохраняет частичный блок, launcher закрывает принадлежащую ему process group и проверяет ранее известные PID. Handler не выбрасывает исключение посреди записи артефакта. SIGKILL launcher и аппаратное отключение не дают этой гарантии.

Один объект RSS sampler используется во всех блоках. Для каждого переданного PID проверяются start time и parent PID; исчезновение или замена процесса прекращает эксперимент. RSS хранится отдельно по процессам и не суммируется как unified-memory footprint. Health/profile проверяется на границах окон и блоков. Неизменный профиль проверяется перед первым решением каждого блока; fingerprints решений сопоставляются между warmup и всеми блоками. Первый отказ, изменение решения, исчерпание request cap или неизвестный cleanup исключают успешный итог. Ретраев и автоматических restart нет.

Наблюдение раз в 30 секунд не обнаруживает каждое кратковременное изменение между samples. Process start/parent checks служат дополнительным свидетельством идентичности; они не являются криптографической аттестацией. Внешняя параллельная нагрузка ОС не контролируется этим инструментом.

## Запуск и независимая проверка

Пример из корня репозитория с установленными pinned зависимостями и resident manifest:

```sh
python3 scripts/run-decision-soak.py \
  --evidence-dir docs/private/2026-10-03/continuous-soak/run-0.12.2 \
  --runtime-python /absolute/path/to/resident-mlx/bin/python \
  --manifest /absolute/path/to/resident-model/decider-2b.json \
  --profile docs/qualification/local-decisions/performance/profiles/runtime-0.12.2.json \
  --policy docs/qualification/local-decisions/policy.shadow.v1.json \
  --dataset docs/qualification/local-decisions/development/evidence/2026-09-26/dataset.json \
  --duration-s 7200 --block-s 900

python3 scripts/verify-decision-soak.py \
  docs/private/2026-10-03/continuous-soak/run-0.12.2 \
  --output docs/private/2026-10-03/continuous-soak/run-0.12.2/verification.json
```

[Offline verifier](../../../../scripts/verify-decision-soak.py) читает source snapshot именно измеренного commit. Проверяет implementation fingerprint, точные pinned dependencies, committed profile/policy/dataset, seals, duration каждого блока и всего прогона, отсутствие пропущенных запросов/окон, пересчёт summaries, идентичность PID во всех samples, полный journal и успешный cleanup. Допуск 0,0011 для вычисленных elapsed/rate summaries учитывает округление timestamps до 0,001 мс; counts, fingerprints и остальные поля сравниваются точно.

Verifier проверяет целостность и согласованность измерений, а не истинность меток и не повторяет модель. Seals — контрольные суммы, а не цифровые подписи. Отменённый, неполный или неисправный прогон не получает verifier `pass`, даже если его JSON заново resealed. Для анализа такого отказа сохраняются частичные evidence.

[Экспорт публичной сводки](../../../../scripts/summarize-decision-soak.py) сначала повторяет полную независимую проверку. Он использует явный allowlist: версии, fingerprints, duration, counts и latency; source text, разметка, PID, host и system resources остаются приватными. Общие p50/p95 рассчитываются по всем измеренным строкам, без warmup и без усреднения percentiles блоков. Три regression tests подтверждают сохранение counts, отказ несвязанной verification и отсутствие приватных полей; неравные размеры блоков отдельно проверяют pooled p95.

Анализатор требует committed собственные исходники и точное совпадение измеряемых файлов с сохранённым snapshot. Public summary фиксирует отдельно measured commit и analysis commit, SHA анализатора и приватных plan/launcher/verification artifacts. Команда после успешного завершения:

```sh
python3 scripts/summarize-decision-soak.py \
  docs/private/2026-10-03/continuous-soak/run-0.12.2 \
  --output docs/qualification/local-decisions/performance/evidence/2026-10-03/continuous-soak-0.12.2/result-summary.json
```

## Проверки реализации

Целевые regression tests проверяют кооперативную отмену после завершённого запроса, profile mismatch до warmup, сохранение первого checkpoint при позднем ENOSPC, отсутствие второй попытки после изменившегося решения, общий sampler и warmup только первого блока. Launcher отдельно проверяется при отказе inventory, журналирования и pre-admission log allocation, SIGTERM и несовпадении dependency set. Verifier отклоняет resealed сокращённую длительность, неверные counts, замену процесса, потерю journal и неизвестный cleanup.

Результаты до полного двухчасового измерения закреплены в [sanitized checks](./evidence/2026-10-03/continuous-soak-tooling/checks.json), исходники измерены на commit `034e0dd`:

| Проверка | Результат |
|---|---|
| Целевые regression tests | 23/23 pass |
| Полный docs/scripts набор | 12 Node tests; 463 Python tests, четыре прежних opt-in Docker skips |
| Runtime regression | 122 tests, три opt-in skips; serving implementation fingerprint неизменен |
| Реальный pinned decider smoke | Два блока по 20 секунд, 209 measured calls, три warmup; 40 229,650 мс измерений; независимый verifier pass |
| Реальный SIGTERM | Два завершённых measured calls и три warmup сохранены; частичный первый блок, второй не стартовал; `cancelled`, launcher fail как ожидается |
| Process identity и cleanup | Smoke verifier подтвердил идентичность; оба запуска завершили все наблюдаемые owned PID, cleanup errors нет |
| Verifier отменённого запуска | Отказал неполному прогону; verification artifact не создан |

Длительный эксперимент фиксируется отдельно. Эти короткие прогоны подтверждают instrumentation и cleanup, но не выполнение двухчасового gate или прикладную qualification.

## Основание выбора методики

[Grafana k6: soak testing](https://grafana.com/docs/k6/latest/testing-guides/test-types/soak-testing/) рекомендует часы/дни нагрузки, предварительные smoke/обычные прогоны и наблюдение ресурсов. Здесь применяется двухчасовой локальный development gate; масштаб и предметные criteria production soak не объявляются выполненными. [Документация Python signal](https://docs.python.org/3/library/signal.html#execution-of-python-signal-handlers) объясняет выполнение handler основным Python thread и последствия исключений из handler; поэтому использован флаг кооперативной остановки. Источники проверены 03.10.2026.
