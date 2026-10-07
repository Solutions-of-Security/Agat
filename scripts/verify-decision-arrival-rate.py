#!/usr/bin/env python3
"""Independently recount scheduled arrivals and bind raw evidence to measured Git sources."""
import argparse
import hashlib
import io
import json
import re
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import read_json, sealed, verify_seal, write_new
from decision_runtime.contracts import Request, fingerprint, number
from scripts.lib.decision_arrival_rate import PLAN_SCHEMA, RESULT_SCHEMA, PHASE_SCHEMA, SOURCE_PATHS
from scripts.lib.decision_performance import distribution, profile_from_health, validate_result
from workers.local_decisions import CALLER_TIMING_VERSION


def require(value, message):
    if not value:
        raise ValueError(message)


def observation(row, request, profile, tokens):
    raw = row['observation']
    timing = raw['callerTiming']
    require(set(timing) == {'schemaVersion', 'clock', 'boundary', 'durationMs'}
            and timing['schemaVersion'] == CALLER_TIMING_VERSION and timing['clock'] == 'monotonic'
            and timing['boundary'] == 'local_http_call', 'Invalid negotiated timing')
    number(timing['durationMs'], 0, 86_400_000)
    require(row['callerMs'] == timing['durationMs'], 'Caller timing differs from raw observation')
    body = {k: v for k, v in raw.items() if k != 'callerTiming'}
    if set(body) == {'result'}:
        result = body['result']
        validate_result(result, request, profile)
        require(row['status'] == result['status'] and row['reason'] == result['reason'], 'Typed status differs')
        if result['status'] != 'error':
            require(result['inputTokens'] == tokens and result['generatedTokens'] == 0,
                    'Input count differs or generation enabled')
    else:
        require(set(body) == {'status', 'reason'} and body['status'] == 'unavailable'
                and body['reason'] in {'busy', 'timeout', 'cancelled', 'unreachable', 'invalid_response', 'profile_mismatch'}
                and row['status'] == body['status'] and row['reason'] == body['reason'], 'Invalid unavailable observation')
    require(number(row['wallMs'], 0, 86_400_000) + .01 >= row['callerMs'], 'Caller timing exceeds measurement wall')


def verify_phase(phase, schedule, case, plan):
    request = Request.from_dict(case['request'])
    require(all(phase[k] == v for k, v in {
        'caseId': request.id, 'inputSha256': request.input_sha256, 'targetTokens': case['targetTokens'],
        'ratePerSecond': schedule['ratePerSecond'], 'count': schedule['count'],
        'clientSlots': schedule['clientSlots'], 'maxSchedulerLagMs': schedule['maxSchedulerLagMs'],
        'callerTimeoutMs': plan['callerTimeoutMs'], 'thresholdMs': plan['thresholdMs']}.items()),
        'Phase differs from fixed plan')
    rows = phase['rows']
    require(len(rows) == schedule['count'] and [r['index'] for r in rows] == list(range(schedule['count'])),
            'Missing, duplicate or reordered scheduled attempts')
    admitted, scored, good, measured, drops, statuses, failures, intervals = [], [], [], [], {}, {}, {}, []
    for index, row in enumerate(rows):
        require(abs(number(row['scheduledMs'], 0, 120000) - index / schedule['ratePerSecond'] * 1000) < .0011,
                'Arrival schedule was shifted by response times')
        lag = number(row['dispatchMs'], 0, 86_400_000) - row['scheduledMs']
        require(lag >= -.0011, 'Arrival dispatched early')
        if row['status'] == 'dropped':
            require(set(row) == {'index', 'scheduledMs', 'dispatchMs', 'status', 'reason'}
                    and row['reason'] in {'client_capacity', 'scheduler_lag', 'cancelled'},
                    'Dropped arrival contains invented HTTP metadata')
            require(row['reason'] != 'scheduler_lag' or lag + .0011 > schedule['maxSchedulerLagMs'],
                    'False scheduler lag')
            require(row['reason'] != 'client_capacity' or lag <= schedule['maxSchedulerLagMs'] + .0011,
                    'Scheduler lag hidden as capacity drop')
            drops[row['reason']] = drops.get(row['reason'], 0) + 1
            continue
        require(lag <= schedule['maxSchedulerLagMs'] + .0011, 'Overdue arrival sent as catch-up burst')
        began = number(row['startedMs'], 0, 86_400_000)
        finished = number(row['finishedMs'], began, 86_400_000)
        require(began + .0011 >= row['dispatchMs'] and finished <= phase['elapsedMs'] + .0011,
                'Invalid HTTP interval')
        require(row['status'] != 'measurement_error', 'Measurement error is not valid caller evidence')
        observation(row, request, plan['profile'], case['targetTokens'])
        require(finished - began + .02 >= row['wallMs'], 'HTTP wall exceeds execution interval')
        intervals.append((began, finished))
        admitted.append(row); measured.append(row)
        statuses[row['status']] = statuses.get(row['status'], 0) + 1
        if row['status'] in {'ok', 'abstain'}:
            scored.append(row)
            if row['callerMs'] <= plan['thresholdMs']:
                good.append(row)
        else:
            failures[row['reason']] = failures.get(row['reason'], 0) + 1
    for began, _ in intervals:
        require(sum(start <= began and end > began + .0011 for start, end in intervals) <= schedule['clientSlots'],
                'Unbounded client concurrency')
    expected = {'scheduled': len(rows), 'admitted': len(admitted), 'scored': len(scored),
        'dropped': drops, 'statuses': statuses, 'failures': failures,
        'goodWithinThreshold': len(good), 'goodPerScheduled': len(good) / len(rows),
        'goodPerAdmitted': len(good) / len(admitted) if admitted else None,
        'callerMsAllMeasured': distribution([r['callerMs'] for r in measured]),
        'callerMsScored': distribution([r['callerMs'] for r in scored]),
        'dispatchLagMs': distribution([r['dispatchMs'] - r['scheduledMs'] for r in rows])}
    require(phase['summary'] == expected, 'Summary omits failures or scheduled arrivals')
    return expected


