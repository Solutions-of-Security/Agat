# Длительный совместный прогон с checkpoint каждого блока

05.10.2026 MSK. Продолжение [двух resident shared-load опытов](./resident-shared-load-0.12.2.md).
Инструмент повторяет прежние шесть фаз на одном decider и одном primary,
сохраняет каждую завершённую пару и первый отказ. Serving source, веса,
policy, calibration и профиль 0.12.2 не меняются. Прикладная qualification
и production SLO остаются `not_assessed`, routing выключен.

## План и длительность

[CLI](../../../../scripts/benchmark-decision-shared-soak.py) использует
[контроллер](../../../../scripts/lib/decision_shared_soak.py) и тот же
[paired probe](../../../../scripts/lib/decision_shared_load.py): два rounds
на фазу, две отдельные warmup pairs на блок, один активный запрос на модель
и никаких retries. Controller поддерживает до 30 development cases; для
используемого закреплённого набора из 15 cases полный блок содержит 240
measured calls (для 30 cases — 480).

По умолчанию цель — **7200 секунд измеряемых окон**, максимум 128 блоков.
Measured time каждого блока — его elapsed после последнего warmup; включает
phase/final control API checks. Начальные health/tag checks нового блока
и serialization создают отдельные wall-clock gaps. Это closed-loop repeated
phase load, без обещания постоянной интенсивности поступления или GPU usage.

Бюджет блока — оставшаяся цель, округлённая вверх, от 30 до 600 секунд.
Заключительный блок может быть неполным: boundary проверяется между парами,
активные вызовы завершаются. Его `time_budget` допустим только при реально
истёкшем block budget и отсутствии ошибок, смены модели/профиля/decider
signatures. Warmup никогда не добавляется к measured duration. Request/block
cap даёт `limited`, cancellation или отказ — `degraded`; существующий план
после просмотра результата не уменьшается. Короткий smoke не заменяет 7200.

Source/input bytes должны совпасть с точным Git commit до модели. Dataset и
profile — committed public files под `docs`, без private/calibration/holdout.
Expected profile проверяется перед primary factory и inference каждого блока.
Исторические веса и package проверяются отдельно resident manager/native gate.
Advertised HTTP profile сам по себе не является hardware/model attestation.

## Журнал и отказ

Новый output directory обязателен под `docs/private`, mode 0700; файлы имеют
mode 0600. Plan фиксирует commit, source SHA, dataset/profile, primary digest,
целевую длительность и cap до нагрузки. `pairs.jsonl` немедленно записывает и
flush-ит warmup и completed pairs, без request text, gold или generations.
Каждый sealed block сохраняется до следующего. Launcher связывает result,
plan и journal hashes; изменение source bytes исключает его success.

Первый ошибочный sequential primary не запускает новый decider. При overlapping
отказе оба уже активных вызова завершаются; следующая пара не стартует. Busy,
wrong profile, изменившаяся decider signature и отказ observer сохраняют
частичный блок. Ошибка checkpoint сохраняет прежние checkpoints и journal;
дальнейшая нагрузка прекращается. Disk/power failure не гарантирует запись
последней строки или финального отчёта.

SIGINT/SIGTERM устанавливают флаг. После уже активных calls сохраняется partial
result; старые handlers восстанавливаются. GPU work не объявляется отменённой.
Инструмент получает только URLs и **не останавливает/перезапускает службы**.
Caller отвечает за свои temporary model processes и за независимое supervisor
наблюдение. Native resident wrapper отдельно фиксирует PID, profile, metrics,
retirement events и cleanup своих временных processes.

## Воспроизведение и независимая проверка

Обе loopback model services должны быть готовы с закреплёнными weights/profile.
Primary используется как structured label generator, не полный production agent.
Следующие команды не регистрируют и не удаляют LaunchAgents:

```sh
python3 scripts/benchmark-decision-shared-soak.py \
  --decision-url http://127.0.0.1:8766 \
  --primary-url http://127.0.0.1:11535 --primary-model qwen3:8b \
  --expected-primary-digest 500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41 \
  --dataset docs/qualification/local-decisions/development/evidence/2026-09-26/dataset.json \
  --profile docs/qualification/local-decisions/performance/profiles/runtime-0.12.2.json \
  --duration-s 7200 --max-blocks 128 \
  --output-dir docs/private/new-shared-soak

python3 scripts/verify-decision-shared-soak.py docs/private/new-shared-soak \
  --output docs/private/new-shared-soak/verification.json
```

