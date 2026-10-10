# Worker deadline с двумя actual primary slots

10.10.2026: actual gate прошёл с **двумя worker slots и двумя actual primary model slots**. Все четыре workflow завершены, все четыре primary outputs сохранены.
Worker записал known unavailable/timeout в исходный lease target после deadline
250 ms. Native runtime завершился после EOF и восстановился с двумя warmups,
пока actual primary-запрос peer ещё выполнялся. Ответ модели передан сразу после
получения, без искусственного удержания.

[Summary](public-workflow-two-slot-parallel-real-primary-deadline-summary.json) ·
[Archive receipt](public-workflow-two-slot-parallel-real-primary-deadline-archive-summary.json) ·
[План](../../../local-decision-model-plan-2026-09-21.md) ·
[Two-primary-slot cancellation](public-workflow-two-slot-parallel-real-primary-cancellation.md) ·
[Прежний fixture deadline](public-workflow-two-slot-deadline.md).

## Prospective профиль

До launch сохранены commit и отдельная sealed design declaration. Plan содержит
прежние **49 целых public development inputs / 44 groups**, 46 eligible и 3
overlong; context SHA `c1c45eca0e0d11180dc060d292ab14480708ada261a00c1b79c57649f04dcf8c`.
Focused projection **[24,25,12,27]** задаёт prefix, eligible target, longest
whole original peer и eligible recovery suffix. Peer выбран по original token
count: 9253 decision tokens / 9013 actual primary prompt
tokens. Входы, native profile и генерация не сокращены и не подменены.

Coordinator sequential/global=2; один Python worker с concurrency=2;
start → agent → end, whole run input один раз, initial stage.input=null.
У одного published process две версии: v1 caller budget=10000 ms,
v2 только для target budget=250 ms. Raw graphs совпадают после удаления этой
единственной разницы. Creation order=[0,1,2,3], case versions=[1,2,1,1].
Peer workflow создаётся при первом raw read-only target GET /slots witness
n_decoded=116..127; до target response наблюдаются два is_processing slots.
Actual task/slot IDs связаны с runner log, original inputs/native request SHA и
финальными response prompt/decode counts. Progress reads не являются model calls.

Primary — Qwen3:8b / pinned Ollama 0.35.1; digest `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`,
пять blobs / 5225388164 bytes перепроверены до/после. NUM_PARALLEL=2,
MAX_QUEUE=1, MAX_LOADED_MODELS=1; actual runner подтверждает -np 2,
n_seq_max=2, n_ctx=65536, n_ctx_seq=32768. Request context=32768, decode=128,
temperature=0, seed=0, think=false, keep_alive=5m; HTTP и driver budgets=180000 ms.
Native 2B profile сохраняет input limit=2048 и inference deadline=5000 ms.
Relay должен увидеть active native handler до ответа и EOF; retry=0,
retirement budget=10000 ms, recovery budget=90000 ms.

## Raw-bound результат

| Проверка | Наблюдение |
| --- | --- |
| Workflow / actual primary | 4 completed / 4 actual ответы / 4 durable outputs |
| Caller denominator | 4 known returns; 1 local unavailable/timeout, 3 healthy native |
| Healthy native | 2 abstain; whole peer context_too_long / HTTP 422 |
| Worker local deadline / relay target | 252.662 / 251.627 ms |
| Active target | EOF, upstream shutdown, 0 response bytes; native typed terminal неизвестен |
| Native child stop | exit=-9; terminate/join200 ms → SIGKILL; старый IPC закрыт |
| Retirement after EOF / recovery after retirement | 1345.000 / 7904.000 ms |
| Target admission / два processing slots | decoded=116; target task=60 / peer task=643; 456 pre / 4 parallel GET reads |
| Actual primary HTTP overlap | 5629.000 ms |
| Peer primary pending connection | 27864.000 ms; response после recovery на 12710.000 ms |
| Original leases | target timeout и primary complete в исходном lease; peer assignment/stage/worker сохранены |
| Coordinator writes | cancellation=0; fail=0; observations/completions=200, renewal=204 |
| Physical native | 8 starts / 7 known terminals / 2 epochs / 4 warmups |
| Owned cleanup | runtime exits=[75,130], primary=0, driver/worker=0; 15 PID absent |
| Общий launcher elapsed | 64473.742 ms |

