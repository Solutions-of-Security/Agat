# Отказ полного resident совместного soak 0.12.2

05.10.2026 MSK. Новый полный опыт после [warmup отказа](./shared-soak-warmup-failure.md)
запущен на merged main `b7b9da55369a3da39b3602f31ecae99169bd10c2`.
**Gate failed:** вместо закреплённых 7200 секунд выполнено 3 773 806,379
measured milliseconds. Исходный план, 35 checkpoint блоков, pair journal,
727 native samples и failed protocol сохранены без изменения цели.
Routing выключен; qualification — `not_assessed`.

## Нагрузка и первый отказ

Использованы прежние 15 development cases, два rounds на каждую из шести
фаз, две отдельные warmup пары на блок, максимум один активный запрос на
модель. Retries отсутствуют. Decider — прежний resident 0.12.2: max input
2048, allocator cache 128 MiB, isolated inference deadline **5000 мс**,
exit-on-backend-unavailable. Primary — Qwen3:8b через Ollama 0.35.1,
контекст 8192, `think=false`, temperature/seed 0 и generation cap 128.
Identity/profile fingerprints доступны в [публичной сводке](./evidence/2026-10-05/resident-shared-soak-failure-0.12.2/result-summary.json).

Первые 34 блока завершены. В блоке 35, фазе `decision_only_before`, первый
decider запрос второго round вернул `inference_timeout`. Контроллер сохранил
первоначальный `request_failed`; следующего запроса или блока нет. HTTP
wall time ошибочного запроса — **6374,116 мс**. В него входят transport,
планирование ОС и завершение inference child; это не чистое GPU время.

| Измерение | Decider | Primary |
|---|---:|---:|
| Measured attempts | 4096 | 4080 |
| Успешные вычисления, включая abstain | 4095 | 4080 |
| Ошибки | 1 timeout | 0 |
| Wall p50 успешных, мс | 343,082 | 387,115 |
| Wall p95 успешных, мс | 799,858 | 2143,913 |
| Wall max успешных, мс | 3573,094 | 4384,689 |

Всего — **8176 measured attempts, 8175 успешных**, 140 успешных отдельных
warmup calls и 1020 overlapping HTTP pairs. Квантили пересчитаны по всем
успешным measured строкам; warmup и единственная ошибка вынесены отдельно.
У обоих моделей для повторяемых inputs изменения decision signatures
не обнаружены. Это повторяемость этого development набора, не доказательство
предметной правильности или производственного SLO.

## Retirement, recovery и ресурсы

Исходный final log snapshot содержит `decision.backend_retired`, reason
`inference_timeout`, runtime exit **75**, child exit **-15**, с прежним
профилем. После отказа отдельный read-only verifier подтвердил новый runtime
PID, точный bundle и профиль 0.12.2, свежий scrape и 47 series. Prometheus
PID не изменился. Все три наблюдавшихся временных процесса завершены,
cleanup/inventory errors отсутствуют; два постоянных jobs работают.

До последнего ready sample parent, inference child и Prometheus оставались
теми же: 725 таких samples, затем два health errors. Последний ready sample
сообщал `memory_pressure -Q` free percentage 22%, 14 761 984 free bytes,
13 114 179 584 wired bytes и 11 676 385 280 compressor-occupied bytes;
`vm.swapusage` — used 18 686,31 **M в исходной метке команды**. За опыт
swap used вырос с 11 276,81 M. Все эти показатели относятся ко всей машине,
включая другие приложения. **Причинность timeout не установлена.** RSS не
суммируется с Metal allocations и не считается полной памятью моделей.
[Apple](https://support.apple.com/guide/activity-monitor/view-memory-usage-actmntr1004/mac)
также рассматривает давление памяти через совокупность free/swap/wired/cache.

Финальный computed-counter delta и время recovery не утверждаются: retirement
сбросил runtime counters, а scrape асинхронный. `/api/ps` unload после отказа
не подтверждён; подтверждено отсутствие собственных server/runner PID.

## Независимая проверка и следующий этап

Два отдельных private audits перепроверили seals, исторические Git source
bytes, входы, точный harness set, фазы и case order, 140 успешных warmup,
token bounds, primary residence/context, все summaries, measured duration,
exact journal, повторные signatures, единственную последнюю ошибку,
retirement/recovery и cleanup. Первый audit сохранён неизменным; второй
связан с его SHA. Полный verifier отклонил исходный launcher с
`Launcher is incomplete or failed`. Успешный префикс не превращён в полный
gate. Public allowlist summary содержит fingerprints и агрегаты; запросы,
gold, outputs, case IDs, PID и локальные пути остались private.
Проверки документации прошли: 12 Node tests, 2271 локальная ссылка в 250
Markdown-файлах и process catalog; serving code этим этапом не менялся.

Следующий инженерный этап — отдельный opt-in serving profile с явным
per-process wired-memory budget. [MLX API](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.set_wired_limit.html)
предоставляет эту настройку на macOS 15+, с default 0 и проверкой системной
границы. [Официальный MLX-LM generation context](https://github.com/ml-explore/mlx-lm/blob/v0.31.3/mlx_lm/generate.py)
использует её; наш direct-logits backend этот context не вызывает. Native
preflight установленного MLX 0.32.2 подтвердил применение и возврат 4 GiB
в пустом отдельном процессе, без модели или изменения системного sysctl.
Это основание для контролируемой проверки, не доказательство исправления
timeout. Сначала нужны guard tests и короткий native опыт, затем новый полный
7200-секундный план; deadline не увеличивается автоматически.

Источники проверены 05.10.2026 MSK. Причина исторического внепланового exit 75,
independent business data/reviews, calibration/holdout, boot/login и owner/SLO
остаются открытыми.
