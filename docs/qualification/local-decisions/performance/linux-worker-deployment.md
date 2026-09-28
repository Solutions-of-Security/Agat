# Linux worker image: настройки и завершение контейнера

Дата: **28.09.2026**. Ранее незавершённая [Linux-проверка deployment](./embedding-deployment-settings.md) после Docker ENOSPC теперь выполнена: **50/50 round trips, 143/143 worker tests и 3/3 сценария настоящего container entrypoint** прошли. Production runtime, defaults и существующие deployments не изменены.

## Образ и воспроизводимость

Собран текущий `workers/Dockerfile`, Linux arm64 / Python **3.13.15**, user `agat` / UID 10001. Image ID: **`sha256:3cbc714690f682d8a53c17c97255a4090ff49434d0372adc5269893d8050b8c5`**. В [build log](./evidence/2026-09-28/linux-worker-deployment/build.log) зафиксирован base `python:3.13-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b`. Тег Dockerfile остаётся плавающим; результат относится к указанному собранному image, а не ко всем будущим сборкам.

В build передан [tar context](https://docs.docker.com/build/concepts/context/#local-tarballs) из восьми файлов: Dockerfile, requirements и шесть runtime modules, **225 280 байт**. Модели, venv и рабочие credentials в context не включены. [Исходный план](./evidence/2026-09-28/linux-worker-deployment/intent.json) фиксирует SHA файлов/context. Final qualification harness закреплён commit `eb637ddfe2c4ff8f396e00df41eaf2565bbe913d`.

[Round-trip result](./evidence/2026-09-28/linux-worker-deployment/round-trips-final.json) побайтно сверяет шесть `/opt/agat` modules с исходниками. Отдельный [suite runner](../../../../scripts/qualify-worker-image-tests.py) проверяет те же imports до и после тестов, а также установленные pinned requirements, включая LangGraph 1.2.11. Тестовые fixture-модули доступны из read-only source mount. Это проверка runtime image, не установка runtime поверх него.

## Настройки и lifecycle

Существующий [deployment qualifier](../../../../scripts/qualify-embedding-deployment.py) повторил 16 наборов параметров через Compose, Kustomize, base/managed worker и production parser внутри Linux image: **20 допустимых комбинаций приняты**, **30 ошибочных отклонены с exit 2**. Три параметра — transport, deadline, idle timeout — дошли без потерь. Defaults `isolated / 900 / 0`, fallback пустых значений, дробные сроки, rollback и несовместимые/нечисловые значения проверены. Compose services не запускались; Kubernetes API не вызывался.

[Новый container qualifier](../../../../scripts/qualify-worker-container.py) запускает production image без переопределения worker ENTRYPOINT и без `--init`: **Python worker действительно PID 1**, UID 10001, его HTTP helper имеет PPID 1. Отдельный synthetic coordinator/model fixture живёт во временной internal Docker network, без опубликованных портов. Внешние модели и production coordinator здесь не используются.

| Режим | Helper между lease | Второй helper | SIGTERM при удержанном втором ответе |
|---|---|---|---|
| isolated, idle 0 | Закрыт | Новый PID | Worker остаётся жив, принимает ответ, сохраняет вторую партию, exit 0 |
| session, idle 0 | Остаётся жив | Тот же PID | То же корректное завершение |
| session, idle 1 с | Закрыт после простоя | Новый PID | То же корректное завершение |

Оба model HTTP-ответа удерживаются до команды fixture, что позволяет увидеть реальный helper в активном запросе. После первого ответа проверяется reuse/retire, после SIGTERM — ожидание второго ответа без преждевременного выхода. Во всех трёх случаях приняты ровно две lease и две точные embedding-партии, без повторов и fail. Deadline задан как 45 с; отдельное срабатывание deadline этим smoke не измеряется, оно покрыто полным worker suite.

[Final entrypoint result](./evidence/2026-09-28/linux-worker-deployment/entrypoint-final/result.json) сохраняет `/proc/1` argv/UID, PID/PPID/state helpers, принятые lease и exit status. Все созданные контейнеры и сети удалены; shared deployments и чужие containers/images не затрагивались. Production image оставлен доступным локально. Проверка не заменяет реальный coordinator, SQL/RLS, Temporal или workload на настоящей модели.

## Регрессия и ограничения

[Полный Linux suite](./evidence/2026-09-28/linux-worker-deployment/linux-suite-final.log): **143 pass, 0 skip**, 41,344 с. В нём проверяются cancellation/deadline, private pipes, parent exit, session/idle lifecycle и передача отмены в LangGraph. Suite-контейнер использует `--init --network none` для его тестовых дочерних процессов; отличие от production PID 1 smoke выше явно сохранено. Docker описывает [ответственность главного процесса за дочерние процессы](https://docs.docker.com/engine/containers/multi-service_container/).

Первый suite запуск не прошёл: мой runner не добавил `/repo` в `sys.path`, поэтому test-only `decision_runtime` не импортировался. [Первый log](./evidence/2026-09-28/linux-worker-deployment/linux-suite-first.log) и [его runner](./evidence/2026-09-28/linux-worker-deployment/linux-suite-first-runner.py) сохранены. Исправлены test imports и main guard для multiprocessing; image runtime не менялся. После добавления защиты от `python -O` все три проверки повторены на committed harness. [Negative checks](./evidence/2026-09-28/linux-worker-deployment/negative-checks.json) подтверждают отказ при отключённых assertions и отказ перезаписи evidence.

```bash
python3 scripts/qualify-embedding-deployment.py --root "$PWD" \
  --output docs/qualification/local-decisions/performance/evidence/local/linux-env.json \
  --image <локальный-образ>
python3 scripts/qualify-worker-container.py --image <локальный-образ> \
  --output docs/qualification/local-decisions/performance/evidence/local/linux-entrypoint
```

Образ должен уже существовать локально; container qualifier не скачивает его. [Итоговые проверки и hashes](./evidence/2026-09-28/linux-worker-deployment/checks.json) связывают runtime, harness и evidence. [Итоговая регрессия](./evidence/2026-09-28/linux-worker-deployment/final-validation.json): 12 Node + 344 Python tests, links и process catalog — pass. Linux arm64 не подтверждает Windows или amd64. Idle 1 с является значением fixture, не production SLO.

Следующий этап исходного плана — совместный сквозной запуск настоящих Qwen3/embeddinggemma с Temporal и PostgreSQL. Пока реальные модели проверены в обычном workflow, а Temporal/PostgreSQL recovery — с контролируемыми model responses. Независимые бизнес-данные, holdout и предметная qualification остаются открытыми.
