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

## Native offline replay 08.10 MSK

Из `e9487b3` CLI дважды проверил сохранённый native report: **pass / exact**,
**107 measured / 101 current verifier sources**, **343 scheduled physical
handlers / 345 с warmup**. Inference/network/live PID calls отсутствовали.
Подтверждены весь inventory **49 cases / 44 groups**, 46 fully computed
cases из 42 groups и три wholly context-rejected cases; 343 variants не
выданы за независимые задачи. [Allowlisted summary](./public-option-diagnostic-replay-summary.json)
закрепляет raw SHA/seals и отдельную posthoc decomposition.

| Наблюдение среди 46 fully computed original cases | Cases |
| --- | ---: |
| Argmax semantic ID меняется | 8 |
| Status/reason/value меняются; accepted и abstained orders | 11 |
| Несколько разных ненулевых accepted values | 0 |
| Accepted с одним и тем же value во всех orders | 26 |
| Abstained во всех orders | 9 |
| Original accepted, другой order abstained | 5 |
| Original abstained, другой order accepted | 6 |

Эти группы пересекаются: восемь argmax changes и одиннадцать status changes
вместе охватывают прежние 18 semantic-outcome changes. 31 original accepted
case / 37 cases с хотя бы одним observed accepted order не являются
числом корректно размеченных случаев. Ноль conflicting accepted values
в этом run не устанавливает correctness или safety на другой population.

Independent audit — **201 checks**, saved source повторно дал идентичные
audit bytes. Шесть новых / 14 joint tests, full **891 Python / 4 optional
skips, 12 Node**, links/catalog прошли. Cleanup report остаётся historical,
live false; labels/calibration/holdout/owners/SLO не добавлены.

Private archive сохранён в original workspace: **16 entries / 558363 bytes**,
SHA-256 `c6c8733763c50694f2d84169c86366b0dc8c573aa807d9620419338129c65e5a`.
Обе копии проверены по CRC, размеру и SHA каждого файла; source inventory
сверен с двумя Git states, parent archives повторно прочитаны и проверены.
[Archive summary](./public-option-diagnostic-replay-archive-summary.json)
содержит только allowlisted metadata.
