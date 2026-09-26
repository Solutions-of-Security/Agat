#!/usr/bin/env python3
"""Independently check the recorded Prometheus wire format and request ledger."""

import argparse
import hashlib
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime import VERSION, implementation_sha256
from decision_runtime.artifacts import read_json, sealed, verify_seal, write_new
from decision_runtime.contracts import Request, fingerprint
from scripts.lib.decision_performance import validate_result

PARSER_SHA256 = 'fa93d06737aa02bacd05794768508bb97d2fbee28cb3bca04eaae92f0ca953d6'
OUTCOMES = ('ok', 'abstain', 'busy', 'invalid', 'profile_mismatch', 'context_rejected',
            'backend_error', 'timeout', 'cancelled', 'unavailable')
GROUPS = {'computed': ('ok', 'abstain'), 'rejected': ('busy', 'invalid', 'profile_mismatch', 'context_rejected'),
          'failed': ('backend_error', 'timeout', 'cancelled', 'unavailable')}
BOUNDARIES = ('0.05', '0.1', '0.25', '0.5', '1', '2', '5', '10', '+Inf')
PREFIX = 'agat_decision_'


def require(value, message):
    if not value:
        raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--source-plan', type=Path, required=True)
    parser.add_argument('--parser-wheel', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(hashlib.sha256(args.parser_wheel.read_bytes()).hexdigest() == PARSER_SHA256, 'Parser changed')
    sys.path.insert(0, str(args.parser_wheel.resolve()))
    from prometheus_client.parser import text_string_to_metric_families
    plan = verify_seal(read_json(args.evidence_dir / 'metrics-plan.json'), 'agat.decision.metrics-plan.v1')
    result = verify_seal(read_json(args.evidence_dir / 'metrics-result.json'), 'agat.decision.metrics-probe.v1')
    source = verify_seal(read_json(args.source_plan), 'agat.decision.synthetic-robustness-plan.v1')
    profile = plan['profile']
    require(profile['runtimeVersion'] == VERSION and profile['model']['implementationSha256'] == implementation_sha256(),
            'Runtime changed; use the recorded implementation')
    require(plan['profileSha256'] == fingerprint(profile) and plan['sourcePlanSha256'] == source['sha256'], 'Profile/source mismatch')
    require(plan['parser'] == {'name': 'prometheus-client', 'version': '0.26.0', 'wheelSha256': PARSER_SHA256}, 'Parser identity mismatch')
    require(result['planSha256'] == plan['sha256'] and result['status'] == 'observed' and result['failure'] is None,
            'Incomplete or unbound result')
    require(result['routingEnabled'] is False and result['qualifiedForRouting'] is False, 'Unexpected routing claim')
    for name, digest in plan['harnessFiles'].items():
        path = (ROOT / name).resolve()
        require(path.is_relative_to(ROOT) and hashlib.sha256(path.read_bytes()).hexdigest() == digest, 'Frozen harness changed')
    requests = {}
    for case in plan['cases']:
        request = Request.from_dict(case['request'])
        original = next(c for c in source['cases'] if c['request']['id'] == request.id)
        require(request.to_dict() == original['request'] and request.input_sha256 == case['inputSha256'] == original['inputSha256']
                and case['inputTokens'] == original['targetTokens'], 'Input changed or truncated')
        requests[request.id] = (request, case['inputTokens'])
    require([r['phase'] for r in result['responses']] == ['invalid', 'profile_mismatch', 'computed',
            'busy_while_computing', 'computed_long', 'unavailable'], 'Missing or duplicated response')
    expected_http = {'invalid': (400, 'invalid_request'), 'profile_mismatch': (409, 'profile_mismatch'),
                     'computed': (200, 'accepted'), 'busy_while_computing': (503, 'busy'),
                     'computed_long': (200, 'accepted'), 'unavailable': (500, 'backend_unavailable')}
    for row in result['responses']:
        answer = row['result']
        require((row['httpStatus'], answer['reason']) == expected_http[row['phase']], 'Unexpected HTTP outcome')
        require(all(answer.get(k) == v for k, v in profile.items()) and answer['mode'] == 'shadow', 'Response profile mismatch')
        if answer['id'] is not None:
            request, tokens = requests[answer['id']]
            validate_result(answer, request, profile)
            if answer['status'] != 'error':
                require(answer['inputTokens'] == tokens, 'Input truncated')
        else:
            require(row['phase'] in ('invalid', 'profile_mismatch', 'busy_while_computing')
                    and answer['status'] == 'error' and answer['inputSha256'] is None
                    and answer['distribution'] == [] and answer['value'] is None and answer['selectedOptionId'] is None,
                    'Unexpected unbound response')
    require(result['workerOutcome'] == {'status': 'unavailable', 'reason': 'timeout'}, 'Worker cancellation path missing')
    phases = ['initial', 'computed', 'in_progress', 'busy_while_computing', 'computed_long', 'cancelled', 'unavailable']
    require([s['phase'] for s in result['snapshots']] == phases, 'Missing or duplicated metric snapshot')
    expected = dict.fromkeys(OUTCOMES, 0)
    previous = None
    for index, snapshot in enumerate(result['snapshots']):
        phase = snapshot['phase']
        if phase == 'computed': expected.update(ok=1, invalid=1, profile_mismatch=1)
        elif phase == 'busy_while_computing': expected['busy'] = 1
        elif phase == 'computed_long': expected['ok'] = 2
        elif phase == 'cancelled': expected['cancelled'] = 1
        elif phase == 'unavailable': expected['unavailable'] = 1
        raw = snapshot['raw']
        families = list(text_string_to_metric_families(raw))
        parsed = [{'name': s.name, 'labels': s.labels, 'value': s.value} for f in families for s in f.samples]
        require(parsed == snapshot['samples'] and len(families) == 5 and len(parsed) == 46 and raw.endswith('\n'), 'Invalid export')
        rows = {(s['name'], tuple(sorted(s['labels'].items()))): s['value'] for s in parsed}
        require(len(rows) == 46 and all(math.isfinite(v) and v >= 0 for v in rows.values()), 'Duplicate/invalid sample')
        def key(name, **labels): return (PREFIX + name, tuple(sorted(labels.items())))
        def get(name, **labels): return rows[key(name, **labels)]
        expected_keys = {key(name) for name in ('backend_ready', 'requests_in_progress', 'server_start_time_seconds')}
        expected_keys.update(key('requests_total', outcome=name) for name in OUTCOMES)
        for group, outcomes in GROUPS.items():
            count = sum(expected[o] for o in outcomes)
            expected_keys.update(key('request_duration_seconds_' + suffix, **{'class': group}) for suffix in ('sum', 'count'))
            expected_keys.update(key('request_duration_seconds_bucket', **{'class': group, 'le': le}) for le in BOUNDARIES)
            buckets = [get('request_duration_seconds_bucket', **{'class': group, 'le': le}) for le in BOUNDARIES]
            require(buckets == sorted(buckets) and all(v.is_integer() for v in buckets) and buckets[-1] == count
                    and get('request_duration_seconds_count', **{'class': group}) == count, 'Invalid histogram count/buckets')
            total = get('request_duration_seconds_sum', **{'class': group})
            # The sum must fit the observations' bucket ranges, independently of renderer code.
            lower = sum((buckets[i] - (buckets[i - 1] if i else 0)) * (float(BOUNDARIES[i - 1]) if i else 0)
                        for i in range(len(BOUNDARIES)))
            upper = sum((buckets[i] - (buckets[i - 1] if i else 0)) * float(BOUNDARIES[i]) for i in range(8))
            require(total >= lower - 1e-10 and (buckets[-1] != buckets[-2] or total <= upper + 1e-10), 'Histogram sum outside bounds')
        require(set(rows) == expected_keys, 'Unexpected labels or sample names')
        require(all(get('requests_total', outcome=name) == count for name, count in expected.items()), 'Request ledger mismatch')
        require(get('backend_ready') == (0 if index >= 5 else 1)
                and get('requests_in_progress') == (1 if index in (2, 3) else 0), 'Incorrect readiness or in-progress state')
        require(all(request.id not in raw and request.state not in raw and request.question not in raw
                    for request, _ in requests.values()), 'Input leaked into export')
        if previous:
            require(snapshot['elapsedMs'] > result['snapshots'][index - 1]['elapsedMs'], 'Nonmonotonic timeline')
            for name, value in rows.items():
                if name[0] not in (PREFIX + 'backend_ready', PREFIX + 'requests_in_progress'):
                    require(value >= previous[name], 'Counter/histogram decreased without restart')
            require(get('server_start_time_seconds') == previous[key('server_start_time_seconds')], 'Unexpected server restart')
        previous = rows
    require(expected == plan['expectedFinalCounters'], 'Plan ledger mismatch')
    diagnostics = result['backendDiagnostics']
    require(diagnostics['stopReason'] == 'inference_cancelled' and diagnostics['childExitCode'] is not None
            and diagnostics['available'] is False and result['inferenceChildGone'] is True, 'Backend retirement missing')
    pids = [result['managerPid'], diagnostics['childPid']]
    for pid in pids:
        try: os.kill(pid, 0)
        except ProcessLookupError: continue
        raise ValueError(f'Owned PID {pid} exists; inspect before claiming cleanup')
    report = sealed({'schemaVersion': 'agat.decision.metrics-verification.v1', 'createdAt': datetime.now(timezone.utc).isoformat(),
                     'status': 'verified', 'planSha256': plan['sha256'], 'resultSha256': result['sha256'],
                     'profileSha256': fingerprint(profile), 'runtimeVersion': VERSION, 'implementationSha256': implementation_sha256(),
                     'parserWheelSha256': PARSER_SHA256, 'snapshots': len(phases), 'familiesPerSnapshot': 5, 'samplesPerSnapshot': 46,
                     'verifiedFinalCounters': expected, 'verifiedGonePids': pids,
                     'verifierSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                     'checks': {'wireParsedIndependently': True, 'requestLedgerMatches': True, 'fixedLabels': True,
                                'histogramCountsBoundsAndSums': True, 'busyIsSeparateFromComputed': True,
                                'profileAndInputBound': True, 'readinessAfterCancellation': True},
                     'limitations': ['Small synthetic execution probe; no model-quality, fleet-load or production-SLO acceptance.']})
    write_new(args.output, report)
    print({k: report[k] for k in ('status', 'snapshots', 'samplesPerSnapshot', 'verifiedGonePids')})


if __name__ == '__main__': main()
