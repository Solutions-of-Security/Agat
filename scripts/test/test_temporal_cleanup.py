"""Launcher cleanup must stop owned processes even when observation is unavailable."""
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import test_temporal_shadow_control as fixture

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('temporal_cleanup_launcher', ROOT / 'scripts/run-temporal-real-rag.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


@contextmanager
def owned_runtime():
    with tempfile.TemporaryDirectory(prefix='agat-cleanup-test-') as temporary:
        directory = Path(temporary); script = directory / 'fixture.py'; ready = directory / 'ready'
        script.write_text(fixture.FIXTURE)
        process = subprocess.Popen([sys.executable, str(script), str(ready)], cwd=ROOT,
            env={**os.environ, 'PYTHONPATH': str(ROOT)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        try:
            deadline = time.monotonic() + 10
            while not ready.exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('Owned cleanup fixture did not start')
                time.sleep(.02)
            yield process, int(ready.read_text())
        finally:
            fixture.shared.stop(process)


@unittest.skipUnless(os.name == 'posix', 'Owned process-group cleanup requires POSIX')
class LauncherCleanupTests(unittest.TestCase):
    def test_inventory_error_still_stops_and_reaps_the_owned_tree(self):
        with owned_runtime() as (process, child):
            owned, errors = set(), []
            with patch.object(launcher.shared, 'inventory', side_effect=PermissionError('private path')):
                launcher.stop_owned_process(process, owned, errors)
            self.assertEqual(process.poll(), 130)
            self.assertNotIn(child, fixture.shared.inventory(os.getpid())[1])
            self.assertIn(process.pid, owned)
            self.assertEqual(errors, ['inventory:PermissionError'])

    def test_stop_error_is_reported_and_an_explicit_retry_reaps_the_tree(self):
        with owned_runtime() as (process, child):
            owned, errors = set(), []
            with patch.object(launcher.shared, 'stop', side_effect=OSError('private path')):
                launcher.stop_owned_process(process, owned, errors)
            self.assertIsNone(process.poll())
            self.assertEqual(errors, ['stop:OSError'])
            self.assertIn(child, owned)
            launcher.stop_owned_process(process, owned, errors)
            self.assertEqual(process.poll(), 130)
            self.assertEqual(errors, ['stop:OSError'])

    def test_a_stop_callback_that_leaves_the_process_alive_is_not_success(self):
        with owned_runtime() as (process, _child):
            owned, errors = set(), []
            with patch.object(launcher.shared, 'stop'):
                launcher.stop_owned_process(process, owned, errors)
            self.assertIsNone(process.poll())
            self.assertEqual(errors, ['stop:ProcessStillRunning'])
            launcher.stop_owned_process(process, owned, errors)
            self.assertEqual(process.poll(), 130)

    def test_missing_handle_does_not_observe_or_stop_unowned_processes(self):
        with patch.object(launcher.shared, 'inventory') as observe, patch.object(launcher.shared, 'stop') as stop:
            owned, errors = set(), []
            launcher.stop_owned_process(None, owned, errors)
            observe.assert_not_called(); stop.assert_not_called()
            self.assertEqual(owned, set()); self.assertEqual(errors, [])

    def test_unknown_final_inventory_is_not_an_empty_remaining_list(self):
        errors = []
        with patch.object(launcher.shared, 'inventory', side_effect=PermissionError('private path')):
            self.assertIsNone(launcher.remaining_owned_processes({1, 2}, errors))
        self.assertEqual(errors, ['inventory:PermissionError'])
        with patch.object(launcher.shared, 'inventory', return_value=(set(), {2, 3, 999})):
            self.assertEqual(launcher.remaining_owned_processes({1, 2, 3}, []), [2, 3])


if __name__ == '__main__':
    unittest.main()
