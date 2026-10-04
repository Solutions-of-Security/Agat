"""Save bounded shared-load blocks while retaining the first failure and pair journal."""
from __future__ import annotations

import copy
import math
import time
from pathlib import Path

from decision_runtime.artifacts import sealed, verify_seal, write_new
from decision_runtime.contracts import Request
from scripts.lib.decision_baselines import development_cases
from scripts.lib.decision_shared_load import SCHEMA as BLOCK_SCHEMA, benchmark_shared

SCHEMA = 'agat.decision.shared-soak.v1'


def validate_plan(dataset, duration_s, max_blocks):
    cases = development_cases(dataset)
    if (not cases or len(cases) > 30
            or any(Request.from_dict(c['request']).kind not in {'choice', 'boolean'} for c in cases)
            or type(duration_s) is not int or not 1 <= duration_s <= 7200
            or type(max_blocks) is not int or not 1 <= max_blocks <= 128):
        raise ValueError('Unsupported shared soak plan')


def measured_ms(report):
    rows = [r for phase in report['phases'] for r in phase['rows']]
    if not rows:
        return 0.0
    warmup_end = max((row['finishedMs'] for row in report['warmup']), default=0)
    result = report['elapsedMs'] - warmup_end
    if not math.isfinite(result) or result <= 0:
        raise ValueError('Invalid shared block elapsed time')
    return result


def shared_soak(dataset, decision_url, primary_factory, directory, profile, *, duration_s=7200,
                max_blocks=128, journal=None, cancel_requested=None, progress=None,
                probe=benchmark_shared, clock=time.perf_counter, writer=write_new):
    validate_plan(dataset, duration_s, max_blocks)
    if any(c is not None and not callable(c) for c in (journal, cancel_requested, progress)):
        raise ValueError('Invalid shared soak callbacks')
    directory = Path(directory)
    signatures = {};changes = {'decision': set(), 'primary': set()};blocks = []
    measured = 0.0;attempts = 0;warmups = 0;stopped = None;failure = None;started = clock();observation_error = None;primary_identity = None
    profile = copy.deepcopy(profile)

    def cancelled():
        nonlocal observation_error
        try:
            return bool(changes['decision']) or (cancel_requested is not None and cancel_requested())
        except Exception as error:
            observation_error = type(error).__name__
            return True

    def observe(phase, index, rows):
        if journal:
            journal({'block': len(blocks), 'phase': phase, 'pairIndex': index, 'rows': copy.deepcopy(rows)})
        for row in rows:
            if row['status'] in {'ok', 'abstain'}:
                key = (row['kind'], row['inputSha256'])
                if signatures.setdefault(key, row['decisionSha256']) != row['decisionSha256']:
                    changes[row['kind']].add(row['caseId'])

    for index in range(max_blocks):
        if cancelled():
            stopped = 'observation_failed' if observation_error else 'decision_changed_across_blocks' if changes['decision'] else 'cancelled'
            break
        remaining = duration_s - measured / 1000
        if remaining <= 0:
            stopped = 'duration_complete'
            break
        budget = min(600, max(30, math.ceil(remaining)))
        began = round((clock() - started) * 1000, 3)
        try:
            report = probe(dataset, decision_url, primary_factory, rounds=2, warmup=2,
                           time_budget_s=budget, timeout_ms=10000, pair_observer=observe,
                           stop_on_failure=True, cancel_requested=cancelled, expected_profile=profile)
        except Exception as error:
            stopped = 'block_failed';failure = type(error).__name__
            break
        verify_seal(report, BLOCK_SCHEMA)
        name = f'block-{index + 1:03d}.json'
        try:
            writer(directory / name, report)
            (directory / name).chmod(0o600)
        except OSError as error:
            stopped = 'evidence_write_failed';failure = type(error).__name__
            break
        elapsed = measured_ms(report)
        block_rows = [r for phase in report['phases'] for r in phase['rows']]
        measured += elapsed;attempts += len(block_rows);warmups += len(report['warmup'])
        blocks.append({'path': name, 'sha256': report['sha256'], 'status': report['status'],
                       'stoppedReason': report['stoppedReason'], 'startedMs': began,
                       'finishedMs': round((clock() - started) * 1000, 3), 'measuredMs': round(elapsed, 3)})
        if progress:
            try: progress(index, report)
            except Exception as error: observation_error = type(error).__name__
        all_rows = report['warmup'] + block_rows
        failed = any(row['status'] not in {'ok', 'abstain'} for row in all_rows)
        if primary_identity is None: primary_identity = copy.deepcopy(report['primary'])
        cancellation = cancelled()
        if observation_error:
            stopped = 'observation_failed';failure = observation_error
        elif changes['decision']:
            stopped = 'decision_changed_across_blocks'
        elif report['decisionProfile'] != profile or not report['decisionProfileStable']:
            stopped = 'profile_changed'
        elif (report['primary'] != primary_identity or not report['primaryStable'] or len(report['primaryResidence']) != len(report['phases']) + 1
              or any(r['status'] != 'resident' for r in report['primaryResidence'])):
            stopped = 'primary_changed_or_unloaded'
        elif failed or report['repeatDecisionChanges']['decision']:
            stopped = report['stoppedReason'] or 'request_failed'
        elif report['status'] != 'observed' and report['stoppedReason'] != 'time_budget':
            stopped = report['stoppedReason'] or 'block_degraded'
        elif cancellation:
            stopped = 'cancelled'
        elif measured >= duration_s * 1000 and attempts > 0:
            stopped = 'duration_complete'
        if stopped:
            break
    if stopped is None:
        stopped = 'block_cap'
    return sealed({'schemaVersion': SCHEMA,
                   'status': 'observed' if stopped == 'duration_complete' else 'limited' if stopped == 'block_cap' else 'degraded',
                   'stoppedReason': stopped, 'failureType': failure, 'qualifiedForRouting': False,
                   'decisionProfile': profile, 'datasetSha256': dataset['sha256'],
                   'plan': {'durationSeconds': duration_s, 'maxBlocks': max_blocks, 'roundsPerBlock': 2,
                            'warmupPairsPerBlock': 2, 'blockBudgetMaxSeconds': 600,
                            'lastBlockBudgetMinimumSeconds': 30, 'maxConcurrentCallsPerModel': 1,
                            'retry': False, 'stopOnFailure': True, 'runtimeRestarts': 0},
                   'measuredMs': round(measured, 3), 'wallMs': round((clock() - started) * 1000, 3),
                   'measuredAttempts': attempts, 'separateWarmupCalls': warmups, 'blocks': blocks,
                   'crossBlockDecisionChanges': sorted(changes['decision']),
                   'crossBlockPrimaryChanges': sorted(changes['primary']),
                   'limits': ['Repeated development inputs; no quality, calibration or production SLO claim.',
                              'Measured duration sums block elapsed after warmup, including phase/final API checks; initial health and serialization create wall-clock gaps.',
                              'A final bounded block may end at its time budget; failures are never accepted as a duration boundary.',
                              'Cancellation stops after already active calls; it does not guarantee cancellation of GPU work.',
                              'The benchmark never stops or restarts either supplied model service.']})
