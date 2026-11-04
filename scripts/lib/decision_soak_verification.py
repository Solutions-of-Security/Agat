"""Offline binding and measurement checks for a completed owned soak experiment."""
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import Policy, Request, canonical_json, fingerprint, number
from scripts.lib.decision_baselines import development_cases
from scripts.lib.decision_endurance import SCHEMA as BLOCK_SCHEMA
from scripts.lib.decision_performance import profile_from_health, summarize
from scripts.lib.decision_soak import SCHEMA, validate_plan

SOURCE_PATHS = ['decision_runtime', 'workers/local_decisions.py', 'scripts/run-decision-soak.py',
                'scripts/run-temporal-real-rag.py', 'scripts/profile-embedding-rag.py',
                'scripts/lib/decision_soak.py', 'scripts/lib/decision_endurance.py',
                'scripts/lib/decision_baselines.py', 'scripts/lib/decision_performance.py',
                'scripts/lib/decision_soak_verification.py', 'scripts/verify-decision-soak.py',
                'scripts/benchmark-decision-endurance.py']
PLAN_SCHEMA = 'agat.decision.continuous-soak-plan.v1'
LAUNCHER_SCHEMA = 'agat.decision.continuous-soak-launcher.v1'
VERIFIER_SCHEMA = 'agat.decision.continuous-soak-verification.v1'


def require(value, reason):
    if not value:
        raise ValueError(reason)


def committed_sources(root, plan):
    require(re.fullmatch('[0-9a-f]{40}', plan['implementationCommit']), 'Invalid measured commit')
    paths = list(plan['sourceSha256'])
    require(0 < len(paths) <= 2000 and all(isinstance(p, str) and not PurePosixPath(p).is_absolute()
            and '..' not in PurePosixPath(p).parts for p in paths), 'Invalid measured source paths')
    raw = subprocess.check_output(['git', 'archive', plan['implementationCommit'], '--', *paths],
                                  cwd=root, timeout=15)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        sources = {item.name: archive.extractfile(item).read() for item in archive if item.isfile()}
    require({p: hashlib.sha256(v).hexdigest() for p, v in sources.items()} == plan['sourceSha256'],
            'Measured source snapshot differs from Git')
    require(all(p in sources or any(k.startswith(p + '/') for k in sources) for p in SOURCE_PATHS),
            'Incomplete measured source snapshot')
    digest = hashlib.sha256()
    for name in sorted(p for p in sources if PurePosixPath(p).parent == PurePosixPath('decision_runtime') and p.endswith('.py')):
        digest.update(PurePosixPath(name).name.encode() + b'\0' + sources[name] + b'\0')
    require(digest.hexdigest() == plan['profile']['model']['implementationSha256'], 'Runtime implementation changed')
    requirements = dict(line.split('==') for line in sources['decision_runtime/requirements-mlx.txt'].decode().splitlines()
                        if line and not line.startswith('#'))
    require(plan['runtime']['packages'] == requirements, 'Runtime package set changed')
    for key in ('datasetPath', 'profilePath', 'policyPath'):
        name = plan[key]
        require(name.startswith('docs/') and not name.startswith('docs/private/') and name in sources, 'Input is not committed')
    require(json.loads(sources[plan['profilePath']]) == plan['profile'], 'Profile differs from its committed input')
    policy = Policy.from_dict(json.loads(sources[plan['policyPath']])).to_dict()
    require(plan['profile']['policy'] == {**policy, 'sha256': fingerprint(policy)}, 'Policy differs from its committed input')
    name = plan['datasetPath']
    require(name.startswith('docs/') and not name.startswith('docs/private/') and name in sources, 'Dataset is not committed')
    dataset = json.loads(sources[name])
    return dataset


def matching_summary(actual, expected):
    # Timestamps are serialized to 0.001 ms; allow that rounding in derived rates.
    actual, expected = dict(actual), dict(expected)
    for key in ('elapsedMs', 'completedPerSecond'):
        if not math.isclose(actual.pop(key), expected.pop(key), rel_tol=0, abs_tol=.0011):
            return False
    return actual == expected


