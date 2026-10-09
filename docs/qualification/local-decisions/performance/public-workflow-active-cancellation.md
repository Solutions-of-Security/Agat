# Actual coordinator cancellation during active native HTTP

09.10.2026. A fresh native run on committed sources joined the
[peer cancellation boundary](./public-peer-active-cancellation.md) to the actual
coordinator and Python worker. All **49 original development cases / 44 groups**
were scheduled once. The measured primary was a deterministic chat-completions
fixture; the decision endpoint used the frozen native MLX runtime 0.12.3.

[Summary](./public-workflow-active-cancellation-summary.json) and
[archive metadata](./public-workflow-active-cancellation-archive-summary.json)
pin this run. [The launcher](../../../../scripts/run-public-support-active-cancellation.py)
uses owned temporary loopback endpoints, offline model loading and read-only
installed Python/weights. Resident port 8766 is excluded. Python 3.13.12 arm64,
all 34 dependency pins, 2,048 input tokens, 4,096 MiB wired memory and 128 MiB cache
match the original context. Original questions, states and candidates remain
whole; only the workflow stage id and explicit optional contract defaults change
on the wire. Over-limit requests receive whole-context rejection.

Target index 25, all budgets and source pins were committed and recorded before
native scoring. After the first 25 cases, a quiescent native counter snapshot
arms the target before its instance is created. The driver then records the
actual pending assignment, its durable intent and an unauthorized cancel rejection
(HTTP 401). Only after preparation does the relay take a fresh snapshot with
exactly one active upstream handler, the original epoch/prefix counters and no
readable response bytes. An already completed response cannot pass this gate.

The driver sends authenticated `POST /api/v1/runs/{runId}/cancel` and receives
HTTP 204. Actual worker renewal receives HTTP 404 for the revoked lease. The
local caller returns `unavailable.cancelled`, closes its socket, and the relay
propagates EOF with upstream `shutdown(SHUT_RDWR)`. Native runtime retirement
records exit 75, reason `inference_cancelled`, and owned inference child exit -15.
All three original native PIDs are absent before the replacement starts.

| Result | Observed |
|---|---:|
| Original scheduled cases / primary fixture calls | 49 / 49 |
| Completed instances / bound durable caller returns | 48 / 48 |
| Cancelled instances / unknown durable returns | 1 / 1 |
| Delivered native ok / abstain / context rejection | 31 / 14 / 3 |
| Runtime epochs / separate native warmups | 2 / 4 |
| Total physical POST starts / known completions | 53 / 52 |
| Healthy original suffix / decision retries | 23 / 0 |

The cancelled assignment retains `returned=null` and `outcome=return_missing`.
Its local return is evidenced by the rejected HTTP request, rather than inserted
into durable accounting. Late observation, primary completion and fail requests
all receive HTTP 400. The closed HTTP journal binds every request to the 49
original assignments; one renewal body observed before full upload remains
explicitly incomplete. All 48 healthy instances preserve their primary route.

Active sampling preceded authenticated cancel by 13.000 ms. The cancel API took
2.698 ms; cancel-to-upstream-EOF was 595.000 ms and the local cancelled call lasted
641.313 ms. Native retirement followed EOF by 472.000 ms. The same endpoint and
profile recovered under a fresh process/epoch, completing two warmups 6,909.000 ms
after retirement. Every suffix instance was created after that barrier. The full
launcher took 44,456.527 ms. Computed caller p50/p95/max were
209.547 / 559.902 / 646.073 ms. These are diagnostics, without an accepted SLO.

[Offline replay](../../../../scripts/verify-public-support-workflow.py) checks
independently pinned context/plan/result bytes, historical source closure, the
full durable census, raw authenticated cancellation exchange, lease journal,
six physical snapshots and four warmups with zero model calls. Measured/verifier
commit `523b6dba9e128a2b5df34e2c28bb560959b5c26b` has 193 measured contributors;
the verifier has 114 contributors. Recomputed seals cannot admit a plan created
after the first native origin. All 54 focused tests passed, including a real
coordinator/worker against a labelled synthetic endpoint and prior protocol
compatibility. Standalone TypeScript checking passed with the script's `.ts`
import option enabled; the initial command configuration error is retained.

A separate standard-library audit imported no application/verifier modules. It
performed 1,952 evidence checks, separately checked 28,972 JSON keys for duplicate
names, and matched all 48 delivered signatures to the original healthy baseline.
All 13 recorded temporary PIDs were absent. Read-only resident captures retained
the four protected PIDs, 20 installed runtime hashes, bundle seal, profile,
configuration, registration, plists, ready monitoring and unchanged inference
counters. The complete ZIP and original workspace copy are verified by CRC,
per-file SHA/size and outer SHA; both remain under `/docs/private`.

The interrupted target has no typed native result; its final terminal counter
remains unknown. Active HTTP cancellation and process retirement do not establish
GPU kernel preemption. Human labels, calibration/holdout, appointed owners,
customer workload and SLO remain open; routing is disabled and qualification is
`not_assessed`. Next: exercise an actual worker caller deadline while the native
response remains active, followed by owned recovery and the full original suffix.

[Python socket shutdown](https://docs.python.org/3.13/library/socket.html#socket.socket.shutdown)
documents explicit connection shutdown. [Temporal cancellation](https://docs.temporal.io/develop/python/workflows/cancellation)
documents cancellation request delivery and cleanup as separate steps; this
SQLite coordinator measurement does not claim a Temporal activity gate.
