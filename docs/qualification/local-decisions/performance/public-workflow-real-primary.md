# Полный development inventory с настоящим primary и matched control

10.10.2026 MSK: actual coordinator и Python worker завершили 98 workflow —
49 control и 49 shadow — на всех исходных public development cases / 44 groups.
Каждый workflow получил фактический ответ pinned Qwen3:8b через Ollama 0.35.1
и сохранил его в completed stage. Все 49 observational calls к отдельному
MLX runtime 0.12.3 получили известный durable return: 31 `ok`, 15 `abstain`,
три `context_too_long`. Input, question, варианты и native профиль сохранены.

Engineering gate — `integration_pass`; qualification `not_assessed`, routing
false, reference labels 0. Это bounded serial closed-model diagnostic;
customer capacity, accuracy, calibration и производственные SLO не измерены.

## Закреплённый протокол v2

Measurement source `b6858d2f025b552b52d54a14852c1f8cba5f78b2`, 202 contributors,
закреплён до plan, запуска собственных origins и scoring. Historical context
source — `5ac6504db17e8407076bd2ad1f740c7715977776`, 40 files; raw context SHA
`c1c45eca0e0d11180dc060d292ab14480708ada261a00c1b79c57649f04dcf8c`.
В inventory 46 eligible и три whole overlong inputs; max decider input —
9253 tokens. Ни один случай не исключён и не сокращён.

[Launcher](../../../../scripts/run-public-support-real-primary.py) запускает
собственные native decider и Ollama на временных loopback ports.
[Driver](../../../../scripts/run-public-support-real-primary.mts) использует
actual coordinator/worker, worker concurrency 1, sequential scheduler,
global concurrency 1. Один process опубликован двумя версиями: control 1,
shadow 2. Графы `start → agent → end` различаются только shadow config;
имя процесса, agent, system prompt, model и полный primary request совпадают.
Чётные cases выполняются control-first, нечётные — shadow-first; внутри
каждой пары оба workflow завершены перед следующей парой.

Начальный agent stage имеет null input; coordinator lease передаёт полный
run input через existing fallback. Worker получает исходный текст в primary
prompt ровно один раз. Native decision request содержит тот же полный state,
frozen question/options и original input fingerprint. Production coordinator
и worker для этого gate не изменялись.

