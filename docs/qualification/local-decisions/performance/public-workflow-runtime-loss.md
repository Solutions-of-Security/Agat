# Whole public workflow при потере owned runtime

[Launcher](../../../../scripts/run-public-support-workflow.py) принимает
`--stop-runtime-before-index N`. Новый v2 plan до запуска закрепляет полный
original development inventory и границу после завершения предыдущего
instance, до создания следующего. Default v1 не допускает unavailable
вместо ожидаемого результата; прежние receipts не переинтерпретируются.

Исходный план требует проверять отказ и сохранение штатной обработки.
[Google SRE: Testing for Reliability](https://sre.google/sre-book/testing-reliability/)
различает component, integration и system checks; [Addressing Cascading
Failures](https://sre.google/sre-book/addressing-cascading-failures/)
рекомендует испытывать реальные failure modes зависимостей и не создавать
повторными вызовами дополнительную нагрузку. Наш выбор — controlled loss
между заданиями через реальный worker/coordinator, fixture primary и
заранее выбранный N. Он проверяет continuation при недоступном endpoint;
crash во время inference, recovery и production SLO требуют отдельных
измерений.

Driver публикует request только после полного healthy prefix. Launcher
снимает quiescent counters, останавливает свой живой Popen process session,
подтверждает exit и отсутствие собственных потомков, затем удерживает
original loopback port TCP guard: `accept()` и `SO_LINGER(1,0)` сбрасывают
соединение, payload не читается. Guard считает каждый reset;
заменить endpoint другим listener до конца inventory нельзя. Ack появляется
через exclusive link готового private файла, поэтому reader не видит
частичный JSON. Только после ack driver создаёт следующий instance.

Полный authenticated cohort, ordered journal и caller/assignment ledger
проверяются для обеих частей. Каждый post-loss case должен сохранить
actual negotiated intent, durable `unavailable/unreachable` return,
caller timing, original input/profile и завершённый primary route. Result,
logits, tokens и server handler отсутствующему runtime не приписываются.
Последний metrics snapshot относится к healthy prefix перед остановкой;
transport failures и TCP resets учитываются отдельно от physical HTTP handlers.

Первый native socket regression выявил, что bound port без listener на
macOS даёт `EAGAIN` по timeout, а не `ECONNREFUSED`. Поэтому такой blocker
заменён reset guard до model experiment; это явная transport injection,
не скрытый дополнительный model server. Семантика `SO_LINGER` закреплена
в [Apple setsockopt manual](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/setsockopt.2.html).

Порты/PID выбирает launcher, request не может выбрать внешний процесс.
Permanent resident не участвует в остановке. Ownership, accuracy,
calibration/holdout, SLO и actual boot/login этим экспериментом не
подтверждаются; routing false / not_assessed.

## Native experiment 08.10 MSK

Из `c5b9268`, N=5 до запуска: **49 original inputs / 49 completed instances /
49 durable caller returns**, все 49 fixture primary calls и primary routes
сохранены. Healthy prefix: **5 computed (1 ok / 4 abstain)**. После exit
owned decider: **44 unavailable/unreachable**, result/token metadata
отсутствует, TCP guard измерил ровно **44 accepts / 44 resets / 0 errors**,
payloads не читал. Три long-context inputs после границы не были admitted
и не выдаются за physical context rejections.

Caller local HTTP p50/p95/max: healthy prefix **360.614 / 587.021 / 587.021
ms**, post-loss **0.559 / 0.644 / 1.533 ms**. Это serial integration с fixture
primary; queue/workflow latency, real primary model overhead и customer
SLO здесь не измеряются. Последний quiescent raw snapshot до остановки:
**7 physical model handlers**, включая два warmup и пять scheduled calls;
44 transport failures посчитаны отдельно. Native elapsed **22 935.487 ms**,
actual decider exit **130** (controlled SIGTERM, не crash во время inference).

[Allowlisted summary](./public-workflow-runtime-loss-summary.json) закрепляет
raw pins и independent audit. **180 sources**, все **30 observed owned PIDs**
отсутствуют после cleanup; ephemeral worker credentials удалены. Четыре
permanent resident PID, 35 protected sources и counters **1/0/0** сохранены;
raw counters совпали с retained previous native audit baseline и свежим
post-run scrape. Actual boot/login остаётся `awaiting_event`.

Семь новых regressions и полный docs check: **868 Python / 4 optional skips,
12 Node**, links/catalog pass. Initial failed socket expectation сохранена
в private logs; corrected real-client test прошёл до model experiment.
Accuracy, owners, calibration/holdout и SLO не выводятся из этого результата.
Следующий инженерный этап — reusable offline verifier v2 для новых
loss receipts, с отдельным учётом server handlers и transport resets.
