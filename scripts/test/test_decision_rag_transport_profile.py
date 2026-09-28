"""Reject ambiguous transport labels in independently verified RAG evidence."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('rag_transport_verifier', ROOT / 'scripts/verify-decision-rag-workflow.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class RagTransportProfileTests(unittest.TestCase):
    def profile(self, mode='isolated'):
        return {'embeddingTransport': mode, 'files': {
            'workers/embedding_http.py': 'a' * 64, 'workers/embedding_transport.py': 'b' * 64}}

    def test_old_evidence_remains_explicitly_unspecified(self):
        self.assertEqual(verifier.transport_profile({}, {}), 'legacy_unspecified')

    def test_matching_isolated_and_session_profiles(self):
        for mode in ('isolated', 'session'):
            self.assertEqual(verifier.transport_profile(self.profile(mode), {'embeddingTransport': mode}), mode)

    def test_one_sided_or_different_transport_is_rejected(self):
        for plan, launcher in ((self.profile(), {}), ({}, {'embeddingTransport': 'isolated'}),
                               (self.profile(), {'embeddingTransport': 'session'})):
            with self.subTest(plan=plan, launcher=launcher), self.assertRaisesRegex(ValueError, 'profile mismatch'):
                verifier.transport_profile(plan, launcher)

    def test_invalid_matching_values_are_not_a_valid_profile(self):
        for mode in (None, '', 'default', 'ISOLATED', 0, True, [], {}):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'profile mismatch'):
                verifier.transport_profile(self.profile(mode), {'embeddingTransport': mode})

    def test_both_http_helpers_must_be_frozen(self):
        for name in self.profile()['files']:
            for value in (None, '', 'b' * 63, 'Z' * 64, 42):
                plan = self.profile()
                if value is None:
                    del plan['files'][name]
                else:
                    plan['files'][name] = value
                with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, 'source digest'):
                    verifier.transport_profile(plan, {'embeddingTransport': 'isolated'})


if __name__ == '__main__':
    unittest.main()
