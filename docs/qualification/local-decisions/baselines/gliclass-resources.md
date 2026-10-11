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
/private/tmp/agat-gliclass-env/bin/python -B scripts/profile-gliclass-development.py measure \
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

Native resource measurement добавляется после проверки production CLI.
Одна последовательность development permutations не задаёт concurrency
capacity или принятый SLO. Этот профиль измеряется отдельно от прежнего run;
контролируемое сравнение latency не заявляется.
