# Реальные модели: RAG, Temporal и PostgreSQL

Дата: 28.09.2026. Статус: **два ограниченных интеграционных сценария прошли**. Qwen3 и embeddinggemma работают через обычный Python worker; Temporal переживает потерянное подтверждение tick и принудительный restart worker. Принятые ответы, retrieval provenance и Run ID сохранены. Предметная qualification остаётся `not_assessed`; shadow и автоматическая маршрутизация decision-модели выключены.

Это следующий gate после [Linux deployment](./linux-worker-deployment.md). Раньше реальные модели проверялись в [sequential RAG](./rag-http-isolation.md), а [Temporal/PostgreSQL recovery](./temporal-postgres-rag.md) — с контролируемыми ответами. Здесь эти части соединены в одном запуске.

## Закреплённый эксперимент

[План](./evidence/2026-09-28/temporal-real-rag/run-final/plan.json) фиксирует 159 файлов commit `83f59a94c54c84cd8111724eff3528a11865d6c6`, два транспорта `isolated/session`, concurrency 1 и прежний авторский синтетический [fixture](./rag-workflow.fixture.json). Каждый режим получает отдельные PostgreSQL 17.6 и Temporal 1.8.1 на случайных loopback-портах. Используются штатные schema migration/admission и runtime-роли `agat_system/agat_tenant`, schema 30, изолированный SQL retrieval.

Хост: Apple M1 Max, 32 GiB unified memory, macOS arm64; Node 24, Python 3.14.3, Ollama 0.34.2, Temporal SDK 1.24.0. Модели закреплены по digest: Qwen3:8b `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`, embeddinggemma `85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1`. Primary: temperature 0,2, seed 0, context 8192, максимум 384 output tokens, thinking off; embedding: context 2048, без truncation. Cloud выключен; одновременно разрешены две модели.

Пятисекундный durable timer предшествует трём агентным шагам. HTTP proxy действительно передаёт запросы локальным моделям. Первый ответ tick теряется после commit coordinator. После получения второго настоящего ответа Qwen3 proxy удерживает его, пока Temporal worker получает SIGKILL, replacement worker читает ту же историю и отвечает на query. Затем ответ выпускается и процесс завершается.

