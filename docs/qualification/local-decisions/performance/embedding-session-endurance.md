# Повторная работа embedding-сессий и восстановление helper

27.09.2026. Продолжение [проверки на настоящей модели](./embedding-session-model.md). Этот опыт проверяет жизненный цикл прототипа на контролируемом HTTP-стенде. Обычный worker, Ollama и model weights не меняются.

## Закреплённый сценарий

[Harness](../../../../scripts/profile-embedding-session-endurance.py) и [независимый verifier](../../../../scripts/verify-embedding-session-endurance.py) сохранены commit **`134bf54`** до запуска. [Plan](./evidence/2026-09-27/embedding-session-endurance/plan.json) содержит полный commit, SHA всех исполняемых зависимостей, четыре actor, 120 раундов с периодом одна секунда и точное расписание десяти faults. Профиль выполнен на macOS arm64 / Python 3.14.3.

Четыре сессии запускают helper один раз перед серией. В каждом раунде каждый actor выполняет успешный запрос, чередуя batch 1/32 и размерность 768/4096. Ответы содержат обратный порядок индексов; `LocalModelClient` разбирает и проверяет их обычным кодом. Actor 3 служит контролем без отказов. В раундах 19, 29, …, 109 один из actor 0/1/2 получает cancel-headers, cancel-body либо deadline-body; сразу после отказа он выполняет свой успешный запрос на новом helper. Другая сессия при этом не отменяется.

Каждый запрос сохраняет времена, полную идентичность input/expected vectors через независимо пересчитываемые SHA, PID владельца, состояние процесса/pipes на возврате. HTTP fixture сохраняет собственные наблюдения запроса и disconnect. После каждого раунда выполняется quiescent snapshot RSS, FD и guard threads. Сырые данные находятся в [result](./evidence/2026-09-27/embedding-session-endurance/result.json), производные — в [replay](./evidence/2026-09-27/embedding-session-endurance/replay.json).

## Наблюдения

Серия заняла **120,003 с**, полный harness — **120,159 с**. Получены **480 успешных ответов** с точными ожидаемыми векторами и **10 ожидаемых отказов**: 7 отмен и 3 deadline. Все 10 recovery-вызовов успешны. HTTP fixture наблюдает ровно **490 запросов**, без повторов. Фактическое перекрытие достигает четырёх вызовов во всех 120 раундах.

Всего запущены **14 helper**: четыре исходных и десять замен. При каждом отказе прежний процесс уже завершён и pipes закрыты до возвращения error. Recovery использует новый PID, а контрольный actor выполняет все 120 запросов на одном процессе. Четыре последних helper штатно завершились после close; остальные десять имеют ожидаемый код принудительного завершения.

**FD: 6 до запуска → 14 во всех 120 idle snapshots → 6 после close**. Между раундами нет оставшихся deadline-guard threads. Контрольный helper: RSS **28,578 МиБ** после первого маленького ответа, **34,156 МиБ** после первой большой партии, **35,828 МиБ** в конце. После девятого раунда наблюдаемый диапазон — **31,125–35,828 МиБ**; поздние контрольные снимки находятся около 35,8 МиБ. Максимальная сумма idle RSS четырёх helper — **142,094 МиБ**. Это ограниченное наблюдение, а не доказательство отсутствия роста при произвольной длительности или других payload.

Parent RSS — **47,438 → 104,172 МиБ**, максимальный снимок **109,047 МиБ**. Harness сохраняет растущие списки HTTP/request evidence, использует fixture, hashes и allocator; эта величина не является памятью обычного worker. Снимки получены между запросами, они не измеряют peak RSS или GPU memory.

| Исход / форма | N | p50, мс | p95, мс | Max, мс |
|---|---:|---:|---:|---:|
| Success 768 × batch 1 | 120 | 6.475 | 11.038 | 16.247 |
| Success 768 × batch 32 | 115 | 49.473 | 86.352 | 96.309 |
| Success 4096 × batch 1 | 120 | 12.903 | 26.520 | 29.564 |
| Success 4096 × batch 32 | 115 | 229.136 | 345.384 | 352.936 |
| Recovery, включая запуск нового helper | 10 | 92.614 | 288.581 | 288.581 |
| Возврат после выставления cancellation Event | 7 | 72.246 | 112.574 | 112.574 |
| Deadline 250 мс, полное время вызова | 3 | 287.911 | 295.252 | 295.252 |

Recovery выделен из обычных success-ячеек: пять запросов batch 32 × 768 и пять batch 32 × 4096. Поэтому сумма успешных наблюдений остаётся 480. Это workload с заданным ритмом, а не saturation throughput или оценка production p95; для малых N p95 совпадает с max.

**Polling guard 20 мс не означает гарантированную реакцию за 20 мс.** Под конкурентным разбором больших JSON, работой Python/GIL и планированием возврат после cancellation достигает **112,574 мс**, а вызов с deadline 250 мс возвращается до **295,252 мс**. Эти измерения не выделяют вклад каждой причины. Deadline не допускает принятия позднего HTTP-ответа, но не даёт hard real-time границу времени cleanup. Документация прототипа уточнена; гарантия прекращения backend computation не добавляется.

## Проверка и следующий gate

**Девять offline-тестов** отклоняют сокращённую серию/пропущенный раунд, изменение fault schedule/source SHA, повтор HTTP, подмену input/vector, поздний cancellation timestamp, возврат до reap, повтор погибшего PID, чужого владельца, замену контрольного процесса, посторонний RSS PID, оставшийся guard, рост FD и неполный cleanup.

```sh
python3 scripts/profile-embedding-session-endurance.py --output docs/qualification/local-decisions/performance/evidence/<date>/<new-directory>
python3 scripts/verify-embedding-session-endurance.py docs/qualification/local-decisions/performance/evidence/2026-09-27/embedding-session-endurance
python3 -m unittest discover -s scripts/test -p test_embedding_session_endurance_verifier.py -v
npm run docs:check
```

[Журнал проверок и SHA evidence](./evidence/2026-09-27/embedding-session-endurance/checks.json): 9/9 целевых verifier-тестов, 12 Node + 232 Python в docs suite; architecture audit — PASS, 0 ошибок и предупреждений.

Данные позволяют перейти к **явному opt-in в worker с ограниченным числом сессий**, не более его concurrency, и гарантированным close после drain. Остаются проверки admission/deadline ожидания, совместимости 2 МиБ request budget с реальными chunk/query пределами, Docker packaging и настоящего PostgreSQL lease/renewal/recovery-пути. Прототип не включается автоматически; baseline transport остаётся default до отдельного решения о rollout.
