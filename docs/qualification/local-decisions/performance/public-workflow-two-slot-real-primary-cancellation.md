# Active native cancellation с real-primary peer и двумя worker slots

10.10.2026: gate прошёл в явно закреплённом v2 профиле — **два worker slots,
один actual primary model slot с очередью**. Из четырёх workflow три завершены,
target отменён. Peer сохранил первичный ответ и исходный lease через native
EOF, retirement и recovery. Ответ primary передаётся сразу по завершении модели.
Два предыдущих NUM_PARALLEL=2 native attempts сохранены как failed; этот результат
не квалифицирует cancellation при двух primary model slots.

[Summary](public-workflow-two-slot-real-primary-cancellation-summary.json) ·
[Archive receipt](public-workflow-two-slot-real-primary-cancellation-archive-summary.json) ·
[План](../../../local-decision-model-plan-2026-09-21.md) ·
[Прежний full paired inventory](public-workflow-paired-real-primary.md) ·
[Прежний two-slot deadline](public-workflow-two-slot-deadline.md).

## Prospective профиль и область проверки

В plan сохранён прежний sealed контекст: **49 полных public development inputs,
44 groups, 46 eligible / 3 overlong**, raw context SHA `c1c45eca0e0d11180dc060d292ab14480708ada261a00c1b79c57649f04dcf8c`.
Focused projection выбирает original indices **[24, 25, 12, 27]**: prefix,
eligible target, самый длинный whole original peer и eligible recovery suffix.
Peer выбран по максимуму original decision token count до успешного прогона.
Его вход содержит 9253 decision tokens / 9013 actual primary
prompt tokens; вход не сокращён. Это focused fault gate, а не новая полная
performance выборка.

Coordinator scheduler sequential/global=2, один Python worker с concurrency=2,
один published process version и прямой start → agent → end. Whole run input
передаётся один раз, начальный agent stage.input=null. Target actual primary request
должен быть pending до создания peer workflow; оба actual HTTP запроса пересекаются.
После завершения target primary coordinator отменяет только его run при свежем
native active gauge=1, до любых upstream response bytes. Native caller budget
10000 ms, inference deadline 5000 ms, свежесть active snapshot ≤250 ms,
cancel→EOF ≤2500 ms, retirement ≤10000 ms, recovery ≤90000 ms.

Actual Qwen3:8b / pinned Ollama 0.35.1, model digest
`500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`; пять cached blobs / 5225388164 bytes перепроверены до/после.
V2 NUM_PARALLEL=1 / MAX_QUEUE=1 / MAX_LOADED_MODELS=1; actual native runner log
подтверждает -np 1 / n_seq_max=1 / n_ctx=32768 / n_ctx_seq=32768. Профиль
request context=32768, decode=128, temperature=0, seed=0, think=false,
keep_alive=5m сохраняет генерацию предыдущих real-primary gates. Primary HTTP
budget=180000 ms, driver window=180000 ms; primary warmup отдельно.
Конкурентность worker и фактическое число model slots записаны отдельно.

## Наблюдаемый результат

| Проверка | Raw-bound результат |
| --- | --- |
| Workflow | 4: 3 completed, 1 cancelled; retry=0 |
| Actual primary | 4 полных ответа; 3 durable outputs; target output=null |
| Caller denominator | 3 known returns, 1 unknown return_missing |
| Healthy native | 2 abstain, 1 typed context_too_long / HTTP 422 |
| Active target | actual EOF, upstream shutdown, 0 response bytes; typed result неизвестен |
| Actual primary HTTP overlap | 5955.000 ms |
| Primary peer pending connection | 35697.000 ms; response после recovery на 22145.000 ms |
| Cancel API | authenticated 204 / anonymous 401; 4.214 ms |
| Cancelled caller / cancel→EOF | 603.254 / 566.000 ms |
| Retirement after EOF / recovery after retirement | 479.000 / 6498.000 ms |
| Late writes | observation=400, primary complete=400, fail=400; revoked renew=404 |
| Physical native denominator | 8 starts, 7 known terminals; 2 epochs, 4 warmups; target terminal counter unknown |
| Owned cleanup | runtime exits [75,130], primary=0, driver/worker=0; 14 recorded PIDs absent, credential removed |
| Overall integration | 61276.084 ms |

Primary raw native request/response, adapter response и output SHA связаны с
каждым actual run. Target primary завершился до отмены, но ни primary output,
ни shadow result не были committed после revocation. Peer сохраняет один и тот
же assignment, stage, worker и whole input до завершения; его context rejection
сохранён как known caller return. Eligible suffix подтверждает вычисление в новом
native epoch. Все три healthy native typed signatures совпали с frozen baseline
после исключения только id/durationMs. Unknown target не превращён в известный
cancelled native counter.

## Сохранённые неуспешные попытки