Модельный вызов остаётся вне deterministic Workflow, в обычном worker за Activity-driven coordinator. Это соответствует [ограничениям Temporal](https://docs.temporal.io/workflow-definition#deterministic-constraints): внешние вызовы должны находиться вне replay пути. Replay выполнен нативным `Worker.runReplayHistory`, как в [официальных тестах SDK](https://github.com/temporalio/sdk-typescript/blob/main/packages/test/src/test-replay.ts).

## Результат

| Наблюдение | isolated | session |
|---|---:|---:|
| Завершённые агентные шаги / primary calls | 3 / 3 | 3 / 3 |
| Embedding calls / items | 5 / 5 | 5 / 5 |
| События Temporal history | 63 | 63 |
| Tick requests / потерянные ответы | 6 / 1 | 6 / 1 |
| Удержание второго ответа модели | 10 772,463 мс | 11 525,474 мс |
| Время сценария с запуском и cleanup | 37 839,956 мс | 34 982,942 мс |

[Полные результаты isolated](./evidence/2026-09-28/temporal-real-rag/run-final/isolated.json), [session](./evidence/2026-09-28/temporal-real-rag/run-final/session.json) и [независимая проверка](./evidence/2026-09-28/temporal-real-rag/run-final/verification.json) подтверждают:

- Первая Activity действительно повторяется с attempt 2. Workflow Run ID не меняется; в истории присутствуют обе worker identity и сработавший timer.
- Каждый агентный stage принят ровно один раз, attempt 1. Первый принятый stage совпадает с сохранённым до SIGKILL снимком; proxy outputs совпадают с persisted outputs.
- В обеих фазах одинаковые prompts, outputs и embeddings пяти уникальных входов. Каждый шаг ссылается на оба источника; шесть markers на workflow сохраняют точную привязку к chunk/source SHA. Неизвестных citations нет.
- Собственный tenant видит `[1 run, 3 retrieval, 2 chunks]`, чужой — `[0, 0, 0]`. Tenant не имеет superuser/BYPASSRLS и получает отказ чтения глобального release registry.
- Live native replay не добавляет model/embedding/tick calls и не меняет trace. После остановки серверов обе сохранённые истории ещё раз прошли [отдельный native replay](./evidence/2026-09-28/temporal-real-rag/native-replay.json) с тем же bundle SHA `f21a8f392f93685d2868428ec69c386f94c125219060e00d726d85f733ce395c`.

В процессном графе следующий lease получает предыдущий output как `run.input`, дополнительно к ordered context. Verifier восстанавливает именно этот контракт из stage inputs; он не подставляет исходный пользовательский текст во все retrieval queries. Поэтому сравнение с последовательным benchmark не означает тождество всех prompts.

[Launcher](./evidence/2026-09-28/temporal-real-rag/run-final/launcher.json) завершился за 103 293,366 мс, включая отдельный warmup одного embedding и одного chat request, сборки, migration/admission, оба сценария и cleanup. Модели выгружены; Ollama и оба launcher process завершились с кодом 0. Четыре собственных контейнера удалены, 69 наблюдавшихся собственных PID отсутствуют, cleanup errors нет. Это sampled inventory процессов; он не является полным учётом каждого короткоживущего helper.

## Смысл ответов и ограничения

Оба финальных ответа одинаковы: 100 → 120 заявок, +20%; 4 → 3 часа на заявку, −25%; 8% → 5% отклонений, −3 процентных пункта. Числа перепроверены по 400/100, 360/120, 8/100 и 6/120. Сохранены учебный характер данных и неизвестность причин. Это проверка ассистентом двух повторов одного синтетического примера, не независимая разметка и не оценка предметного качества.

Разница длительностей не доказывает ускорение от session: по одному workflow на режим, в фиксированном порядке, с прогретыми моделями и искусственным удержанием HTTP. Здесь не проверяются backend compute cancellation, падение самого model server, реальные бизнес-коннекторы, throughput или production SLO. Default transport и production runtime не изменены.

## Перепроверка и сохранённые отказы

[Opt-in integration test](../../../../apps/coordinator/test/temporal-real-rag.integration.test.ts) по умолчанию пропускается без frozen plan, model URL, выбранного transport и настоящего PostgreSQL/Temporal. [Launcher](../../../../scripts/run-temporal-real-rag.py) отказывается измерять незакоммиченные исходники и перезаписывать evidence. [Offline verifier](../../../../scripts/verify-temporal-real-rag.py) сверяет Git snapshot, model pins, историю и её payloads, tick responses, stages, retrieval queries, citations, RLS evidence и cleanup. [Девять mutation/replay tests](../../../../scripts/test/test_temporal_real_rag_verifier.py) изменяют содержимое и пересчитывают внешние SHA: внутренние нарушения всё равно отклоняются.

Первый [preflight](./evidence/2026-09-28/temporal-real-rag/preflight-failure.json) остановился до запуска ресурсов из-за несуществующего `packages` в списке source paths. Затем [первый workload](./evidence/2026-09-28/temporal-real-rag/run/launcher.json) прошёл isolated, но session столкнулся с одинаковыми именами коллекции в общей БД до первого запроса модели; [лог сохранён](./evidence/2026-09-28/temporal-real-rag/run/tests.log). Исправлена изоляция тестовых БД; финальный запуск выполнен заново целиком. Ни один из этих отказов не объявлен успехом.

Финальная локальная проверка: 293 coordinator tests прошли, 28 opt-in сценариев пропущены; strict TypeScript, два native replay и девять новых verifier tests прошли. Общая проверка документации и SHA логов фиксируются в [checks.json](./evidence/2026-09-28/temporal-real-rag/checks.json).

Следующий gate — добавить в этот же Temporal/PostgreSQL сценарий настоящий закреплённый локальный shadow decider, проверить сохранность его наблюдений при восстановлении и отсутствие повторного inference при replay. Qualification и разрешение автоматических решений остаются отдельными требованиями.
