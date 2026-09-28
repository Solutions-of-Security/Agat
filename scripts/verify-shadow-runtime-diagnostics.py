#!/usr/bin/env python3
"""Verify the predeclared three-run shadow resource experiment without inference."""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('temporal_diagnostics_verifier', ROOT / 'scripts/verify-temporal-real-rag.py')
verifier = importlib.util.module_from_spec(spec); spec.loader.exec_module(verifier)


def verify(directory, original_evidence_path=None):
    if not __debug__: raise RuntimeError('Assertions must be enabled')
    directory = directory.resolve(); assert directory.is_relative_to(ROOT / 'docs')
    archived_directory = Path(original_evidence_path) if original_evidence_path is not None else directory.relative_to(ROOT)
    assert not archived_directory.is_absolute() and archived_directory.parts[0] == 'docs'
    assert '..' not in archived_directory.parts
    experiment_path = directory / 'experiment.json'; experiment = json.loads(experiment_path.read_text())
    assert experiment['schema'] == 'agat.shadow-resource-experiment.v1'
    assert experiment['runs'] == ['run-1', 'run-2', 'run-3']
    assert experiment['protocol'] == 'agat.temporal.real-rag-plan.v4'
    assert experiment['inferenceDeadlineMs'] == 5000 and experiment['workloadBudgetSecondsPerRun'] == 600
    assert experiment['concurrency'] == 1 and experiment['keepAllFailedRuns'] is True
    assert experiment['systemSettingsChanged'] is False and experiment['qualityQualification'] == 'not_assessed'
    results, commits, signatures, vectors, all_owned, all_sampled = [], set(), set(), {}, set(), set()
    for name in experiment['runs']:
        path = directory / name
        result = verifier.verify(path)
        assert result == json.loads((path / 'verification.json').read_text())
        assert result['schema'] == 'agat.temporal.real-rag-verification.v4'
        commits.add(result['implementationCommit'])
        launcher = json.loads((path / 'launcher.json').read_text())
        replay = json.loads((path / 'native-replay.json').read_text())
        assert replay['status'] == 'pass' and replay['planSha256'] == launcher['planSha256']
        assert len(replay['phases']) == 2
        for index, transport in enumerate(('isolated', 'session')):
            phase = json.loads((path / f'{transport}.json').read_text())
            assert replay['workflowBundleSha256'] == phase['workflowBundleSha256']
            assert replay['phases'][index] == {'transport': transport, 'workflowId': phase['workflowId'],
                'historyEvents': len(phase['history']['events']), 'status': 'pass'}
            signatures.add(tuple((row['messagesSha256'], row['outputSha256'], row['inputTokens'], row['outputTokens'])
                                 for row in phase['primaryCalls']))
            for call in phase['embeddingCalls']:
                for key, value in zip(call['inputSha256'], call['vectorSha256'], strict=True):
                    assert vectors.setdefault(key, value) == value
        rows = launcher['resources']['samples']
        def shadow_total(row, field):
            return sum(process[field] for process in row['processes'] if process['pid'] in row['groups'].get('shadow', []))
        all_owned.update(launcher['ownedPids']); all_sampled.update(launcher['resources']['ownedPids'])
        results.append({'run': name, 'status': 'pass', 'launcherElapsedMs': launcher['elapsedMs'],
            'samples': len(rows), 'pressureDispatchCounts': dict(Counter(str(row['pressureDispatchLevel']) for row in rows)),
            'maxObservedShadowRssBytes': max(shadow_total(row, 'rssBytes') for row in rows),
            'maxObservedShadowFootprintBytes': max(shadow_total(row, 'footprintBytes') for row in rows),
            'swapUsedBytesRange': result['resources']['swapUsedBytesRange'],
            'warmupRuntimeMs': [state['warmup']['result']['durationMs'] for state in launcher['shadowRecovery']['runtimes']],
            'shadowRuntimeMs': [phase['shadow']['runtimeMs'] for phase in result['phases']],
            'ownedObservedPids': len(launcher['ownedPids']), 'ownedSampledPids': len(launcher['resources']['ownedPids']),
            'afterCleanupPressureDispatchLevel': rows[-1]['pressureDispatchLevel']})
    assert len(commits) == len(signatures) == 1 and len(vectors) == 5
    commit = next(iter(commits))
    for name in ('experiment.json', 'sampler-smoke.json'):
        path = directory / name
        assert subprocess.check_output(['git', 'show', f'{commit}:{archived_directory / name}'], cwd=ROOT, timeout=10) == path.read_bytes()
    smoke = json.loads((directory / 'sampler-smoke.json').read_text())
    assert smoke['errors'] == []
    calibration, timebase = smoke['nativeCpuCalibration'], smoke['timebase']
    before, after = calibration['before'], calibration['after']
    ticks = after['userTicks'] + after['systemTicks'] - before['userTicks'] - before['systemTicks']
    expected = calibration['processTimeAfterNs'] - calibration['processTimeBeforeNs']
    assert expected >= 200_000_000 and abs(ticks * timebase['numer'] / timebase['denom'] - expected) <= 1_000_000 + .02 * expected
    return {'schema': 'agat.shadow-resource-series-verification.v1', 'status': 'pass', 'implementationCommit': commit,
        'experimentSha256': hashlib.sha256(experiment_path.read_bytes()).hexdigest(), 'runs': results,
        'primaryCalls': 18, 'embeddingItems': 30, 'shadowAttempts': 18, 'shadowInferenceCalls': 12,
        'unavailableObservations': 6, 'separateWarmupCalls': 9, 'runtimeRestarts': 6, 'nativeReplayHistories': 6,
        'equalPromptsOutputsTokensAndFiveVectors': True, 'ownedObservedPids': len(all_owned),
        'ownedSampledPids': len(all_sampled), 'ownedContainersRemoved': 12,
        'unplannedFailureReproduced': False, 'causalConclusion': 'not_established', 'qualification': 'not_assessed'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path); parser.add_argument('--output', type=Path)
    parser.add_argument('--original-evidence-path', type=Path, help='Repository-relative docs path before a local evidence relocation')
    args = parser.parse_args(); result = verify(args.directory, args.original_evidence_path)
    if args.output:
        assert args.output.resolve().is_relative_to(ROOT / 'docs/private')
        with args.output.open('x') as stream:
            json.dump(result, stream, indent=2); stream.write('\n')
    print(json.dumps(result, separators=(',', ':')))
