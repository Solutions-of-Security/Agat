#!/usr/bin/env python3
"""Replay a pinned real-model transport comparison from compressed full vectors."""
import argparse
from collections import defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASELINE = 'f9a16ea289845b9d7db142e5ee2144aad0d553ff'
MODEL = 'embeddinggemma:latest'
DIGEST = '85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1'
SOURCES = {'scripts/profile-embedding-model-transport.py', 'scripts/verify-embedding-model-transport.py',
           'scripts/profile-embedding-transport.py', 'workers/agat_worker.py', 'workers/embedding_http.py',
           'workers/telemetry.py', 'workers/web_tools.py', 'workers/local_decisions.py'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def summary(values):
    ordered = sorted(values)
    return {'count': len(ordered), 'min': round(ordered[0], 3), 'p50': round(ordered[math.ceil(len(ordered) * .5) - 1], 3),
            'p95': round(ordered[math.ceil(len(ordered) * .95) - 1], 3), 'max': round(ordered[-1], 3)}


def verify(directory):
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    directory = directory.resolve()
    assert directory.is_relative_to(ROOT / 'docs')
    plan = json.loads((directory / 'plan.json').read_text())
    result = json.loads((directory / 'result.json').read_text())
    assert result['status'] == 'observed' and result['failure'] is None
    assert 0 < result['elapsedSeconds'] <= 210 and result['planSha256'] == sha((directory / 'plan.json').read_bytes())
    assert (plan['schema'], plan['model'], plan['modelDigest'], plan['endpoint'], plan['dimensions']) == (
        'agat.embedding.model-transport.v1', MODEL, DIGEST, 'http://127.0.0.1:11434/v1', 768)
    assert plan['maxComponentDifference'] == 1e-6 and plan['maxCosineDistance'] == 1e-10
    assert plan['baselineCommit'] == BASELINE and re.fullmatch('[0-9a-f]{40}', plan['implementationCommit'])
    assert set(plan['sourceSha256']) == SOURCES
    for name, value in plan['sourceSha256'].items():
        assert sha(subprocess.check_output(['git', 'show', f"{plan['implementationCommit']}:{name}"], cwd=ROOT, timeout=10)) == value
    assert sha(subprocess.check_output(['git', 'show', f'{BASELINE}:workers/agat_worker.py'], cwd=ROOT, timeout=10)) == plan['baselineWorkerSha256']
    before, after = plan['metadataBefore'], result['metadataAfter']
    assert before['version'] == after['version'] and before['model'] == after['model']
    assert before['model']['name'] == MODEL and before['model']['digest'] == DIGEST and before['model']['size'] > 0
    assert any(model['name'] == MODEL and model['digest'] == DIGEST for model in after['loaded'])
    expected_cases = {}
    for batch in (1, 32):
        for case in range(4):
            expected_cases[f'b{batch}-{case}'] = [f'Учебный документ {case}-{index}. За неделю поступило {20 + case + index} обращений. '
                f'Сотрудник проверил источник и зарегистрировал результат. '
                f'Статус заявки: выполнено. Группа: поддержка. Период: сентябрь 2026.' for index in range(batch)]
    assert plan['cases'] == expected_cases
    expected = [(f'warm-b{b}-{m}', b, 1, m, 1, False) for b in (1, 32) for m in ('direct', 'isolated')]
    expected += [(f'b{b}-c{c}-{i}', b, c, m, 4, True) for b in (1, 32) for c in (1, 4)
                 for i, m in enumerate(('direct', 'isolated', 'isolated', 'direct'))]
    keys = ('id', 'batch', 'concurrency', 'mode', 'calls', 'measured')
    assert [tuple(item[key] for key in keys) for item in plan['phases']] == expected
    assert [phase['id'] for phase in result['phases']] == [item[0] for item in expected]
    blobs = {}
    for digest, metadata in result['vectorFiles'].items():
        assert re.fullmatch('[0-9a-f]{64}', digest)
        path = directory / 'vectors' / f'{digest}.json.gz'
        data = path.read_bytes()
        assert sha(data) == metadata['sha256'] and len(data) == metadata['bytes']
        # Each supported batch fits the existing 8 MiB response body budget.
        with gzip.open(path, 'rb') as stream:
            raw = stream.read(8 * 1024 * 1024 + 1)
        assert len(raw) <= 8 * 1024 * 1024 and len(raw) == metadata['decodedBytes'] and sha(raw) == digest
        blobs[digest] = json.loads(raw)
    assert {path.name for path in (directory / 'vectors').iterdir()} == {f'{digest}.json.gz' for digest in blobs}
    references = {}
    referenced = set()
    observed = defaultdict(list)
    warmup = {}
    concurrency = {}
    exact = total = helpers = 0
    max_component = max_cosine = 0.0
    for item, phase in zip(plan['phases'], result['phases'], strict=True):
        assert [row['case'] for row in phase['rows']] == list(range(item['calls']))
        intervals = []
        for row in phase['rows']:
            assert row['id'] == f"{item['id']}-{row['case']}" and 0 < row['startedNs'] < row['finishedNs']
            payload = {'model': MODEL, 'input': expected_cases[f"b{item['batch']}-{row['case']}"]}
            assert row['inputSha256'] == sha(json.dumps(payload, ensure_ascii=False).encode())
            vectors = blobs[row['vectorSha256']]
            referenced.add(row['vectorSha256'])
            assert len(vectors) == item['batch'] and all(len(vector) == 768 for vector in vectors)
            for vector in vectors:
                assert all(type(value) in (int, float) and math.isfinite(value) for value in vector)
                assert any(value != 0 for value in vector)
            key = f"b{item['batch']}-{row['case']}"
            reference_sha, reference = references.setdefault(key, (row['vectorSha256'], vectors))
            exact += row['vectorSha256'] == reference_sha
            for vector, original in zip(vectors, reference, strict=True):
                difference = max(abs(a - b) for a, b in zip(vector, original, strict=True))
                dot = math.fsum(a * b for a, b in zip(vector, original, strict=True))
                norm = math.sqrt(math.fsum(a * a for a in vector) * math.fsum(b * b for b in original))
                assert norm > 0 and math.isfinite(norm)
                distance = max(0.0, 1 - dot / norm)
                assert difference <= plan['maxComponentDifference'] and distance <= plan['maxCosineDistance']
                max_component = max(max_component, difference)
                max_cosine = max(max_cosine, distance)
            assert len(row['children']) == (item['mode'] == 'isolated')
            for child in row['children']:
                assert type(child['pid']) is int and child['pid'] > 0 and child['returncode'] == 0
                assert child['stdinClosed'] is True and child['stdoutClosed'] is True
                helpers += 1
            intervals.extend([(row['startedNs'], 1), (row['finishedNs'], -1)])
            wall = (row['finishedNs'] - row['startedNs']) / 1e6
            if item['measured']:
                observed[f"b{item['batch']}-c{item['concurrency']}-{item['mode']}"].append(wall)
            else:
                warmup[row['id']] = round(wall, 3)
            total += 1
        active = peak = 0
        for _, change in sorted(intervals, key=lambda entry: (entry[0], entry[1])):
            active += change
            assert active >= 0
            peak = max(peak, active)
        assert active == 0 and 1 <= peak <= item['concurrency']
        concurrency[item['id']] = peak
    assert referenced == set(blobs) and len(references) == 8
    return {'status': 'verified', 'calls': total, 'measuredCalls': sum(map(len, observed.values())), 'helpersReaped': helpers,
            'uniqueVectorBlobs': len(blobs), 'exactReferenceMatches': exact, 'maxComponentDifference': max_component,
            'maxCosineDistance': max_cosine, 'latencyMs': {key: summary(values) for key, values in observed.items()},
            'warmupMs': warmup, 'observedConcurrency': concurrency,
            'planSha256': result['planSha256'], 'resultSha256': sha((directory / 'result.json').read_bytes())}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.directory), indent=2, allow_nan=False))
