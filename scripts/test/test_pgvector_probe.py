import copy
import struct
import unittest

from scripts.lib.pgvector_probe import close_vectors, make_vectors, rank_vectors, verify_exact, inspect_hits


class PgvectorProbeTests(unittest.TestCase):
    def test_float32_counterexample_has_strictly_better_candidate_outside_tied_pool(self):
        rows = close_vectors()
        self.assertEqual(rank_vectors(rows, [1, 0], 1)[0]['id'], 12)
        self.assertEqual(len({struct.pack('ff', *row['vector']) for row in rows}), 1)
        self.assertNotIn(12, [row['id'] for row in sorted(rows, key=lambda row: row['id'])[:8]])

    def test_fixture_has_larger_than_supported_scope_and_closer_excluded_rows(self):
        rows = make_vectors(16)
        selected = [row for row in rows if row['project'] == 'project-a' and row['collection'] == 'chosen']
        self.assertEqual(len(selected), 6001)
        self.assertEqual(sum(row['project'] == 'project-b' for row in rows), 400)
        self.assertEqual(rows, make_vectors(16))
        self.assertNotIn(rank_vectors(rows, [1] + [0] * 15, 1)[0]['id'], {row['id'] for row in selected})

    def test_verifier_distinguishes_shortfall_recall_leak_and_invalid_scores(self):
        expected = [{'id': 1, 'score': 1}, {'id': 2, 'score': .8}]
        hits = [{'id': row['id'], 'project': 'project-a', 'collection': 'chosen', 'distance': 1 - row['score']}
                for row in expected]
        verify_exact(hits, expected)
        inspect_hits([], 2)  # ANN underfill is measured, not a leaked result or test failure.
        for bad in ([], hits[:1], list(reversed(hits)), [hits[0], hits[0]]):
            with self.assertRaises(ValueError):
                verify_exact(bad, expected)
        for key, value in [('project', 'project-b'), ('collection', 'excluded'), ('distance', float('nan')),
                           ('distance', 3), ('distance', .1), ('id', 3)]:
            bad = copy.deepcopy(hits)
            bad[0][key] = value
            with self.assertRaises(ValueError):
                verify_exact(bad, expected)


if __name__ == '__main__':
    unittest.main()
