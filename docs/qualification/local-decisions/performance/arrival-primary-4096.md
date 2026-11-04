# Fixed arrivals при одновременной работе primary

07.10.2026 MSK. **На 2048 tokens при 1 decision arrival/с active-фаза
дала 12/24 computed и 12 client-capacity drops; idle-фазы дали 48/48.**
Caller p95 active — 1116.329 мс. Два отдельных native repeats подтвердили
эту картину. Planning envelope из [предыдущего опыта](./arrival-rate-4096.md)
требует проверки под совместной нагрузкой; следующий bounded опыт —
0.5 decision arrival/с с тем же primary. Это данные для
[draft owner/SLO](../shadow/observability/resident-service-acceptance.md).

## Закреплённый метод

[Launcher](../../../../scripts/run-decision-arrival-rate.py) использует
committed source `b2ee21ef9ccb49c53cfdc573fc311f53af34125a`, 53 source
contributors и 34 runtime dependency pins. Temporary MLX runtime:
[профиль 0.12.3](./profiles/runtime-0.12.3-wired-4096.json), wired 4096 МиБ,
cache 128 МиБ, max input 2048, inference deadline 5000 мс, caller timeout
10000 мс. Permanent resident работает параллельно без новых inference.

Два authored synthetic inputs имеют ровно 256 и 2048 tokens вместе с
runtime wrapper. Tokenization локальная; runtime подтвердил длины,
отсутствие truncation и decode. На каждый input следуют фазы
`primary_idle_before`, `primary_active`, `primary_idle_after`: по 12 секунд,
1 decision arrival/с, один client slot. Fixed monotonic offsets не ждут
ответа. Занятый slot даёт явный drop; catch-up, retry и queue отсутствуют.
Каждый scheduled arrival входит в denominator. Это следует подходу
[open workload](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/)
с отдельным учётом [dropped arrivals](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/dropped-iterations/).

Owned Ollama 0.35.1 проверен по [release pin](./toolchains/ollama-0.35.1.json)
и SHA каждого файла; cached `qwen3:8b` — по manifest и пяти blobs.
Model digest `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`.
Один model, один parallel request, context 8192, `num_predict=128`,
`temperature=0`, seed 0, `stream=false`, `think=false`, timeout 30 с.
Qwen загружен во всех трёх conditions; только active-фаза планирует calls:
0.5 primary arrival/с, один client slot. Отдельный warmup предшествует
измерению. Параметры и server timings соответствуют
[Ollama chat API](https://docs.ollama.com/api/chat); явные ограничения
parallelism/model residence заданы согласно [Ollama FAQ](https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests).

## Два повтора

Quantiles пересчитаны по pooled raw rows, только для computed `ok/abstain`.
Drops не имеют выдуманных HTTP intervals.

| Input tokens | Condition | Computed / scheduled | Client drops | Caller p50 / p95 / max, мс |
|---:|---|---:|---:|---:|
| 256 | idle before | 24/24 | 0 | 144.306 / 163.031 / 166.050 |
| 256 | active | 24/24 | 0 | 201.951 / 214.997 / 220.150 |
| 256 | idle after | 24/24 | 0 | 138.045 / 151.417 / 153.883 |
| 2048 | idle before | 24/24 | 0 | 855.505 / 869.603 / 871.987 |
| 2048 | active | 12/24 | 12 | 1093.090 / 1116.329 / 1116.329 |
| 2048 | idle after | 24/24 | 0 | 852.483 / 873.854 / 878.409 |

Всего 144 scheduled / 132 computed, 12 client drops, без busy,
scheduler-lag drops и measurement errors. Все 132 computed укладываются
в diagnostic threshold 5000 мс; восемь decision warmups учтены отдельно.
Для длинного active input good/admitted = 100%, good/scheduled = 50%.
Максимальный dispatch lag — 9.946 мс.

Primary запланировал 24 calls, вернул 10; 14 пропущены из-за занятого
client slot. Каждый returned call декодировал 128 tokens. Wall p50 —
3361.154 мс, p95/max — 5314.672 мс; два primary warmups отдельно.
0.5 — scheduled rate, а не достигнутый throughput. Проверены 36 пар
реально перекрывающихся primary/decision HTTP intervals, отсутствие
primary calls в idle и завершение всех active calls перед следующей фазой.

## Перепроверка и ограничения

[Independent verifier](../../../../scripts/verify-decision-arrival-rate.py)
сверил fixed offsets, slots, phase seals, HTTP timing, actual overlap,
token counts, journal и counters после warmup/каждой фазы. Все 140 runtime
POST computed с учётом warmups. Outputs, distributions и tokens каждого
exact input совпали во всех фазах и обоих runtime processes.

Отдельный audit прошёл 9 checks; все 12 recorded temporary PID отсутствуют.
Четыре resident observations сохранили process births/bindings, 34 pins,
35 protected source files, fresh scrape и counters 1/0/0. Сохранены 18
phase-boundary health/memory observations. Память не измерена непрерывно;
peaks и причинный эффект настроек не установлены. Other system workload
не контролировался; порядок фаз фиксирован, permanent resident работал
на той же машине. Эти ограничения не позволяют переносить цифры на
customer population или объявлять причинный эффект primary.

21 targeted test и docs check прошли: 755 Python tests (4 optional skips),
12 Node checks. Ранее записанное v1 evidence также прошло новый verifier.
[Public allowlist](./evidence/2026-10-07/arrival-primary-4096/result-summary.json)
содержит counts, timings и provenance без raw text/process identities.
Private ZIP: 47 files, 40,998,823 bytes, SHA-256
`79bcb1c1d3e5fca0a0aeb937b83245ebbc0066d584f50f5ee7bc12b9d1c84ae4`.
CRC, каждый SHA/size и идентичная копия в исходном workspace проверены.
Архив содержит native evidence, audit, tests и измеренный Git state;
последующая публикация и CI в этот архив не включены.

Для повторения к CLI предыдущего опыта добавляются парные параметры
`--primary-binaries` и `--primary-models` с локальными проверенными файлами;
evidence-dir должен быть новым ignored каталогом под `docs/private`.
Actual boot/login, owners/SLO, независимая human review, calibration и
holdout остаются открытыми. Routing выключен; qualification `not_assessed`.
