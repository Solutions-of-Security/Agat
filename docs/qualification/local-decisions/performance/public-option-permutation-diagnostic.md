# Whole public option-order diagnostic

[Frozen tokenizer preflight](./public-option-permutation-context.md) закрепил
весь development inventory и prospective budget до inference. [Native CLI](../../../../scripts/run-public-support-permutation-diagnostic.py)
проверяет independent original/permutation raw pins, оба historical source
inventories, committed execution sources, native dependencies и exact
model/profile bytes. Один отдельный owned wired runtime, два warmup и
единственный последовательный caller обслуживают каждый full variant.
Постоянный resident не участвует в scoring.

Budget **600 s** резервирует полный **10000 ms** timeout перед каждым
новым вызовом; inference deadline остаётся **5000 ms**. Retry — **0**,
primary companion отсутствует. Long inputs отправляются целиком с
ожидаемым `context_too_long`. Первый неожиданный ответ, cancellation или
исчерпание budget прекращает admission: остальные variants записываются
как `not_attempted`, complete/pass не выдаётся. Immediate private journal
и sealed phase сохраняют original denominator и фактические outcomes.

Смысловые результаты сопоставляются по option IDs после каждой перестановки.
Для каждого original case считаются distinct `(status, reason, selected ID,
value)` и максимальная абсолютная разница probabilities по тем же ID
относительно original order. Original/repeat comparison отдельно сохраняет
изменение outcome и probability delta при одинаковом prompt. Five cyclic
orders также дают observed argmax/accepted counts по zero-based positions.
Фиксированный порядок не доказывает причинный эффект позиции.

Единицы наблюдения — original cases/groups; variants зависят друг от друга.
Fully context-rejected cases остаются в whole inventory, без выдуманных
predictions. Case/group counts с полными computations и changed outcomes
показаны отдельно. Labels/accuracy, independence, calibration/holdout,
owners/SLO и routing не возникают из agreement или confidence.

Offline verifier проверяет каждый caller result/token/interval, whole
journal и summaries, exact zero-origin counters, два warmup, три quiescent
snapshots и отсутствие recorded restart. Historical code читается через
Git, не исполняется. Consumed raw files повторно читаются до публикации.
Warmup caller/wall durations ограничены timeout; сумма warmup и полная
измеренная фаза должны помещаться между соответствующими snapshots.
`reportedCleanupComplete=true / liveCleanupVerified=false` отделяют
recorded cleanup от нового live PID наблюдения. Native producer завершает
собственный Popen и сверяет fresh absence до записи результата.
