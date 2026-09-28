"""Own real process trees while testing the diagnostic kill/restart controller."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.lib.temporal_shadow_control import ShadowRecoveryControl

spec = importlib.util.spec_from_file_location('embedding_profile', ROOT / 'scripts/profile-embedding-rag.py')
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)
FIXTURE = '''import signal,sys,time
from pathlib import Path
from decision_runtime.isolated import IsolatedBackend
from decision_runtime.tests.test_isolated import fixture_factory
def stop(_signal,_frame):
    raise KeyboardInterrupt
if __name__ == '__main__':
    with IsolatedBackend(fixture_factory, {'mode':'normal'}, timeout_ms=1000) as backend:
        signal.signal(signal.SIGTERM,stop)
        Path(sys.argv[1]).write_text(str(backend.diagnostics()['childPid']))
        try:
            while True:time.sleep(1)
        except KeyboardInterrupt:pass
    raise SystemExit(130)
'''


@unittest.skipUnless(os.name == 'posix', 'Process-group qualification requires POSIX')
class ShadowControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='agat-shadow-control-test-')
        self.directory = Path(self.temporary.name)
        self.script = self.directory / 'fixture.py'; self.script.write_text(FIXTURE)
        self.processes = []
        self.fail_start = False
        self.control = ShadowRecoveryControl(self.directory / 'control', self.start_runtime, shared.inventory, shared.stop, time.monotonic())

    def start_runtime(self, state):
        ready = self.directory / f'ready-{len(self.processes)}'
        process = subprocess.Popen([sys.executable, str(self.script), str(ready)], cwd=ROOT,
            env={**os.environ, 'PYTHONPATH': str(ROOT)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        state['process'] = process; self.processes.append(process)
        if self.fail_start: raise RuntimeError('Injected startup failure after spawn')
        deadline = time.monotonic() + 10
        while not ready.exists():
            self.assertIsNone(process.poll())
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.02)
        state.update(health={'profileSha256': 'a' * 64}, warmup={'status': 'fixture'}, childPid=int(ready.read_text()))

    def tearDown(self):
        try:
            self.control.close()
            alive = shared.inventory(os.getpid())[1]
            self.assertFalse(self.control.owned & alive)
            self.assertTrue(all(process.poll() is not None for process in self.processes))
        finally:
            # A regression assertion must not leave its real fixture running.
            for process in self.processes:
                if process.poll() is None:
                    shared.stop(process)
            self.temporary.cleanup()

    def request(self, transport, action, instance, **extra):
        payload = {'schema': 'agat.shadow.control.v1', 'transport': transport, 'action': action, 'instanceId': instance, **extra}
        path = self.control.directory / f'{transport}-{action}.request.json'
        path.write_text(json.dumps(payload))
        self.control.poll(transport)
        response = self.control.directory / f'{transport}-{action}.response.json'
        return json.loads(response.read_text()) if response.exists() else None

    def test_two_owner_sigkills_retire_real_isolated_children_then_restart(self):
        self.control.start()
        for transport in ('isolated', 'session'):
            instance = str(uuid4()); previous = self.control.state
            self.assertIn(previous['childPid'], self.control.sample())
            killed = self.request(transport, 'kill', instance)
            self.assertEqual(killed['status'], 'pass'); self.assertEqual(killed['exitCode'], -9)
            self.assertEqual(killed['remainingAfterKill'], [])
            self.assertIn(previous['childPid'], killed['ownedBeforeKill'])
            restarted = self.request(transport, 'restart', instance)
            self.assertEqual(restarted['status'], 'pass'); self.assertNotEqual(restarted['newPid'], previous['process'].pid)
        self.control.close()
        report = self.control.report()
        self.assertTrue(report['closed']); self.assertIsNone(report['failure'])
        self.assertEqual([row['exitCode'] for row in report['runtimes']], [-9, -9, 130])
        self.assertEqual(len(report['events']), 4)

    def test_early_restart_and_caller_supplied_pid_cannot_kill_anything(self):
        self.control.start(); process = self.control.state['process']; instance = str(uuid4())
        self.assertIsNone(self.request('isolated', 'restart', instance))
        self.assertIsNone(process.poll()); self.assertEqual(self.control.events, [])
        rejected = self.request('isolated', 'kill', instance, pid=os.getpid())
        self.assertEqual(rejected['status'], 'fail'); self.assertIsNone(process.poll())

    def test_changed_workflow_cannot_restart_after_kill(self):
        self.control.start(); self.request('isolated', 'kill', str(uuid4()))
        rejected = self.request('isolated', 'restart', str(uuid4()))
        self.assertEqual(rejected['status'], 'fail'); self.assertEqual(len(self.processes), 1)

    def test_partial_startup_is_owned_and_closed(self):
        self.fail_start = True
        with self.assertRaisesRegex(RuntimeError, 'Injected startup failure'):
            self.control.start()
        self.control.close()
        self.assertIsNotNone(self.processes[0].poll())

    def test_inventory_failure_does_not_prevent_stopping_a_ready_runtime(self):
        state = self.control.start()
        with patch.object(self.control, 'inventory', side_effect=PermissionError('private diagnostic')):
            with self.assertRaisesRegex(RuntimeError, 'PermissionError'):
                self.control.close()
        self.assertIsNotNone(state['process'].poll())
        self.assertNotIn(state['childPid'], shared.inventory(os.getpid())[1])
        report = self.control.report()
        self.assertTrue(report['closed'])
        self.assertEqual(report['failure']['type'], 'CleanupError')
        self.assertNotIn('private diagnostic', json.dumps(report))

    def test_inventory_failure_during_partial_startup_still_stops_owned_parent(self):
        self.fail_start = True
        with self.assertRaisesRegex(RuntimeError, 'Injected startup failure'):
            self.control.start()
        process = self.processes[0]
        with patch.object(self.control, 'inventory', side_effect=PermissionError):
            with self.assertRaisesRegex(RuntimeError, 'PermissionError'):
                self.control.close()
        self.assertIsNotNone(process.poll())
        self.assertIn(process.pid, self.control.owned)
        self.assertEqual(self.control.report()['failure']['type'], 'CleanupError')

    def test_stop_error_does_not_skip_other_roots_and_cleanup_can_retry(self):
        older = self.control.start()['process']; newer = self.control.start()['process']
        def stop(process):
            if process is newer:
                raise OSError('private stop diagnostic')
            shared.stop(process)
        with patch.object(self.control, 'stop_group', side_effect=stop):
            with self.assertRaisesRegex(RuntimeError, 'OSError'):
                self.control.close()
        self.assertIsNotNone(older.poll())
        self.assertIsNone(newer.poll())
        self.assertFalse(self.control.closed)
        failure = self.control.report()['failure']
        self.assertEqual(failure['type'], 'CleanupError')
        self.assertNotIn('private stop diagnostic', json.dumps(failure))
        self.control.close()
        self.assertIsNotNone(newer.poll())
        self.assertTrue(self.control.closed)
        # Retrying cleanup must not turn the failed experiment into a pass.
        self.assertEqual(self.control.report()['failure'], failure)

    def test_successful_stop_callback_must_really_finish_the_process(self):
        process = self.control.start()['process']
        with patch.object(self.control, 'stop_group'):
            with self.assertRaisesRegex(RuntimeError, 'ProcessStillRunning'):
                self.control.close()
        self.assertFalse(self.control.closed)
        self.assertIsNone(process.poll())
        self.control.close()
        self.assertTrue(self.control.closed)
        self.assertIsNotNone(process.poll())


if __name__ == '__main__':
    unittest.main()
