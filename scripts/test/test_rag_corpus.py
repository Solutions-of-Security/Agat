import copy
import math
import unittest

from scripts.lib.rag_corpus import cosine, reference_topk, validate_hits


def row(key, vector):
    return {'key': key, 'vector': vector, 'contentSha256': f'chunk-{key}', 'documentSha256': f'doc-{key}'}


class CorpusOracleTests(unittest.TestCase):
    def test_geometry_and_precision(self):
        self.assertEqual(cosine([3, 4], [3, 4]), 1)
        self.assertEqual(cosine([1, 0], [0, 1]), 0)
        self.assertEqual(cosine([1, 0], [-1, 0]), -1)
        result = reference_topk([row('near', [1, .0005]), row('exact', [1, 0])], [1, 0], 1)
        self.assertEqual([item['key'] for item in result['eligible']], ['exact'])

    def test_ties_require_strictly_better_member(self):
        reference = reference_topk([row('best', [1, 0]), row('a', [1, 1]), row('b', [1, -1])], [1, 0], 2)
        self.assertEqual(reference['required'], ['best'])
        hits = copy.deepcopy(reference['eligible'])
        validate_hits([hits[0], hits[2]], reference, 2)
        with self.assertRaisesRegex(ValueError, 'Missing strictly better'):
            validate_hits(hits[1:], reference, 2)

    def test_rounding_is_allowed_but_wrong_score_is_rejected(self):
        reference = reference_topk([row('a', [1, 1])], [1, 0], 1)
        hits = copy.deepcopy(reference['eligible'])
        hits[0]['score'] = round(hits[0]['score'], 6)
        validate_hits(hits, reference, 1)
        hits[0]['score'] += .000002
        with self.assertRaisesRegex(ValueError, 'Changed score'):
            validate_hits(hits, reference, 1)

    def test_wrong_provenance_duplicates_and_order(self):
        reference = reference_topk([row('a', [1, 0]), row('b', [1, 1])], [1, 0], 2)
        hits = copy.deepcopy(reference['eligible'])
        for changed in ([hits[1], hits[0]], [hits[0], hits[0]]):
            with self.assertRaises(ValueError):
                validate_hits(changed, reference, 2)
        hits[1]['documentSha256'] = 'wrong'
        with self.assertRaises(ValueError):
            validate_hits(hits, reference, 2)

    def test_rejects_broken_vectors_and_candidate_keys(self):
        for left, right in [([], []), ([1], [1, 2]), ([0], [1]), ([math.inf], [1]), ([math.nan], [1])]:
            with self.assertRaises(ValueError):
                cosine(left, right)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            reference_topk([row('a', [1]), row('a', [1])], [1], 1)
        with self.assertRaisesRegex(ValueError, 'Invalid topK'):
            reference_topk([row('a', [1])], [1], 2)


if __name__ == '__main__':
    unittest.main()
