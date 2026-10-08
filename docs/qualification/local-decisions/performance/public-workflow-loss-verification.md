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
