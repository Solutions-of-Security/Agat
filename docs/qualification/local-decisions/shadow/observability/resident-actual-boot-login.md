# Actual resident boot and GUI login

09.10.2026 MSK. The installed one-shot observer automatically recorded a real
new boot and GUI login for the unchanged wired resident 0.12.3. Both original
event checks returned `verified`, exit 0. A separate read-only audit subsequently
confirmed the same new OS session, four live resident processes and fresh
successful Prometheus scrape. [The summary](./resident-actual-boot-login-summary.json)
binds the original raw receipts and audit by file SHA and seal.

The baseline was captured on 07.10 MSK, before this event. Its independently
published file SHA remains
`582cb7b1a0c4e6fc77406dfaa15cdfe9b3050cd8e5389de1045a08ac47bbe67e`.
The observer used its existing detached source snapshot from
`7ed2770bd9eb37be81de2b95918c1069e462fc8c`, with its own Git objects.
No new baseline, bootstrap, kickstart or reinstall was used for acceptance.

The automatic collector began at **19:39:03 MSK**. The two event receipts retain
their actual OS boot identity, GUI session, process birth times, service states,
package bindings and monitoring API replies in private evidence. The new boot
UUID differs from the pinned baseline on the same host and owner. Boot time and
all four process start times follow the baseline. The collector's installed
LaunchAgent has one run, last exit 0 and no active PID after collection.

The subsequent independent audit recomputed artifact seals and raw file pins,
checked every historical source against its committed Git bytes and reread the
resident package, model, registration, installed plists and dependencies.
All **35 historical source hashes** and **34 dependency pins** match. The
runtime, inference child, multiprocessing helper and Prometheus remained in the
same four-process census between the automatic receipts and fresh inspection.
The physical HTTP counter origin belongs to this boot; every outcome counter
is zero, readiness is 1 and in-progress handlers are 0. The audit requested no
inference and changed no service or installed file.

The [existing acceptance protocol](./resident-boot-login.md) and
[observer protocol](./resident-login-observer.md) remain unchanged. This closes
their previously open actual boot/login gate for this host, installed package
and observed event. It does not establish a recovery duration from boot: these
receipts prove ready state after the event and do not retain a continuously
sampled readiness boundary. Multi-day availability, customer SLO, business
owners, independent human labels, calibration and holdout remain open.
Routing remains disabled; qualification is `not_assessed`.

The first independent audit reached the native checks but failed while looking
for a nonexistent metrics field in the ready observation. Its copied raw
evidence and failure are retained. The corrected audit explicitly fetched the
bounded raw `/metrics` response and passed **861 checks**, including JSON key,
seal, source, event chronology, ownership and fresh-state checks. The original
automatic boot/login receipts were not rewritten or replaced.

[The private archive summary](./resident-actual-boot-login-archive-summary.json)
records both verified copies of the ZIP under `/docs/private`. CRC, every entry
SHA and size, and the complete copied ZIP SHA were checked. Raw OS identifiers,
PID, local paths, API replies and failed audit evidence remain private.

The [Apple launchd guide](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)
documents loading per-user agents at login and terminating them at logout.
Actual acceptance here is based on the retained OS event and source-bound
service checks. The guide was reread on 09.10.2026. Calendar boot time is
checked for chronology; exact equality across observations is not required,
consistent with the existing clock-adjustment regression.

The next runtime engineering gate in the main plan remains delivery of
coordinator cancellation during an actual active native HTTP handler, with
upstream disconnect, isolated backend state and a healthy workflow suffix.
