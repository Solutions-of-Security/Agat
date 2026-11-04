"""Admission and cleanup faults around the owned continuous-soak launcher."""
from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
import signal
import stat
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import canonical_json, fingerprint
from scripts.lib.decision_soak_verification import LAUNCHER_SCHEMA
from scripts.test.test_decision_baselines import dataset
from scripts.test.test_decision_soak import SoakFixture

spec = importlib.util.spec_from_file_location('owned_soak', Path(__file__).parents[1] / 'run-decision-soak.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class Process:
    pid = 12345
    returncode = None
    def poll(self): return self.returncode


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve(); (self.root / 'docs').mkdir()
        self.output = self.root / 'docs/private/run'
        profile = SoakFixture(self.root).profile
        policy = {k: v for k, v in profile['policy'].items() if k != 'sha256'}
        self.profile = profile
        for name, body in [('dataset', dataset()), ('profile', profile), ('policy', policy)]:
            (self.root / 'docs' / (name + '.json')).write_text(json.dumps(body))
        (self.root / 'decision_runtime').mkdir()
        (self.root / 'decision_runtime/requirements-mlx.txt').write_text('fixture==1.0\n')
        self.process = Process()
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(launcher, 'ROOT', self.root))
        self.stack.enter_context(patch.object(launcher, 'frozen_sources', return_value=('1' * 40, {})))
        self.stack.enter_context(patch.object(launcher.runtime, 'require_resident_shadow_model'))
        self.stack.enter_context(patch.object(launcher, 'verify_manifest', return_value=(profile['model'], self.root)))
        self.stack.enter_context(patch.object(launcher.subprocess, 'check_output', return_value=b'{"python":"fixture","packages":{"fixture":"1.0"}}'))
        self.started = self.stack.enter_context(patch.object(launcher.subprocess, 'Popen', side_effect=self.start))
        self.stack.enter_context(patch.object(launcher.runtime, 'request', return_value={
            'status': 'ready', 'mode': 'shadow', 'profileJson': canonical_json(profile), 'profileSha256': fingerprint(profile)}))
        self.inventory = self.stack.enter_context(patch.object(launcher.runtime.shared, 'inventory',
            side_effect=lambda pid: ({pid, 12346}, {12345, 12346}) if pid == 12345 else ({pid}, set())))
        self.stop = self.stack.enter_context(patch.object(launcher.runtime.shared, 'stop', side_effect=self.stopped))
        self.stack.enter_context(patch.object(launcher.socket, 'socket', return_value=MagicMock(**{
            '__enter__.return_value.getsockname.return_value': ('127.0.0.1', 12345)})))
        self.probe = self.stack.enter_context(patch.object(launcher, 'continuous_soak', side_effect=self.probing))
        self.stack.enter_context(patch.object(launcher.sys, 'argv', ['soak', '--evidence-dir', str(self.output),
            '--runtime-python', '/fixture/python', '--manifest', '/fixture/model.json',
            '--profile', str(self.root / 'docs/profile.json'), '--policy', str(self.root / 'docs/policy.json'),
            '--dataset', str(self.root / 'docs/dataset.json'), '--duration-s', '4', '--block-s', '2']))
        self.stack.enter_context(patch('builtins.print'))

    def start(self, _args, **kwargs):
        kwargs['stdout'].write(b'private fixture log\n'); kwargs['stdout'].flush()
        return self.process

    def stopped(self, process):
        self.assertIs(process, self.process)
        process.returncode = -15

    def probing(self, *_args, **kwargs):
        kwargs['journal']({'block': 0, 'phase': 'measured', 'row': {'fixture': True}})
        return sealed({'status': 'observed'})

    def result(self):
        value = json.loads((self.output / 'launcher.json').read_text())
        verify_seal(value, LAUNCHER_SCHEMA)
        self.assertEqual(stat.S_IMODE((self.output / 'runtime.log').stat().st_mode), 0o600)
        return value

    def test_normal_exit_stops_owned_root_and_verifies_known_children(self):
        self.assertEqual(launcher.main(), 0)
        result = self.result()
        self.assertEqual((result['status'], result['remainingOwnedPids'], result['cleanupErrors']), ('pass', [], []))
        self.assertEqual(result['ownedPids'], [12345, 12346])
        self.stop.assert_called_once_with(self.process)

    def test_journal_failure_retains_logs_and_stops_owned_root(self):
        def full(*args, **kwargs):
            self.probing(*args, **kwargs)
            raise OSError(28, 'Injected disk full')
        self.probe.side_effect = full
        self.assertEqual(launcher.main(), 1)
        result = self.result()
        self.assertEqual(result['failure']['type'], 'OSError')
        self.assertEqual(result['remainingOwnedPids'], [])
        self.assertIn('fixture', (self.output / 'observations.jsonl').read_text())
        self.assertEqual((self.output / 'runtime.log').read_bytes(), b'private fixture log\n')
        self.stop.assert_called_once_with(self.process)

    def test_inventory_failure_still_stops_root_and_cannot_report_pass(self):
        self.inventory.side_effect = OSError('Injected ps failure')
        self.assertEqual(launcher.main(), 1)
        result = self.result()
        self.assertIsNone(result['remainingOwnedPids'])
        self.assertTrue(result['cleanupErrors'])
        self.stop.assert_called_once_with(self.process)
        self.probe.assert_not_called()

    def test_sigterm_during_probe_is_recorded_restores_handler_and_stops_root(self):
        previous = signal.getsignal(signal.SIGTERM)
        def cancelling(*args, **kwargs):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            self.assertTrue(kwargs['cancel_requested']())
            return self.probing(*args, **kwargs)
        self.probe.side_effect = cancelling
        self.assertEqual(launcher.main(), 1)
        self.assertTrue(self.result()['cancelled'])
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)
        self.stop.assert_called_once_with(self.process)

    def test_log_admission_failure_starts_no_process(self):
        with patch.object(launcher.runtime, 'open_private_log', side_effect=OSError(28, 'Injected disk full')):
            self.assertEqual(launcher.main(), 1)
        result = json.loads((self.output / 'launcher.json').read_text())
        verify_seal(result, LAUNCHER_SCHEMA)
        self.assertEqual(result['ownedPids'], [])
        self.assertEqual(result['failure']['type'], 'OSError')
        self.started.assert_not_called()

    def test_dependency_mismatch_rejects_before_admission(self):
        with patch.object(launcher.subprocess, 'check_output', return_value=b'{"python":"fixture","packages":{"fixture":"2.0"}}'):
            with self.assertRaisesRegex(ValueError, 'dependencies differ'):
                launcher.main()
        self.started.assert_not_called()
        self.assertFalse(self.output.exists())


if __name__ == '__main__': unittest.main()
