# Ограничение времени вычисления отдельным процессом

26.09.2026. Runtime `0.7.0` добавляет операторский флаг `serve --inference-timeout-ms`. MLX загружается в отдельный процесс; родитель обслуживает локальный HTTP и ограничивает время обмена с вычислительным процессом. При timeout процесс завершается, канал закрывается, дальнейшие вычисления отклоняются до перезапуска runtime. Режим опционален, автоматическая маршрутизация остаётся выключенной. В `0.7.1` он доступен также в `score` и `evaluate`, чтобы получать совместимый калибровочный artifact.

## Причина и выбранное поведение

Клиентский watchdog закрывает HTTP-соединение worker, но сам по себе не прекращает уже запущенный Metal forward. В `0.11.0` добавлена отдельная [opt-in отмена изолированного процесса](./cancellation.md). [Длинный контекст](../performance/context.md) и [совместная нагрузка](../performance/shared-load.md) показали, почему независимый серверный предел всё равно нужен: даже при низкой медиане отдельное вычисление может занимать больше секунды.

Использован `multiprocessing.get_context("spawn")`: MLX импортируется и модель загружается внутри ребёнка. Родитель не создаёт копию весов. Выбор учитывает [предупреждение Python о небезопасности fork на macOS](https://docs.python.org/3.13/library/multiprocessing.html). Private socket pair передаёт ограниченные JSON-сообщения с длиной; входы и ответы не передаются через pickle или общий Queue. Сериализация служебного запуска `multiprocessing` не является внешним API.

Каждый запрос использует один monotonic deadline для отправки и всех чтений, включая частичные сообщения. Слишком поздний результат отклоняется. HTTP сохраняет прежний немедленный `busy`; backend дополнительно защищает канал от конкурентных вызовов. Успешные logits и token count проверяются в родителе, а policy/calibration и типизированный результат рассчитываются прежним `DecisionEngine`.

При deadline родитель посылает terminate, ждёт до 200 мс, при необходимости посылает kill и ждёт ещё до 500 мс. Канал и состояние не используются повторно. Crash, некорректный ответ или изменение identity также выводят процесс из работы. Обычный `context_too_long` не останавливает здоровый процесс. Внутри runtime нет автоматической повторной загрузки или retry: поток запросов не должен создавать цикл дорогих перезапусков. По умолчанию оператор перезапускает сервис явно. С `0.10.0` [опциональный exit 75](./service-recovery.md) позволяет поручить новый запуск внешнему менеджеру с ограничением частоты.

`SIGTERM`/`Ctrl+C` родителя закрывают вычислительный процесс. Наблюдатель внутри ребёнка также проверяет исчезновение родителя, чтобы завершить осиротевший процесс при аварийном выходе; это проверено на блокирующей тестовой функции. Период 250 мс не гарантирует время отмены при зависании ОС/native runtime. Доказана остановка процесса, а не мгновенное аппаратное preemption конкретного GPU kernel.

## Контракт и эксплуатация

```bash
.venv/decision/bin/python -m decision_runtime serve \
  --manifest .local-models/decisions/decider-2b.json \
  --policy docs/qualification/local-decisions/policy.shadow.v1.json \
  --cache-limit-mib 128 --inference-timeout-ms 2000 --port 8766
```

Параметр допускает целое число 100–10 000 мс. Загрузка имеет отдельный предел 60 секунд. Без параметра сохраняется прямое исполнение в HTTP-процессе. С `0.7.1` один и тот же флаг доступен в `score`, `serve` и `evaluate`. Все три команды должны использовать одинаковые deadline, token limit и cache при fit и последующем обслуживании. В `model.inferenceExecution` закреплены `isolated-process`, `spawn`, deadline и `stop_process_require_restart`. Другой deadline меняет profile SHA; конфигурацию процесса Агат нужно проверить и опубликовать с точным новым `profileJson`.

| Событие | HTTP/наблюдение | Следующее действие |
|---|---|---|
| Успешное вычисление | Прежние `ok` / `abstain` | Процесс используется снова, со свежим состоянием модели |
| Серверный deadline | HTTP 504, `error / inference_timeout` | Процесс останавливается; требуется restart |
| Запрос после остановки | HTTP 500, `error / backend_unavailable` | Быстрый отказ, без inference/retry |
| Health после остановки/смерти ребёнка | HTTP 503, `status: unavailable` | Тот же профиль, сервис не готов |
| Клиент перестал ждать раньше | Транспортный `unavailable / timeout` | С opt-in в `0.11.0` процесс отменяется; без него действует серверный deadline |

Серверный deadline — отдельный операторский предел, не копия `decisionShadow.timeoutMs`. Выбирайте клиентский бюджет с запасом на IPC, HTTP и завершение процесса, если нужно получить именно `inference_timeout` в observation. При более коротком ожидании сохранится клиентский `timeout`; primary остаётся основным в обоих случаях. В `0.11.0` worker явно согласуется на отмену при закрытии соединения; без opt-in сохраняется прежний deadline. Ни один из путей не гарантирует мгновенное аппаратное прерывание GPU: остановка процесса зависит от ОС.

Coordinator проверяет input/profile SHA и сохраняет ошибку один раз. Ошибка не содержит выбранного варианта, вероятностей или значения. UI различает серверный deadline и недоступность процесса. Сырые input и exception messages не копируются в диагностику.

## Реальная проверка

Apple M1 Max, 32 ГиБ, Python 3.13.12, MLX 0.32.2, mlx-lm 0.31.3:

- [Score через worker/coordinator, deadline 2000 мс](./evidence/2026-09-26/real-mlx-isolated-score.json): 87 токенов, 788.364 мс, выбран `two`, среднее `0.6535024448554396`; распределение совпало с прежним прямым runtime. Маршрут — `PRIMARY_BRANCH`, health остался ready.
- [30 HTTP-вызовов на 15 development-входах](../performance/evidence/2026-09-26/decider-isolated-http.json): 30/30 вычислений, 16 `ok`, 14 `abstain`, без ошибок; p50 188.164 мс, p95 287.682 мс, максимум 295.554 мс. Это короткий контроль, не доказательство ускорения или SLO.
- [Score с deadline 100 мс](./evidence/2026-09-26/real-mlx-isolated-timeout.json): `inference_timeout`, 190.973 мс включая остановку; единственный observation сохранён, `PRIMARY_BRANCH` и completed run сохранены, health стал unavailable.
- [Проверка процесса после timeout](./evidence/2026-09-26/isolated-timeout-process.json): PID ребёнка уже отсутствует; следующий запрос вернул `backend_unavailable` за 0.997 мс, без нового процесса.
- [Остановка по SIGTERM](./evidence/2026-09-26/isolated-sigterm-cleanup.json): остановились родитель и дочерние процессы. Временные runtime после опытов выключены.

В smoke основной генератор — явно обозначенная chat-completions fixture. Реален decision MLX и путь worker/coordinator; полный primary workflow с реальной генеративной моделью здесь не измерен. Результаты — `integration_pass` / `observed`, качество — `not_assessed`.

Девять проверок supervisor без MLX покрывают reuse, привязку параметров, timeout с игнорируемым SIGTERM и переходом к SIGKILL, отсутствие поздней записи, crash/ошибочные ответы, startup failure/timeout, конкурентный вызов, частичный IPC frame, закрытие и аварийный выход родителя. Worker-тест проверяет настоящий HTTP 504 и health 503; coordinator отвергает подменённые или «успешные» поля внутри ошибки. [Протокол версии 0.7](./evidence/2026-09-26/verification-isolated.json) содержит итоговые тесты и hashes.

Для воспроизведения отказа запустите отдельный тестовый экземпляр с `--inference-timeout-ms 100`:

```bash
npm run qualify:decision-shadow -- \
  --url http://127.0.0.1:8766 \
  --request docs/qualification/local-decisions/request.score.example.json \
  --expect-inference-timeout \
  --output docs/qualification/local-decisions/shadow/evidence/new-run/isolated-timeout.json
```

Smoke требует точного `inference_timeout` и недоступного health; другой отказ не считается успехом. Затем перезапустите runtime с рабочим deadline. Исторические свидетельства не перезаписываются; новый профиль не наследует qualification прошлой версии. [Проверка калибровочного пути 0.7.1](../calibration/isolated.md) описывает новый fit и HTTP smoke на явно синтетических fixtures.
