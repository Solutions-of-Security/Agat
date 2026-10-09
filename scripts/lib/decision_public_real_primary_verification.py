"""Model-free replay of immutable primary/control native receipts."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
from pathlib import Path

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import Request, fields, fingerprint, parse_json, number
from decision_runtime.metrics import outcome
from scripts.lib import decision_arrival_primary as primary
from scripts.lib import decision_public_real_primary as diagnostic
from scripts.lib.decision_performance import profile_from_health, validate_result
from scripts.lib.decision_public_load import validate_context
from scripts.lib.decision_public_load_verification import CONTEXT_PATHS, counters, same, sources_at
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_public_workflow import PROFILE_PATH, shared_config
from scripts.lib.decision_shadow_pilot import require, timestamp


def verify(root, directory, context_path, *, context_sha, plan_sha, result_sha):
    context = validate_context(parse_json(pinned_input(context_path, context_sha, 32*1024*1024)))
    plan = verify_seal(parse_json(pinned_input(directory/'plan.json', plan_sha, 64*1024*1024)), diagnostic.PLAN_SCHEMA)
    result = verify_seal(parse_json(pinned_input(directory/'result.json', result_sha, 64*1024*1024)), diagnostic.RESULT_SCHEMA)
    fields(plan, {'schemaVersion', 'sha256', 'createdAt', 'sourceCommit', 'sourceFiles', 'contextProfileFileSha256', 'context', 'config',
        'runtime', 'manifestFileSha256', 'primary', 'protocol', 'referenceLabels', 'classificationAccuracyMeasured', 'ownersAppointed', 'sloAccepted', 'routingEnabled', 'qualification'})
    fields(result, {'schemaVersion', 'sha256', 'status', 'planSha256', 'evidence', 'warmup', 'samples', 'failure', 'ownedPids', 'remainingOwnedPids',
        'cleanupErrors', 'runtimeExitCode', 'primaryExitCode', 'driverExitCode', 'artifactSha256', 'elapsedMs', 'referenceLabels',
        'classificationAccuracyMeasured', 'ownersAppointed', 'sloAccepted', 'routingEnabled', 'qualification'})
    for value in (plan, result):
        require(type(value['referenceLabels']) is int and value['referenceLabels'] == 0 and value['qualification'] == 'not_assessed'
            and all(value[k] is False for k in ('classificationAccuracyMeasured', 'ownersAppointed', 'sloAccepted', 'routingEnabled')),
            'Diagnostic grants unsupported labels/authority/qualification')
    require(plan['contextProfileFileSha256'] == context_sha and plan['manifestFileSha256'] == context['manifestFileSha256'], 'Context/model pin differs')
    same(plan['context'], context, 'Original context changed'); same(plan['protocol'], diagnostic.PROTOCOL, 'Posthoc primary protocol')
    same(plan['config'], shared_config(context), 'Shadow question/options changed'); same(plan['runtime'], context['tokenizerEnvironment'], 'Runtime dependencies differ')
    require(result['status'] == 'observed' and result['failure'] is None and result['planSha256'] == plan['sha256']
        and result['remainingOwnedPids'] == [] and result['cleanupErrors'] == [] and result['driverExitCode'] == 0
        and type(result['runtimeExitCode']) is int and type(result['primaryExitCode']) is int, 'Run or reported cleanup failed')
    require(isinstance(result['ownedPids'], list) and result['ownedPids'] and len(set(result['ownedPids'])) == len(result['ownedPids'])
        and all(type(pid) is int and 0 < pid < 2**31 for pid in result['ownedPids']), 'Invalid owned PID inventory')
    number(result['elapsedMs'], 0, 3_800_000)
    context_sources = sources_at(root, context['sourceCommit'], context['sourceFiles'], CONTEXT_PATHS)
    sources = sources_at(root, plan['sourceCommit'], plan['sourceFiles'], diagnostic.SOURCE_PATHS)
    require(hashlib.sha256(sources[PROFILE_PATH]).hexdigest() == context['profileFileSha256'], 'Historical profile file differs')
    same(parse_json(sources[PROFILE_PATH]), context['profile'], 'Historical profile semantics differ')
    implementation = hashlib.sha256()
    for name in sorted(n for n in sources if Path(n).parent == Path('decision_runtime') and n.endswith('.py')):
        implementation.update(Path(name).name.encode()+b'\0'+sources[name]+b'\0')
    require(implementation.hexdigest() == context['profile']['model']['implementationSha256'], 'Historical runtime implementation differs')
    requirements = dict(line.split('==') for line in sources['decision_runtime/requirements-mlx.txt'].decode().splitlines() if line and not line.startswith('#'))
    same(plan['runtime'], {'python': '3.13.12', 'machine': 'arm64', 'packages': requirements}, 'Historical runtime pins differ')
    primary_profile = plan['primary']; fields(primary_profile, {'model', 'manifestSha256', 'blobCount', 'blobBytes', 'release', 'releaseFileSha256', 'generation', 'settings', 'warmupRequest'})
    require(primary_profile['model'] == primary.MODEL and primary_profile['manifestSha256'] == primary.DIGEST
        and type(primary_profile['blobCount']) is int and primary_profile['blobCount'] == 5
        and type(primary_profile['blobBytes']) is int and primary_profile['blobBytes'] > 5_000_000_000, 'Primary model artifact differs')
    pin_raw = sources[primary.PRIMARY_SOURCES[2]]
    same(primary_profile['release'], parse_json(pin_raw), 'Pinned native primary release differs')
    require(primary_profile['releaseFileSha256'] == hashlib.sha256(pin_raw).hexdigest(), 'Primary release file pin differs')
    same(primary_profile['generation'], diagnostic.GENERATION, 'Primary generation settings differ')
    same(primary_profile['settings'], diagnostic.SETTINGS, 'Primary residency/concurrency settings differ')
    same(primary_profile['warmupRequest'], diagnostic.WARMUP_REQUEST, 'Primary warmup changed')
    count = len(context['inputs']); names = diagnostic.ARTIFACTS | {f'trace-{i:03d}-{c}.http.json' for i in range(count) for c in ('control', 'shadow')}
    fields(result['artifactSha256'], names)
    artifacts = {name: pinned_input(directory/name, result['artifactSha256'][name], 32*1024*1024) for name in names}
    recipe = parse_json(artifacts['workflow-plan.json']); driver = parse_json(artifacts['workflow-driver.json'])
    evidence = diagnostic.verify_inventory(context, plan['protocol'], recipe, driver, artifacts); same(result['evidence'], evidence, 'Embedded result differs from raw replay')
    require(set(driver['ownedPids']) <= set(result['ownedPids']) and len(set(driver['ownedPids'])) == 2, 'Driver/worker ownership omitted')
    endpoints = recipe['endpoints']; require(set(endpoints) == {'primaryUrl', 'decisionUrl', 'primaryAdapterUrl', 'decisionAdapterUrl', 'coordinatorUrl'}, 'Unexpected endpoints')
    from urllib.parse import urlsplit
    ports = []
    for raw in endpoints.values():
        url = urlsplit(raw); require(url.scheme == 'http' and url.hostname == '127.0.0.1' and url.port
            and url.port not in (8766, 9095, 11434) and url.path == '' and not url.query and not url.fragment and not url.username and not url.password,
            'Run addresses a protected/non-owned endpoint'); ports.append(url.port)
    require(len(set(ports)) == len(ports), 'Owned endpoint collision')
    samples = result['samples']; require(len(samples) == 3, 'Missing physical snapshots')
    values = []; origins = []; elapsed = -1
    for label, sample in zip(('ready_before_scoring', 'after_warmup', 'after_inventory'), samples):
        require(sample['label'] == label and elapsed < number(sample['elapsedMs'], 0, result['elapsedMs'])
            and sample['health']['status'] == 'ready' and profile_from_health(sample['health']) == context['profile'], 'Invalid physical snapshot')
        parsed, origin = counters({k: v for k, v in sample.items() if k not in ('counters', 'serverStart')})
        same(parsed, sample['counters'], 'Counters differ from raw metrics'); same(origin, sample['serverStart'], 'Native origin differs from metrics')
        require(sample['ownedPids'] and all(type(pid) is int and pid in result['ownedPids'] and pid not in driver['ownedPids'] for pid in sample['ownedPids']), 'Unowned physical runtime snapshot')
        values.append(parsed); origins.append(origin); elapsed = sample['elapsedMs']
    require(len(set(origins)) == 1 and all(v == 0 for v in values[0].values()), 'Restarted/nonzero native origin')
    native_origin = datetime.fromtimestamp(origins[0], timezone.utc)
    require(timestamp(context['createdAt'], 'context.createdAt') <= timestamp(plan['createdAt'], 'plan.createdAt') < native_origin
        <= timestamp(recipe['startAt'], 'recipe.startAt'), 'Plan was not fixed before native origin/scoring')
    warmup = result['warmup']; require(len(warmup) == 2, 'Wrong native warmup denominator')
    case = next(c for c in context['inputs'] if c['contextEligible']); expected_warm = Counter()
    for i, row in enumerate(warmup):
        require(row['iteration'] == i and row['caseId'] == case['id'] and row['status'] in ('ok', 'abstain'), 'Warmup identity/result differs')
        response = row['observation']['result']; validate_result(response, Request.from_dict(case['request']), context['profile'])
        require(response['inputTokens'] == case['inputTokens'], 'Warmup context changed'); expected_warm[outcome(response)] += 1
    same(values[1], {key: expected_warm[key] for key in values[1]}, 'Warmup physical calls differ')
    expected_final = expected_warm.copy()
    for row in diagnostic.journal(artifacts['decision-http.jsonl']): expected_final[outcome(parse_json(row['responseBody']))] += 1
    same(values[2], {key: expected_final[key] for key in values[2]}, 'Hidden, lost or extra physical native call')
    require(sum(values[2].values()) == count+2, 'Wrong native terminal denominator')
    primary_warmup = parse_json(artifacts['primary-warmup.json']); same(primary_warmup['request'], diagnostic.WARMUP_REQUEST, 'Raw primary warmup changed')
    primary.validate_response(primary_warmup['response']); number(primary_warmup['wallMs'], 0, 90000)
    for name in ('primary-before.json', 'primary-after.json'):
        entry = parse_json(artifacts[name]); require(entry['version'] == {'version': '0.35.1'}, 'Actual primary version changed')
        tags = [m for m in entry['tags']['models'] if m.get('name') == primary.MODEL]
        models = entry['residence']['models']; require(len(tags) == len(models) == 1 and tags[0]['digest'] == models[0]['digest'] == primary.DIGEST
            and models[0]['context_length'] == 32768 and not tags[0].get('remote_host') and not tags[0].get('remote_model'), 'Primary residence/model/context changed')
        require(entry['ownedPids'] and set(entry['ownedPids']) <= set(result['ownedPids']) and not set(entry['ownedPids']) & set(driver['ownedPids']), 'Unowned primary residence')
    require(timestamp(parse_json(artifacts['primary-before.json'])['capturedAt'], 'primary.before') < timestamp(recipe['startAt'], 'workflow.startAt')
        and timestamp(parse_json(artifacts['primary-after.json'])['capturedAt'], 'primary.after') >= timestamp(driver['actualWindow']['endAt'], 'workflow.endAt'), 'Primary snapshots do not enclose whole workflows')
    return sealed({'schemaVersion': diagnostic.VERIFICATION_SCHEMA, 'status': 'pass', 'verifiedAt': diagnostic.now(),
        'rawFileSha256': {'context': context_sha, 'plan': plan_sha, 'result': result_sha}, 'sourceCommit': plan['sourceCommit'],
        'verifiedSourceFiles': len(sources), 'verifiedContextSourceFiles': len(context_sources), 'artifactCount': len(artifacts),
        'evidence': evidence, 'nativeCalls': count+2, 'primaryScoringCalls': 2*count, 'primaryWarmupCalls': 1,
        'modelCallsDuringVerification': 0, 'referenceLabels': 0, 'ownersAppointed': False, 'sloAccepted': False, 'routingEnabled': False, 'qualification': 'not_assessed'})
