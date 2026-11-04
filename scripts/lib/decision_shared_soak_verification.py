"""Independently bind shared endurance sources, rows, pair journal and duration."""
import hashlib
import io
import json
import math
import re
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

from decision_runtime.artifacts import read_json, sealed, verify_seal
from decision_runtime.contracts import Request, fingerprint, parse_json
from decision_runtime.evaluation import validate_dataset
from scripts.lib.decision_baselines import development_cases

SCHEMA = 'agat.decision.shared-soak-verification.v1'
PHASES = ('decision_only_before', 'primary_only_before', 'sequential_pair', 'overlapping_pair',
          'primary_only_after', 'decision_only_after')
SOURCES = {'scripts/benchmark-decision-shared-soak.py', 'scripts/lib/decision_shared_soak.py',
           'scripts/benchmark-decision-shared-load.py', 'scripts/lib/decision_shared_load.py',
           'scripts/lib/decision_baselines.py', 'scripts/lib/decision_performance.py', 'workers/local_decisions.py',
           'decision_runtime/artifacts.py', 'decision_runtime/contracts.py', 'decision_runtime/evaluation.py',
           'scripts/verify-decision-shared-soak.py', 'scripts/lib/decision_shared_soak_verification.py'}
TOL = .0011


def require(value, reason):
    if not value: raise ValueError(reason)


def committed_inputs(root, plan):
    verify_seal(plan, 'agat.decision.shared-soak-plan.v1')
    require(re.fullmatch('[0-9a-f]{40}', plan['implementationCommit']), 'Invalid measured commit')
    names = plan['sourceFiles']
    for key in ('datasetPath', 'profilePath'):
        name = plan[key]
        require(name.startswith('docs/') and not name.startswith('docs/private/'), 'Input is not public and committed')
    require(set(names) == SOURCES | {plan['datasetPath'], plan['profilePath']}, 'Incomplete source binding')
    require(all(not PurePosixPath(p).is_absolute() and '..' not in PurePosixPath(p).parts for p in names), 'Unsafe source path')
    raw = subprocess.check_output(['git', 'archive', plan['implementationCommit'], '--', *names], cwd=root, timeout=15)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        sources = {item.name: archive.extractfile(item).read() for item in archive if item.isfile()}
    require({p: hashlib.sha256(v).hexdigest() for p, v in sources.items()} == names, 'Sources differ from measured Git commit')
    data = validate_dataset(parse_json(sources[plan['datasetPath']]))
    development_cases(data)
    profile = parse_json(sources[plan['profilePath']])
    require(data['sha256'] == plan['datasetSha256'] and fingerprint(profile) == plan['profileSha256'], 'Input fingerprint mismatch')
    return data, profile


def distribution(values):
    values = sorted(values)
    return {'count': len(values), 'p50': round(values[math.ceil(len(values) * .5) - 1], 3),
            'p95': round(values[math.ceil(len(values) * .95) - 1], 3), 'max': round(values[-1], 3)} if values else {
                'count': 0, 'p50': None, 'p95': None, 'max': None}


def close(a, b): return math.isclose(a, b, rel_tol=0, abs_tol=TOL)


