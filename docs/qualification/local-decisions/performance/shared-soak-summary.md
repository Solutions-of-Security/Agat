# Воспроизводимая публичная сводка shared soak

05.10.2026 MSK. [Экспортер](../../../../scripts/summarize-decision-shared-soak.py)
повторяет независимую проверку сохранённого [shared soak](./shared-soak.md),
затем строит числовую сводку без запросов к моделям. Он не принимает готовую
метку `verified` из файла: повторно проверяет исторические source/input bytes,
seals, весь journal, chronology, overlap, distributions и фактическую duration.
Failed launcher, незавершённый duration, повреждённый journal или изменённые
analysis sources не создают public export.

## Что публикуется

[Allowlist](../../../../scripts/lib/decision_shared_soak_summary.py) включает
закреплённые fingerprints и commits, counts, duration, исходы `ok/abstain`,
input/output token distributions и wall-time p50/p95/max. Warmup вынесен
отдельно. Есть pooled показатели по модели, фазе и каждому блоку, включая
завершающий неполный `time_budget` блок, если полный verifier принял исходный
duration. Длительность или cap не уменьшаются при экспорте.

Pooled p95 считается по всем measured rows, а не как среднее p95 блоков.
Метод — nearest rank, `ceil(0.95 * count)` после сортировки; число строк и
максимум сохраняются рядом. [Prometheus](https://prometheus.io/docs/practices/histograms/)
объясняет, почему заранее рассчитанные квантильные значения нельзя
агрегировать в общий квантиль. Здесь доступны исходные timings, поэтому
пересчёт не требует оценки по histogram buckets. Источник проверен 05.10.2026.

Тексты, gold, outputs, case IDs и подписи отдельных решений, process inventory,
PID, host paths, raw resources/metrics и residence snapshots не входят в
публичный результат. Неизвестные новые поля verification также не копируются:
набор counts задан явно. Offline export не заменяет native проверку resident
weights, supervisor, ресурсов и cleanup; эти доказательства остаются отдельными.

Измерительный `implementationCommit` и `analysisCommit` различаются: первый
связывает старые inputs/harness, второй — committed код текущего анализа и
его 11 source SHA. Новый анализ не переписывает прежние private artifacts.
Output должен быть новым файлом под `docs`, input — сохранённым каталогом
под `docs/private`; symlink escape и overwrite отклоняются.

```bash
python scripts/summarize-decision-shared-soak.py \
  docs/private/shared-soak \
  --output docs/qualification/local-decisions/performance/evidence/shared-soak/result-summary.json
```

## Проверка на существующем реальном smoke

После commit инструмента заново проанализирован прежний 180-секундный опыт
на measured commit `df5ec4c958abe22801385732d1a577752042d2f7`.
[Новая сводка](./evidence/2026-10-05/shared-soak-summary-smoke/result-summary.json)
закрепляет analysis commit `93019735fe000931ccb956e727419a46937daca0`:
**180 844,854 measured milliseconds / 295 calls / восемь отдельных warmup**,
два блока и 30 overlapping pairs. Последний блок — проверенный `time_budget`.
Новых model calls при этом анализе нет; исходные файлы сверены побайтно при
копировании в новый private каталог.

| Модель | Measured calls | Pooled p50, мс | Pooled p95, мс | Max, мс |
|---|---:|---:|---:|---:|
| Decider | 150 | 372,240 | 1216,196 | 2479,146 |
| Primary | 145 | 534,524 | 2393,634 | 3320,137 |

Counts различаются из-за partial final phase, а не из-за скрытых failures.
Этот export относится только к исходному короткому smoke и не выполняет
7200-секундный gate. Qualification — `not_assessed`, routing выключен.

Шесть новых tests проверяют pooled p95 при различной задержке блоков и
более медленном warmup, allowlist, wrong proof/missing/resealed blocks,
committed analysis pin, повреждённый journal, failed launcher, dirty sources,
overwrite и symlink escape. Целевой shared набор — 53 tests, PASS.
Полный native `npm run docs:check` прошёл: 575 Python tests с четырьмя
прежними opt-in skips, 12 Node tests, 2265 local links и process catalog.
Отдельный private расчет без summary helpers пересчитал каждый pooled,
phase/block и warmup показатель; новый verification seal точно совпал с
сохранённым verifier результатом исходного smoke.
