"""Bounded sustained HTTP probe; repeated development cases are not new quality evidence."""

from __future__ import annotations

import hashlib
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import Request, fingerprint
from scripts.lib.decision_baselines import LoopbackJson, development_cases
from scripts.lib.decision_performance import hardware, profile_from_health, summarize, validate_result
from workers.local_decisions import LocalDecisionClient, PROFILE

SCHEMA = "agat.decision.endurance.v1"
ROOT = Path(__file__).resolve().parents[2]


class ProcessMemory:
    """Read only explicitly supplied PIDs. RSS is neither MLX allocation nor unified-memory total."""
    def __init__(self, pids):
        if (not isinstance(pids, (tuple, list)) or len(pids) > 4 or len(set(pids)) != len(pids)
                or any(type(pid) is not int or pid < 1 for pid in pids)):
            raise ValueError("Supply at most four unique positive process IDs")
        if pids and platform.system() not in {"Darwin", "Linux"}:
            raise ValueError("Process RSS probe supports macOS and Linux only")
        self.pids = list(pids)
        self.identities = {}

    def __call__(self):
        rows = []
        for pid in self.pids:
            try:
                value = subprocess.check_output(['/bin/ps', '-p', str(pid), '-o', 'pid=,ppid=,rss=,lstart='],
                                                timeout=2, text=True, stderr=subprocess.DEVNULL).strip().split(maxsplit=3)
                if len(value) != 4 or int(value[0]) != pid or int(value[2]) < 0:
                    raise ValueError
                identity = (int(value[1]), value[3])
                if self.identities.setdefault(pid, identity) != identity:
                    raise ValueError
                rows.append({'pid': pid, 'parentPid': identity[0], 'rssBytes': int(value[2]) * 1024,
                             'processStart': identity[1], 'available': True})
            except (OSError, ValueError, subprocess.SubprocessError):
                rows.append({'pid': pid, 'available': False})
        return {'kind': 'ps-rss', 'processes': rows, 'available': all(r['available'] for r in rows)}


