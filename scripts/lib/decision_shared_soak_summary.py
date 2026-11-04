"""Public allowlist for independently verified paired endurance evidence."""
from collections import Counter

from decision_runtime.artifacts import sealed, verify_seal
from scripts.lib.decision_shared_soak_verification import SCHEMA as VERIFIER_SCHEMA, PHASES, distribution, require

SCHEMA = 'agat.decision.shared-soak-public-summary.v1'


def outcomes(rows):
    return {
        'calls': len(rows),
        'statuses': dict(sorted(Counter(row['status'] for row in rows).items())),
        'wallMs': distribution([row['wallMs'] for row in rows]),
        'inputTokens': distribution([row['inputTokens'] for row in rows]),
        'outputTokens': distribution([row['outputTokens'] for row in rows]),
    }


def public_summary(plan, result, verification, blocks):
    verify_seal(plan, 'agat.decision.shared-soak-plan.v1')
    verify_seal(result, 'agat.decision.shared-soak.v1')
    verify_seal(verification, VERIFIER_SCHEMA)
    require(verification['status'] == 'verified' and verification['qualification'] == 'not_assessed'
            and verification['routingEnabled'] is False and verification['planSha256'] == plan['sha256']
            and verification['resultSha256'] == result['sha256']
            and verification['implementationCommit'] == plan['implementationCommit'],
            'Summary requires the matching verified experiment')
    require(result['status'] == 'observed' and result['stoppedReason'] == 'duration_complete'
            and result['qualifiedForRouting'] is False, 'Summary requires completed duration')
    require(len(blocks) == len(result['blocks']) == verification['counts']['blocks'], 'Missing summary blocks')
    rows, warmups, by_block = [], [], []
    by_phase = {name: [] for name in PHASES}
    for index, (entry, block) in enumerate(zip(result['blocks'], blocks)):
        verify_seal(block, 'agat.decision.shared-load.v1')
        require(block['sha256'] == entry['sha256'], 'Summary block checksum changed')
        selected = [row for phase in block['phases'] for row in phase['rows']]
        rows.extend(selected); warmups.extend(block['warmup'])
        for phase in block['phases']: by_phase[phase['name']].extend(phase['rows'])
        by_block.append({'index': index + 1, 'measuredMs': entry['measuredMs'],
                         'status': block['status'], 'stoppedReason': block['stoppedReason'],
                         'models': {kind: outcomes([row for row in selected if row['kind'] == kind])
                                    for kind in ('decision', 'primary')}})
    require(len(rows) == result['measuredAttempts'] == verification['counts']['measuredCalls']
            and len(warmups) == result['separateWarmupCalls'] == verification['counts']['warmupCalls'],
            'Summary request counts changed')
    return sealed({
        'schemaVersion': SCHEMA, 'status': 'observed', 'qualification': 'not_assessed', 'routingEnabled': False,
        'implementationCommit': verification['implementationCommit'],
        'profileSha256': verification['profileSha256'], 'datasetSha256': plan['datasetSha256'],
        'primaryDigest': plan['primaryDigest'], 'requestedMeasuredSeconds': plan['durationSeconds'],
        'maxBlocks': plan['maxBlocks'], 'measuredMs': verification['measuredMs'], 'wallMs': result['wallMs'],
        'nonMeasuredWallMs': round(result['wallMs'] - verification['measuredMs'], 3),
        'counts': {name: verification['counts'][name] for name in (
            'blocks', 'developmentUniqueInputs', 'measuredCalls', 'warmupCalls', 'overlappingPairs', 'pairJournalEvents')},
        'models': {kind: outcomes([row for row in rows if row['kind'] == kind])
                   for kind in ('decision', 'primary')},
        'separateWarmup': {kind: outcomes([row for row in warmups if row['kind'] == kind])
                           for kind in ('decision', 'primary')},
        'phases': {name: {kind: outcomes([row for row in selected if row['kind'] == kind])
                          for kind in ('decision', 'primary')} for name, selected in by_phase.items()},
        'blocks': by_block, 'crossBlockPrimaryChanges': len(result['crossBlockPrimaryChanges']),
        'verificationSha256': verification['sha256'], 'privatePlanSha256': plan['sha256'],
        'privateResultSha256': result['sha256'],
        'limits': [
            'Repeated approved development cases are not independent quality examples.',
            'Pooled nearest-rank percentiles use measured rows; warmup is reported separately.',
            'Closed-loop repeated phases do not establish a fixed arrival rate or production SLO.',
            'Offline verification does not attest resident weights, host resources, supervisor or cleanup.',
        ],
    })
