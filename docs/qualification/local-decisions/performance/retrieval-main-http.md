# HTTP-профиль обычного PostgreSQL coordinator

Дата: **27.09.2026**. План измерения подготовлен до запуска. Следующий шаг после [включения isolated retrieval](./retrieval-postgres-opt-in.md), [drain очереди](./retrieval-shutdown-drain.md) и [завершения UI/A2A HTTP-ответов](./coordinator-stream-shutdown.md). Цель — повторить bounded concurrent HTTP-профиль через собранный main, его штатный maintenance и параметры конфигурации.

## Зафиксированный протокол

Один и тот же commit запускается дважды: `sync`, затем `isolated`. Для каждого запуска создаётся отдельный одноразовый PostgreSQL. Проверяются 9716 готовых кандидатов по 768 измерений, бюджет 10000. Один старый документ имеет точный вектор `[1/3, …]`, остальные ортогональны. Это синтетическая нагрузка с известным победителем, без модели embeddings или семантической оценки.

Каждый запуск содержит warmup, idle 2 с и три всплеска каждого уровня конкурентности 1/2/4. Всего в паре **42 измеряемых поиска + 2 warmup**. Отдельный Python-клиент планирует health каждые 100 мс с максимумом четырёх ожидающих HTTP-запросов. Невыпущенные probes записываются отдельно. Это closed-loop bursts, не фиксированная интенсивность входящего production-потока.

Fixture-процесс готовит данные и leases, затем запускает настоящий `apps/coordinator/dist/server.js` с PostgreSQL artifact store. Его HTTP-handler и retrieval executor создаёт обычный main через конфигурацию. Main получает полный `AGAT_POSTGRES_POOL_MAX=4` на роль: sync использует 4, isolated — main 3 + executor 1. Fixture имеет отдельный pool=1 на роль; admission резервирует два экземпляра по 4, то есть консервативно вмещает оба процесса. Это не новые требования к production capacity.

Измерительный preload оборачивает `AgatStore.prototype.maintenanceTick`: вызывает исходный метод с теми же аргументами, измеряет время и повторно выбрасывает ошибку. Он не запускает дополнительный maintenance и не принимает HTTP-запросы. Через приватный IPC собираются event-loop delay, память и lifetime peak RSS **дочернего main**, а не fixture-процесса. Оба режима используют один observer. Дополнительные gateway/launcher/Temporal/telemetry switches выключены; подписанные worker releases не требуются для синтетических fixture nodes. Production workload и его latency этим не воспроизводятся.

Перед запуском launcher проверяет совпадение всех coordinator TypeScript sources с commit, собирает coordinator и закрепляет SHA каждого JS artifact. Helper сверяет эти SHA перед fork. Plan также фиксирует Node 24, исходники измерителя, порядок backend/modes, конфигурационный pool budget и начальную нагрузку хоста. После всех фаз main завершается SIGTERM с кодом 0, а launcher удаляет PostgreSQL и собственные процессы.

## Критерии проверки

- Каждый HTTP-поиск возвращает единственный старый точный документ, score=1, `K1` и исходные SHA; в durable trace ровно один retrieval с candidateLimit=10000.
- Для каждой фазы подтверждены фактическая HTTP-конкурентность и все выпущенные health replies. В isolated проверяются режим, queue bounds и accepting; после фазы очередь пуста.
- Реальный maintenance выполняется в idle и не сообщает ошибок. Метрики event loop относятся к main process; lifetime RSS включает его worker threads и не является изолированной метрикой scorer.
- Два запуска имеют одинаковые исходники, JS artifacts, параметры и pool budget. Handler-only опыт нельзя выдать за main или смешать с ним в паре. Старые evidence остаются воспроизводимыми без изменения результатов.

```sh
python3 scripts/run-rag-http-probe.py --coordinator-entry main --execution-mode sync --maintenance-interval-ms 1000 --evidence-dir docs/qualification/local-decisions/performance/evidence/2026-09-27/retrieval-main-sync
python3 scripts/run-rag-http-probe.py --coordinator-entry main --execution-mode isolated --maintenance-interval-ms 1000 --evidence-dir docs/qualification/local-decisions/performance/evidence/2026-09-27/retrieval-main-isolated
python3 scripts/verify-rag-http-probe.py --evidence-dir docs/qualification/local-decisions/performance/evidence/2026-09-27/retrieval-main-isolated --compare-control docs/qualification/local-decisions/performance/evidence/2026-09-27/retrieval-main-sync
```

Результаты будут оценены после обоих запусков. Порядок не рандомизирован, хост общий; разница будет описательной, без причинного вывода или нового SLO. Числа предыдущего handler-only опыта относятся к его собственному commit и инструментированию.