def verify(directory):
    plan = verify_seal(read_json(directory / 'plan.json'), PLAN_SCHEMA)
    result = verify_seal(read_json(directory / 'result.json'), RESULT_SCHEMA)
    require(result['status'] == 'observed' and result['planSha256'] == plan['sha256']
            and result['failure'] is None and result['cancelled'] is False
            and result['cleanupErrors'] == [] and result['remainingOwnedPids'] == [], 'Native gate or cleanup failed')
    for artifact in (plan, result):
        require(artifact['sloAccepted'] is False and artifact['routingEnabled'] is False
                and artifact['customerPopulationMeasured'] is False and artifact['qualification'] == 'not_assessed',
                'Diagnostic measurements promoted to qualification')
    require(plan['profileSha256'] == fingerprint(plan['profile']) and plan['thresholdMs'] == 5000
            and plan['callerTimeoutMs'] == 10000 and plan['warmupPerCase'] == 2, 'Pinned profile/budgets changed')
    expected_schedules = [{'caseIndex': i, 'ratePerSecond': rate, 'count': int(rate * plan['phaseSeconds']),
                           'clientSlots': slots, 'maxSchedulerLagMs': 100}
                          for i in range(2) for rate, slots in ((.5, 1), (1, 1), (2, 1), (2, 2))]
    require(plan['schedules'] == expected_schedules, 'Open-arrival protocol changed')
    paths = list(plan['sourceSha256'])
    require(paths and all(not Path(p).is_absolute() and '..' not in Path(p).parts for p in paths), 'Unsafe source path')
    raw = subprocess.check_output(['git', 'archive', plan['implementationCommit'], '--', *paths], cwd=ROOT, timeout=15)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        sources = {item.name: archive.extractfile(item).read() for item in archive if item.isfile()}
    require({p: hashlib.sha256(v).hexdigest() for p, v in sources.items()} == plan['sourceSha256'], 'Git source snapshot mismatch')
    require(all(p in sources or any(k.startswith(p + '/') for k in sources) for p in SOURCE_PATHS), 'Missing contributors')
    require(json.loads(sources[plan['profilePath']]) == plan['profile'], 'Committed profile differs')
    implementation = hashlib.sha256()
    for name in sorted(p for p in sources if Path(p).parent == Path('decision_runtime') and p.endswith('.py')):
        implementation.update(Path(name).name.encode() + b'\0' + sources[name] + b'\0')
    require(implementation.hexdigest() == plan['profile']['model']['implementationSha256'], 'Runtime implementation mismatch')
    requirements = dict(line.split('==') for line in sources['decision_runtime/requirements-mlx.txt'].decode().splitlines()
                        if line and not line.startswith('#'))
    require(plan['runtime']['packages'] == requirements, 'Runtime dependency mismatch')
    for name, digest in result['logSha256'].items():
        require(name in {'runtime.log', 'arrivals.jsonl'} and hashlib.sha256((directory / name).read_bytes()).hexdigest() == digest,
                'Raw log drift')
    report = result['report']
    require([c['targetTokens'] for c in plan['cases']] == [256, 2048], 'Wrong context probes')
    require(len(report['warmup']) == 4 and len(report['phases']) == len(plan['schedules']) == 8, 'Incomplete planned work')
    for index, row in enumerate(report['warmup']):
        case = plan['cases'][index // 2]; request = Request.from_dict(case['request'])
        require(case['inputSha256'] == request.input_sha256 and row['caseId'] == request.id
                and row['iteration'] == index % 2 and row['targetTokens'] == case['targetTokens']
                and row['status'] in {'ok', 'abstain'}, 'Warmup binding mismatch')
        observation(row, request, plan['profile'], case['targetTokens'])
    summaries = []
    for index, (phase, schedule) in enumerate(zip(report['phases'], plan['schedules'])):
        phase_file = verify_seal(read_json(directory / f'phase-{index}.json'), PHASE_SCHEMA)
        require({k: v for k, v in phase_file.items() if k != 'sha256'} == phase, 'Phase file drift')
        summaries.append(verify_phase(phase, schedule, plan['cases'][schedule['caseIndex']], plan))
    records = [json.loads(line) for line in (directory / 'arrivals.jsonl').read_text().splitlines()]
    expected = {(i, r['index']): r for i, phase in enumerate(report['phases']) for r in phase['rows']}
    require(len(records) == len(expected), 'Raw journal denominator mismatch')
    seen = set()
    for record in records:
        key = (record.pop('phase'), record['index'])
        require(key not in seen and key in expected and record == expected[key], 'Journal row drift or duplicate')
        seen.add(key)
    labels = ['ready_before_first_scoring', 'warmup-256', 'warmup-2048', *[f'phase-{i}' for i in range(8)]]
    require([s['label'] for s in report['healthSamples']] == labels, 'Incomplete readiness snapshots')
    for sample in report['healthSamples']:
        require(profile_from_health(sample['health']) == plan['profile']
                and set(sample['ownedPids']) <= set(result['ownedPids']), 'Runtime profile or process drift')
    def counters(sample):
        values = {}
        for line in sample['metricsRaw'].splitlines():
            match = re.fullmatch(r'agat_decision_requests_total\{outcome="([a-z_]+)"\} ([0-9]+(?:\.0+)?)', line)
            if match:
                require(match[1] not in values, 'Duplicate runtime outcome counter')
                values[match[1]] = int(float(match[2]))
        require(set(values) == {'ok', 'abstain', 'busy', 'invalid', 'profile_mismatch', 'context_rejected',
                               'backend_error', 'timeout', 'cancelled', 'unavailable'}, 'Missing runtime outcome counters')
        return values
    expected_counters = dict.fromkeys(counters(report['healthSamples'][0]), 0)
    require(counters(report['healthSamples'][0]) == expected_counters, 'Runtime received prior inference traffic')
    for index, sample in enumerate(report['healthSamples'][1:]):
        additions = ([report['warmup'][index * 2], report['warmup'][index * 2 + 1]] if index < 2
                     else [r for r in report['phases'][index - 2]['rows'] if r['status'] != 'dropped'])
        for row in additions:
            outcome = row['status'] if row['status'] in {'ok', 'abstain'} else row['reason']
            require(outcome in expected_counters, 'No supported runtime counter for caller outcome')
            expected_counters[outcome] += 1
        require(counters(sample) == expected_counters, 'Runtime counters differ from scheduled/admitted inventory')
    return sealed({'schemaVersion': 'agat.decision.arrival-rate-verification.v1', 'status': 'pass',
        'planSha256': plan['sha256'], 'resultSha256': result['sha256'], 'sourceFiles': len(sources),
        'scheduled': len(expected), 'warmup': 4, 'phaseSummaries': summaries,
        'qualification': 'not_assessed', 'sloAccepted': False, 'routingEnabled': False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.evidence_dir)
    write_new(args.output, result); args.output.chmod(0o600)
    print(json.dumps({'status': result['status'], 'scheduled': result['scheduled'], 'sourceFiles': result['sourceFiles']}))


if __name__ == '__main__':
    main()
