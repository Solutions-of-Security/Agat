"""Deterministic compatibility fixtures and independent topK checks."""
import math

from scripts.lib.rag_corpus import cosine


def close_vectors():
    # All values round to [1, 1] in float32, but candidate 12 is strictly best.
    return [{'id': index, 'vector': [1, 1 + index * 1e-9]} for index in range(1, 12)] + [
        {'id': 12, 'vector': [1, 1]}]


def make_vectors(dimensions=768):
    if dimensions < 16 or dimensions > 4096:
        raise ValueError('Fixture dimension must be between 16 and 4096')
    state = 20260927
    def next_value():
        nonlocal state
        state = (1664525 * state + 1013904223) % 2**32
        return (state >> 16) / 65536
    rows = []
    for index in range(6501):
        chosen = index < 6001
        vector = ([.2 + .1 * next_value()] + [.7 + .5 * next_value() for _ in range(15)] if chosen
                  else [1] + [.01 * next_value() for _ in range(15)])
        rows.append({'id': index + 1, 'project': 'project-a' if index < 6101 else 'project-b',
                     'collection': 'chosen' if chosen else 'excluded',
                     'vector': vector + [0] * (dimensions - 16)})
    return rows


def rank_vectors(rows, vector, top_k):
    return sorted(({'id': row['id'], 'score': cosine(row['vector'], vector)} for row in rows),
                  key=lambda row: (-row['score'], row['id']))[:top_k]


def inspect_hits(rows, top_k):
    if len(rows) > top_k or len({row['id'] for row in rows}) != len(rows):
        raise ValueError('Invalid ANN hit count or duplicate')
    for row in rows:
        if row['project'] != 'project-a' or row['collection'] != 'chosen':
            raise ValueError('Project/collection leak')
        if not math.isfinite(row['distance']) or not 0 <= row['distance'] <= 2:
            raise ValueError('Invalid cosine distance')
    if any(left['distance'] > right['distance'] for left, right in zip(rows, rows[1:])):
        raise ValueError('Unordered hits')


def verify_exact(rows, expected):
    inspect_hits(rows, len(expected))
    if [row['id'] for row in rows] != [row['id'] for row in expected]:
        raise ValueError('Exact topK differs from float64 oracle')
    if any(abs(1 - row['distance'] - ref['score']) > 1e-6 for row, ref in zip(rows, expected)):
        raise ValueError('Exact score outside declared float32 tolerance')
