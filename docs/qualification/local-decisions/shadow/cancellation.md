# Отмена изолированного inference при уходе клиента

26.09.2026. Runtime **0.11.0** поддерживает отмену активного изолированного inference при закрытии соединения клиентом, который явно согласился на такую семантику. Worker передаёт это согласие автоматически. Реальные MLX-прогоны подтвердили завершение процессов до серверного deadline, восстановление с прежним распределением и сохранение primary-маршрута через coordinator. Качество модели не квалифицировано, routing остаётся выключенным.

## Протокол

Заголовок `X-Agat-Decision-Cancel-On-Disconnect: 1` означает: клиент сохраняет обе стороны соединения открытыми до получения ответа; EOF/reset до завершения ответа отменяет этот запрос. Допустимо одно поле со значением `1`; дубликаты и другие значения возвращают 400 до inference. Старые клиенты без заголовка сохраняют прежнюю семантику. Прямой backend без отдельного процесса принимает запрос, но аппаратной отмены не предоставляет.

Причина явного согласия — [RFC 9112, §9.6](https://www.rfc-editor.org/rfc/rfc9112.html#section-9.6): TCP half-close со стороны клиента сам по себе не означает, что ему больше не нужен HTTP-ответ. Поэтому сервер не трактует любой EOF обычного HTTP как отмену. Worker Агат не использует half-close при ожидании ответа; его watchdog закрывает транспорт при timeout или сигнале отмены.

Для изолированного backend после полного чтения JSON создаётся наблюдатель именно этого соединения. Он проверяет EOF/reset каждые 20 мс, не читает тело другого запроса и не исполняет pipelined requests. Сигнал связан только с текущим вызовом. Наблюдатель останавливается до освобождения inference lock, поэтому закрытие завершённого соединения не отменяет следующий lease.

Родитель MLX проверяет отмену при отправке/чтении private IPC, включая частичный frame и backpressure. При отмене после отправки он закрывает канал и останавливает ребёнка: terminate, до 200 мс ожидания, затем при необходимости kill и до 500 мс ожидания. Результат не принимается, состояние не используется повторно, автоматического retry внутри запроса нет. Если сигнал пришёл до dispatch, здоровый ребёнок сохраняется. Серверный monotonic deadline продолжает действовать независимо от отмены.

Внутренний исход — `error / inference_cancelled`, без distribution/value. Worker, уже закрывший соединение, сохраняет свой транспортный `unavailable / cancelled` либо `unavailable / timeout`. Coordinator допускает связанный с запросом `inference_cancelled` как ошибку и сохраняет `fallback: primary`; подмена input/profile или полей успешного ответа отклоняется.

После начала остановки `/health` становится 503. Это означает отсутствие готовности принимать inference, а не доказательство, что ОС уже завершила ребёнка. С `--exit-on-backend-unavailable` HTTP-сервис завершится с 75; без флага останется недоступным до явного restart. [Управление жизненным циклом](./service-recovery.md) описывает внешнее восстановление и ограничение частоты повторных запусков.

## Реальный транспорт и восстановление

[План](./evidence/2026-09-26/cancellation-plan.json) зафиксирован до первого inference. Использован один прежний синтетический запрос `robust-single-2048-front`, полный вход 2048 токенов, `Mapika/decider-2b`, cache 128 МиБ, серверный deadline 10 000 мс. Все три процесса — собственные foreground-сервисы на одном временном loopback-порту; native service manager не устанавливался.

| Событие | Возврат worker от старта, мс | Наблюдаемый exit сервиса после триггера, мс | Итог |
|---|---:|---:|---|
| Сигнал отмены после запланированных 150 мс; фактически 153.961 мс | 170.304 | 413.048 | `cancelled`, exit 75, дети завершены |
| Клиентский timeout 100 мс | 100.786 | 459.796 | `timeout`, exit 75, дети завершены |

Второй столбец включает клиентский вызов; третий — наблюдение выхода HTTP-сервиса, включая его polling/cleanup. Это два функциональных опыта, не p95 и не гарантированное время освобождения GPU. Предварительно заданная проверка требовала exit менее чем за 2 секунды после триггера. Серверный предел 10 секунд в обоих случаях не был причиной остановки.

Исходный успешный вызов занял 1080.305 мс. После явного восстановления два новых вызова заняли 1001.942 и 890.831 мс, дали тот же профиль, logits, выбранный вариант и распределение. Закрытие первого завершённого соединения не помешало второму. [Полный результат](./evidence/2026-09-26/cancellation-result.json) содержит PID, версии, timings и исходы; [инструмент](../../../../scripts/check-decision-cancellation.py) ограничивает работу и удаляет собственные процессы.

Профиль опыта — `dd6bf4fffb7735ee519ed4ad6c88870e9e05c4c63847ce953669a309fcb56e42`. В `model.inferenceExecution` добавлены `cancellationAction: stop_process_require_restart` и `cancellationPollIntervalMs: 20`. Implementation SHA — `75262fe8d4b1227c26b75b4438cc263e072dd8c7d2a70373b65acd5bede67bcc`. Исторические fit/eval от прежних реализаций автоматически не переносятся.

## Сквозной worker/coordinator

В [сквозном плане](./evidence/2026-09-26/cancellation-workflow-retirement-plan.json) клиентский budget равен 100 мс, серверный — 10 секунд. Decision backend — реальный MLX; Python worker, SQLite coordinator, lease validation и запись observation — реальные. Primary chat-completions endpoint здесь **явная заглушка**, чтобы проверить сохранность маршрута.

[Результат](./evidence/2026-09-26/cancellation-workflow-retirement-result.json) — `integration_pass`: primary вызван ровно один раз, run завершён через `PRIMARY_BRANCH`, записано одно `unavailable / timeout` с `fallback: primary`, ребёнок остановлен с `inference_cancelled`, profile SHA сохранён. State не попал в событие `decision.shadow`. Полный опыт с загрузкой занял 6.447 секунды; ожидание reap после smoke — 0.006 мс, ребёнок уже завершился к этой точке.

Предыдущий [неполный запуск](./evidence/2026-09-26/cancellation-workflow-result.json) сохранён: вложенный worker/coordinator smoke уже прошёл, но внешняя проверка требовала немедленного наличия child exit code после unavailable. Завершение процесса выполнялось асинхронно. [Прежний исходник](./evidence/2026-09-26/cancellation-workflow-initial-source.json) архивирован; [исправленный измеритель](../../../../scripts/check-decision-cancellation-workflow.py) ждёт reap максимум 1500 мс, не принимает другой stop reason и сохраняет начальное/конечное состояние. Два CPU-теста воспроизводят промежуточную недоступность и проверяют предел ожидания.

## Проверки и границы

Восемь дополнительных runtime-тестов покрывают отмену до dispatch, зависший процесс с игнорируемым SIGTERM, partial receive/backpressured send, обычный half-close и opt-in, FIN/RST, timeout/отмену реального worker-клиента, повторный здоровый запрос и неверный заголовок. Полный `npm test`: 206 coordinator, 52 web, 45 worker (1 необязательный skip), 105 runtime (3 необязательных skip), Temporal/replay и process pack — без ошибок. Изменённый smoke дополнительно прошёл strict TypeScript check и прежний HTTP regression с Boolean/Score.

[Независимая проверка](./evidence/2026-09-26/cancellation-verification.json) пересчитала seals, hashes кода, input/profile bindings и результаты policy; сверила прежний исходник неполного опыта, одинаковые распределения после restart и отсутствие всех 13 записанных PID. [Verifier](../../../../scripts/verify-decision-cancellation.py) не загружает модель и не выполняет отмену заново.

[Протокол регрессии](./evidence/2026-09-26/cancellation-checks.json) связывает итоговый код с проверками. Все workspace typecheck прошли; docs:check выполнил 76 Python- и 12 Node-тестов, проверил каталог процессов и локальные ссылки. После расширения smoke-инструмента отдельно повторены его HTTP regression, strict TypeScript и реальный MLX workflow.

20 мс — интервал программной проверки, а не гарантия мгновенного hardware preemption. ОС/native driver могут задержать остановку. Отмена уничтожает загруженный процесс, поэтому восстановление снова требует загрузки модели; внешний manager должен ограничивать restart rate и проверять readiness. Pipelining и дополнительные байты после тела не поддерживаются; при невозможности обнаружить EOF сохраняется серверный deadline. Для прямого backend или клиента без opt-in остаётся прежняя граница: закрытие транспорта не останавливает уже начатый forward. Данных для production SLO и приёмки качества эти проверки не добавляют.

Исходники runtime `0.11.0` и verifier перед переходом к метрикам `0.12.0` сохранены в [архиве](./evidence/2026-09-26/runtime-0.11-sources.json). Проверка исторического опыта требует именно этой реализации.

## Воспроизведение

```bash
python3 scripts/check-decision-cancellation.py \
  --manifest .local-models/decisions/decider-2b.json \
  --policy docs/qualification/local-decisions/policy.shadow.v1.json \
  --source-plan docs/qualification/local-decisions/performance/evidence/2026-09-26/robustness-plan.json \
  --plan-output docs/qualification/local-decisions/shadow/evidence/new-run/cancellation-plan.json \
  --output docs/qualification/local-decisions/shadow/evidence/new-run/cancellation-result.json

.venv/decision/bin/python scripts/check-decision-cancellation-workflow.py \
  --manifest .local-models/decisions/decider-2b.json \
  --policy docs/qualification/local-decisions/policy.shadow.v1.json \
  --source-plan docs/qualification/local-decisions/performance/evidence/2026-09-26/robustness-plan.json \
  --plan-output docs/qualification/local-decisions/shadow/evidence/new-run/workflow-plan.json \
  --output docs/qualification/local-decisions/shadow/evidence/new-run/workflow-result.json
```

Output создаются эксклюзивно. Для уже запущенного изолированного сервера без exit-on-failure доступен `qualify:decision-shadow -- --expect-worker-timeout` с прежними `--url`, `--request`, `--output`. Исторический smoke-инструмент до расширения сохранён в [snapshot](./evidence/2026-09-26/smoke-harness-before-worker-cancellation.json).
