"""Check the native CPU unit conversion and real maximum-capacity fixture."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('idle_measurement_test', ROOT / 'scripts/profile-embedding-idle-budget.py')
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


@unittest.skipUnless(sys.platform == 'darwin', 'Native measurement adapter is scoped to macOS; replay is portable')
class IdleMeasurementTests(unittest.TestCase):
    def test_native_units_agree_with_independent_python_process_cpu(self):
        counters = profile.DarwinCounters()
        sample = counters.calibration()
        self.assertGreaterEqual(sample['processTimeAfterNs'] - sample['processTimeBeforeNs'], 200_000_000)
        self.assertEqual(profile.ctypes.sizeof(profile.Usage), 96)

    def test_maximum_pool_is_reused_after_idle_and_releases_all_resources(self):
        counters = profile.DarwinCounters()
        for capacity in (1, 4, 32):
            with self.subTest(capacity=capacity):
                phase = profile.run_phase({'id': 'probe', 'capacity': capacity, 'guard': True, 'idleSeconds': .02},
                                          profile.guard.embedding_transport, counters)
                self.assertEqual(len(phase['children']), capacity)
                self.assertEqual(len(phase['serverRows']), capacity * 2)
                self.assertEqual(phase['fdBefore'], phase['fdAfterClose'])
                self.assertEqual(phase['fdIdle'], phase['fdBefore'] + 1 + 2 * capacity)
                self.assertEqual(phase['remainingThreads'], [])
                self.assertTrue(all(row['returncode'] == 0 and row['stdinClosed'] and row['stdoutClosed'] for row in phase['children']))


if __name__ == '__main__':
    unittest.main()
