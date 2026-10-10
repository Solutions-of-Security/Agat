# Worker deadline с queued real-primary peer и двумя worker slots

10.10.2026: actual gate прошёл с **двумя worker slots и одним primary model slot
с очередью**. Все четыре workflow завершены, все четыре primary outputs сохранены.
Worker записал known unavailable/timeout в исходный lease target после deadline
250 ms. Native runtime завершился после EOF и восстановился с двумя warmups,
пока actual primary-запрос peer ещё выполнялся. Ответ модели передан сразу после
получения, без искусственного удержания.

[Summary](public-workflow-two-slot-real-primary-deadline-summary.json) ·
[Archive receipt](public-workflow-two-slot-real-primary-deadline-archive-summary.json) ·
[План](../../../local-decision-model-plan-2026-09-21.md) ·
[Queued-primary cancellation](public-workflow-two-slot-real-primary-cancellation.md) ·
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
Target actual primary request должен быть pending до создания peer workflow.

Primary — Qwen3:8b / pinned Ollama 0.35.1; digest `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`,
пять blobs / 5225388164 bytes перепроверены до/после. NUM_PARALLEL=1,
MAX_QUEUE=1, MAX_LOADED_MODELS=1; actual runner подтверждает -np 1,
n_seq_max=1, n_ctx=32768, n_ctx_seq=32768. Request context=32768, decode=128,
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
| Worker local deadline / relay target | 254.246 / 251.053 ms |
| Active target | EOF, upstream shutdown, 0 response bytes; native typed terminal неизвестен |
| Retirement after EOF / recovery after retirement | 338.000 / 7172.000 ms |
| Actual primary HTTP overlap | 5666.000 ms |
| Peer primary pending connection | 34297.000 ms; response после recovery на 20853.000 ms |
| Original leases | target timeout и primary complete в исходном lease; peer assignment/stage/worker сохранены |
| Coordinator writes | cancellation=0; fail=0; observations/completions=200, renewal=204 |
| Physical native | 8 starts / 7 known terminals / 2 epochs / 4 warmups |
| Owned cleanup | runtime exits=[75,130], primary=0, driver/worker=0; 14 PID absent |
| Общий launcher elapsed | 61865.051 ms |

Deadline произошёл после target primary ответа. Worker сохранил этот ответ и
recorded local timeout; coordinator не отменял run и не отзывал lease. Peer
оставался assigned/running с output=null до native recovery; его actual HTTP
response получен позднее и сразу отправлен worker. Suffix вычислен в новом
native epoch. Три healthy typed signatures совпадают с frozen healthy control
после исключения только id/durationMs.

Known local timeout не превращён в известный native timeout counter: у
interrupted target нет native typed response и завершённого terminal counter.
Six incomplete HTTP request bodies относятся только к renewals, чьи actual
204 statuses сохранены; scoring/observation/complete bodies полные и SHA-bound.
Это один successful native attempt, повторных failed native attempts нет.

## Проверки и происхождение

Measurement source `812d1b5c8dfbb716235e24bde36f2e186d0e89e4` / 215 files;
original context `5ac6504db17e8407076bd2ad1f740c7715977776` / 40 files.
Plan SHA `26c75caff5428734923c032a906a30bf18461902d054ce8bf80d3817b507ee3c`; result SHA `fd59b1062bd31c5fe9f5d1e18b8a38916068eed90457ff6710e3e059686a062f`.
Все 39 raw artifacts проверены; actual requests, native и
translated responses, output SHA, leases, timing, epochs и cleanup связаны с runs.
Stdlib audit: 772 checks / 29394 JSON key checks,
seal `06c4a40a31057165cabef0c2182eb7d1946d5b1ab6f828ceb875ec5323f88cd4`, application imports/model calls=0.

Offline replay PASS / model calls=0. Historical fixture cancellation/deadline
и queued real-primary cancellation повторно прошли текущие verifiers.
12 новых / 81 related tests, 1065 Python tests (4 optional
skips), strict TypeScript / 12 Node documentation tests прошли.
Read-only resident сохранил 20 installed source hashes, profile/seals/config,
четыре protected PIDs и exact health/metrics/monitor responses. Отдельный live
ps check подтвердил отсутствие 14 owned PIDs; offline replay этого не устанавливает.

Private immutable ZIP сохраняет full original context, raw receipts, frozen
baseline, source snapshots, selected Git objects, independent audit, тесты и
restored offline replay. CRC/SHA/size каждого entry, exclusive original-workspace
copy и replay из actual original copy зафиксированы в archive receipt.
Публичный документ содержит metadata; whole input/output остаются в /docs/private.

## Следующий gate

Пройден focused worker fault gate при одном actual primary model slot с очередью.
HTTP overlap не квалифицирует два одновременно вычисляющих model slots.
Следующий этап — отдельный prospective native fault protocol при NUM_PARALLEL=2,
с actual target-first admission и естественно pending peer. Early response
останется failed gate и будет сохранён; удержание готового ответа не применяется.
Customer traffic/capacity, causal overhead, accepted SLO, classification accuracy,
human calibration и holdout не установлены; labels=0, owners/customer/SLO открыты,
routing=false, qualification=not_assessed.

Stream=false и immediate complete response сверены с [официальным API
Ollama](https://docs.ollama.com/api/streaming). Queue и requested parallel slots
сверены с [официальным FAQ](https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests);
фактические slots и context подтверждены pinned runner log.
