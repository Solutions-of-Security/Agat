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
