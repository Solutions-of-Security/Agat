# Offline receipt verifier public workflow

[CLI](../../../../scripts/verify-public-support-workflow.py) принимает
independent raw SHA для original context, launch plan и launch result.
Он читает sealed artifacts и полный historical Git snapshot; historical
Python/TypeScript не исполняется. Никаких HTTP/model calls или live PID
queries. Новый private report содержит отдельный verifier commit/source
snapshot и runtime; original artifacts сохраняются.

Проверяются весь frozen development inventory, config/profile/runtime
identity и implementation SHA, 34 package pins, recipe before first
creation window, complete authenticated cohort scope, raw run-set hash,
полный ordered journal и claims driver. Единственный assignment/caller
intent/return каждого case, typed response, token admission и primary
route проходят [workflow contract](./public-support-workflow.md).
Сокращение denominator, потерянный return, transformed input или raw
context в DTO отвергаются. Весь measured source inventory сверяется
через `git archive`; missing/extra source contributors — ошибка.

Два warmup реконструируются отдельно. Три raw metrics snapshots должны
быть quiescent, иметь один server-start и нулевой origin; computed/error
outcomes совпадают с durable cohort и raw counters. Cached counters
самостоятельно не принимаются. SHA/seals повторно сверяются после replay,
чтобы изменение consumed file не создало успешный report.

`reportedCleanupComplete: true` означает согласованные claims исторического
результата. `liveCleanupVerified: false` остаётся false: PID мог быть
переиспользован, и offline replay не подтверждает текущее состояние хоста.
Success означает integrity/accounting pass, не classification accuracy,
назначение owner, SLO agreement или customer qualification.

Четыре новых regression tests используют actual temporary Git repository
и только synthetic responses. Они проверяют offline pass, rehashed
journal/ledger/physical/authority/source corruptions, mutation во время
consumption и private exclusive output. Повторно запускать inference для
проверки сохранённого [native workflow](./public-support-workflow.md) не
требуется.

## Native offline replay 08.10 MSK

Из `8ea3a86` CLI вернул **pass / exact** для прежнего native workflow:
**49 instances / 49 bound caller returns / 46 computed / 3 context rejection**.
Все **177** measured source files и **97** verifier contributors закреплены;
verifier — Python 3.14.3 / Darwin, native MLX runtime остаётся записанным
Python 3.13.12 / arm64 с 34 package pins. Inference не повторялся.

Raw counter parity: 49 scheduled handlers, два warmup отдельно, 51 total
handlers; profile, assignment/intent/result и original inputs сохранены.
`reportedCleanupComplete=true / liveCleanupVerified=false`: повторная
source-bound проверка не заменяет fresh PID census прежнего native audit.
[Allowlisted summary](./public-workflow-verification-summary.json) связывает
independent raw pins и новый sealed report. Full docs: **861 Python / 4
optional skips, 12 Node**, links/catalog pass; 10 joint targeted tests.
Owners/human review/calibration/holdout и actual boot/login остаются
открытыми; routing false / not_assessed, success не меняет customer SLO.

Private ZIP сохранён в исходном workspace: **9 entries / 524 381 bytes**,
SHA `910fc62624313d6b72bb0d9eacb34b5f1c68e299ef30d30c859e1fd5da8d1523`.
CRC, size и SHA каждого entry проверены после обоих записей. Два Git
snapshot сохраняют все 97 verifier contributors; parent workflow archive
повторно сверен по независимому raw SHA. [Archive receipt](./public-workflow-verifier-archive-summary.json)
публикует только allowlisted metadata.
