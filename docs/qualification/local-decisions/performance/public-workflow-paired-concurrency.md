# Native busy admission через две actual workflow

10.10.2026 MSK: свежий прогон исходных 49 development cases / 44 groups через
временный native runtime 0.12.3, actual coordinator и один Python worker с
concurrency 2 завершил все 49 instances, durable caller returns и primary-ветки.
Native вернул 24 HTTP `503/busy`, выполнил 24 вычисления (15 `ok`, 9 `abstain`)
и отклонил один полный вход как `context_too_long`. Ещё два длинных входа
получили `busy` до проверки контекста; все три исходных длинных входа учтены.

Gate — `integration_pass`; qualification `not_assessed`, routing false,
reference labels 0, owners/SLO не утверждены. Primary здесь — локальный fixture
chat-completions. Это дополнительная проверка admission перед следующим
плановым сравнением полного inventory с real primary и matched control.

## Проспективный протокол v8

Measurement source закреплён до запуска:
`b41a3b1fd120b912c0f5cfcde1686063480bd89f`, 195 contributors.
[Launcher](../../../../scripts/run-public-support-workflow.py) принимает
`--paired-concurrency`; [driver](../../../../scripts/run-public-support-workflow.mts)
публикует одну process version 1. Все входы сохранены в исходном порядке:
25 batches по два workflow, последняя содержит один случай. Следующая пара
создаётся после завершения обоих текущих instances. Native arrival order
внутри пары сохраняется отдельно и может отличаться от inventory order.

Временный coordinator использует `schedulerMode=sequential`,
`globalMaxConcurrency=2`; worker имеет два слота. Batch deadline — 20 секунд,
максимальный creation skew — 1000 мс, workflow deadline — 240 секунд.
Полный launcher занял 24 271.551 мс. Caller budget 10 000 мс, inference budget
5000 мс, upstream timeout 8000 мс, 2048 original tokens, wired limit 4096 MiB,
cache 128 MiB, frozen model/manifest/tokenizer/policy и 34 зависимости сохранены.
Перед inventory выполнены два actual warmups. Retry, restart, усечение,
padding, перестановка случаев или изменение question/options отсутствуют.

[Offline verifier](../../../../scripts/lib/decision_public_workflow_verification.py)
требует `context.createdAt <= plan.createdAt < native origin <= relay start`.
Отдельная регрессия отвергает rehashed plan, созданный после native origin;
последующий recipe также обязан следовать исходному плану.

## Отказ admission и сохранение primary

[Native server](../../../../decision_runtime/server.py) пытается занять inference
lock без ожидания. Занятый слот возвращает `503/busy` до чтения/парсинга input
и tokenization. Ответ содержит frozen profile, null request ID/fingerprint,
пустое распределение; request-bound result, duration и token counts отсутствуют.
Worker сохраняет `unavailable/busy` без typed result. Поэтому два длинных
входа получили busy, а один отдельный context rejection; в denominator
сохраняются все 49 original POST.

[Relay](../../../../scripts/lib/decision_public_workflow_paired.py) сохраняет
actual request/response bytes и сразу передаёт ответ. Барьер перед native
POST и удержание вычисленного ответа не используются. Для каждого busy
проверяются raw native 503 и принятый HTTP 200 durable return. После получения
503 worker может закрыть соединение до записи тела: это отдельно отражено
в `downstreamWriteCompleted`.

HTTP 503 обозначает временную перегрузку по
[RFC 9110 §15.6.4](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.6.4).
Сам статус не разрешает автоматический повтор POST;
[RFC 9110 §9.2.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.2)
требует основания для безопасного retry. Здесь retry count заранее равен 0.

Existing worker вызывает primary перед observational shadow. Raw
`primary-http.json` связывает каждый original full input и fixture output
с последующим intent и completion. Сохранены 49 actual intents, 49 accepted
returns и 49 accepted primary completions; lease journal содержит 199 records.
Шесть renewal bodies имеют explicit `requestBodyComplete=false`; тела всех
обязательных intent/return/complete прочитаны полностью. Renewals — 204,
revocation/fail отсутствуют. Все 49 прежних primary-веток сохранены.

## Физический учёт и независимые проверки

Один native epoch, три idle snapshots: 0 до scoring, 2 после warmup,
51 после inventory. Delta всех десяти outcome counters точно равна
24 computed + 24 busy + 1 context rejected = 49. Все terminal outcomes известны.
Runtime штатно остановлен с exit 130; driver и worker — 0.

Отдельный metadata GET зафиксировал `backend_ready=1`,
`requests_in_progress=1` и completed busy >= 1 в original epoch. Raw SHA,
actual native PID и timestamp связаны с исходной парой indices 0/1: busy
уже получен, admitted native запрос ещё активен. Metadata GET не вызывает
модель. Краткий handler gauge 2 не принимается за свидетельство; параллельные
GPU kernels не заявляются.

Все 25 delivered typed signatures совпали с independently pinned healthy
native control после исключения request ID и duration. Вероятности независимо
пересчитаны из logits и frozen temperature. Accuracy/calibration не измерены.

| Caller interval | Count | p50 ms | p95 ms | max ms |
|---|---:|---:|---:|---:|
| Computed | 24 | 203.091 | 634.492 | 692.782 |
| Busy | 24 | 2.328 | 3.015 | 3.370 |

Busy меняет состав admitted группы: эти числа не устанавливают customer SLO
и не сравнивают latency всех 49 случаев с serial control.

[Целевые тесты](../../../../scripts/test/test_decision_public_workflow_paired.py)
и регрессии прежних протоколов — 63 PASS; strict TypeScript driver check — PASS.
Fresh raw-pinned v8 offline replay и повторные v6/v7 native control replay —
PASS, model calls 0. Независимый stdlib audit без application/verifier imports
прошёл 3177 evidence checks и отдельно проверил 45 347 JSON keys на дубликаты.
Он сверил исходный healthy ZIP по outer SHA, CRC, каждому file SHA/size,
все native/lease/primary receipts, источник measurement и источники verifier.
Первая ошибка имени test module и первый CLI argument error сохранены;
исправленные команды прошли, native attempt был один.

Все 34 записанных temporary PIDs отсутствуют. Protected resident на 8766
сохранил четыре PID, 20 installed runtime files, deployment/config/plist seals,
ready profile и inference counters; monitor 9095 готов. Temporary credentials
удалены. Raw requests/responses и первичные тексты находятся только в
`/docs/private`; публичны metadata и SHA pins из
[summary](./public-workflow-paired-concurrency-summary.json).
Raw plan SHA: `41966068f63fd56f5af748d61f15d18c43fb0b0093c09ec9ace608dbfb243985`;
raw result SHA: `781c17688e08926679eaeb33a45e8d7c5eb74a30cdfef72aaebeb716a39798eb`.
Проверенные ZIP/source snapshots и original workspace copy описаны в
[archive metadata](./public-workflow-paired-concurrency-archive-summary.json).

Следующий плановый gate — полный inventory с real primary и matched control.
Cancellation при concurrency 2 также остаётся отдельным runtime gate.
Разметка, calibration, независимый holdout, customer traffic/SLO и назначения
owners требуют соответствующего внешнего evidence. Actual boot/login
защищённого package уже закрыт отдельным протоколом.
