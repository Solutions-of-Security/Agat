# Actual coordinator cancellation across the public development workflow

09.10.2026 MSK. The v5 protocol runs all 49 frozen development cases through
the published process, actual coordinator and Python worker, and a temporary
MLX runtime. For one prospectively selected case, an owned proxy obtains the
complete native response but delivers no headers or bytes. An authenticated
coordinator cancellation then revokes the run. The worker observes the revoked
lease, closes its decision socket and continues with the remaining inputs.

The primary chat-completions endpoint is a fixture. These results measure an
integration boundary, not classification accuracy, calibration, customer traffic
or an accepted SLO. Upstream inference has already finished when cancellation
begins; this experiment does not establish interruption of active GPU work.

## Prospective protocol and implementation

[The launcher](../../../../scripts/run-public-support-workflow.py) accepts
`--cancel-run-at-index` exclusively with the older loss and caller timeout flags.
The target must be context eligible and have both a prefix and suffix. Before
scoring, the sealed v5 launch plan binds the full input inventory, source closure,
raw context/profile/manifest pins, native dependencies and cancellation boundary.
Historical v1–v4 protocols retain their original accounting.

[The cancellation proxy](../../../../scripts/lib/decision_public_workflow_cancellation.py)
reuses the finite v4 transport accounting. Each original POST is forwarded once
to the owned runtime. Both ports are literal loopback addresses and exclude
resident port 8766. The completed target response is held without downstream
headers or bytes. A sealed ready receipt binds its raw request and response
hashes, case, stage, profile and upstream completion time.

[The driver](../../../../scripts/run-public-support-workflow.mts) first obtains
an authenticated full trace proving a running target with a negotiated caller
intent and no observation. Cancel without credentials must return 401. The
actual authenticated POST to that run must return 204. Its private receipt binds
the ready and trace file hashes, empty HTTP bodies, identities and measured
timestamps. The proxy records actual EOF and publishes a sealed drain receipt;
the next instance is created only after that receipt.

The cancelled stage is not counted as a completed primary branch. Its primary
fixture was called once, but late observation and completion POSTs receive 400
after ownership revocation. The coordinator keeps `returned: null`,
`return_missing` and `ended_without_observation`, without fabricating a durable
cancelled observation. The separate HTTP journal records the actual local
`unavailable / cancelled` return and its monotonic caller timing.

The journal explicitly records whether each request body was completely read.
Renewal can finish its response before the incoming body ends: four such records
retain `requestBodyComplete: false`. Their hashes identify observed bytes only.
Intent, return and completion bodies must be fully observed. Authentication
headers and temporary credential contents are excluded from retained evidence.

Caller timeout remains 10,000 ms, upstream timeout 8,000 ms, cancel API deadline
5,000 ms and proxy drain bound 15,000 ms. The prospective diagnostic admission
requires EOF within 2,500 ms of the actual cancel request start. This start
boundary permits the worker to close before the driver finishes reading 204.
There are no decision POST retries, runtime restarts, replacement inputs or
truncation. None of these diagnostic bounds is an agreed service SLO.

## Native result

[The allowlisted summary](./public-workflow-cancellation-summary.json) binds
source `bb8aabb286fe3321c33b45b5b6f0c6f03a544e77` and 188 measured contributors.
Only the original 49 development cases / 44 groups were used. Runtime 0.12.3,
model, tokenizer, policy, 2,048-token limit, 4,096 MiB wired limit, 128 MiB cache,
isolated 5,000 ms inference timeout and all 34 dependencies matched the frozen
arm64 / Python 3.13.12 context.

| Inventory | Observed |
|---|---:|
| Scheduled original cases / actual primary fixture calls | 49 / 49 |
| Completed instances / cancelled instances | 48 / 1 |
| Accepted durable caller returns / unknown durable returns | 48 / 1 |
| Actual attempted caller-return POSTs | 49 |
| Computed accepted observations: ok / abstain | 45: 31 / 14 |
| Whole-input context rejections | 3 |
| Accepted unavailable observations | 0 |
| Physical scheduled upstream outcomes: ok / abstain / context rejected | 49: 31 / 15 / 3 |
| Completed upstream responses withheld from the caller | 1 abstain |
| Separate warmups / total physical handlers | 2 / 51 |
| Healthy cases after cancellation | 45 |
| Runtime epochs / decision retries / restarts | 1 / 0 / 0 |

