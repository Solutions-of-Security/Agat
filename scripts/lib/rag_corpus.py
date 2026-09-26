"""Independent float64/compensated-sum oracle for the bounded RAG corpus probe."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

TOLERANCE = 1e-12


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path: Path, value) -> None:
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def cosine(left, right):
    if not left or len(left) != len(right) or any(not math.isfinite(x) for x in [*left, *right]):
        raise ValueError('Invalid vector dimensions/values')
    ln = math.sqrt(math.fsum(x*x for x in left))
    rn = math.sqrt(math.fsum(x*x for x in right))
    if ln == 0 or rn == 0:
        raise ValueError('Zero vector')
    return max(-1.0, min(1.0, math.fsum(x*y for x, y in zip(left, right)) / (ln*rn)))


def reference_topk(candidates, query, top_k):
    if not isinstance(top_k, int) or top_k < 1 or len(candidates) < top_k:
        raise ValueError('Invalid topK')
    if len({row['key'] for row in candidates}) != len(candidates):
        raise ValueError('Duplicate candidate key')
    ranked = sorted(({key: value for key, value in item.items() if key != 'vector'}
                     | {'score': cosine(query, item['vector'])} for item in candidates),
                    key=lambda row: (-row['score'], row['key']))
    boundary = ranked[top_k - 1]['score']
    return {'cutoffScore': boundary,
            'required': [row['key'] for row in ranked if row['score'] > boundary + TOLERANCE],
            'eligible': [row for row in ranked if row['score'] >= boundary - TOLERANCE]}


def validate_hits(hits, expected, top_k):
    if len(hits) != top_k or len({row['key'] for row in hits}) != top_k:
        raise ValueError('Wrong count/duplicate hit')
    references = {row['key']: row for row in expected['eligible']}
    if not set(expected['required']).issubset({row['key'] for row in hits}):
        raise ValueError('Missing strictly better candidate')
    scores = []
    for hit in hits:
        source = references.get(hit['key'])
        if source is None:
            raise ValueError('Incorrect topK member')
        if (not math.isfinite(hit['score']) or abs(hit['score'] - source['score']) > 0.00000050001
                or hit['contentSha256'] != source['contentSha256']
                or hit['documentSha256'] != source['documentSha256']):
            raise ValueError('Changed score or provenance')
        scores.append(source['score'])
    if any(right > left + TOLERANCE for left, right in zip(scores, scores[1:])):
        raise ValueError('Incorrect ranking order')


def make_oracle(data: Path, evidence: Path):
    plan = json.loads((evidence / 'plan.json').read_text())
    corpus = json.loads((data / 'corpus.json').read_text())
    vectors = {item['contentSha256']: item['vector'] for item in json.loads((data / 'vectors.json').read_text())}
    queries = json.loads((data / 'queries.json').read_text())
    candidates = [{'key': f"{doc['name']}:{chunk['ordinal']}", 'documentSha256': doc['contentSha256'],
                   'contentSha256': chunk['sha256'], 'vector': vectors[chunk['sha256']]}
                  for doc in corpus for chunk in doc['chunks']]
    if len(candidates) != plan['chunkCount']:
        raise ValueError('Corpus count changed')
    embedding = json.loads((evidence / 'embedding.json').read_text())
    if embedding['vectorsSha256'] != file_sha(data / 'vectors.json'):
        raise ValueError('Vector cache changed')
    result = {'planSha256': file_sha(evidence / 'plan.json'), 'candidateCount': len(candidates),
              'method': 'Python math.fsum dot product and norms; full sort; ties within 1e-12 at cutoff are interchangeable',
              'queries': [reference_topk(candidates, query, plan['topK']) for query in queries]}
    write_new(evidence / 'oracle.json', result)
    return result


def verify_results(evidence: Path):
    plan = json.loads((evidence / 'plan.json').read_text())
    oracle = json.loads((evidence / 'oracle.json').read_text())
    plan_sha = file_sha(evidence / 'plan.json')
    if oracle['planSha256'] != plan_sha:
        raise ValueError('Oracle plan mismatch')
    summary = {}
    for backend in ('sqlite', 'postgresql'):
        result = json.loads((evidence / f'search-{backend}.json').read_text())
        if result['planSha256'] != plan_sha or result['backend'] != backend:
            raise ValueError('Search plan/backend mismatch')
        attempts = result['attempts']
        expected_order = [(p, q) for p in range(plan['passes']) for q in range(len(plan['queries']))]
        if [(row['pass'], row['query']) for row in attempts] != expected_order:
            raise ValueError('Missing or repeated search attempts')
        for row in attempts:
            validate_hits(row['hits'], oracle['queries'][row['query']], plan['topK'])
            if row['retrievalEvents'] != 1 or [hit['marker'] for hit in row['hits']] != [f'K{i+1}' for i in range(plan['topK'])]:
                raise ValueError('Incorrect retrieval events/markers')
            if not math.isfinite(row['wallMs']) or row['wallMs'] < 0:
                raise ValueError('Invalid timing')
        warm = sorted(row['wallMs'] for row in attempts if row['pass'] > 0)
        def percentile(fraction):
            index = (len(warm) - 1) * fraction
            lower = math.floor(index)
            upper = math.ceil(index)
            return round(warm[lower] + (warm[upper] - warm[lower]) * (index - lower), 3)
        summary[backend] = {'attempts': len(attempts), 'verifiedTopK': len(attempts),
                            'firstPassMs': [row['wallMs'] for row in attempts if row['pass'] == 0],
                            'warmSamples': len(warm), 'warmP50Ms': percentile(.5), 'warmP95Ms': percentile(.95),
                            'warmMaxMs': max(warm), 'baselineRssBytes': result['baseline']['memory']['rss'],
                            'processLifetimePeakRssBytes': result['finalPeakRssBytes']}
    return {'status': 'verified', 'planSha256': plan_sha, 'summary': summary}