1. Native attempt 1, source `f5d94ae586fe4375fcc38e8c0471a614c075f075`, NUM_PARALLEL=2:
   inherited fixture driver wait=30s закончился до target primary reply/native
   intent. Реальные target/peer запросы были pending; driver aborted их при cleanup.
   В протоколе primary уже имел 180s budget. Все 12 owned PIDs absent.
2. Native attempt 2, source `60fb9eab949482b3af7d308436cf5cf626fea58c`, NUM_PARALLEL=2:
   driver wait исправлен до prospective primary budget. Whole peer дал actual
   200 response / 9013 prompt tokens / 128 decoded tokens до native recovery и
   до target caller. Gate корректно fail-closed, ответ не удержан; все 10 PIDs absent.
3. Native attempt 3 — отдельный заранее закреплённый v2 NUM_PARALLEL=1 профиль,
   target-first actual HTTP admission, source `2534ee30b83e0530249aa8152d95e1f48c1dc72d`: PASS.

Первые два attempts не показывают ошибку native cancellation: нужный active
caller/recovery сценарий не был достигнут. Они не удалены, не переименованы
в успешный paired-primary gate и не используются как статистическое повторение v2.
Primary native error journals сохраняют полученные response bytes и unfinished
request identities. Fixture failures разработки тоже сохранены отдельно от native attempts.

## Перепроверки и происхождение

Measurement source `2534ee30b83e0530249aa8152d95e1f48c1dc72d` / 210 files, original context
source `5ac6504db17e8407076bd2ad1f740c7715977776` / 40 files.
Plan SHA `4723f00e23aa55352c507c92f4a6cde61d43ceba58fec227db6a2281cfbaeaa9`;
result SHA `198c5f1c3c6894241985a08554339f9f39e3770ade5b9c6b3eacf295de071646`.
Все 37 successful raw artifacts перепроверены. Независимый stdlib audit:
743 checks / 28892 duplicate JSON key checks,
seal `1674aed126e1a240c2518ecf0c2585231480b37ea12a2b753f09a570ab332104`; application imports/model calls=0.

Offline raw replay PASS / model calls=0. Historical actual cancellation,
deadline и full paired-primary receipts повторно прошли текущие verifiers.
12 новых / 69 related regressions, 1053 Python tests (4 optional skips),
strict TypeScript и 12 Node documentation tests прошли. Read-only protected resident
сохранил все 20 installed source hashes, profile/seals/config, четыре protected
PIDs и exact health/metrics/monitor responses; scoring resident не выполнялся.

Immutable ZIP включает все три attempts, полные source snapshots, исходный
контекст, frozen healthy baseline, независимые audit/replay receipts и результаты
проверок. ZIP CRC/SHA/size, exclusive original-workspace copy и восстановление
из actual copy фиксируются в archive receipt. Raw input/output остаются под /docs/private.

## Интерпретация и следующий шаг

Проверена изоляция двух worker assignments при queued actual primary. Actual
pending HTTP overlap не доказывает два одновременно вычисляющих primary model
slots. У NUM_PARALLEL=2 cancellation gate результата PASS нет; [полный paired
inventory](public-workflow-paired-real-primary.md) остаётся отдельным performance
наблюдением. Один focused v2 прогон не устанавливает causal overhead, customer
capacity, accepted SLO, classification accuracy, human calibration или holdout.
Owners/customer/SLO открыты; reference labels=0, routing=false, not_assessed.

Следующий runtime gate — actual worker deadline с двумя worker slots, этим явно
закреплённым real-primary profile, естественно pending peer и owned recovery;
затем требуется отдельный prospective fault protocol для NUM_PARALLEL=2.

Выбор native stream=false и immediate response основан на [официальном API
Ollama](https://docs.ollama.com/api/streaming). Requested parallel slots, очередь
и общий context проверяются по [официальным настройкам
Ollama](https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests) и
фактическому pinned runner log. Следующий профиль нельзя квалифицировать одним
изменением env или искусственным удержанием готового ответа.

## Immutable archive и actual original copy replay

[Archive receipt](public-workflow-two-slot-real-primary-cancellation-archive-summary.json):
149 files / 43362674 bytes, SHA
`3bcd5731acbef01520623d539e86a6509ba4b79202f60a20c9ba31cc1b354969`. Every entry CRC/SHA/size verified. Selected pack содержит
3115 Git objects; отдельные roles: context 40, failed measurement 1/2 по
210, successful measurement/verifier по 210 и documentation 3 files.
Именно exclusive ZIP в original workspace независимо восстановлен и прошёл
source-bound replay: 210 measured sources / 40 context sources /
37 successful raw artifacts, model calls 0. Proof SHA
`239cdbb9e5a446dc50dbf5610caab4e7f0d566389d900789b798a6b36e8b2eb0`. Неуспешные attempts остаются внутри immutable ZIP.
