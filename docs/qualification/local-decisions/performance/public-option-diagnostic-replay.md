# Offline replay and outcome decomposition

[CLI](../../../../scripts/verify-public-support-permutation-diagnostic.py)
требует independent raw pins original context, whole permutation recipe,
native plan и native result. Historical context/tokenizer/runtime sources
читаются через Git, не исполняются; все прежние full-input, serial timing,
warmup/phase, journal и quiescent physical accounting checks сохраняются.
Current verifier sources закрепляются отдельно, до/после выполнения.
Новых model/network/live PID calls нет.

После первого полного replay инструмент делает **posthoc descriptive**
разбор прежних verified rows; второй полный replay перед публикацией
проверяет artifact drift. Изначальные native summaries и prospective
budget не переписываются. Omitted cases/rows, изменённый result/журнал,
source drift и existing/nonprivate output не получают pass.

Для fully computed original cases отдельно считаются изменения argmax
semantic ID, status, reason и value. Конфликт **accepted values** означает
более одного ненулевого value среди `ok` responses. Переход `ok/abstain`
при неизменном argmax не считается вторым accepted value. Отдельно видны
original accepted → observed abstention, original abstention → observed
acceptance, always accepted same value и always abstained cases.
Флаги пересекаются, их counts нельзя суммировать как непересекающиеся группы.

Полностью context-rejected и mixed-admission cases остаются в original
denominator; флаги сравнения у них `null`. Case/group/variant единицы
показаны отдельно. Наблюдённое согласие не становится gold, accuracy,
calibrated confidence, causal position bias или новой decision policy.
Owners/SLO, applicable calibration/holdout и qualification остаются
открытыми; routing false. Recorded cleanup остаётся отдельным от нового
live наблюдения: `reportedCleanupComplete=true / liveCleanupVerified=false`.

Шесть tests проверяют invariance против synthetic position bias, confidence
threshold switch с одним accepted value, whole/mixed admission,
actual temporary Git/CLI replay с запретом model/network/native Popen,
artifact mutation между replay, source drift и output permissions.