def verify_run(directory, plan, launcher, dataset):
    """Checks saved reports, never contacts or starts a model. Only complete runs pass."""
    verify_seal(plan, PLAN_SCHEMA); verify_seal(launcher, LAUNCHER_SCHEMA)
    require(launcher['status'] == 'pass' and launcher['failure'] is None and launcher['cancelled'] is False
            and launcher['cleanupErrors'] == [] and launcher['remainingOwnedPids'] == [], 'Run did not complete with verified cleanup')
    require(plan['qualification'] == launcher['qualification'] == 'not_assessed'
            and plan['routingEnabled'] is launcher['routingEnabled'] is False, 'Qualification boundary changed')
    require(launcher['planSha256'] == hashlib.sha256((directory / 'plan.json').read_bytes()).hexdigest(), 'Plan checksum changed')
    profile = plan['profile']
    profile_from_health({'status': 'ready', 'mode': 'shadow', 'profileJson': canonical_json(profile),
                         'profileSha256': plan['profileSha256']})
    require(all(profile['model'][key] == plan['model'][key] for key in ('repository', 'revision', 'artifactSha256')),
            'Pinned model changed')
    validate_plan(dataset, plan['durationSeconds'], plan['blockSeconds'])
    require(dataset['sha256'] == plan['datasetSha256'], 'Pinned dataset changed')
    cases = [Request.from_dict(case['request']) for case in development_cases(dataset)]
    report = verify_seal(launcher['observed'], SCHEMA)
    count = math.ceil(plan['durationSeconds'] / plan['blockSeconds'])
    require(report['status'] == 'observed' and report['stoppedReason'] == 'duration_complete'
            and report['qualifiedForRouting'] is False and report['crossBlockDecisionChanges'] == [], 'Soak was incomplete or changed decisions')
    require(report['plan'] == {'durationSeconds': plan['durationSeconds'], 'blockSeconds': plan['blockSeconds'],
            'blocks': count, 'concurrency': 1, 'retry': False, 'runtimeRestarts': 0}
            and len(report['blocks']) == count and report['profileSha256'] == plan['profileSha256']
            and report['datasetSha256'] == dataset['sha256'], 'Soak binding changed')
    owned = launcher['ownedPids']
    require(len(owned) in (2, 3, 4) and len(set(owned)) == len(owned)
            and all(type(pid) is int and pid > 0 for pid in owned), 'Invalid owned process set')
    expected_journal, signatures, identities = [], {}, {}
    attempts, warmups, measured_ms, last_finished = 0, 0, 0, 0
    for index, saved in enumerate(report['blocks']):
        name = f'block-{index + 1:03d}.json'
        require(saved['path'] == name, 'Invalid block order or path')
        block = verify_seal(json.loads((directory / name).read_bytes()), BLOCK_SCHEMA)
        duration = min(plan['blockSeconds'], plan['durationSeconds'] - index * plan['blockSeconds'])
        require(saved['sha256'] == block['sha256'] and saved['status'] == block['status'] == 'observed'
                and saved['stoppedReason'] == block['stoppedReason'] == 'duration_complete', 'Invalid block completion')
        require(block['profile'] == profile and block['profileSha256'] == plan['profileSha256']
                and block['dataset']['sha256'] == dataset['sha256'] and block['dataset']['usage'] == 'development-only'
                and block['dataset']['caseIds'] == [r.id for r in cases]
                and block['qualifiedForRouting'] is False and block['repeatDecisionChanges'] == [], 'Invalid block binding')
        require(block['plan'] == {'durationSeconds': duration, 'windowSeconds': min(30, duration),
                'maxRequests': 10000, 'warmup': 3 if index == 0 else 0, 'timeoutMs': 10000,
                'processPids': owned, 'concurrency': 1, 'arrivalPattern': 'closed loop; next request after prior completion',
                'retry': False, 'stopOnFirstFailure': True}, 'Invalid block plan')
        harness = ['scripts/benchmark-decision-endurance.py', 'scripts/lib/decision_endurance.py',
                   'scripts/lib/decision_performance.py', 'scripts/lib/decision_baselines.py', 'workers/local_decisions.py']
        require(block['harnessFiles'] == {p: plan['sourceSha256'][p] for p in harness}, 'Block harness changed')
        elapsed = number(block['elapsedMs'], duration * 1000, 10_000_000)
        begin, end = number(saved['startedMs'], last_finished, 10_000_000), number(saved['finishedMs'], 0, 10_000_000)
        require(end >= begin + elapsed + block['warmupElapsedMs'] - 2 and end <= report['wallMs'], 'Invalid block wall interval')
        last_finished = end
        require(len(block['warmup']) == (3 if index == 0 else 0) and 0 < len(block['rows']) <= 10000, 'Invalid request counts')
        for phase, rows in [('warmup', block['warmup']), ('measured', block['rows'])]:
            previous_end = 0
            for offset, row in enumerate(rows):
                request = cases[offset % len(cases)]
                require(row['index'] == offset and row['caseId'] == request.id and row['inputSha256'] == request.input_sha256
                        and row['status'] in ('ok', 'abstain') and re.fullmatch('[0-9a-f]{64}', row['decisionSha256']), 'Invalid measured row')
                start = number(row['startedMs'], max(0, previous_end - .01), 10_000_000)
                wall = number(row['wallMs'], 0, 10_000_000)
                previous_end = start + wall
                number(row['runtimeMs'], 0, 3_600_000)
                require(type(row['inputTokens']) is int and row['inputTokens'] > 0, 'Invalid measured token count')
                require(signatures.setdefault(request.input_sha256, row['decisionSha256']) == row['decisionSha256'], 'Decision changed across blocks')
                expected_journal.append({'block': index, 'phase': phase, 'row': row})
            require(previous_end <= (elapsed if phase == 'measured' else block['warmupElapsedMs']) + 2, 'Row exceeds block interval')
        require(matching_summary(block['summary'], summarize(block['rows'], elapsed)), 'Block summary mismatch')
        offset, window_end = 0, 0
        for window_index, window in enumerate(block['windows']):
            end = window['finishedMs']
            require(window['index'] == window_index and window['startedMs'] == window_end
                    and window_end < end <= elapsed, 'Invalid measurement window')
            window_rows = []
            while offset < len(block['rows']) and block['rows'][offset]['startedMs'] + block['rows'][offset]['wallMs'] <= end + .01:
                window_rows.append(block['rows'][offset]); offset += 1
            require(matching_summary(window['summary'], summarize(window_rows, end - window_end)), 'Window summary mismatch')
            window_end = end
        require(offset == len(block['rows']), 'Rows missing from windows')
        require(block['samples'][0]['phase'] == 'after_warmup' and block['samples'][-1]['phase'] == 'finished', 'Missing boundary samples')
        for sample in block['samples']:
            memory = sample['memory']
            require(sample['healthProfileStable'] is True and memory['available'] is True and memory['kind'] == 'ps-rss'
                    and [p['pid'] for p in memory['processes']] == owned, 'Missing stable process observation')
            for process in memory['processes']:
                require(process['available'] is True and type(process['rssBytes']) is int and process['rssBytes'] >= 0, 'Invalid process sample')
                identity = (process['parentPid'], process['processStart'])
                require(identities.setdefault(process['pid'], identity) == identity, 'Owned process replaced')
        attempts += len(block['rows']); warmups += len(block['warmup']); measured_ms += elapsed
    require(report['attempts'] == attempts and report['separateWarmupCalls'] == warmups
            and report['measuredMs'] == measured_ms >= plan['durationSeconds'] * 1000, 'Soak summary mismatch')
    for name in ('observations.jsonl', 'runtime.log'):
        require(launcher['logSha256'][name] == hashlib.sha256((directory / name).read_bytes()).hexdigest(), 'Private log checksum changed')
    journal = [json.loads(line) for line in (directory / 'observations.jsonl').read_text().splitlines()]
    require(journal == expected_journal, 'Observation journal differs from saved blocks')
    return sealed({'schemaVersion': VERIFIER_SCHEMA, 'status': 'pass', 'implementationCommit': plan['implementationCommit'],
                   'planSha256': plan['sha256'], 'launcherSha256': launcher['sha256'], 'profileSha256': plan['profileSha256'],
                   'datasetSha256': dataset['sha256'], 'blocks': count, 'attempts': attempts, 'separateWarmupCalls': warmups,
                   'measuredMs': measured_ms, 'processIdentityStable': True, 'cleanupVerified': True,
                   'qualification': 'not_assessed', 'routingEnabled': False})
