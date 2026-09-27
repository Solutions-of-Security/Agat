"""Profilers must still observe helpers whose CLI includes an owner PID."""
import importlib.util
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('profile_tracking_support', ROOT / 'scripts/profile-embedding-model-transport.py')
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)
loopback = model.support


class ProfileProcessTrackingTests(unittest.TestCase):
    def test_loopback_profile_records_and_reaps_guarded_helper(self):
        fixture = loopback.Fixture()
        try:
            phase = loopback.phase({'id': 'observer-probe', 'mode': 'isolated', 'kind': 'success',
                                    'batch': 1, 'dimensions': 768, 'concurrency': 2, 'calls': 2},
                                   fixture, loopback.agat_worker.LocalModelClient)
            self.assertEqual(len(phase['rows']), 2)
            self.assertEqual(phase['fdBefore'], phase['fdAfter'])
            for row in phase['rows']:
                self.assertEqual(len(row['children']), 1)
                self.assertEqual(row['children'][0]['returncode'], 0)
                self.assertTrue(row['children'][0]['stdinClosed'] and row['children'][0]['stdoutClosed'])
                self.assertEqual(row['vectorSha256'], fixture.expected[1, 768])
        finally:
            fixture.close()

    def test_model_profile_records_guarded_helper_without_real_model(self):
        fixture = loopback.Fixture()
        identity = 'model-observer'
        fixture.entered[identity] = threading.Event()
        try:
            # Exercise actual HTTP/process tracking; only the model's response
            # is a deterministic fixture. This is not model quality evidence.
            with patch.object(model, 'BASE', fixture.url.removesuffix('/v1')), patch.object(model, 'MODEL', 'synthetic-768'), \
                    patch.object(model, 'inputs', return_value=[f'{identity}/success/0']):
                observed = model.run_phase({'id': identity, 'mode': 'isolated', 'batch': 1, 'concurrency': 1, 'calls': 1}, None)
            self.assertEqual(len(observed), 1)
            row, vectors = observed[0]
            self.assertEqual(loopback.sha(loopback.encoded(vectors)), fixture.expected[1, 768])
            self.assertEqual(len(row['children']), 1)
            self.assertEqual(row['children'][0]['returncode'], 0)
            self.assertTrue(row['children'][0]['stdinClosed'] and row['children'][0]['stdoutClosed'])
        finally:
            fixture.close()


if __name__ == '__main__':
    unittest.main()
