# Остановка worker во время embedding и восстановление очереди

Дата: 2026-09-27. Статус: четыре реальные PostgreSQL-проверки прошли в режимах `isolated` и `session`.

После [полного RAG-профиля](./embedding-worker-rag.md) проверен следующий lifecycle gate: SIGTERM приходит, когда обычный Python worker уже получил embedding lease и ожидает model HTTP. Штатная реализация корректно закрывает admission, дожидается текущего выполнения и затем закрывает helper. Изменять production runtime для этих сценариев не потребовалось; добавлены постоянные integration tests и наблюдение фактически обработанного сигнала.

## Что проверено

Новый тест запускает настоящий coordinator `main`, PostgreSQL 17.6 и Python `worker_loop`. Модель заменена собственным управляемым loopback HTTP endpoint с двумя известными векторами. Цель — состояние lease, drain и сохранность данных, а не качество модели. Во время первого запроса в базе появляется ещё один pending-документ.

| Сценарий | Проверка после SIGTERM | Состояние перед restart | Проверка нового worker |
|---|---|---|---|
| `renew-complete`, оба транспорта | Текущий model HTTP остаётся открыт; штатный renewer через 45 с возвращает **HTTP 204**; срок того же lease увеличивается. После освобождения модели `/complete` возвращает 200 | Первый документ ready, его вектор `[1,0]` сохранён; второй документ не арендован; failures=0 | Перезапущены coordinator и worker. Обрабатывается только второй документ; вектор, `embedded_at` и ready-event первого не переписываются |
| `deadline`, оба транспорта | SIGTERM не отменяет активную работу; общий embedding deadline 3 с завершает helper и закрывает model HTTP. `/fail` возвращает 200 | Первый документ pending, vector отсутствует, failures=1, ровно один retrying-event; второй документ не арендован | Новые leases завершают оба документа: `[1,0]` и `[0,1]`. Старый lease не переиспользуется, нет лишних model calls или ready-events |

Наблюдатель фиксирует возврат штатного `request_stop` и время сигнала; production handler не заменяется. После этого нет новых embedding lease HTTP-запросов от останавливаемого worker. Его единственный terminal POST выполняется после сигнала и относится к прежнему lease. Отдельный renewer продолжает работу при drain: общий stop-event worker не используется вместо lease-specific renew-stop.

В обоих сценариях оба процесса worker завершаются с кодом 0, без активных coordinator-запросов и фоновых потоков. Каждый renewer завершён до возврата своего execution. У всех helper получен exit status и закрыты оба pipe; deadline helper имеет отрицательный returncode, успешный helper — 0. `session` остаётся жив между здоровыми запросами и закрывается при выходе из worker, без принудительной отмены здорового inference.

Локальный финальный прогон: **4/4 pass**, 105,184 с; успешные drain-сценарии заняли 46,819 и 46,765 с, включая настоящий 45-секундный renewal. Deadline-сценарии с restart — 4,945 и 4,795 с. [Полный журнал](./evidence/2026-09-27/embedding-worker-drain/postgres.log).

## Перепроверка теста

Первый прогон нашёл ошибку ожидания в новом тесте: renewal endpoint штатно отвечает 204, а тест ожидал 200. Исправлено только ожидание контракта и диагностический текст; production код не менялся. Сохранён [первый журнал](./evidence/2026-09-27/embedding-worker-drain/fixture-status-correction.log); после исправления все четыре сценария выполнены заново.

Настроенный `npm run typecheck` прошёл. Дополнительный strict TypeScript-запуск непосредственно на всём integration-файле, который не включён в штатный `tsconfig`, возвращает 18 прежних diagnostics. Сравнение с исходным commit по коду ошибки, сообщению и строке исходника подтвердило **18 → 18, без новых diagnostics**; это не обозначено как успешный strict check. [Сравнение](./evidence/2026-09-27/embedding-worker-drain/extra-typecheck.json).

Команда воспроизведения использует отдельный контейнер PostgreSQL и удаляет его после проверки:

```bash
bash scripts/test-fleet-ha-postgres.sh \
  --test-name-pattern='drains active model HTTP on SIGTERM'
```

Исходники, SHA-256 и остальные проверки перечислены в [checks.json](./evidence/2026-09-27/embedding-worker-drain/checks.json). Новые четыре случая включены в обычный HA PostgreSQL CI; полный набор теперь содержит 87 тестов.

## Границы и следующий gate

Graceful drain не является обещанием быстрого shutdown. Worker ждёт выполняющиеся futures, как и определено для [ThreadPoolExecutor](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Executor.shutdown). В проверке успешному запросу сознательно дали более 45 с; при другом внешнем grace period supervisor может принудительно завершить процесс раньше.

В текущем Kubernetes worker manifest и локальном launcher выставлены **30 с** termination grace, при этом максимальный embedding timeout worker — **900 с**. Эти настройки здесь не меняются: primary generation, tools, coordinator HTTP и уже идущие действия требуют отдельного общего контракта остановки. Docker также переводит stop в SIGKILL после истечения внешнего timeout: [docker container stop](https://docs.docker.com/reference/cli/docker/container/stop/).

Следующий gate — принудительное завершение родительского worker при удерживаемом model HTTP: проверить оставшиеся helper/соединения и durable lease recovery, затем исправить только подтверждённый дефект. Эта проверка не доказывает поведение SIGKILL, остановку backend GPU inference, Windows или production SLO.