[Offline verifier](../../../../scripts/verify-decision-shared-soak.py) не вызывает
модели. Он читает historical Git source/input bytes, проверяет seals/bindings,
профиль и digest, реальные prefixes фаз, все input hashes, порядок и интервалы
строк, token bounds, independently computed distributions, true HTTP overlap,
repeat signatures, model residence, точное равенство journal и reports и
requested measured duration без warmup. Failure/busy не становится ожидаемой
time boundary после пересчёта outer SHA. Proof проверяет integrity/consistency,
а не истинность меток; hashes не являются цифровыми подписями.

## Проверки реализации

42 целевых regression tests прошли: сохранение и изоляция callback data,
последовательный/overlapping отказ, profile pin до inference, cancellation,
observer/checkpoint failures, cap/duration, изменение primary между блоками,
private output/source guards и восстановление signal handlers. Mutation tests
пересчитывают связанные seals и проверяют отказ при неверных counts, p95,
input, HTTP overlap, chronology, отсутствующем journal, неистёкшем budget и
неполном source binding. Полный `npm run docs:check` на Python 3.13.12 / Node
24 прошёл: 564 Python tests с четырьмя прежними opt-in skips, 12 Node tests,
проверка локальных ссылок и process catalog.

## Настоящий smoke инструмента

На implementation commit `df5ec4c958abe22801385732d1a577752042d2f7` заранее
закреплённый план **180 секунд / максимум 8 блоков** завершил два блока и
**180 844,854 мс measured time**, отдельно от warmup; library wall time —
193 975,142 мс. [Публичная сводка](./evidence/2026-10-04/shared-soak-tooling/result-summary.json)
связывает private protocol, оба независимых verification artifacts, source,
profile, package и block hashes. Raw evidence и исполнявшиеся native
driver/verifier сохранены в `docs/private`.

Первый блок полон (`observed`, 144 467,113 мс). Второй сохранил частичный
`degraded/time_budget` блок (36 377,740 мс): его заранее объявленный budget
реально истёк, ошибок и смены decider не было. Offline verifier проверил
этот boundary и общую длительность; partial block не переименован в полный.

Получены **295 measured calls**: 150 decider и 145 primary, ещё восемь
warmup calls, 30 overlapping HTTP pairs и 239 немедленных journal events.
Mixed-phase decider p50/p95/max — 372,240 / 1216,196 / 2479,146 мс; primary —
534,524 / 2393,634 / 3320,137 мс. Эти pooled значения относятся к разным фазам
одного smoke и не являются причинной оценкой накладных расходов shadow.

86 supervisor/metric samples подтвердили стабильные runtime parent,
inference child и Prometheus PID, точный профиль и 47 конечных series с
правильными job/instance. Computed counter вырос **245 → 399**, ровно на
154 decider calls вместе с warmup. Retirement events отсутствуют. Независимая
повторная offline verification совпала с сохранённой; native verifier также
перепроверил source, package, журнал, raw metrics и cleanup. Три наблюдавшихся
временных PID завершены, Qwen выгружен; два постоянных resident jobs работают.

В этом tooling/smoke этапе полный совместный 7200-секундный gate не запускался;
по тогдашнему указанию пользователя работа была остановлена после его
завершения. После нового указания продолжить [первая полная попытка](./shared-soak-warmup-failure.md)
05.10 остановилась на warmup timeout до measured нагрузки; failed evidence
сохранён, диагностика первичного отказа исправлена. Qualification и SLO
остаются открытыми, прежний внеплановый exit 75 не объяснён.

[Grafana k6 soak testing](https://grafana.com/docs/k6/latest/testing-guides/test-types/soak-testing/)
рекомендует длительную нагрузку после smoke/ordinary tests и наблюдение ресурсов.
Здесь используется прежний локальный probe с увеличением общей длительности;
это инженерный development gate. Ресурсы, rollout owners, real business data,
independent human reviews и SLO требуют отдельных доказательств. Источник
методики проверен 05.10.2026 MSK.