Target index 3, `support-au-1569930`, retained all 1,337 input tokens. Its actual
upstream call completed in 566.381 ms. Local caller duration was 1,179.725 ms;
proxy acceptance to EOF was 1,178.859 ms. Actual cancel request start to EOF
was 458.000 ms. The ownership renewal returned 404, and the late observation
and primary completion each returned 400. All 45 subsequent cases completed
on the same worker.
The worker's subsequent failure-report POST also returned 400; it could not
revive the cancelled lease or alter the durable unknown return.

The 45 computed accepted caller observations had p50 / p95 / max
206.657 / 513.559 / 655.985 ms. The cancelled local duration and three context
rejections are outside that computed denominator and remain separately counted.
All 49 native upstream result signatures, including the withheld target,
matched the earlier frozen healthy baseline after excluding only stage id and
runtime duration; numeric equivalence permits integral JSON floats but keeps
booleans distinct.

## Verification and retention

[Thirteen new regressions](../../../../scripts/test/test_decision_public_workflow_cancellation.py)
cover a real socket cancellation and healthy suffix, the complete actual
Node/coordinator/Python-worker driver against an explicitly typed fixture,
historical Git replay, missing or duplicate HTTP records, unknown return
preservation, phantom observations, authenticated barriers, partial renewal
body accounting, modified native bytes, source drift, physical counters and
protocol mixing. The initial fixture test logs remain private: they exposed an
incorrect assumption about compact census run status and renewal body reading.
The corrected fixture driver passed before native scoring.
An additional red mutation exposed admission of a renewal outside the frozen
cohort. The final verifier requires every renewal and failure request to bind
an original lease and exactly one rejected failure-report POST for the target.
Foreign, extra, missing, reordered or successful failure reports are rejected.
This stricter replay passed the unchanged native evidence and the earlier v4
control without new model calls.

Thirty-eight focused tests passed, including v1–v4 compatibility. The full
930 Python documentation tests passed with four existing optional skips, as
did 12 Node documentation checks, workspace typechecks, the standalone strict
TypeScript driver check, document links and process catalog. The previous
169-worker suite already verifies the unchanged scoped lease watcher.

[The common offline verifier](../../../../scripts/verify-public-support-workflow.py)
reconstructed v5 from independently pinned raw bytes without new model calls.
A separate standard-library audit, importing no application or receipt-verifier
modules, completed 2,907 checks against raw HTTP bodies, historical Git sources,
the healthy control, cancelled durable accounting, barriers, counters, timings
and live cleanup. All 33 recorded temporary PIDs were absent. Short-lived helper
PIDs are not claimed to be exhaustively inventoried; their cleanup is separately
covered by the actual worker transport regressions.

Read-only resident captures before and after the native run confirmed the same
four PIDs, 35 protected source hashes, package seal, registration, plists,
profile and dependencies, with fresh ready monitoring. No inference was sent
to the protected resident and no installed package or service was changed.

[Archive summary](./public-workflow-cancellation-archive-summary.json) records
the verified ZIP and its original-workspace copy under `/docs/private`.
The archive retains raw cohort and cancellation HTTP artifacts, native and
fixture logs, independent audit, historical controls, source snapshots and
public documents. Both copies are checked by CRC, SHA-256 and entry sizes;
temporary credential files are excluded.

Human owners, real eligible workloads and SLO, independent labels, calibration
and holdout, and actual boot/login remain open. Routing remains disabled and
qualification remains `not_assessed`.

## Source rationale

[Python Event](https://docs.python.org/3/library/threading.html#event-objects)
provides cooperative cancellation; its signal is observed through actual
coordinator renewal. [Python socket.recv](https://docs.python.org/3/library/socket.html#socket.socket.recv)
defines an empty read as peer disconnect, which the proxy records separately
from upstream completion. [RFC 9110 section 9.2.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.2)
restricts automatic retry of non-idempotent requests; this protocol makes each
decision and cancellation POST once. Primary sources rechecked 09.10.2026.
