# Наблюдение Python-вызовов native backend

Статус: **четыре заранее запланированные dev-попытки выполнены; оригинальный
журнал и независимый replay PASS**. Этап дополняет
[source-contract accounting](./public-workflow-native-execution-accounting.md)
фактически наблюдёнными Python-вызовами в отдельном процессе.

## Что выполнено

В disposable worker загружен установленный runtime 0.12.3 с implementation SHA
`e403f87db306a727bf25fa319d03f5a66f96a7cc3606a3c0d26cf1d929a6c9d8`.
Использованы исходные policy, manifest и stock `IsolatedBackend`: max input 2048,
allocator cache 128 MiB, wired budget 4096 MiB, inference deadline 5000 ms,
startup deadline 60 s. После удаления явного маркера instrumentation профиль
точно совпадает с установленным профилем; instrumented профиль имеет отдельный SHA.

Выбор зафиксирован до первого model call: два самых коротких eligible dev-входа
по `(inputTokens, id)`, повтор первого и самый длинный over-context вход.
Это три уникальных входа и четыре последовательные попытки через engine/IPC.
9253-token request передан целиком. Источники — существующие unlabelled dev-входы;
измерение не оценивает предметное качество. Primary runner и HTTP admission
не входили в эксперимент.

| Python boundary | Наблюдено entries | Наблюдено returns | Наблюдено raises |
|---|---:|---:|---:|
| Native `backend.score` | 4 | 3 | 1 |
| Точный `text_model.model.__call__` | 3 | 3 | 0 |
| `mx.eval` в scoring-потоке | 3 | 3 | 0 |

Обе попытки одинакового короткого входа содержат отдельный text-backbone call
и отдельный `mx.eval`. Over-context попытка содержит `DecisionError` из score
и не содержит model/eval entry. Ответы: два `abstain/below_threshold`, один
`ok/accepted`, один `error/context_too_long`; эти статусы не являются human labels.
Generated decision tokens=0. Counts старых 100 HTTP attempts остаются
source-inferred: новый журнал содержит только собственные четыре попытки.

## Семантика наблюдения

[Observer](../../../../scripts/lib/decision_native_python_observations.py)
временно меняет Python `__call__` класса с фильтром по точному model instance
и owner thread, а также `mx.eval` этого процесса. Сам model object сохраняется.
Методы восстанавливаются в `finally`, включая refusal и exceptions. Process-wide
lock отказывает concurrent/nested instrumentation. Другие instances и threads
делегируют исходным методам и не входят в counts.

25 JSONL records связывают PID, thread, последовательность, input SHA, span,
start/end, return/raise и подтверждение восстановления. Каждая запись имеет
SHA и ссылку на предыдущую; файл создаётся exclusively с mode 0600. Полный текст
request в journal отсутствует. Убитый worker может оставить незаконченный span;
entry без return не означает завершённое вычисление. Model loading происходит
до установки hooks и не измерен этим журналом.

MLX строит вычисления лениво, а `eval` запрашивает evaluation arrays. Поэтому
возврат Python model call и длительность его span не задают GPU kernel count
или GPU-only time. Это интерпретация границ наблюдения на основе
[MLX lazy evaluation](https://ml-explore.github.io/mlx/build/html/usage/lazy_evaluation.html)
и [MLX eval](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.eval.html).
Документация сейчас относится к 0.32.3; эксперимент использовал установленный
MLX 0.32.2 / MLX-LM 0.31.3 и закреплённые runtime sources.

Host spans используют `perf_counter_ns`, CPU spans — время вызывающего потока
`thread_time_ns` по [Python 3.13 time](https://docs.python.org/3.13/library/time.html).
Запись начального события входит в span; instrumentation overhead не вычтен.
Full regressions могли выполняться параллельно. Эти durations сохранены как
диагностика и не сравниваются с прежней ненаблюдаемой latency или SLO.

## Проверка и evidence

Source commit `af0d690f1ad3c40e880dc7e34df87f6b2139d809`: два instrument/test файла.
Отдельно сохранены все 20 installed runtime files и три config files по SHA.
12 новых [tests](../../../../scripts/test/test_decision_native_python_observations.py)
проверяют delegation, repeated inputs, pre-model refusal, model/eval exceptions,
восстановление inherited method, другие instances/threads, lock, exclusive file,
chain и явные границы измерения. Полный прогон: 1271 Python tests PASS,
4 optional skips; 12 Node tests PASS.

Native probe: 104 checks. Независимый stdlib auditor: 3411 checks / 2779 JSON keys;
seal `4f1f9acdb06698ed6a5f9444bed95560d4a245c3b387ae9e2d720b7978572213`. Он воспроизводит весь журнал, schedule, request
fingerprints, оригинальные responses, implementation SHA и нормализованный
профиль. Replay не импортирует runtime/observer и не вызывает модель.
Первый auditor остановился на ошибочно переписанном expected profile SHA;
его source и failure receipt сохранены. V2 связывает SHA с pinned profile file
и original resident capture. Оригинальное измерение не повторялось.
Первый report preflight ожидал TAP counters вместо фактического Node reporter
и отказал до записи файлов; формат counters уточнён по сохранённому PASS log.

Protected resident: 27 checks и восемь полей capture неизменны; все три owned
PID отсутствуют после выхода. Resident HTTP scoring=0; model calls в собственном
probe=3, в planning/audit/archive replay=0. Routing=false / qualification=not_assessed.
Human labels, owner criteria, representative traffic, accuracy, SLO и sealed
holdout gates остаются открыты.

[Aggregate summary](./native-python-call-observations-summary.json) и
[archive summary](./native-python-call-observations-archive-summary.json).
Private evidence сохраняет original plan, requests/responses, journal, captures,
исходники, тестовые логи, неудачный auditor и corrected stdlib replay.

Архив: 72 files / 38173693 bytes / 3203 selected Git objects;
ZIP SHA `e353a323c954d2c438cb3071d302fe891998d66d16a45d27dfe797ba3b6fe7aa`. CRC, every file SHA и размеры совпали.
Восстановленный stdlib replay фактической копии в исходном workspace PASS,
без повторения native calls. Все 20 installed source pins и три config pins
связаны с original before capture. Отдельная proof-квитанция сохранена рядом;
file SHA `daf7f2e9c521db4823c2b90427e0113be2f3da0bd44fb85e4ea6a3565a243ec2`.
