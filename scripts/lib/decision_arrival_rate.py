"""Bounded open-arrival diagnostic: preserve every scheduled attempt, including drops."""
from __future__ import annotations

import math
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from decision_runtime.contracts import Request, fingerprint, number
from scripts.lib.decision_performance import distribution, validate_result
from workers.local_decisions import CALLER_TIMING_VERSION, PROFILE

PLAN_SCHEMA = 'agat.decision.arrival-rate-plan.v1'
RESULT_SCHEMA = 'agat.decision.arrival-rate-result.v1'
PHASE_SCHEMA = 'agat.decision.arrival-rate-phase.v1'
SOURCE_PATHS = ['decision_runtime', 'workers/local_decisions.py',
    'scripts/run-decision-arrival-rate.py', 'scripts/verify-decision-arrival-rate.py',
    'scripts/lib/decision_arrival_rate.py', 'scripts/test/test_decision_arrival_rate.py',
    'scripts/lib/decision_context.py', 'scripts/lib/decision_resources.py',
    'scripts/lib/decision_performance.py', 'scripts/lib/decision_baselines.py',
    'scripts/run-temporal-real-rag.py', 'scripts/profile-embedding-rag.py']


def validate_schedule(rate, count, slots, late_ms):
    if (type(rate) not in (int, float) or not math.isfinite(rate) or not .1 <= rate <= 4
            or type(count) is not int or not 1 <= count <= 240
            or type(slots) is not int or not 1 <= slots <= 8
            or type(late_ms) is not int or not 1 <= late_ms <= 1000
            or count / rate > 120):
        raise ValueError('Unsupported or excessive arrival schedule')


def drive_arrivals(rate, count, slots, late_ms, dispatch, *, clock=time.monotonic,
                   sleep=time.sleep, cancelled=lambda: False, start=None):
    """Fixed offsets; overdue arrivals become explicit drops, never a catch-up burst."""
    validate_schedule(rate, count, slots, late_ms)
    start = clock() if start is None else start
    for index in range(count):
        due = start + index / rate
        while not cancelled() and (remaining := due - clock()) > 0:
            sleep(min(.02, remaining))
        observed = clock()
        reason = ('cancelled' if cancelled() else 'scheduler_lag'
                  if (observed - due) * 1000 > late_ms else None)
        dispatch(index, due - start, observed - start, reason)
    return start


def measure_one(client, request, profile, timeout_ms):
    started = time.monotonic()
    observation = None
    try:
        observation = client.decide({'profile': PROFILE, 'profileSha256': fingerprint(profile),
            'timeoutMs': timeout_ms, 'callerTimingVersion': CALLER_TIMING_VERSION,
            'request': request.to_dict()})
        timing = observation['callerTiming']
        if (set(timing) != {'schemaVersion', 'clock', 'boundary', 'durationMs'}
                or timing['schemaVersion'] != CALLER_TIMING_VERSION or timing['clock'] != 'monotonic'
                or timing['boundary'] != 'local_http_call'):
            raise ValueError('Invalid caller timing')
        number(timing['durationMs'], 0, 86_400_000)
        body = {k: v for k, v in observation.items() if k != 'callerTiming'}
        if set(body) == {'result'}:
            validate_result(body['result'], request, profile)
            status, reason = body['result']['status'], body['result']['reason']
        elif (set(body) == {'status', 'reason'} and body['status'] == 'unavailable'
              and body['reason'] in {'busy', 'timeout', 'cancelled', 'unreachable',
                                     'invalid_response', 'profile_mismatch'}):
            status, reason = body['status'], body['reason']
        else:
            raise ValueError('Invalid caller observation')
        return {'status': status, 'reason': reason, 'observation': observation,
                'callerMs': timing['durationMs'],
                'wallMs': round((time.monotonic() - started) * 1000, 3)}
    except Exception as error:
        # Failed measurements remain in the scheduled denominator, without invented timing.
        return {'status': 'measurement_error', 'reason': type(error).__name__, 'observation': observation,
                'wallMs': round((time.monotonic() - started) * 1000, 3)}


