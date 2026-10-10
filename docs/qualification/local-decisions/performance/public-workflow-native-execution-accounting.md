# Native HTTP and logical forward accounting

Статус: implementation и CPU tests; source-bound анализ исходных receipts ещё
не выполнен. Этап уточняет denominator [native replication](./public-workflow-counterbalanced-real-primary.md)
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
