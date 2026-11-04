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

## Native diagnostic 08.10 MSK

Из `c1b44cc` завершены все **343 variants / 49 original cases / 44 groups**:
**322 computed** (219 ok / 103 abstain), **21 full context rejections**,
ноль not-attempted. 46 original cases из 42 groups вычислены во всех семи
orders, три original cases целиком отклонены по длине.

У **28/46** fully computed cases все `(status, reason, selected ID, value)`
одинаковы; у **18/46** (18 groups) хотя бы одно поле меняется между orders.
**46/46** original/repeat comparisons совпали, maximum probability delta
у repeat — **0**. Maximum semantic probability delta относительно
original order — **0,466296**. Cyclic argmax counts по positions 0–4:
**44 / 47 / 48 / 50 / 41**, denominator 230 computed cyclic variants.
Эти marginal counts не доказывают отсутствие order sensitivity; fixed
order и отсутствие reference labels не позволяют выводить causal bias
или classification accuracy.

Измеренная фаза — **78 319,744 ms**; computed caller p50 **198,720 ms**,
p95 **579,983 ms**, max **696,437 ms**. Physical accounting — **343**
scheduled handlers / **345** с двумя warmup, zero origin / no recorded
restart. **107** source bindings и 34 native dependencies сохранились.
Все 46 original computed typed results совпали с прежним standalone
run после удаления transport ID/duration; три original context errors
тоже сохранились. Это контроль прежних responses, не gold labels.

Independent audit — **2653 checks**, включая независимые softmax/policy,
case/group/order/repeat summaries, raw files, metrics и fresh cleanup.
Все три recorded owned PIDs отсутствуют. Protected resident сохранил
четыре PID, 35 source pins и fresh before/after counters; actual boot/login
не наблюдался. Восемь targeted tests и full **885 Python / 4 optional
skips, 12 Node**, links/catalog прошли. [Allowlisted native summary](./public-option-permutation-diagnostic-summary.json)
закрепляет raw SHA/seals и результаты; labels/owners/SLO/qualification
остаются открытыми, routing false.

Private ZIP **30 entries / 917 372 bytes**, SHA
`c5bf8bd38c11000b0c0417e0da6fbfe585143334b49aef89836f3006f224d8ba`
сохранён в исходном workspace с 0600. Обе копии проверены по CRC и каждому
entry SHA/size, оба parent archive SHA сверены. Raw measured files,
audit/initial fixture failures, resident snapshots и два 107-source
committed states сохранены. [Archive receipt](./public-option-permutation-diagnostic-archive-summary.json).
