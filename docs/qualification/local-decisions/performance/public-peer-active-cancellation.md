# Active native peer cancellation and same-endpoint recovery

09.10.2026. The full original public development inventory was exercised on a
fresh committed implementation: **49 cases / 44 groups**. This checkpoint proves
local caller EOF propagation, native isolated backend retirement and recovery.
Actual coordinator cancellation and durable lease accounting are the next gate.

[Summary](./public-peer-active-cancellation-summary.json) and
[archive metadata](./public-peer-active-cancellation-archive-summary.json) bind the
fresh raw evidence. [The launcher](../../../../scripts/run-public-support-peer-cancellation.py)
uses only an owned temporary loopback endpoint, the frozen runtime 0.12.3 profile,
read-only installed Python/weights and offline model loading. Resident port 8766
is excluded. Python 3.13.12 arm64 and all 34 dependencies match the original context.
Limits are 2,048 input tokens, 4,096 MiB wired memory and 128 MiB cache.

Target index 25 is fixed before scoring. Each original request is forwarded once,
with its original state, question, candidates and id. The standard contract
serializer only makes optional `abstain=false` values explicit. Whole over-limit
inputs are rejected, without truncation or replacement. Two native warmups are
recorded separately before each of the two runtime epochs.

[The relay](../../../../scripts/lib/decision_public_workflow_active_cancellation.py)
requires one active upstream HTTP handler, the original counter epoch, exact
completed-prefix counters and no readable upstream response bytes. It rejects
an already completed response. Only then does the launcher signal the actual
local caller Event. The relay observes caller EOF and applies upstream
`shutdown(SHUT_RDWR)`. No target response is reconstructed or delivered.

| Result | Observed |
|---|---:|
| Original scheduled requests / returned local observations | 49 / 49 |
| Delivered native results | 48 |
| Computed ok / abstain / whole-context rejection | 31 / 14 / 3 |
| Local unavailable.cancelled / interrupted native target | 1 / 1 |
| Runtime epochs / separate warmups | 2 / 4 |
| Total physical POST starts / known completions | 53 / 52 |
| Healthy original suffix / decision retries | 23 / 0 |

The local target call lasted 50.127 ms. Fresh active sampling
preceded the cancellation Event by 4.000 ms; Event
to EOF was 21.000 ms. Native retirement followed EOF by
579.000 ms, with exit 75, reason `inference_cancelled`
and owned inference child exit -15. All three recorded original native PIDs
were absent before replacement. The same port and profile recovered with a
fresh process and server-start epoch; two new warmups finished
9454.000 ms after recorded retirement. The full
launcher took 38783.907 ms; native exits were 75 and 130.

The diagnostic budgets remain 250 ms from active sample to Event, 2,500 ms from
Event to EOF, 10,000 ms for retirement and 90,000 ms for recovery. They are not an
accepted customer SLO. An active HTTP handler and inference-child retirement
do not establish GPU kernel preemption. The interrupted target retains no typed
native result and its final physical outcome counter remains unknown.

[Offline replay](../../../../scripts/verify-public-support-peer-cancellation.py)
checks raw pins, historical Git source closure, six physical snapshots, four
warmups and all original inputs with zero model calls. Measured commit
`c06da45532ed7cead03d8dc89ad46cd1342a481e` contains 193 contributors; final
verifier `79cd457866d943d56e93bed2c3d2fb618af6cfc2` contains 108 contributors.
An additional guard rejects posthoc plan dates even when seals are recomputed.
All 23 focused relay/ledger/CLI tests passed.

A separate standard-library audit imported no application/verifier modules. It
matched all 48 delivered signatures to the published healthy baseline, checked
raw SHA/seals, exact physical deltas, retirement/recovery order and live PID
absence. Its initial strict dictionary comparison rejected explicit optional
contract defaults; that failed log and script are retained. The corrected audit
independently reconstructs the unchanged request fingerprints. All six temporary
native PIDs were absent. Read-only resident captures retained the same four
protected PIDs, 20 installed runtime file hashes, bundle seal, configuration,
profile, registration, plists and ready monitoring.

[Actual resident boot/login](../shadow/observability/resident-actual-boot-login.md)
is already verified for the pinned installed package. Owner/customer workload,
SLO, independent human labels, calibration and holdout remain open. Primary and
coordinator were not exercised here; routing is disabled and qualification is
`not_assessed`. Next: join this boundary to actual coordinator run cancellation,
rejected late writes, unknown durable return and a healthy full original suffix.

[Python socket shutdown](https://docs.python.org/3.13/library/socket.html#socket.socket.shutdown)
supports explicit upstream EOF propagation;
[multiprocessing](https://docs.python.org/3.13/library/multiprocessing.html#contexts-and-start-methods)
documents the separate resource tracker under spawn. These are primary source
references for transport/owned-process handling, not performance evidence.
