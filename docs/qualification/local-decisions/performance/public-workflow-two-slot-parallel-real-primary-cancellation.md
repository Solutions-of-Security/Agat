# Active native cancellation с двумя actual primary slots

10.10.2026: gate прошёл для отдельного prospective протокола: **worker/global=2,
actual primary NUM_PARALLEL=2, поздний decode target до admission peer**.
Из четырёх workflow три завершены, target отменён. Peer сохранил original
assignment/stage/worker и primary output через native EOF, retirement и cold
recovery. Завершённый primary response передаётся сразу.

[Summary](public-workflow-two-slot-parallel-real-primary-cancellation-summary.json) · [Archive receipt](public-workflow-two-slot-parallel-real-primary-cancellation-archive-summary.json) ·
[План](../../../local-decision-model-plan-2026-09-21.md) ·
[Прежний queued-primary cancellation](public-workflow-two-slot-real-primary-cancellation.md) ·
[Прежний queued-primary deadline](public-workflow-two-slot-real-primary-deadline.md).

## Зафиксированный протокол

Whole sealed public development контекст: **49 inputs / 44 groups, 46 eligible / 3
overlong**, raw SHA `c1c45eca0e0d11180dc060d292ab14480708ada261a00c1b79c57649f04dcf8c`. Focused projection
**[24,25,12,27]** выбирает prefix, eligible target, самый длинный whole original peer
и eligible suffix до измерения. Peer: 9253 decision / 9013
actual primary prompt tokens. Whole run input передаётся один раз, agent stage.input=null.
Это focused fault gate; результаты не являются customer performance выборкой.

Первый NUM_PARALLEL=2 attempt сохранён как failed. Target был принят первым,
но длинный peer prefill задержал target decode; peer ответил до target native intent.
Поэтому source/plan второго candidate вводит отдельную admission boundary:
read-only GET /slots owned runner показывает target n_decoded=116..127 до создания
peer; следующий raw snapshot должен показать два is_processing slots до завершения
target primary response. Task/slot IDs сопоставлены с native runner log, request/input
SHA и финальными native prompt/decode counts. HTTP overlap учитывается отдельно.

Pinned Qwen3:8b digest `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`, Ollama 0.35.1;
57 binary files и 5 cached model blobs / 5225388164 bytes проверены до/после.
NUM_PARALLEL=2 / MAX_QUEUE=1 / MAX_LOADED_MODELS=1; actual -np2 / n_seq_max2 /
n_ctx65536 / n_ctx_seq32768. Generation прежняя: context32768/decode128,
temperature0/seed0/thinkfalse/keep_alive5m. Четыре scoring primary requests и один
primary warmup. Native inference5000 ms, caller10000 ms, primary/driver180000 ms,
retry0. Один published process / sequential scheduler / два worker slots.

## Результат по сырым данным

| Проверка | Результат |
| --- | --- |
| Workflow / primary | 4 actual primary responses; 3 completed / 1 cancelled; 3 durable outputs |
| Caller | 3 known / 1 unknown return_missing; target typed terminal неизвестен |
| Healthy native | 2 abstain / 1 whole-context rejection; 3 signatures совпали с baseline |
| Target admission | decoded=116; task=60 |
| Два processing slots | peer task=641; 454 pre-admission / 3 parallel GET reads |
| Actual primary HTTP overlap | 5618.000 ms |
| Peer pending | 27939.000 ms; response после recovery на 13389.000 ms |
| Authenticated cancellation | 2.381 ms; unauthorized401 / authorized204 |
| Cancel → EOF / EOF → retirement | 466.000 / 916.000 ms |
| Cold recovery → two warmups | 7401.000 ms после retirement |
| Physical accounting | 8 starts / 7 known terminals / 4 warmups / 2 epochs |
| Cleanup | 14 owned PIDs independently absent; resident27 checks unchanged |

Cancelled target lease revoked; late observation/primary completion/fail writes rejected,
peer original lease accepted. Raw relay наблюдает caller EOF, upstream shutdown и ноль
response bytes. Новый runtime epoch готов и дважды прогрет до peer native scoring;
primary peer и suffix записаны в original assignments. Terminal outcome target не
выведен из busy/error или поздней rejected local observation. GPU kernel preemption
из этого HTTP/lifecycle evidence не установлена.

## Проверки и воспроизводимость

Native attempts: 2, successful:1; все failed raw artifacts и их source
сохранены. Отдельный API diagnostic: 1 scoring primary request / 1 warmup /
0 shadow-model calls / 229 raw reads. Его process завершился failed_parser после
успешного wire request: pinned next_token имеет array shape. Offline audit существующих
raw snapshots PASS; diagnostic не повторён и не включён в workflow denominator.

Measurement source `6ee98f63edb5027a201d880797174474f12253a1`: 216 files; context source:
40 files. Independent stdlib audit: 4891 checks /
89948 duplicate-key checks, seal `47c2be734283d1cd64f4d8239234ea8838964d41163e1af198d305fd52b718b2`.
Verifier, audit и restored replay model calls=0. 18 новых / 99 related tests,
1083 Python tests / 4 optional skips; strict five TypeScript drivers /
12 Node documentation tests PASS. Legacy raw cancellation/deadline replay PASS для
прежних fixture и actual queued-primary протоколов.

[Archive receipt](public-workflow-two-slot-parallel-real-primary-cancellation-archive-summary.json) фиксирует private immutable ZIP,
source roles, CRC/every-entry SHA/size verification, сохранённую original-workspace
copy и restored offline replay. Raw state, primary outputs и credentials в public
payloads отсутствуют. Original-copy proof хранится рядом с ZIP отдельным sidecar,
чтобы избежать циклического hash dependency.

Gate не квалифицирует customer capacity, SLO, accuracy, production routing или
NUM_PARALLEL=2 worker deadline. Labels=0, owners/customer/holdout открыты,
routing=false / not_assessed. Следующий runtime gate — prospective worker deadline
с actual двумя model slots и явно зафиксированным admission protocol.

## Источники для профиля

- [Ollama concurrent requests](https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests): parallel slots увеличивают context allocation.
- [Ollama streaming](https://docs.ollama.com/api/streaming): stream=false response передаётся после завершения модели.
- [Pinned scheduler](https://github.com/ollama/ollama/blob/v0.35.1/server/sched.go): NUM_PARALLEL проверяется по actual runner, отдельно от worker concurrency.
- [llama.cpp server API](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md): read-only /slots; wire shape взята из pinned binary evidence.