Primary: official Darwin/arm64 Ollama 0.35.1, pinned model digest
`500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`,
32768 context, 128 decode tokens, temperature 0, seed 0, `think=false`,
`keep_alive=5m`, one model / one parallel request. Model card описывает
[native context 32768](https://huggingface.co/Qwen/Qwen3-8B).
Actual worker chat-completions request сохраняется целиком; локальный adapter
передаёт messages в [native `/api/chat`](https://docs.ollama.com/api/chat)
с явными pinned options и возвращает фактические text/usage/finish reason.
[OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
не предоставляет `num_ctx`, поэтому context задан на native границе.
Read-only hash checks перед/после inventory подтвердили 57 release files
и все пять cached model blobs, 5 225 388 164 bytes. Установленное приложение
Ollama и его listener 11434 не использовались для scoring и не изменялись.

Decider: frozen profile `776fba7fa1244e13292ac3605a53a6d6c6cf557901c8d863cab9489d6d2f47ce`,
2048 input tokens, wired 4096 MiB, allocator cache 128 MiB, inference 5000 ms,
caller 10000 ms. Primary API budget — 180000 ms, instance — 210000 ms,
whole driver — 3600000 ms; retry/restart 0. Один primary warmup и два
computed decision warmups учтены отдельно от inventory. Обе модели загружены
в обеих условиях: сравнение отражает добавочный вызов shadow, а не экономию
от выгрузки decider в control.

## Полные ответы и caller accounting

Сохранены 98 raw native primary requests/responses, 98 translated worker
responses, 98 authenticated full traces и два whole-cohort snapshots.
Anonymous census вернул 401, authenticated — 200; каждый exact version census
содержит 49 completed instances/runs, без replay/truncation.
Control не содержит shadow intent, observation или assignment. Shadow
содержит ровно один negotiated intent, accepted native return и recorded
assignment на original stage attempt 1. Unknown/revoked caller returns — 0.

Primary request bytes совпали для всех 49 пар; actual primary prompt counts —
100–9013 tokens, без truncation warning. Все 98 returned texts после existing
worker trim точно совпали с stage output и lease completion. Между control
и shadow совпали 47 из 49 output hashes. Два различия сохранены; seed 0 и
temperature 0 не принимаются за доказательство полного byte determinism.
41 control и 42 shadow responses имеют `done_reason=length`: заданный
128-token budget достигнут, качество или полнота предметного ответа не
оцениваются. Это не сокращение исходного входа.

Raw lease journal содержит 303 HTTP records: 98 complete, 49 intent,
49 return и 107 renewals. Все required mutation bodies прочитаны полностью;
107 renewal records имеют explicit `requestBodyComplete=false`, их неполные
тела не принимаются за полные bytes. Renewals — 204; intent, return и complete
— 200; fail/revocation нет. Lease path UUID связан с run через accepted intent,
а его assignment ID сверяется с durable trace; эти два UUID не отождествляются.

Один native epoch: три idle snapshots дали 0 → 2 → 51 completed physical calls.
Delta inventory — 46 computed + три context rejections; generated decision
tokens 0. Все 49 typed signatures совпали с independently sealed historical
healthy ZIP после исключения request ID и duration. Probabilities, selected
option, margin и frozen abstention policy независимо пересчитаны из logits.

## Наблюдаемые длительности

| Интервал | Count | p50 ms | p95 ms | max ms |
|---|---:|---:|---:|---:|
| Primary control | 49 | 3850.767 | 8239.713 | 29602.760 |
| Primary shadow | 49 | 3738.388 | 5802.814 | 6349.258 |
| Whole workflow control | 49 | 4121.235 | 8407.051 | 29705.700 |
| Whole workflow shadow | 49 | 4465.132 | 7832.995 | 9094.091 |
| Shadow local HTTP caller | 49 | 532.283 | 2375.613 | 2905.529 |

Actual workflow window — 480.872 s; launcher elapsed — 496217.425 ms.
Для 49 matched workflow pairs `shadow - control`: median 722.171 ms,
mean 237.016 ms, min -25369.091 ms, max 4981.308 ms. Primary residence и
prefix cache общие; самый длинный control prefill — 29.603 s. Counterbalanced
order уменьшает простой order bias, но одного прохода с shared cache и
разными ответами недостаточно для causal performance claim или SLO acceptance.

## Сохранённая неуспешная попытка и проверки

Первый v1 native attempt сохранён отдельно: 24 completed workflow,
12 durable shadow returns; следующий whole input получил primary API timeout
при 90-second budget. Граф с internal transform добавлял state в prompt
второй раз; первый prefill был около 17900 tokens. Анализ actual lease/worker
показал existing whole-run-input fallback. Новый prospective v2 использует
direct agent path, одну копию исходного текста и 180-second budget; весь
inventory выполнен заново, без переноса частичных измерений в pass.

После cleanup первого attempt harness вызвал отсутствующий helper `alive`
и не записал result receipt. Raw plan/journals, exception log и post-hoc
failure analysis сохранены; полный PID denominator первого attempt неизвестен.
V2 использует проверенный `remaining_owned_processes`, ведёт immediate PID
ledger и сохраняет failure receipt при startup/cleanup fault. Два отдельных
fault tests запрещают выдавать unknown cleanup за empty inventory.

Первый CI нового harness также сохранил failure: timer мог разбудить driver
раньше planned census start. Это детерминированно воспроизведено early-timer
fixture до исправления. Driver теперь перепроверяет wall-clock boundary после
каждого пробуждения; строгие census/chronology guards сохранены. Такой guard
соответствует [ограничениям Node timers](https://nodejs.org/api/timers.html#timers_settimeout_callback_delay_args).
Native measurement остаётся связан с исходным B685 source; последующий timing
fix не подменяет его. Финальные 18 tests, strict TypeScript и свежий offline
replay после fix — PASS.

Последующий CI выявил `Pair crossed completion barrier/deadline` в прежнем
v8 fixture. Его общий driver тоже использовал один timer wake без проверки
planned start. Controlled early wake детерминированно исключил run из census
до fix; общий driver теперь применяет тот же wall-clock guard. Пределы
хронологии, deadline и расхождения clocks сохранены; ошибка включает actual
boundary timestamps и обе длительности. 49 related regressions, typecheck
обоих drivers и fresh v8/real-primary offline replay прошли на source
`02258b41f25e6e3870484b6d029c148ae33a1fb1`. Этот CI follow-up хранится отдельным supplementary archive;
исходный immutable native archive сохранён. Полный локальный набор —
1002 Python tests PASS, четыре optional skips; эти skips не принимаются за
выполненные native gates.

[Offline verifier](../../../../scripts/verify-public-support-real-primary.py)
проверяет raw pins, source closure, exact artifact set, оба census и весь
inventory без model/network calls. Независимый stdlib audit без application
imports прошёл 5254 evidence checks и 171430 JSON-key checks. Дополнительное
независимое сравнение проверило historical ZIP по outer SHA/CRC/всем file
SHA/size и подтвердило 49 matching native signatures.

Все 111 записанных temporary PIDs отсутствуют; worker/driver/primary exit 0,
runtime штатно остановлен с exit 130, credentials удалены. Две read-only
resident проверки по 27 checks подтвердили прежние четыре PIDs, 20 installed
runtime files, package/config/plist seals, ready profile, counters и monitor.
Protected listeners 8766/9095 не получили scoring calls.

Raw requests/responses и исходные тексты хранятся только под `/docs/private`.
[Public summary](./public-workflow-real-primary-summary.json) содержит metadata;
[archive metadata](./public-workflow-real-primary-archive-summary.json)
закрепляет successful/failed evidence, committed sources и identical original
workspace copy. Raw v2 plan SHA
`904a76d947fa704686058f9c7a417773ddcf6dc027a7e77deeb9b56767157e2b`;
raw result SHA
`8dbba6d9285535646fb94b2c0f0f4a0e4b8a751eb939fcce335a3b774ef25d1a`.

Следующий runtime gate — cancellation при actual worker concurrency 2 с
сохранением unaffected соседнего workflow и последующим owned recovery.
Independent human reviews, calibration/holdout, реальные eligible business
inputs, customer SLO и назначения owners остаются внешними предметными gates.
