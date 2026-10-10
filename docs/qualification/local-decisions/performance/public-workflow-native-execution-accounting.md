# Native HTTP and logical forward accounting

Статус: **все 100 original native attempts воспроизведены; source-bound replay
и independent stdlib audit PASS**. Этап уточняет denominator [native replication](./public-workflow-counterbalanced-real-primary.md)
и исправляет предположение о cached native return в прежнем отчёте.

## Source contract

Поддерживается только pinned runtime 0.12.3, implementation SHA
`e403f87db306a727bf25fa319d03f5a66f96a7cc3606a3c0d26cf1d929a6c9d8`.
Четыре исходных файла engine/server/isolated/MLX backend закреплены по file SHA.
При изменении implementation или call path accounting останавливается;
новую семантику нельзя автоматически переносить на другой runtime.

Server отказывает busy до engine/backend scoring. У валидного context-too-long
request `encode_request` отказывает до model call. Успешный ok **или abstain**
проходит отдельный `MlxBackend.score`: новый array входа, model call, `mx.eval`
и получение logits. Native decision не генерирует текст: generatedTokens=0.
Два одинаковых успешных warmups проходят этот путь два раза.

| Уровень accounting | Что подтверждается |
|---|---|
| HTTP attempts и ответы | Исходные journals, warmup receipts и native epoch counters |
| Backend score entries и logical model forward completions | Вывод из observed replies и точного source contract |
| GPU kernel dispatch, GPU-only latency, hardware concurrency | Не измерены этим ledger |

Fresh logical forward invocation не задаёт число Metal kernels, их scheduling
или GPU utilization. Reply duration включает работу runtime и IPC; она не
приравнивается к GPU-only duration. Все 98 case attempts и два warmups сохраняются,
включая busy и context refusals. Accepted native status не является expert label
или business acceptance.

## Cache semantics и исправление

Профиль содержит `allocatorCacheLimitBytes=134217728` (128 MiB). В исходниках
это вызов `mx.set_cache_limit`; он ограничивает reusable free buffers.
В зарегистрированном Python score path нет request-result memoization,
cross-request KV/recurrent state не передаётся. Buffer reuse и model
result reuse имеют разную семантику. Первичный Ollama runner имеет собственную
историю prompt/prefill; вывод о native score path её не характеризует.

Фраза предыдущего отчёта о возможном cached native warmup return была
необоснованной. Она исправлена в текущей документации; прежние immutable
archives и raw измерение сохраняются без изменений. Ограничение HTTP-counter
остаётся точным: 100 attempts включает admission/context refusals и не является
измеренным счётчиком GPU kernels.

[MLX set_cache_limit](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.set_cache_limit.html)
описывает free-memory cache и его byte limit;
[MLX eval](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.eval.html)
описывает evaluation arrays. Документация прочитана 10.10.2026 (0.32.3);
измеренный environment содержит MLX 0.32.2 / MLX-LM 0.31.3. Конкретный count
опирается на сохранённый runtime source и replies, а не на перенос benchmark
чисел между версиями библиотек.

## Наблюдение 2026-10-10

Один analysis execution без новых model calls. Исходный dataset — 49 whole inputs,
98 decision case HTTP attempts и два warmups. Все attempts сохранены:

| Scope | HTTP observed | Scores observed | Busy | Context refusal | Backend entries inferred | Logical forward completions inferred |
|---|---:|---:|---:|---:|---:|---:|
| 49 cases × 2 replicas | 98 | 50 | 46 | 2 | 52 | 50 |
| Warmups | 2 | 2 | 0 | 0 | 2 | 2 |
| All recorded HTTP attempts | 100 | 52 | 46 | 2 | 54 | 52 |

Case outcomes: ok=32, abstain=18, busy=46, context_rejected=2; оба warmups
имеют abstain. 50 case scores и два warmups подтверждены replies. Их 52
request-level logical forward completions — вывод из source contract, включая
abstention. 54 inferred backend entries включают два context rejections,
которые заканчиваются до model call. Busy не входит в backend entries.

Эти request-level counts не учитывают отдельную initialization/library работу
и не являются hardware trace. Число GPU kernels, GPU-only duration и hardware
concurrency не измерены. Успешные scores имеют generatedTokens=0; отсутствие
генерации текста не означает отсутствие model forward computation.

## Проверки и сохранность

Source `91e893fb2500feaaccf4e08dba973541529e095f`: 232 files, original
native source — 224, context — 40. Analysis file SHA
`86235f1ee0171075332d3a281e28e233b9c25bbedc8e36e2716f90603dc6b964`.
Source-bound replay заново проверил всю original inventory и native epoch
denominator. Independent stdlib audit воспроизвёл каждый из 100 request rows,
проверил четыре registered source files и AST score path с одним model call
и одним `mx.eval` до успешного возврата.

16 новых / 70 related / 1206 Python tests PASS, 4 optional skips.
Independent audit: 12474 checks / 31185 JSON keys, первая версия PASS;
audit seal `30791fc31c69f4da11f6e58965edbb348bac04b1301649bea4a1e8eac1e62ca8`.
Protected resident: 27 checks и восемь snapshot полей неизменны.
Runtime implementation, policy, raw native measurement и primary settings
не менялись. Cache wording исправлен в текущем native report и его summary;
прежние immutable snapshots сохранены. Forward count остаётся source-inferred.

[Aggregate summary](./public-workflow-native-execution-summary.json) содержит
counts и source contract. Полный 100-row ledger, body hashes и все raw native
receipts сохранены privately. Human labels/owners/customer/SLO/holdout gates
открыты; routing=false / not_assessed.

## Инструменты

[Analyzer](../../../../scripts/analyze-public-support-native-execution.py),
[source-bound verifier](../../../../scripts/verify-public-support-native-execution.py),
[execution contract](../../../../scripts/lib/decision_public_native_execution_accounting.py),
[regressions](../../../../scripts/test/test_decision_public_native_execution_accounting.py).
Анализ сначала воспроизводит весь original native inventory и проверяет его
source/context/plan/result hashes, затем строит полный accounting ledger.
Все forward counts помечаются source-inferred; unsupported paths не исключаются
молча. New model calls=0; labels/owners/customer/SLO/holdout gates открыты,
routing=false / not_assessed.