Deadline произошёл после target primary ответа. Worker сохранил этот ответ и
recorded local timeout; coordinator не отменял run и не отзывал lease. Peer
оставался assigned/running с output=null до native recovery; его actual HTTP
response получен позднее и сразу отправлен worker. Suffix вычислен в новом
native epoch. Три healthy typed signatures совпадают с frozen healthy control
после исключения только id/durationMs.

Known local timeout не превращён в известный native timeout counter: у
interrupted target нет native typed response и завершённого terminal counter.
9 incomplete HTTP request bodies относятся только к renewals, чьи actual
204 statuses сохранены; scoring/observation/complete bodies полные и SHA-bound.
Это один successful native attempt, повторных failed native attempts нет.

## Проверки и происхождение

Measurement source `25fd324aaf315237ee383d67bf7feae3ee7ace2c` / 221 files;
original context `5ac6504db17e8407076bd2ad1f740c7715977776` / 40 files.
Plan SHA `578c7e3d689adb8c68d0bbc8365abfde19379bbf1481c0531c08e006c2e9d3b2`; result SHA `532dda88c69138db19cff7323e61e1bfbac1d181ddad242e2e4e2619eddd2e8c`.
Все 42 raw artifacts проверены; actual requests, native и
translated responses, output SHA, leases, timing, epochs и cleanup связаны с runs.
Stdlib audit: 4946 checks / 90821 JSON key checks,
seal `7a615268e4d00c9a53c1e05731f948c01eb69309b28aaa3311494b09a35c435a`, application imports/model calls=0. Первый audit failure
выявил пропущенный test source; второй — историческое SIGTERM-only предположение.
Оба сохранены. Проверка теперь охватывает все221 источника и предусмотренные
исходы0/-9/-15, с независимой проверкой pinned bounded-stop implementation;
raw evidence и measured source не менялись. Child exit этого прогона=-9.

Offline replay PASS / model calls=0. Historical fixture cancellation/deadline
queued real-primary cancellation/deadline и actual two-primary-slot cancellation
повторно прошли текущие verifiers.
18 новых / 117 related tests, 1101 Python tests (4 optional
skips), strict шесть TypeScript drivers / 12 Node documentation tests прошли.
18 runtime stop/exit-diagnostics regressions дополнительно PASS.
Read-only resident сохранил 20 installed source hashes, profile/seals/config,
четыре protected PIDs и exact health/metrics/monitor responses. Отдельный live
ps check подтвердил отсутствие 15 owned PIDs; offline replay этого не устанавливает.

Private immutable ZIP сохраняет full original context, raw receipts, frozen
baseline, source snapshots, selected Git objects, independent audit, тесты и
restored offline replay. CRC/SHA/size каждого entry, exclusive original-workspace
copy и replay из actual original copy зафиксированы в archive receipt.
Публичный документ содержит metadata; whole input/output остаются в /docs/private.

## Область результата и следующие работы

Focused cancellation и worker deadline gates прошли при NUM_PARALLEL=2 в
отдельном prospective admission protocol. Два processing slots и HTTP overlap
сами по себе не устанавливают customer capacity или simultaneous GPU kernels.
Customer traffic/capacity, causal overhead, accepted SLO, classification accuracy,
human calibration и holdout остаются открытыми; labels=0, owners/customer/SLO открыты,
routing=false, qualification=not_assessed. Дальнейший workload protocol следует
зафиксировать отдельно до измерения; этот gate не расширяет production qualification.

Stream=false и immediate complete response сверены с [официальным API
Ollama](https://docs.ollama.com/api/streaming). Queue и requested parallel slots
сверены с [официальным FAQ](https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests);
фактические slots и context подтверждены pinned runner log.
Bounded terminate/kill semantics сверены с [Python3.13 multiprocessing](https://docs.python.org/3.13/library/multiprocessing.html#multiprocessing.Process.kill).
