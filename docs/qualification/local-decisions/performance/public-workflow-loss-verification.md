# Offline replay public workflow loss v2

[Verifier CLI](../../../../scripts/verify-public-support-workflow.py) теперь
различает launch/recipe/result v1 и v2 по sealed schema. Независимые raw
pins original context, plan и result обязательны; historical code только
читается. Все прежние v1 проверки и их physical denominator сохранены.

V2 replay сверяет весь original corpus, prospective loss spec в plan,
recipe и result, raw request/ack и barrier относительно actual instance
creation timestamps. Runtime PID должен принадлежать recorded owned set
и отличаться от driver/worker; actual exit совпадает с ack. Extra/missing
source contributors, artifacts, journal rows и durable returns отвергаются.

После barrier ожидается `unavailable/unreachable`, без result или
придуманных tokens. TCP guard receipt должен иметь столько же actual
accepts/resets, сколько bound unavailable returns, ноль errors и
`payloadsRead=false`. Типы счётчиков проверяются отдельно от значения.
Последний raw quiescent snapshot — **before_runtime_loss**, physical delta
включает только healthy prefix; два warmup остаются отдельно. Transport
failure не становится HTTP handler отсутствующей модели.

Три новых tests используют actual temporary Git repository и synthetic
responses: exact offline replay, rehashed ownership/barrier/reset/source/
physical-counter corruptions, raw artifact и mid-replay mutation. Старые
v1 regressions проходят в том же targeted наборе. Успешный report оставляет
`reportedCleanupComplete=true / liveCleanupVerified=false`; live PID,
network/model calls, appointed owners, gold labels и SLO не возникают.

## Native offline replay 08.10 MSK

Из `9d95673` новый CLI подтвердил **pass / exact** для сохранённого v2:
**49 instances / 49 bound caller returns**, пять healthy computed и
44 unavailable returns / 44 actual transport resets. Physical scheduled
handlers — **5**, с двумя warmup — **7**. Все **180** measured sources и
**99** current verifier contributors закреплены. Model/network/live PID
calls отсутствовали; Python 3.14.3 / Darwin читает прежний MLX identity и
34 package pins из evidence.

Тот же CLI повторно проверил сохранённый v1 native workflow: **49 scheduled
physical handlers / 51 с warmup**, 177 measured sources, exact accounting.
Введённый v2 не переинтерпретировал старые physical counters и outcomes.
[Allowlisted summary](./public-workflow-loss-verification-summary.json)
закрепляет independent raw pins обоих новых reports.

Три новых / семь joint targeted tests прошли; full docs — **871 Python / 4
optional skips, 12 Node**, links/catalog. Обе проверки сохраняют
`reportedCleanupComplete=true / liveCleanupVerified=false`, no labels /
unappointed owners / SLO unaccepted / routing false / not_assessed.

Private ZIP **11 entries / 540 129 bytes**, SHA
`7357af1a0715005d4a9898cc6af887e2fbbeeac8e4a8b28ec0526e5df790606b`
сохранён в исходном workspace (0600), CRC/size/SHA каждого entry проверены
после обеих записей. 99 verifier contributors сохранены в двух Git
snapshot; обе parent archive SHA повторно сверены.
[Archive receipt](./public-workflow-loss-verifier-archive-summary.json)
содержит allowlisted metadata, raw v1/v2 reports остаются private.

## CI counter drain correction

Linux CI PR 165 обнаружил гонку в socket regression: клиент уже получил
TCP reset, а guard thread ещё не увеличил `resetConnections`. Итоговая
квитанция теперь отвергается до `close()` и подтверждённого завершения
потока. Проверка active guard явно ожидает отказ; после drain сохраняется
реальная сверка одного accept/reset и независимого живого listener.
[Python Thread.join](https://docs.python.org/3/library/threading.html#threading.Thread.join)
закрепляет требуемое ожидание завершения, без sleep/retry угадывания.

Native producer уже закрывал guard перед публикацией квитанции, поэтому
прежний v2 evidence остаётся действительным. Исправление требует нового
source-bound offline replay и новой проверки CI, без повторного inference.
Исходный failed CI log и прежний архив сохраняются отдельно.

Из `7e1a6d1` оба native offline replay снова прошли: те же 180/177 measured
sources и 99 новых verifier source bindings, прежние 5/49 scheduled physical
handlers и 44/0 transport resets. [Correction summary](./public-workflow-loss-verification-ci-fixed-summary.json)
закрепляет raw SHA/seals новых reports. 14 joint tests и полный набор
871 Python / 4 optional skips, 12 Node, links/catalog прошли после исправления.