def endurance(dataset, url, *, duration_s=600, window_s=30, max_requests=5000, warmup=3,
              timeout_ms=10000, process_pids=(), health_transport=None, client=None,
              memory_sampler=None, clock=time.perf_counter, progress=None):
    cases = development_cases(dataset)
    requests = [Request.from_dict(c['request']) for c in cases]
    if (not requests or len(requests) > 100 or any(r.kind not in {'choice', 'boolean'} for r in requests)
            or type(duration_s) is not int or not 1 <= duration_s <= 1800
            or type(window_s) is not int or not 1 <= window_s <= min(300, duration_s)
            or type(max_requests) is not int or not 1 <= max_requests <= 10000
            or type(warmup) is not int or not 0 <= warmup <= 20
            or type(timeout_ms) is not int or not 100 <= timeout_ms <= 10000):
        raise ValueError("Unsupported or excessive endurance plan")
    memory = memory_sampler or ProcessMemory(process_pids)
    transport = health_transport or LoopbackJson(url, timeout=2)
    client = client or LocalDecisionClient(url)
    profile = profile_from_health(transport('GET', '/health'))
    pinned = fingerprint(profile)
    created = datetime.now(timezone.utc).isoformat()
    signatures, changes, snapshots, windows, measured = {}, set(), [], [], []
    started = clock()

    def one(request, index):
        begin = clock()
        observation = client.decide({'profile': PROFILE, 'profileSha256': pinned,
                                     'timeoutMs': timeout_ms, 'request': request.to_dict()})
        end = clock()
        row = {'index': index, 'caseId': request.id, 'inputSha256': request.input_sha256,
               'startedMs': round((begin-started)*1000, 3), 'wallMs': round((end-begin)*1000, 3)}
        try:
            if set(observation) == {'result'}:
                result = observation['result']; validate_result(result, request, profile)
                row.update(status=result['status'], reason=result['reason'], runtimeMs=result['durationMs'])
                if result['status'] != 'error':
                    row['inputTokens'] = result['inputTokens']
                    signature = fingerprint({k: result[k] for k in ('status', 'reason', 'value', 'selectedOptionId', 'distribution')})
                    row['decisionSha256'] = signature
                    if signatures.setdefault(request.input_sha256, signature) != signature:
                        changes.add(request.id)
            elif (set(observation) == {'status', 'reason'} and observation['status'] == 'unavailable'
                  and observation['reason'] in {'busy', 'timeout', 'cancelled', 'unreachable', 'invalid_response', 'profile_mismatch'}):
                row.update(observation)
            else:
                raise ValueError
        except (KeyError, ValueError, TypeError, OverflowError):
            row.update(status='unavailable', reason='invalid_response')
        return row

    def snapshot(phase):
        stable = False
        try:
            stable = fingerprint(profile_from_health(transport('GET', '/health'))) == pinned
        except (OSError, ValueError, KeyError, TypeError):
            pass
        sampled = memory()
        snapshots.append({'phase': phase, 'elapsedMs': round((clock()-started)*1000, 3),
                          'healthProfileStable': stable, 'memory': sampled})
        return stable and sampled['available']

    warm = []
    stopped = None
    for i in range(warmup):
        row = one(requests[i % len(requests)], i); warm.append(row)
        if row['status'] not in {'ok', 'abstain'} or changes:
            stopped = 'warmup_failed'; break
    # Start the measurement clock after warmup; warmup timestamps use their own origin.
    warmup_elapsed = clock()-started
    started = clock()
    if not snapshot('after_warmup'):
        stopped = 'health_or_process_unavailable'
    window_start, offset = 0.0, 0
    while stopped is None and len(measured) < max_requests and clock()-started < duration_s:
        row = one(requests[len(measured) % len(requests)], len(measured)); measured.append(row)
        elapsed = clock()-started
        if row['status'] not in {'ok', 'abstain'}:
            stopped = 'request_failed'
        elif changes:
            stopped = 'decision_changed'
        if elapsed-window_start >= window_s:
            windows.append({'index': len(windows), 'startedMs': round(window_start*1000, 3),
                            'finishedMs': round(elapsed*1000, 3),
                            'summary': summarize(measured[offset:], (elapsed-window_start)*1000)})
            offset, window_start = len(measured), elapsed
            if not snapshot(f'window_{len(windows)}'):
                stopped = 'health_or_process_unavailable'
            if progress: progress(windows[-1], snapshots[-1])
    elapsed = clock()-started
    if offset < len(measured):
        windows.append({'index': len(windows), 'startedMs': round(window_start*1000, 3),
                        'finishedMs': round(elapsed*1000, 3),
                        'summary': summarize(measured[offset:], (elapsed-window_start)*1000)})
    if not snapshot('finished'):
        stopped = 'health_or_process_unavailable'
    if stopped is None:
        stopped = 'duration_complete' if elapsed >= duration_s else 'request_cap'
    source_files = ['scripts/benchmark-decision-endurance.py', 'scripts/lib/decision_endurance.py',
                    'scripts/lib/decision_performance.py', 'scripts/lib/decision_baselines.py', 'workers/local_decisions.py']
    return sealed({'schemaVersion': SCHEMA, 'createdAt': created,
                   'status': 'observed' if stopped == 'duration_complete' else 'limited' if stopped == 'request_cap' else 'degraded',
                   'qualifiedForRouting': False, 'stoppedReason': stopped,
                   'profile': profile, 'profileSha256': pinned,
                   'dataset': {'id': dataset['id'], 'sha256': dataset['sha256'], 'usage': 'development-only',
                               'caseIds': [r.id for r in requests]},
                   'host': {**hardware(), 'serverResourcesMeasured': bool(process_pids), 'resourceMetric': 'sampled per-process RSS' if process_pids else None},
                   'harnessFiles': {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in source_files},
                   'plan': {'durationSeconds': duration_s, 'windowSeconds': window_s, 'maxRequests': max_requests,
                            'warmup': warmup, 'timeoutMs': timeout_ms, 'processPids': list(process_pids),
                            'concurrency': 1, 'arrivalPattern': 'closed loop; next request after prior completion',
                            'retry': False, 'stopOnFirstFailure': True},
                   'warmupElapsedMs': round(warmup_elapsed*1000, 3), 'warmup': warm,
                   'elapsedMs': round(elapsed*1000, 3), 'summary': summarize(measured, elapsed*1000),
                   'windows': windows, 'samples': snapshots, 'rows': measured, 'repeatDecisionChanges': sorted(changes),
                   'limits': ['Bounded local HTTP endurance probe, not a multi-hour production soak or SLO.',
                              'Only approved development inputs; repeats are not independent quality examples.',
                              'Initial runtime and OS cache warm state is unknown; model load is excluded.',
                              'Resource/health snapshots occur between windows, not during inference.',
                              'Per-process RSS overlaps mapped/unified allocations; do not sum it with MLX or other PIDs.',
                              'PID/start/parent checks detect process replacement; supplied PIDs are operator assertions.',
                              'No primary generator or coordinator persistence; no open-loop arrival-rate guarantee.',
                              'Duration is checked between requests; each HTTP call is separately bounded.',
                              'No retries or auto-restarts hide failures; a failed request stops the probe.']})
