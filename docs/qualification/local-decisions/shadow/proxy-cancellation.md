# Отмена через измерительный HTTP-прокси

26.09.2026. Исправлен observation-прокси нагрузочного workflow: он сохраняет заголовок `X-Agat-Decision-Cancel-On-Disconnect` и прекращает upstream-вызов при преждевременном закрытии ответа opt-in клиенту. Прямой путь worker → runtime уже проверялся в [протоколе отмены](./cancellation.md); теперь проверен и промежуточный Node-прокси.

## Найденная граница и изменение

Прежний [workflow-harness](../../../../scripts/lib/decision-primary-workflow.ts) передавал profile header, но не новый opt-in заголовок. Его `fetch` был связан только с общим прекращением опыта и собственным timeout. Поэтому закрытие соединения worker не закрывало соединение прокси с runtime; при таком измерении нельзя было распространять доказательство прямой отмены на всю цепочку.

[Новый helper](../../../../scripts/lib/decision-shadow-proxy.ts) передаёт исходное тело, profile и cancellation header, не добавляя прочие входящие заголовки. Для каждого запроса создаётся отдельный сигнал отмены. Он срабатывает на `ServerResponse.close`, только если ответ ещё не `writableFinished` и клиент явно передал opt-in `1`. Событие `IncomingMessage.close` не используется как доказательство ухода клиента: оно может означать завершение чтения обычного тела, пока клиент ещё ждёт ответ.

После завершённого ответа listener удаляется и не влияет на следующий запрос. Без opt-in преждевременное закрытие клиента не получает новой семантики отмены inference. Общая остановка стенда и upstream deadline 12 секунд сохраняются. Ответ ограничен 131 072 байтами **во время чтения**. HTTP-код `busy` передаётся без превращения в успешное решение; уход opt-in клиента записывается измерителем как `decision_client_disconnected`.

Изменена только инфраструктура эксперимента. Рабочий coordinator не получил новый reverse proxy; runtime остаётся `0.12.0`. При отмене MLX по-прежнему требуется восстановление процесса. Нагрузочный опыт с неожиданным disconnect получает `incomplete`, даже если основной ответ успел сохраниться; это не скрытый повтор или успешная latency-проба.

## Проверки

Пять [transport-тестов](../../../../apps/coordinator/test/decision-shadow-proxy.test.ts) проверяют сохранность тела/заголовков, нормальное завершение и следующий запрос, отмену pending upstream, legacy без opt-in, остановку владельцем, передачу 503 и ограничение размера. Ещё два прежних [workflow-теста](../../../../apps/coordinator/test/decision-primary-workflow.test.ts) проверяют bounded workload, реальные worker/coordinator, два параллельных primary HTTP-вызова, сохранность исходных ответов и отказ при усечённой генерации. Все **7/7** прошли.

Реальный [план](./evidence/2026-09-26/proxy-cancellation-plan.json) закрепил исходники и профиль до inference: `robust-single-2048-front`, 2048 токенов, decider-2b, cache 128 МиБ, серверный предел 5000 мс. Worker передавал запрос через собственный Node-прокси, использующий тот же helper, что workload-harness.

[Опыт](./evidence/2026-09-26/proxy-cancellation-result.json) — `observed`:

- контрольный запрос через прокси получил валидное `ok` с полным входом;
- следующий worker-вызов завершился `unavailable / timeout` при budget 100 мс;
- завершение дочернего MLX наблюдалось через **196.611 мс от начала вызова**, stop reason `inference_cancelled`, exit -15;
- измеритель проверил `GET /health` через прокси: 503, unavailable и неизменный profile SHA;
- весь опыт с загрузкой/cleanup занял 8.782 секунды; Node-прокси завершился с 0, собственный MLX остановлен.

[Перепроверка](./evidence/2026-09-26/proxy-cancellation-verification.json) сверила seals, source/profile/input bindings, распределение контрольного ответа, время относительно серверного deadline и отсутствие трёх записанных PID. Health body в результате не сохранён: проверка 503 выполнена assertion внутри закреплённого измерителя, повторно разбирать этот ответ из артефакта нельзя. Это один функциональный опыт, не p95 и не гарантия hardware preemption.

## Воспроизведение и исторические источники

```bash
node --import tsx --test \
  apps/coordinator/test/decision-shadow-proxy.test.ts \
  apps/coordinator/test/decision-primary-workflow.test.ts

.venv/decision/bin/python scripts/check-decision-proxy-cancellation.py \
  --manifest .local-models/decisions/decider-2b.json \
  --policy docs/qualification/local-decisions/policy.shadow.v1.json \
  --source-plan docs/qualification/local-decisions/performance/evidence/2026-09-26/robustness-plan.json \
  --plan-output docs/qualification/local-decisions/shadow/evidence/new-run/proxy-cancellation-plan.json \
  --output docs/qualification/local-decisions/shadow/evidence/new-run/proxy-cancellation-result.json
```

Python-[измеритель](../../../../scripts/check-decision-proxy-cancellation.py) закрывает собственный [Node-процесс](../../../../scripts/probe-decision-shadow-proxy.ts), server и MLX в `finally`. Прокси также ограничен 30 секундами и завершается при EOF родительского stdin. Output создаются эксклюзивно.

До изменения сохранён [snapshot трёх прежних файлов](../performance/evidence/2026-09-26/workflow-before-proxy-cancellation.json). План следующих workflow-опытов включает SHA нового helper. Старые результаты `0.10.0` и их прежние semantics не переписаны. Строгий TypeScript check проверил CLI, helper и оба набора тестов; [протокол регрессии](./evidence/2026-09-26/proxy-cancellation-checks.json) связывает код и итоговые проверки.

Выбор событий сверён с официальными [Node HTTP ServerResponse.close](https://nodejs.org/api/http.html#event-close_2), [writableFinished](https://nodejs.org/api/http.html#responsewritablefinished) и [IncomingMessage.close](https://nodejs.org/api/http.html#event-close_3). Завершение request body и завершение/обрыв response — разные события жизненного цикла.
