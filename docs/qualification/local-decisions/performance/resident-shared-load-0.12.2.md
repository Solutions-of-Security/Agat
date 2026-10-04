# Совместная нагрузка постоянного runtime 0.12.2 и Qwen

05.10.2026 MSK. После [постоянного rollout](../shadow/observability/resident-rollout.md)
два реальных совместных прогона прошли независимую проверку: **480 measured
calls и восемь отдельных warmup**, без ошибок, смены решений, профиля или
наблюдаемых runtime PID. Постоянные decider и Prometheus продолжают работать;
четыре временных процесса опыта завершены. Qualification — `not_assessed`,
routing выключен. Причина прежнего внепланового exit 75 остаётся открытой.

## Закреплённый опыт

Measured commit — `b1a978be2707b8a2c93b641785c5551edc375b34`, merged main
после PR #120 и всех 15 успешных CI checks. Приватный driver сверил 39
committed source/input files, resident package seal и полный
[профиль 0.12.2](./profiles/runtime-0.12.2.json): isolated spawn, deadline
5000 мс, input limit 2048 tokens, allocator cache 128 МиБ. Обслуживающие
исходники, model bytes, policy и calibration не менялись.

Использован прежний [shared-load инструмент](../../../../scripts/benchmark-decision-shared-load.py)
и [development export](../development/evidence/2026-09-26/dataset.json) из
15 Choice/Boolean inputs. Два rounds на фазу, два warmup pairs, бюджет
600 секунд на каждый опыт; один активный вызов на модель, без retry.
Каждый опыт содержит 120 decider и 120 primary measured calls, плюс по два
warmup каждой модели. Holdout/calibration и новые предметные примеры не
использовались.

Primary — настоящий структурированный label generator Ollama **0.35.1**,
`qwen3:8b`, digest
`500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`.
Temperature 0, seed 0, context 8192, максимум 128 output tokens,
`think=false`. Digest проверяется перед каждым вызовом. Отдельный owned
Ollama process слушал свободный loopback port с cloud features выключенными,
`MAX_LOADED_MODELS=1`, `NUM_PARALLEL=1`, keep-alive 5 минут. Модели уже
находились на машине; новые веса не скачивались.

Обе модели остаются загруженными и в одиночных controls. Семь `/api/ps`
снимков каждого опыта подтвердили Qwen с context 8192 и
`size = size_vram = 6 528 046 202` bytes. [Ollama API](https://docs.ollama.com/api/ps)
определяет это как состояние загруженной модели; оно не является максимумом
совместной unified memory. Параллельность и context закреплены согласно
[Ollama FAQ](https://docs.ollama.com/faq), поскольку оба влияют на память.
Системные memory/wired limits не менялись.

## Задержки и повторяемость

Каждая непустая ячейка содержит p50 / p95, мс, по **30 вызовам**.
Nearest-rank p95 здесь — 29-е значение упорядоченного ряда; максимум
сохранён отдельно в [публичной сводке](./evidence/2026-10-04/resident-shared-load-0.12.2/result-summary.json).

| Фаза | Decider, опыт 1 | Primary, опыт 1 | Decider, опыт 2 | Primary, опыт 2 |
|---|---:|---:|---:|---:|
| Decider до | 291,568 / 391,545 | — | 273,363 / 359,676 | — |
| Primary до | — | 940,954 / 1663,793 | — | 273,959 / 1377,177 |
| Последовательно | 283,973 / 1049,417 | 275,723 / 1247,834 | 262,856 / 471,296 | 275,888 / 1216,893 |
| С перекрытием | 315,452 / 556,576 | 399,310 / 1543,532 | 381,301 / 1248,975 | 504,098 / 1837,539 |
| Primary после | — | 264,344 / 1208,407 | — | 307,446 / 1583,242 |
| Decider после | 274,971 / 380,341 | — | 275,417 / 405,257 | — |

Полное время benchmark с warmup и контрольными API checks — 103 625,458
и 88 383,527 мс. Startup загрузки primary исключён из measured phases.
Максимальные decider wall times — 1870,782 и 2040,562 мс; они не скрыты
в p95 и существенно превышают медианы. Оба опыта завершили полный план:
busy, timeout, invalid responses и изменения подписей решений обеих моделей
отсутствуют, включая сравнение между опытами.

Во всех 60 overlapping pairs действительно пересеклись интервалы HTTP
вызовов; в sequential pairs перекрытия нет. Это не доказывает одновременное
исполнение GPU kernels. Медианы обеих моделей при перекрытии выше соседних
одиночных controls, но caches, thermal state и другие приложения не
контролируются. Первые primary calls также отличаются от последующих.
Опыт не выделяет причинную стоимость concurrency и не является прямым
сравнением с [runtime 0.6.0 / Ollama 0.34.2](./shared-load.md).

## Supervisor и настоящий scraper

Driver сохранил **95 samples**: health/profile, реальные launchd paths/PID,
собственное process tree с RSS отдельно по процессам и raw Prometheus API.
Наблюдение выполнялось примерно раз в две секунды; сам scraper работал с
прежним интервалом 15 секунд. Каждый metric sample содержит 47 finite series:
46 runtime series и `up`, с ожидаемыми job/instance labels. Во всех samples
`up=1`; computed counter вырос **1 → 245**, ровно на 244 decider calls
включая warmup. В финале дождались scrape с этим приростом.

HTTP parent, isolated inference child и Prometheus PID стабильны во всех
снимках. Между сохранёнными начальным и конечным stderr нет retirement
events. Эти дискретные наблюдения не исключают все кратковременные события
между samples и не устанавливают причину исторического отказа. RSS разных
процессов не суммируется как unified-memory footprint; [MLX memory API](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.get_active_memory.html)
также различает активные allocations и системный расход с cache.

Независимый verifier проверил historical/current sources, seals/log hashes,
JSONL samples, все строки и их input hashes, фазовые counts и nearest-rank
summaries, временные интервалы, signatures, model residence, counters и
идентичность parent/child. Qwen выгружен: `/api/ps` вернул пустой список.
Четыре наблюдавшихся benchmark-owned PID отсутствуют; два прежних resident
jobs остаются установленными и доступными.

Raw reports, process inventories, stdout/stderr, Prometheus API, driver и
verifier находятся в `docs/private`. Публичный export использует явный
allowlist counts, timings, pins, hashes и outcomes. Requests, source text,
gold labels, outputs, PID и host paths не публикуются.

`npm run docs:check` на Python 3.13.12 / Node 24 прошёл: 528 Python tests
с четырьмя прежними opt-in skips, 12 Node tests, 2237 local link targets
и process catalog. Дополнительный verifier пересчитал input/output token
distributions и допустимые token bounds; исходный verification artifact
сохранён отдельно. Удалённые CI checks проверяются перед merge.

## Следующий gate

Короткая совместная нагрузка на 0.12.2 подтверждена. Следующий инженерный
шаг — более длительный заранее ограниченный совместный soak с сохранением
каждого completed pair, supervisor/retirement evidence и первого отказа.
Позже [инструмент этого gate и настоящий 180-секундный smoke](./shared-soak.md)
прошли проверку. Полный 7200-секундный совместный план не запускался;
пользователь попросил завершить этап tooling/smoke и остановиться.
Повторяемые development inputs не заменяют независимые бизнес-источники,
human reviews, calibration/holdout и согласованные owner/SLO. При отсутствии
нового отказа исторический exit 75 останется необъяснённым; оснований менять
memory limits, deadline или включать routing этот опыт не даёт.
