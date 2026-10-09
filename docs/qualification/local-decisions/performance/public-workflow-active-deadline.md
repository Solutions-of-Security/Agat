# Published worker deadline during active native HTTP

09.10.2026. A fresh v7 native run exercised all **49 original development cases /
44 groups** through the actual coordinator and Python worker. The selected
shadow call timed out while its upstream native handler was active, the timeout
was durably recorded and every primary route completed. A replacement on the
same endpoint/profile completed the full original suffix.

[Summary](./public-workflow-active-deadline-summary.json) and
[archive metadata](./public-workflow-active-deadline-archive-summary.json) bind the
fresh raw receipts. [The launcher](../../../../scripts/run-public-support-active-deadline.py)
uses owned temporary loopback endpoints and offline MLX loading from read-only
installed Python/weights. Runtime 0.12.3, Python 3.13.12 arm64, all 34 dependency
pins, 2,048 input tokens, 4,096 MiB wired memory and 128 MiB cache match the original
context. Resident port 8766 is excluded. Primary is a deterministic fixture and
the coordinator store is SQLite.

The prospective plan fixes target index 25 and a 250 ms caller budget before
native scoring. A single published process version routes a unique literal from
the original state to the same primary agent with that short shadow budget;
the other 48 cases retain 10,000 ms. The graph keeps the input whole through
`{{ input }}` and retains the original question/options. Only stage id and
explicit optional contract defaults differ in HTTP serialization. Whole
over-limit inputs are rejected without truncation. Selector literal and raw
inputs remain private; the public summary contains its canonical JSON SHA.

After the first 25 cases, quiescent native counters arm the target before its
instance is created. The relay checks exactly one active upstream handler,
original epoch/prefix counters and no readable response bytes. It selects both
sockets continuously; a completed native response fails the gate. The actual
worker's whole-call monotonic watchdog expires, closes the caller socket and
returns `unavailable.timeout`. The relay observes EOF and applies upstream
`shutdown(SHUT_RDWR)`. It injects no cancellation Event or coordinator run cancel.

| Result | Observed |
|---|---:|
| Original cases / completed instances / bound durable returns | 49 / 49 / 49 |
| Delivered native ok / abstain / context rejection | 31 / 14 / 3 |
| Accepted unavailable.timeout / unknown durable returns | 1 / 0 |
| Primary fixture calls / completed primary routes | 49 / 49 |
| Native epochs / separate warmups | 2 / 4 |
| Physical POST starts / known completions | 53 / 52 |
| Healthy original suffix / decision retries | 23 / 0 |

Timeout observation and primary completion both receive HTTP 200. The closed
lease journal contains 49 original intents, 49 accepted returns and 49 accepted
completions; renewals remain HTTP 204. The target assignment is `recorded`, its
caller ledger is `returned`, and its original run completes. This differs from
the [operator cancellation gate](./public-workflow-active-cancellation.md), where
revocation rejects late writes and the durable return remains unknown.

The target local call lasted 250.732 ms and the relay interval was 250.216 ms.
Fresh active sampling preceded upstream EOF by 237.000 ms. Native retirement
followed EOF by 325.000 ms, recording exit 75, reason `inference_cancelled` and
owned inference child exit -15. All three original native PIDs were absent before
replacement. Same-port/profile recovery under a fresh epoch completed two
warmups 7,106.000 ms after retirement; every suffix instance was created later.
The full launcher took 44,271.451 ms. Computed caller p50/p95/max were
214.435 / 557.929 / 644.817 ms. These are diagnostic budgets and observations.

[Offline replay](../../../../scripts/verify-public-support-workflow.py) passed
with zero model calls, binding original context, full graph, all raw requests,
durable census and six physical snapshots. Commit
`3c0a73a9387e21bb102950c8ece380ec33dd1f13` contains 195 measured contributors;
the verifier has 117 contributors. A rehashed plan created after the first native
origin is rejected. All 62 focused tests passed, including actual model-free
coordinator/worker deadline delivery and older protocol compatibility. Standalone
TypeScript checking passed. The updated verifier also replayed the fresh v6
native receipts from the previous checkpoint without additional inference.

A separate standard-library audit imported no application/verifier modules. It
performed 2,243 evidence checks and separately checked 28,723 JSON keys for
duplicates. All 48 delivered signatures matched the original healthy baseline;
all 49 assignments, published budgets, HTTP attempts and physical counter deltas
matched. All 10 recorded temporary PIDs were absent. Read-only resident captures
retained four protected PIDs, 20 installed runtime hashes, package/config/profile,
registration/plists, ready monitoring and inference counters. Complete ZIP and
original workspace copy retain raw receipts and source snapshots under
`/docs/private`, verified by CRC, SHA and size.

The interrupted native target has no typed result and its terminal counter
remains unknown. Process retirement does not establish GPU kernel preemption.
Actual resident boot/login is already verified for the pinned package; owners,
customer workloads/SLO, independent human labels, calibration and holdout remain
open. Routing is disabled; qualification is `not_assessed`. The next engineering
measurement joins the whole inventory to a real primary with a matched control.

[Python time.monotonic](https://docs.python.org/3.13/library/time.html#time.monotonic)
provides a clock unaffected by system-clock updates. The
[HTTP retry contract](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.2)
supports an explicit no-retry policy for decision POSTs with unknown completion.
These references explain transport choices and do not establish customer SLO.