def verify_measurements(plan, launcher, result, reports, journal, dataset, profile):
    verify_seal(plan, 'agat.decision.shared-soak-plan.v1')
    verify_seal(launcher, 'agat.decision.shared-soak-launcher.v1')
    verify_seal(result, 'agat.decision.shared-soak.v1')
    require(plan['qualification'] == launcher['qualification'] == 'not_assessed'
            and plan['routingEnabled'] is launcher['routingEnabled'] is False, 'Unexpected qualification/routing')
    require(launcher['status'] == 'observed' and launcher['failureType'] is None
            and launcher['planSha256'] == plan['sha256'] and launcher['resultSha256'] == result['sha256']
            and launcher['implementationCommit'] == plan['implementationCommit']
            and launcher['serviceOwnership'] == 'caller' and launcher['servicesStoppedOrRestarted'] is False, 'Launcher is incomplete or failed')
    require(result['status'] == 'observed' and result['stoppedReason'] == 'duration_complete'
            and result['failureType'] is None and result['qualifiedForRouting'] is False, 'Soak is incomplete or failed')
    require(result['decisionProfile'] == profile and result['datasetSha256'] == dataset['sha256']
            and fingerprint(profile) == plan['profileSha256'] and dataset['sha256'] == plan['datasetSha256'], 'Profile/dataset mismatch')
    require(result['plan'] == {'durationSeconds': plan['durationSeconds'], 'maxBlocks': plan['maxBlocks'],
            'roundsPerBlock': 2, 'warmupPairsPerBlock': 2, 'blockBudgetMaxSeconds': 600,
            'lastBlockBudgetMinimumSeconds': 30, 'maxConcurrentCallsPerModel': 1,
            'retry': False, 'stopOnFailure': True, 'runtimeRestarts': 0}, 'Unexpected soak plan')
    require(type(plan['durationSeconds']) is int and 1 <= plan['durationSeconds'] <= 7200
            and type(plan['maxBlocks']) is int and 1 <= plan['maxBlocks'] <= 128, 'Invalid duration/cap')
    requests = [Request.from_dict(c['request']) for c in development_cases(dataset)]
    inputs = {r.id: r.input_sha256 for r in requests};case_ids = list(inputs);expected_cases = case_ids * 2
    require(len(result['blocks']) == len(reports) and 0 < len(reports) <= plan['maxBlocks'], 'Block count mismatch')
    global_signatures = {};global_primary_changes = set();events = [];measured = 0;attempts = 0;warmup_count = 0;overlap_count = 0
    primary_identity = None;last_end = 0
    for index, (entry, report) in enumerate(zip(result['blocks'], reports)):
        verify_seal(report, 'agat.decision.shared-load.v1')
        require(entry['path'] == f'block-{index + 1:03d}.json' and entry['sha256'] == report['sha256']
                and entry['status'] == report['status'] and entry['stoppedReason'] == report['stoppedReason'], 'Block binding mismatch')
        require(entry['startedMs'] >= last_end and entry['finishedMs'] >= entry['startedMs'], 'Block chronology mismatch')
        last_end = entry['finishedMs']
        require(report['status'] == 'observed' and report['stoppedReason'] is None
                or report['status'] == 'degraded' and report['stoppedReason'] == 'time_budget', 'Failed block cannot become a duration boundary')
        require(report['decisionProfile'] == profile and report['decisionProfileStable'] is True
                and report['primaryStable'] is True and report['qualifiedForRouting'] is False, 'Block model/profile changed')
        require(report['dataset']['sha256'] == dataset['sha256'] and report['dataset']['caseIds'] == case_ids, 'Block dataset changed')
        require(set(report['harnessFiles']) == {'scripts/benchmark-decision-shared-load.py',
                'scripts/lib/decision_shared_load.py', 'scripts/lib/decision_performance.py',
                'scripts/lib/decision_baselines.py', 'workers/local_decisions.py'}
                and all(plan['sourceFiles'].get(p) == digest for p, digest in report['harnessFiles'].items()), 'Block harness changed')
        block_plan = report['plan']
        require(block_plan['rounds'] == 2 and block_plan['warmupPairs'] == 2
                and block_plan['expectedMeasuredRequests'] == len(requests) * 16
                and block_plan['maxConcurrentCallsPerModel'] == 1 and block_plan['retry'] is False
                and block_plan['stopOnFailure'] is True and block_plan['pairObservation'] is True
                and block_plan['cooperativeCancellation'] is True and block_plan['expectedProfilePinned'] is True
                and block_plan['decisionTimeoutMs'] == 10000 and 30 <= block_plan['timeBudgetSeconds'] <= 600
                and block_plan['phaseOrder'] == list(PHASES), 'Unexpected block plan')
        if report['stoppedReason'] == 'time_budget':
            require(report['elapsedMs'] + TOL >= block_plan['timeBudgetSeconds'] * 1000, 'Block budget did not expire')
        if primary_identity is None: primary_identity = report['primary']
        require(report['primary'] == primary_identity and report['primary']['digest'] == plan['primaryDigest'], 'Primary identity changed')
        require(len(report['primaryResidence']) == len(report['phases']) + 1
                and all(p['status'] == 'resident' and p['digest'] == plan['primaryDigest'] for p in report['primaryResidence']), 'Primary was not resident')
        warmups = report['warmup'];warmup_count += len(warmups)
        require(len(warmups) in (2, 4), 'Partial or missing warmup pair')
        previous_end = 0
        for i in range(0, len(warmups), 2):
            rows = warmups[i:i + 2]
            require([r['kind'] for r in rows] == ['primary', 'decision'] and all(r['caseId'] == case_ids[(i // 2) % len(case_ids)] for r in rows), 'Warmup order changed')
            require(rows[0]['startedMs'] + TOL >= previous_end
                    and rows[1]['startedMs'] + TOL >= rows[0]['finishedMs'], 'Warmup chronology changed')
            previous_end = rows[1]['finishedMs']
            events.append({'block': index, 'phase': 'warmup', 'pairIndex': i // 2 + 1, 'rows': rows})
        require([p['name'] for p in report['phases']] == list(PHASES[:len(report['phases'])]), 'Phase prefix mismatch')
        if report['status'] == 'observed': require(len(report['phases']) == 6 and len(warmups) == 4, 'Observed block is incomplete')
        local_signatures = {};local_changes = {'decision': set(), 'primary': set()}
        all_rows = warmups + [r for p in report['phases'] for r in p['rows']]
        for row in all_rows:
            require(row['caseId'] in inputs and row['inputSha256'] == inputs[row['caseId']] and row['status'] in ('ok', 'abstain'), 'Failed or changed input row')
            require(all(math.isfinite(row[k]) and row[k] >= 0 for k in ('startedMs', 'finishedMs', 'wallMs'))
                    and row['finishedMs'] >= row['startedMs'] and close(row['wallMs'], row['finishedMs'] - row['startedMs'])
                    and row['finishedMs'] <= report['elapsedMs'] + TOL, 'Invalid row duration')
            require(type(row['inputTokens']) is type(row['outputTokens']) is int and row['inputTokens'] > 0, 'Invalid token count')
            require(row['inputTokens'] <= (profile['model'].get('maxInputTokens', 8192) if row['kind'] == 'decision' else 8192), 'Input token bound changed')
            require(re.fullmatch('[0-9a-f]{64}', row['decisionSha256']), 'Invalid decision signature')
            require(row['outputTokens'] == 0 if row['kind'] == 'decision' else 0 < row['outputTokens'] <= 128, 'Output token bound changed')
            key = (row['kind'], row['inputSha256'])
            if local_signatures.setdefault(key, row['decisionSha256']) != row['decisionSha256']: local_changes[row['kind']].add(row['caseId'])
            if global_signatures.setdefault(key, row['decisionSha256']) != row['decisionSha256']:
                require(row['kind'] == 'primary', 'Decision changed across blocks');global_primary_changes.add(row['caseId'])
        require(report['repeatDecisionChanges'] == {k: sorted(v) for k, v in local_changes.items()} and not local_changes['decision'], 'Repeat signature mismatch')
        for phase_index, phase in enumerate(report['phases']):
            paired = phase['name'] in ('sequential_pair', 'overlapping_pair');step = 2 if paired else 1
            rows = phase['rows'];expected_kind = 'decision' if phase['name'].startswith('decision_only') else 'primary'
            expected = [(case, kind) for case in expected_cases for kind in (('primary', 'decision') if paired else (expected_kind,))]
            require(len(rows) % step == 0 and [(r['caseId'], r['kind']) for r in rows] == expected[:len(rows)], 'Row order/prefix mismatch')
            if report['status'] == 'observed' or phase_index < len(report['phases']) - 1: require(len(rows) == len(expected), 'Completed phase is incomplete')
            attempts += len(rows)
            require(len(phase['pairs']) == len(rows) // 2 if paired else phase['pairs'] == [], 'Pair count mismatch')
            for i in range(0, len(rows), step):
                group = rows[i:i + step];events.append({'block': index, 'phase': phase['name'], 'pairIndex': i // step + 1, 'rows': group})
                require(min(r['startedMs'] for r in group) + TOL >= previous_end, 'Pair chronology changed')
                previous_end = max(r['finishedMs'] for r in group)
                if paired:
                    a, b = group;pair = phase['pairs'][i // 2]
                    overlap = max(0, min(a['finishedMs'], b['finishedMs']) - max(a['startedMs'], b['startedMs']))
                    require(pair['caseId'] == a['caseId'] == b['caseId'] and close(pair['requestOverlapMs'], overlap)
                            and close(pair['elapsedMs'], max(a['finishedMs'], b['finishedMs']) - min(a['startedMs'], b['startedMs'])), 'Pair interval mismatch')
                    require(overlap == 0 if phase['name'] == 'sequential_pair' else overlap > 0, 'Wrong HTTP overlap')
                    overlap_count += phase['name'] == 'overlapping_pair'
            for kind in ('decision', 'primary'):
                selected = [r for r in rows if r['kind'] == kind];summary = phase['summary'][kind]
                require(summary['attempts'] == summary['computed'] == len(selected) and summary['failures'] == {}, 'Outcome summary mismatch')
                for field, key in (('wallMs', 'computedWallMs'), ('inputTokens', 'inputTokens'), ('outputTokens', 'outputTokens')):
                    require(summary[key] == distribution([r[field] for r in selected]), 'Independent distribution mismatch')
                require(summary['failedWallMs'] == distribution([]), 'Failed rows hidden in summary')
        elapsed = report['elapsedMs'] - max(r['finishedMs'] for r in warmups) if len(all_rows) > len(warmups) else 0
        require(elapsed >= 0 and close(entry['measuredMs'], elapsed), 'Warmup exclusion mismatch')
        measured += elapsed
        require(entry['finishedMs'] - entry['startedMs'] + TOL >= report['elapsedMs'], 'Block wall time shorter than benchmark')
    require(events == journal, 'Pair journal does not exactly match reports')
    require(close(result['measuredMs'], measured) and measured + TOL >= plan['durationSeconds'] * 1000
            and result['wallMs'] + TOL >= last_end and result['wallMs'] + TOL >= measured, 'Duration target incomplete')
    require(result['measuredAttempts'] == attempts and result['separateWarmupCalls'] == warmup_count
            and attempts > 0 and overlap_count > 0, 'Request totals or overlapping work missing')
    require(result['crossBlockDecisionChanges'] == [] and result['crossBlockPrimaryChanges'] == sorted(global_primary_changes), 'Global signature summary mismatch')
    return sealed({'schemaVersion': SCHEMA, 'status': 'verified', 'qualification': 'not_assessed', 'routingEnabled': False,
                   'implementationCommit': plan['implementationCommit'], 'planSha256': plan['sha256'], 'resultSha256': result['sha256'],
                   'profileSha256': fingerprint(profile), 'measuredMs': round(measured, 3),
                   'counts': {'blocks': len(reports), 'developmentUniqueInputs': len(inputs), 'measuredCalls': attempts,
                              'warmupCalls': warmup_count, 'overlappingPairs': overlap_count, 'pairJournalEvents': len(events)},
                   'checks': {'sealedBindings': True, 'inputRowsAndOrder': True, 'independentDistributions': True,
                              'httpIntervals': True, 'journalExact': True, 'durationWithoutWarmup': True,
                              'stableDecisionAndModelProfile': True, 'primaryResident': True}})


def verify_run(directory, plan, dataset, profile):
    launcher = read_json(directory / 'launcher.json');result = read_json(directory / 'result.json')
    reports = [read_json(directory / f'block-{i + 1:03d}.json') for i in range(len(result['blocks']))]
    raw = (directory / 'pairs.jsonl').read_bytes()
    require(hashlib.sha256(raw).hexdigest() == launcher['journalSha256'], 'Journal hash mismatch')
    proof = verify_measurements(plan, launcher, result, reports, [parse_json(line) for line in raw.splitlines()], dataset, profile)
    return sealed({**{k: v for k, v in proof.items() if k != 'sha256'},
                   'launcherSha256': hashlib.sha256((directory / 'launcher.json').read_bytes()).hexdigest(),
                   'journalSha256': launcher['journalSha256']})
