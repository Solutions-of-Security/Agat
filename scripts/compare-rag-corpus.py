#!/usr/bin/env python3
"""Verify two frozen corpus runs before reporting timing and memory observations."""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.lib.rag_corpus import file_sha, verify_results


def read(directory, name):
    return json.loads((directory / name).read_text())


def verify(directory, commit):
    directory = directory.resolve()
    if not directory.is_relative_to(ROOT / 'docs') or not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('Expected evidence under docs and a full implementation commit SHA')
    launcher = read(directory, 'launcher-result.json')
    assert launcher['status'] == 'observed' and launcher['failure'] is None
    assert launcher['containerRemoved'] and not launcher['cleanupErrors']
    plan = read(directory, 'plan.json')
    expected_phases = {f"plan-{plan['sourceCommit']}", 'embed-http', 'ingest-sqlite', 'search-sqlite',
                       'ingest-postgresql', 'search-postgresql'}
    assert set(launcher['phaseExitCodes']) == expected_phases
    assert all(code == 0 for code in launcher['phaseExitCodes'].values())
    for name, expected in launcher['files'].items():
        path = (directory / name).resolve()
        assert path.parent == directory and file_sha(path) == expected, f'Changed evidence: {name}'
    for name, expected in plan['sourceSha256'].items():
        original = subprocess.check_output(['git', 'show', f'{commit}:{name}'], cwd=ROOT)
        assert hashlib.sha256(original).hexdigest() == expected, f'Implementation mismatch: {name}'
    result = verify_results(directory)
    assert result == read(directory, 'verification.json')
    assert read(directory, 'model-service.json')['afterUnload']['models'] == []
    return {'implementationCommit': commit, 'launcherSha256': file_sha(directory / 'launcher-result.json'),
            'filesVerified': len(launcher['files']), 'sourceFilesVerified': len(plan['sourceSha256']),
            'verification': result}


def main():
    if not __debug__:
        raise RuntimeError('Evidence verification requires Python assertions; do not use -O/PYTHONOPTIMIZE')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--before-ref', required=True)
    parser.add_argument('--after-ref', required=True)
    args = parser.parse_args()
    checks = {'before': verify(args.before, args.before_ref), 'after': verify(args.after, args.after_ref)}
    plans = [read(path, 'plan.json') for path in (args.before, args.after)]
    for key in ('sourceCommit', 'corpusSha256', 'model', 'modelDigest', 'embeddingSettings', 'dimensions',
                'chunkSize', 'chunkOverlap', 'topK', 'passes', 'queries', 'documentCount', 'chunkCount', 'documents', 'nodeVersion'):
        assert plans[0][key] == plans[1][key], f'Changed experiment input: {key}'
    for name in ('launcher-plan.json', 'postgres-image.json'):
        left, right = [read(path, name) for path in (args.before, args.after)]
        if name == 'launcher-plan.json':
            for item in (left, right):
                item.setdefault('copies', 1)
                item.setdefault('candidateLimit', 5000)
        assert left == right, f'Changed host/runtime setup: {name}'
    embeddings = [read(path, 'embedding.json') for path in (args.before, args.after)]
    for key in ('uniqueChunks', 'queryVectors', 'queryVectorsSha256', 'vectorsSha256', 'vectorDigests'):
        assert embeddings[0][key] == embeddings[1][key], f'Changed embeddings: {key}'
    for key in ('name', 'digest', 'version', 'details', 'generation'):
        assert embeddings[0]['identity'][key] == embeddings[1]['identity'][key], f'Changed model identity: {key}'
    count = 0
    for backend in ('sqlite', 'postgresql'):
        before, after = [read(path, f'search-{backend}.json') for path in (args.before, args.after)]
        assert before['nodeVersion'] == after['nodeVersion']
        for left, right in zip(before['attempts'], after['attempts'], strict=True):
            assert (left['pass'], left['query'], left['hits']) == (right['pass'], right['query'], right['hits'])
            count += 1
    descriptions = [item['identity']['descriptionSha256'] for item in embeddings]
    print(json.dumps({'status': 'verified', 'checks': checks,
        'identicalSourceVectors': embeddings[0]['uniqueChunks'], 'identicalQueryVectors': len(embeddings[0]['queryVectors']),
        'identicalHitLists': count, 'modelDescriptionHashes': {'before': descriptions[0], 'after': descriptions[1],
            'match': descriptions[0] == descriptions[1],
            'note': 'The whole /api/show hash is reported separately. Raw descriptions were not retained, so differing hashes remain unexplained; recorded identity/settings and all vectors are checked.'},
        'limits': ['One sequential comparison; no randomized crossover or production SLO inference.',
                   'Process RSS excludes PostgreSQL server, Docker VM and unloaded Ollama.',
                   'Historical process IDs are not probed here because IDs may be reused.']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
