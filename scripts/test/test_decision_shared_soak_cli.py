import json
import runpy
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from decision_runtime.artifacts import sealed, write_new
from scripts.test.test_decision_baselines import dataset
from scripts.test.test_decision_performance import Fixture

ROOT = Path(__file__).resolve().parents[2]


class SharedSoakCliTest(unittest.TestCase):
    def load(self): return runpy.run_path(str(ROOT / 'scripts/benchmark-decision-shared-soak.py'))

    def setup_inputs(self, root):
        data = root / 'dataset.json'; profile = root / 'profile.json'
        write_new(data, dataset()); profile.write_text(json.dumps(Fixture().engine.profile()))
        return data, profile

    def arguments(self, data, profile, output):
        return ['--decision-url', 'http://127.0.0.1:1', '--primary-url', 'http://127.0.0.1:2',
                '--expected-primary-digest', '1' * 64, '--dataset', str(data), '--profile', str(profile),
                '--output-dir', str(output), '--duration-s', '3']

    def test_public_or_existing_output_is_rejected_before_inputs_or_services(self):
        entry = self.load(); calls = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'docs/private/existing').mkdir(parents=True)
            globals_ = entry['main'].__globals__
            with patch.dict(globals_, {'ROOT': root, 'load_dataset': lambda *_: calls.append(True)}):
                for output in (root / 'docs/public/out', root / 'docs/private/existing'):
                    with self.assertRaises(SystemExit):
                        entry['main'](self.arguments(root / 'absent-data', root / 'absent-profile', output))
            self.assertEqual(calls, [])

    def test_source_guard_failure_starts_no_model_probe_and_allocates_no_evidence(self):
        entry = self.load(); calls = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); data, profile = self.setup_inputs(root); output = root / 'docs/private/run'
            def source(*_): raise ValueError('Commit sources')
            with patch.dict(entry['main'].__globals__, {'ROOT': root, 'source_identity': source,
                                                       'shared_soak': lambda *_a, **_k: calls.append(True)}):
                with self.assertRaisesRegex(ValueError, 'Commit sources'):
                    entry['main'](self.arguments(data, profile, output))
            self.assertEqual(calls, []); self.assertFalse(output.exists())

    def test_probe_error_keeps_journal_fails_launcher_and_restores_signal_handlers(self):
        entry = self.load()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); data, profile = self.setup_inputs(root); output = root / 'docs/private/run'
            def probe(*_args, **kwargs):
                kwargs['journal']({'phase': 'warmup', 'rows': [{'status': 'ok'}]})
                raise RuntimeError('PRIVATE INPUT MUST NOT LEAK')
            before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
            with patch.dict(entry['main'].__globals__, {'ROOT': root, 'source_identity': lambda *_: ('a' * 40, {}),
                                                       'shared_soak': probe}):
                self.assertEqual(entry['main'](self.arguments(data, profile, output)), 1)
            launcher = json.loads((output / 'launcher.json').read_text())
            self.assertEqual((launcher['status'], launcher['failureType']), ('failed', 'RuntimeError'))
            self.assertNotIn('PRIVATE', (output / 'launcher.json').read_text())
            self.assertTrue((output / 'pairs.jsonl').read_text().strip())
            self.assertFalse(launcher['servicesStoppedOrRestarted'])
            self.assertEqual(before, {sig: signal.getsignal(sig) for sig in before})
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in output.iterdir()))

    def test_changed_source_cannot_turn_an_observed_model_report_into_launcher_success(self):
        entry = self.load()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); data, profile = self.setup_inputs(root); output = root / 'docs/private/run'
            marker = root / 'source.py'; marker.write_text('before')
            import hashlib
            sources = {'source.py': hashlib.sha256(marker.read_bytes()).hexdigest()}
            def probe(*_args, **_kwargs):
                marker.write_text('after')
                return sealed({'schemaVersion': 'fixture-only', 'status': 'observed'})
            with patch.dict(entry['main'].__globals__, {'ROOT': root, 'source_identity': lambda *_: ('a' * 40, sources),
                                                       'shared_soak': probe}):
                self.assertEqual(entry['main'](self.arguments(data, profile, output)), 1)
            launcher = json.loads((output / 'launcher.json').read_text())
            self.assertEqual((launcher['status'], launcher['failureType']), ('failed', 'SourceChanged'))


if __name__ == '__main__': unittest.main()