def summarize(rows, threshold_ms):
    admitted = [r for r in rows if r['status'] != 'dropped']
    scored = [r for r in admitted if r['status'] in {'ok', 'abstain'}]
    good = [r for r in scored if r['callerMs'] <= threshold_ms]
    measured = [r for r in admitted if 'callerMs' in r]
    return {'scheduled': len(rows), 'admitted': len(admitted), 'scored': len(scored),
        'dropped': dict(Counter(r['reason'] for r in rows if r['status'] == 'dropped')),
        'statuses': dict(Counter(r['status'] for r in admitted)),
        'failures': dict(Counter(r['reason'] for r in admitted if r['status'] not in {'ok', 'abstain'})),
        'goodWithinThreshold': len(good),
        'goodPerScheduled': len(good) / len(rows) if rows else None,
        'goodPerAdmitted': len(good) / len(admitted) if admitted else None,
        'callerMsAllMeasured': distribution([r['callerMs'] for r in measured]),
        'callerMsScored': distribution([r['callerMs'] for r in scored]),
        'dispatchLagMs': distribution([r['dispatchMs'] - r['scheduledMs'] for r in rows])}


def run_phase(client, case, profile, *, rate, count, slots=1, late_ms=100,
              timeout_ms=10000, threshold_ms=5000, cancelled=lambda: False, journal=None, origin=None):
    validate_schedule(rate, count, slots, late_ms)
    if (type(timeout_ms) is not int or not 100 <= timeout_ms <= 10000
            or type(threshold_ms) is not int or not 1 <= threshold_ms <= timeout_ms):
        raise ValueError('Invalid caller budget')
    request = Request.from_dict(case['request'])
    if (case['inputSha256'] != request.input_sha256 or type(case['targetTokens']) is not int
            or not 256 <= case['targetTokens'] <= profile['model']['maxInputTokens']):
        raise ValueError('Invalid case binding')
    capacity = threading.BoundedSemaphore(slots)
    rows = [None] * count
    lock = threading.Lock()
    futures = []
    origin = time.monotonic() if origin is None else number(origin, 0, 86_400_000_000)

    def save(index, row):
        with lock:
            rows[index] = row
            if journal:
                journal(row)

    def one(index, base):
        try:
            began = time.monotonic()
            row = measure_one(client, request, profile, timeout_ms)
            if row['status'] in {'ok', 'abstain'} and row['observation']['result']['inputTokens'] != case['targetTokens']:
                row.update(status='measurement_error', reason='input_token_mismatch')
            save(index, {**base, 'startedMs': round((began - origin) * 1000, 3),
                         'finishedMs': round((time.monotonic() - origin) * 1000, 3), **row})
        finally:
            capacity.release()

    with ThreadPoolExecutor(max_workers=slots) as pool:
        def dispatch(index, due, observed, reason):
            base = {'index': index, 'scheduledMs': round(due * 1000, 3),
                    'dispatchMs': round(observed * 1000, 3)}
            if reason or not capacity.acquire(blocking=False):
                save(index, {**base, 'status': 'dropped', 'reason': reason or 'client_capacity'})
            else:
                futures.append(pool.submit(one, index, base))
        drive_arrivals(rate, count, slots, late_ms, dispatch, cancelled=cancelled, start=origin)
        for future in futures:
            future.result()
    return {'schemaVersion': PHASE_SCHEMA, 'caseId': request.id, 'inputSha256': request.input_sha256,
            'targetTokens': case['targetTokens'], 'ratePerSecond': rate, 'count': count,
            'clientSlots': slots, 'maxSchedulerLagMs': late_ms, 'callerTimeoutMs': timeout_ms,
            'thresholdMs': threshold_ms, 'elapsedMs': round((time.monotonic() - origin) * 1000, 3),
            'rows': rows, 'summary': summarize(rows, threshold_ms)}
