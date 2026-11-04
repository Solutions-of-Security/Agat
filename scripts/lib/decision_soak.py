"""Keep one runtime under continuous load across bounded, independently saved blocks."""
from __future__ import annotations

import math
import time
from pathlib import Path

from decision_runtime.artifacts import sealed, verify_seal, write_new
from decision_runtime.contracts import Request
from scripts.lib.decision_baselines import development_cases
from scripts.lib.decision_endurance import SCHEMA as BLOCK_SCHEMA, ProcessMemory, endurance

SCHEMA = 'agat.decision.continuous-soak.v1'


def validate_plan(dataset, duration_s, block_s):
    cases = development_cases(dataset)
    if (not cases or len(cases) > 100
            or any(Request.from_dict(case['request']).kind not in {'choice', 'boolean'} for case in cases)
            or type(duration_s) is not int or not 1 <= duration_s <= 7200
            or type(block_s) is not int or not 1 <= block_s <= 1800
            or math.ceil(duration_s / block_s) > 32):
        raise ValueError('Unsupported continuous soak plan')


def continuous_soak(dataset, url, directory, profile_sha, *, duration_s=7200, block_s=900,
                    process_pids=(), cancel_requested=None, journal=None, progress=None,
                    probe=endurance, clock=time.perf_counter, memory_sampler=None):
    validate_plan(dataset, duration_s, block_s)
    if any(callback is not None and not callable(callback)
           for callback in (cancel_requested, journal, progress)):
        raise ValueError('Invalid continuous soak callback')
    memory = memory_sampler or ProcessMemory(process_pids)
    signatures, changed, blocks = {}, set(), []
    measured_ms, attempts, warmup_calls = 0, 0, 0
    stopped = None
    started = clock()

    def cancelled():
        return bool(changed) or (cancel_requested is not None and cancel_requested())

    def observe(row, phase):
        if journal:
            journal({'block': len(blocks), 'phase': phase, 'row': dict(row)})
        if row['status'] in {'ok', 'abstain'}:
            key = row['inputSha256']
            if signatures.setdefault(key, row['decisionSha256']) != row['decisionSha256']:
                changed.add(row['caseId'])

    count = math.ceil(duration_s / block_s)
    for index in range(count):
        if cancelled():
            stopped = 'decision_changed_across_blocks' if changed else 'cancelled'
            break
        seconds = min(block_s, duration_s - index * block_s)
        block_start_ms = round((clock() - started) * 1000, 3)
        report = probe(dataset, url, duration_s=seconds, window_s=min(30, seconds),
                       max_requests=10000, warmup=3 if index == 0 else 0, timeout_ms=10000,
                       process_pids=process_pids, cancel_requested=cancelled, observation=observe,
                       expected_profile_sha=profile_sha, memory_sampler=memory)
        verify_seal(report, BLOCK_SCHEMA)
        name = f'block-{index + 1:03d}.json'
        write_new(Path(directory) / name, report)
        blocks.append({'path': name, 'sha256': report['sha256'], 'status': report['status'],
                       'stoppedReason': report['stoppedReason'], 'startedMs': block_start_ms,
                       'finishedMs': round((clock() - started) * 1000, 3)})
        attempts += report['summary']['attempts']
        warmup_calls += len(report['warmup'])
        measured_ms += report['elapsedMs']
        if progress:
            progress(index, report)
        if changed:
            stopped = 'decision_changed_across_blocks'
        elif report['profileSha256'] != profile_sha:
            stopped = 'profile_changed_across_blocks'
        elif report['status'] != 'observed':
            stopped = report['stoppedReason']
        elif report['elapsedMs'] < seconds * 1000:
            stopped = 'incomplete_block'
        if stopped:
            break
    if stopped is None:
        stopped = 'duration_complete' if len(blocks) == count and measured_ms >= duration_s * 1000 else 'incomplete_run'
    return sealed({'schemaVersion': SCHEMA,
                   'status': 'observed' if stopped == 'duration_complete' else 'limited' if stopped == 'request_cap' else 'degraded',
                   'stoppedReason': stopped, 'qualifiedForRouting': False, 'profileSha256': profile_sha,
                   'datasetSha256': dataset['sha256'], 'plan': {'durationSeconds': duration_s, 'blockSeconds': block_s,
                       'blocks': count, 'concurrency': 1, 'retry': False, 'runtimeRestarts': 0},
                   'measuredMs': measured_ms, 'wallMs': round((clock() - started) * 1000, 3),
                   'attempts': attempts, 'separateWarmupCalls': warmup_calls, 'blocks': blocks,
                   'crossBlockDecisionChanges': sorted(changed),
                   'limits': ['One local runtime and closed-loop development inputs; no production SLO or business qualification.',
                              'The same server remains running between blocks; report serialization creates recorded gaps.',
                              'Per-block elapsed time includes its final in-flight request; no retries or restarts hide failures.']})
