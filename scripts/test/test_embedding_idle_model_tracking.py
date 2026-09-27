"""The burst profiler observes real helpers both with and without idle retirement."""
import importlib.util
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('idle_model_tracking', ROOT / 'scripts/profile-embedding-idle-model.py')
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


class IdleModelTrackingTests(unittest.TestCase):
    def test_real_processes_and_transport_intervals_survive_both_bursts(self):
        for mode in ('keep', 'retire'):
            for concurrency in (1, 4):
                with self.subTest(mode=mode, concurrency=concurrency):
                    fixture = profile.guard.support.Fixture()
                    prefix = f'probe-{mode}-{concurrency}'
                    for case in range(4):
                        fixture.entered[f'{prefix}-{case}'] = threading.Event()
                    try:
                        with patch.object(profile.guard.model, 'BASE', fixture.url.removesuffix('/v1')), \
                                patch.object(profile.guard.model, 'MODEL', 'synthetic-768'), \
                                patch.object(profile.guard.model, 'inputs', side_effect=lambda _b, case: [f'{prefix}-{case}/success/0']), \
                                patch.dict(os.environ, {'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'}):
                            phase, rows = profile.run_phase({'id': prefix, 'mode': mode, 'batch': 1, 'concurrency': concurrency,
                                                           'callsPerBurst': 4, 'idleTimeoutSeconds': .15, 'idleSeconds': .5})
                        self.assertEqual(len(rows), 8)
                        self.assertEqual(len(phase['children']), concurrency * (2 if mode == 'retire' else 1))
                        self.assertEqual(len(phase['afterIdle']['helperRssBytes']), 0 if mode == 'retire' else concurrency)
                        self.assertEqual(phase['idleReaps'], concurrency if mode == 'retire' else 0)
                        for row, vectors in rows:
                            self.assertEqual(profile.guard.support.sha(profile.guard.support.encoded(vectors)), fixture.expected[1, 768])
                            self.assertLess(row['startedNs'], row['transportStartedNs'])
                            self.assertLess(row['transportStartedNs'], row['transportFinishedNs'])
                            self.assertLess(row['transportFinishedNs'], row['finishedNs'])
                            self.assertIn(row['helperPid'], {child['pid'] for child in phase['children']})
                        self.assertEqual(phase['remainingThreads'], [])
                        self.assertEqual(phase['before']['descriptors'], phase['afterClose']['descriptors'])
                    finally:
                        fixture.close()


if __name__ == '__main__':
    unittest.main()
