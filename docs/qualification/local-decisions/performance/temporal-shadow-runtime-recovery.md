# Отказ и восстановление настоящего shadow runtime

Дата: 28.09.2026. Статус: **оба embedding transport прошли полный сценарий с SIGKILL decider и восстановлением shadow**. Основные ответы и ранее принятые наблюдения сохранены. Предметная qualification и маршрутизация decision-модели остаются выключенными.

Продолжение [реального shadow в Temporal/PostgreSQL](./temporal-real-shadow.md). Использованы те же pinned Qwen3, embeddinggemma и MLX decider, прежние policy, deadline 5000 мс и input limit 2048. [Frozen plan](./evidence/2026-09-28/temporal-shadow-runtime-recovery/run-journal/plan.json) закрепляет 196 файлов commit `dfa0450c1b3d73296396beb1671a4fae102d0e7d`; каждый transport работает с отдельными PostgreSQL и Temporal.

## Что проверено

После первого принятого stage удерживается второй настоящий primary response. Temporal worker получает SIGKILL; replacement восстанавливает тот же Workflow Run ID. Затем launcher завершает только принадлежащий ему HTTP server decider. Его isolated inference child и resource tracker самостоятельно исчезают вслед за владельцем. Второй primary response выпускается; ordinary Python worker сохраняет его вместе с `shadow/unavailable/unreachable`, `fallback=primary`.

Третий настоящий primary response удерживается. Снимки второго принятого stage и fallback observation снимаются до restart decider. Launcher запускает новый процесс на прежнем loopback URL, проверяет health/profile и выполняет отдельный typed warmup. После выпуска третьего primary response shadow снова возвращает принятое решение. Итоговые первый и второй snapshots совпадают с исходными. Процесс, модельные вызовы и управление ресурсами остаются вне deterministic Temporal Workflow.

[Контроллер](../../../../scripts/lib/temporal_shadow_control.py) принимает четыре фиксированные команды через приватный каталог с правами 0700. Он владеет `Popen` handles и не принимает PID от клиента; action/transport/instance binding проверяются, ack публикуется атомарно без перезаписи. Частично запущенный процесс также принадлежит cleanup. Четыре [process-tree теста](../../../../scripts/test/test_temporal_shadow_control.py) используют настоящий isolated backend с fixture-моделью для проверки ownership; измерение ниже использует настоящие веса MLX.

## Результаты

| Проверка | isolated | session |
|---|---:|---:|
| Primary calls / embedding items | 3 / 5 | 3 / 5 |
| Shadow attempts / реальный inference | 3 / 2 | 3 / 2 |
| Принятые / unavailable observations | 2 / 1 | 2 / 1 |
| Shadow inference до/после restart, мс | 1777,597 / 187,109 | 1686,586 / 107,061 |
| Control restart с ожиданием готовности, мс | 11 099,420 | 7658,524 |
| History events / tick requests | 63 / 6 | 63 / 6 |
| Полное время сценария, мс | 53 811,601 | 39 989,538 |

[Isolated evidence](./evidence/2026-09-28/temporal-shadow-runtime-recovery/run-journal/isolated.json), [session evidence](./evidence/2026-09-28/temporal-shadow-runtime-recovery/run-journal/session.json), [offline verification](./evidence/2026-09-28/temporal-shadow-runtime-recovery/run-journal/verification.json). Всего шесть workflow shadow attempts содержат четыре model results и два отказа соединения. Все четыре inference имеют `generatedTokens=0`; три отдельных startup warmup не входят в workflow count. Их длительности — 707,108 / 939,390 / 743,612 мс.

Все шесть primary outputs, prompts и token counts, а также пять embedding inputs/vectors каждого transport точно совпали с [предыдущим warm shadow run](./evidence/2026-09-28/temporal-shadow-runtime-recovery/baseline-comparison.json). RLS сохраняет видимость собственных `[1 run, 3 retrieval, 2 chunks]` и чужих `[0, 0, 0]`; tenant не получает доступ к release registry. Потерянный tick reply повторяется Activity attempt 2, durable timer срабатывает, Run ID не меняется.

Native replay внутри live harness не добавляет primary/embedding/decision/control/tick calls и не меняет trace. [Независимый native replay](./evidence/2026-09-28/temporal-shadow-runtime-recovery/native-replay.json) повторно принял обе истории после cleanup. [Verifier](../../../../scripts/verify-temporal-real-rag.py) сверяет launcher events с integration ack, PID/exit codes, snapshots и временной порядок в каждой системе отсчёта. Разные origins часов launcher и integration не смешиваются.

[Launcher](./evidence/2026-09-28/temporal-shadow-runtime-recovery/run-journal/launcher.json) завершился за 134 133,609 мс. Три decider runtime имеют exit codes `[-9, -9, 130]`: два намеренных owner SIGKILL и финальный штатный SIGTERM. Все 81 наблюдавшийся собственный PID отсутствуют, четыре контейнера удалены, обе Ollama-модели выгружены; cleanup errors нет. PID inventory является выборочным наблюдением, не счётчиком всех краткоживущих процессов системы.

## Сохранённый отказ и диагностика

[Первый прогон](./evidence/2026-09-28/temporal-shadow-runtime-recovery/run/launcher.json) завершился внеплановым exit 75 до управляемого SIGKILL, несмотря на успешный отдельный warmup 701,421 мс. Launcher остановил integration раньше записи итогового phase evidence. Это **не доказывает конкретную причину inference failure**; первоначальный warmup не является гарантией дальнейшей доступности. Ресурсы того прогона также закрыты без ошибок.

Для устранения потери диагностики v3 сохраняет каждый завершившийся shadow attempt синхронно в отдельный JSONL journal; launcher связывает файл по SHA. При внеплановом завершении тесту даётся до пяти секунд на сохранение результата перед принудительным cleanup. [Isolated journal](./evidence/2026-09-28/temporal-shadow-runtime-recovery/run-journal/isolated.shadow.jsonl) и [session journal](./evidence/2026-09-28/temporal-shadow-runtime-recovery/run-journal/session.shadow.jsonl) точно совпадают с массивами attempts в успешном отчёте. Последующий успешный запуск не отменяет сохранённый отказ.

Сверка с [Ollama FAQ](https://docs.ollama.com/faq) и [MLX memory API](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.set_wired_limit.html) подтверждает, что совместное размещение моделей зависит от доступной памяти, а wired residency является отдельной настройкой. Эти сведения не устанавливают причину наблюдаемого exit 75. Системные лимиты памяти, model profile и inference deadline в этом этапе не менялись.

## Перепроверка и следующий этап

[Checks](./evidence/2026-09-28/temporal-shadow-runtime-recovery/checks.json) связывает логи регрессии, 42 verifier tests, четыре process-control tests, strict TypeScript, documentation checks и независимый replay. Mutation tests согласованно пересчитывают внешние SHA, journal и control ack: неверный signal, оставшийся child, подмена PID/profile/instance, недопустимый inference result при unavailable и restart вне удержанного ответа отклоняются.

Два сценария на одном synthetic fixture подтверждают recovery-протокол, но не дают production SLO или оценки предметного качества. Следующий gate — повторяемая диагностика внепланового decider exit при совместно загруженных моделях: сохранять typed reason и показатели ресурсов, разделить cold startup и последующие запросы, затем выбирать исправление по наблюдениям.
