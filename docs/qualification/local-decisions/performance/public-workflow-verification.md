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
