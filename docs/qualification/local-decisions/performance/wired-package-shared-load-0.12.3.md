# Совместная нагрузка wired package 0.12.3

05.10.2026 MSK. **Исправленный controller завершил два закреплённых
повтора, 480/480 measured calls вычислены; независимый audit прошёл.**
Это короткий gate нового профиля, до полного 7200-секундного shared soak.

[Подготовленный package](./resident-profile-selection.md) с
[native recovery gate](./resident-wired-package-0.12.3.md) запущен через
временный собственный LaunchAgent из Application Support. Новый venv,
manifest, policy и runtime root взяты из sealed bundle; wired budget
4096 МиБ, max input 2048, cache 128 МиБ и deadline **5000 мс** сохранены.
Profile SHA —
`776fba7fa1244e13292ac3605a53a6d6c6cf557901c8d863cab9489d6d2f47ce`.
Измеренный source commit — `f3fa66af56e471f18f1b6ae0213aee903e9b0615`.

Каждый повтор использует прежние 15 development inputs, два rounds и
шесть фаз: два раздельных before, sequential pair, overlapping pair и два
раздельных after. Stop-on-failure включён, model-load/warmup исключены из
measured rows; восемь warmup calls сохранены отдельно. Прежний resident
decider на время остановлен, старый Prometheus продолжал работать.
Candidate наблюдал отдельный временный настоящий scraper.

Qwen3 8B обслуживал собственный временный Ollama 0.35.1: прежний digest
`500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`,
context 8192, predict 128, temperature/seed 0, think=false, один loaded
model и один parallel slot. Все 14 phase-boundary residence records
сохранили этот digest/context. API `size`/`size_vram` составили
6 628 719 984 bytes, против 6 528 046 202 в историческом 0.12.2 block.
[Ollama API](https://docs.ollama.com/api/ps) и
[тип ответа 0.35.1](https://github.com/ollama/ollama/blob/v0.35.1/api/types.go)
хранят memory observations отдельно от digest/context. Причина различия
не установлена; равенство resource observations разных запусков не
предполагается. Первый analysis отказал на ошибочном сравнении с historical
size; его source/log сохранены, повтор проверил фактические records.

| Фаза | Decider wall p50 / p95 / max, мс | Primary wall p50 / p95 / max, мс |
|---|---:|---:|
| Decision only before | 279,669 / 317,300 / 374,462 | — |
| Primary only before | — | 296,928 / 1698,535 / 1836,761 |
| Sequential pair | 274,762 / 369,333 / 2263,269 | 280,599 / 1219,472 / 1266,776 |
| Overlapping pair | 371,087 / 1057,976 / 2792,243 | 466,297 / 1578,804 / 3406,839 |
| Primary only after | — | 284,128 / 1435,993 / 1593,794 |
| Decision only after | 280,671 / 370,781 / 2097,022 | — |

Quantiles пересчитаны по pooled measured rows двух повторов, по 60 calls
на присутствующий model/phase. 240 decision и 240 primary calls успешны;
60 overlapping pairs действительно перекрылись по HTTP intervals.
Все decision signatures и token counts совпали с baseline 0.12.2.
Это сходство результатов прежних inputs, без qualification их correctness.

93 observations подтвердили неизменные candidate PID/children и полный
profile, свежие metrics и system-memory raw samples. Candidate computed
counter вырос 0 → 244, включая четыре decider warmup; failed/rejected
counters остались нулевыми. Retirement events отсутствуют. Qwen выгружен,
шесть временных PID и job отсутствуют, старый resident восстановлен с
точным profile/fresh scrape и прежним Prometheus PID.

Audit перепроверил seals, committed sources, exact phase/case order,
364 journal records, timing/overlap, все token distributions/bounds,
raw counters, model/bundle bytes, baseline signatures и native cleanup.
[Public allowlist summary](./evidence/2026-10-05/wired-package-shared-load-0.12.3/result-summary.json)
содержит counts, pooled timings и fingerprints; raw evidence private.

Предыдущая попытка также вычислила 480 calls, но collector завершился
`failed`: вызову cleanup helper не передан diagnostic path. Исходный
protocol не изменён; отдельная read-only проверка подтвердила отсутствие
job/PID и готовность старого resident. Исправлен collector, добавлена
проверка binding до запуска; весь опыт повторён в новой directory.
Failed attempt не включён в таблицу и не объявлен успешным gate.

Serving code после полного parent `docs:check` не менялся; links/diff
проверены дополнительно. Следующий этап — полный закреплённый совместный
7200-секундный gate. Permanent rollout ждёт CI. Изменённый профиль,
системные условия и API memory observation не позволяют приписать скорости
или прежний timeout одному wired setter. Owner/SLO, boot/login, calibration
и предметная qualification открыты; routing выключен, `not_assessed`.
