# Ресурсы процесса GLiClass baseline

11.10.2026. Добавлен opt-in profiler для открытого memory-пункта
[GLiClass baseline](./gliclass.md). Исходный scorer, weights, full inputs,
thresholds и source-verified development scope сохраняются. Отдельный report
вкладывает полный native baseline, три raw resource snapshots и заново
вычисляемую сводку. Routing/accuracy qualification/human authority остаются
false/not_assessed.

## Измеряемая величина

[`resource.getrusage(RUSAGE_SELF)`](https://docs.python.org/3.13/library/resource.html)
покрывает все threads текущего Python процесса. CPU counters — секунды;
max RSS — high-water mark всего процесса, включая imports, tokenizer, model,
buffers и allocator. Работа дочерних Git/ps процессов сюда не входит.
Разность high-water marks не является точным объёмом allocation модели.

В текущем macOS raw `ru_maxrss` имеет единицы **bytes**. Это проверено отдельным
96 MiB allocation + touch: delta raw 100597760, delta `ps RSS × 1024`
100696064, multiplier=1. Archived
[Apple/BSD manual](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/getrusage.2.html)
описывает килобайты; перенос этой исторической единицы на текущую систему
даёт неверное значение. Profiler явно поддерживает Darwin multiplier=1 и
Linux=1024, остальные платформы отвергаются. Linux conversion проверяется
fixture; actual native run относится к текущему Mac.

Снимки: before-backend, after-backend, after-scoring. Каждый syscall окружён
двумя monotonic timestamps; elapsed интервал сохраняет lower/upper bounds.
Фиксируются PID/platform, maxRSS raw/bytes, user/system CPU, page faults и
context switches. Проверка отвергает другой PID/порядок/platform, обратный
счётчик, нечисловые значения и ошибочный conversion. Model-load время из
native baseline сохраняется отдельно от полного backend phase, включающего
проверку файлов/зависимостей.

## Инструменты

[Profiler CLI](../../../../scripts/profile-gliclass-development.py),
[resource library](../../../../scripts/lib/decision_gliclass_resources.py),
[тесты](../../../../scripts/test/test_decision_gliclass_resources.py).
`measure` требует уже скачанные pinned local weights и всю исходную
development inventory, затем запускает тот же baseline. `verify` использует
стандартную библиотеку, external report/dataset SHA, committed source
snapshot, заново проверяет nested logits/typed results и resource arithmetic,
не вызывает модель. Output — новый `/docs/private` каталог 0700/0600.

```bash
/private/tmp/agat-gliclass-20261011/bin/python -B scripts/profile-gliclass-development.py measure \
  --model-dir docs/private/gliclass-model \
  --dataset docs/qualification/local-decisions/development.v1.json \
  --dataset-file-sha256 ACTUAL_DATASET_FILE_SHA256 --output-dir docs/private/gliclass-profile
python3.13 -B scripts/profile-gliclass-development.py verify \
  --model-dir docs/private/gliclass-model \
  --dataset docs/qualification/local-decisions/development.v1.json \
  --dataset-file-sha256 ACTUAL_DATASET_FILE_SHA256 \
  --report docs/private/gliclass-profile/report.json \
  --report-file-sha256 ACTUAL_REPORT_FILE_SHA256 --output-dir docs/private/gliclass-profile-verified
```

## Выполненный native профиль

Изолированный свежий процесс CPU FP32 выполнил всю прежнюю inventory:
36 authored synthetic cases, 3 зависимых template groups, 72 original/reversed
forward без ошибок. Weights, tokenizer, render, thresholds и inputs совпадают
с предыдущим baseline. Все 72 typed distributions совпали точно; отдельный
stdlib audit повторно проверил всю inventory tokens по предыдущему
real-tokenizer replay, probabilities, synthetic-label metrics и raw resource
arithmetic — 1248 checks.

| Снимок процесса | max RSS bytes | max RSS MiB |
|---|---:|---:|
| До конструктора backend | 45858816 | 43.734375 |
| После конструктора backend | 479854592 | 457.625 |
| После всего scoring | 552878080 | 527.265625 |

| Фаза | Wall interval ms | User CPU s | System CPU s |
|---|---:|---:|---:|
| Полный backend load | 8750.992333–8751.005916 | 3.095406 | 1.266667 |
| Весь scoring | 1558.711334–1558.727250 | 1.935055 | 0.138723 |
| Load + scoring | 10309.712917–10309.723916 | 5.030461 | 1.405390 |

High-water increase за load + scoring — 507019264 bytes. Это прирост пикового
RSS всего процесса, а не точная allocation weights. CPU counters могут
превышать wall time из-за нескольких threads. Один процесс и зависимые
permutations не задают concurrency capacity или принятый SLO; контролируемое
сравнение latency с прежним запуском не заявляется.

Source feature `26ab79ee5814025025b74f3ea9e62d2fb63e722e`, fixture Git
`42402f4eb5197694791a9cbde8e53833572f6cc1`: 146 measured source files,
serving runtime не изменён. Report file SHA-256
`0752f3ba07ab23fe1dd8f228f1cbe0c814fd54a31a03be1637ab80ce626c8b15`;
sealed report `4c767febcce9d6c5cc706f3ef1f89f93f1618a50c91884b55e9e73b10a9ff31a`.
[Публичная сводка](./evidence/2026-10-11/gliclass-resource-summary.json)
не содержит private full inputs.

8 новых resource tests + 12 baseline tests — PASS; весь scripts suite:
1425 tests / 4 skips / 507.688 s. Full log SHA-256
`7ef7bf6c2791eec284056608847edbe8be78be147f45382e1c71aa9468338387`.
Production CLI: 1 positive stdlib replay / 10 refusals, model calls=0;
wrong split, RSS unit, PID, backward CPU, resealed fake summary/authority и
absent/wrong external byte pin не публикуют output. Все 11 owned PID
завершились; четыре native/replay/capture PID тоже отсутствуют. Четыре
protected resident PID, 20 source files и все 8 protected полей сохранены.

## Закрытый архив и original-copy replay

Private archive `docs/private/gliclass-resources-20261011.zip` сохранён
в исходном workspace: SHA-256
`637aba2d2f309e59fe02b3192e03115998bb4bcafcc9c1bb0ccb3294a1b5a6a9`,
123517065 bytes, 391 ZIP entries / 390 restored logical files. Включены
weights, own Git source snapshot, raw reports/snapshots, unit probe,
independent audit, negative CLI evidence и завершённый full-suite log.
[Сводка архива](./evidence/2026-10-11/gliclass-resource-archive-summary.json).

Оба ZIP проверены по CRC, каждому SHA/size и закрытой inventory. Из
фактически восстановленной original-copy запущен скопированный production
profiler verifier со standard Python 3.13: report byte-identical, model0,
owned verifier PID 12569 завершён. Proof —
`docs/private/gliclass-resources-original-copy-proof.json`. Human references
не получены; authority/routing остаются false/not_assessed.
