"""Public allowlist for an independently verified continuous soak."""
from decision_runtime.artifacts import sealed, verify_seal
from scripts.lib.decision_endurance import SCHEMA as BLOCK_SCHEMA
from scripts.lib.decision_performance import summarize
from scripts.lib.decision_soak_verification import LAUNCHER_SCHEMA, PLAN_SCHEMA, VERIFIER_SCHEMA, require

SCHEMA = 'agat.decision.continuous-soak-public-summary.v1'


def public_summary(plan, launcher, verification, blocks):
    verify_seal(plan, PLAN_SCHEMA); verify_seal(launcher, LAUNCHER_SCHEMA)
    verify_seal(verification, VERIFIER_SCHEMA)
    require(verification['status'] == 'pass' and verification['launcherSha256'] == launcher['sha256']
            and verification['planSha256'] == plan['sha256'], 'Summary requires the matching verified experiment')
    report = launcher['observed']
    require(len(blocks) == len(report['blocks']) == verification['blocks'], 'Missing summary blocks')
    rows, by_block = [], []
    warmup_ms = 0
    for index, block in enumerate(blocks):
        verify_seal(block, BLOCK_SCHEMA)
        require(block['sha256'] == report['blocks'][index]['sha256'], 'Summary block checksum changed')
        rows.extend(block['rows']); warmup_ms += block['warmupElapsedMs']
        by_block.append({'index': index, 'measuredMs': block['elapsedMs'], 'attempts': len(block['rows']),
                         'scoredWallMs': block['summary']['scoredWallMs'], 'runtimeMs': block['summary']['runtimeMs'],
                         'statuses': block['summary']['statuses'], 'failures': block['summary']['failures']})
    require(len(rows) == verification['attempts'], 'Summary request counts changed')
    aggregate = summarize(rows, verification['measuredMs'])
    return sealed({'schemaVersion': SCHEMA, 'implementationCommit': verification['implementationCommit'],
        'profileSha256': verification['profileSha256'], 'runtimeVersion': plan['profile']['runtimeVersion'],
        'modelArtifactSha256': plan['model']['artifactSha256'], 'datasetSha256': verification['datasetSha256'],
        'status': 'observed', 'requestedSeconds': plan['durationSeconds'], 'measuredMs': verification['measuredMs'],
        'wallMs': report['wallMs'], 'separateWarmupCalls': verification['separateWarmupCalls'],
        'separateWarmupMs': round(warmup_ms, 3), 'nonMeasuredWallMs': round(report['wallMs']-verification['measuredMs'], 3),
        'summary': aggregate, 'blocks': by_block, 'processIdentityStable': verification['processIdentityStable'],
        'cleanupVerified': verification['cleanupVerified'], 'verificationSha256': verification['sha256'],
        'privatePlanSha256': plan['sha256'], 'privateLauncherSha256': launcher['sha256'],
        'qualification': 'not_assessed', 'routingEnabled': False,
        'limits': ['Repeated approved development cases are not independent quality examples.',
                   'Closed-loop concurrency one on one runtime; no generator, embeddings, coordinator or production SLO.',
                   'Pooled percentiles are calculated from measured rows; warmup is excluded.',
                   'System resources and process IDs remain in private evidence.']})
