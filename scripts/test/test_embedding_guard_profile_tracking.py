"""The guard comparison observes real current and pinned historical helpers."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('guard_tracking', ROOT / 'scripts/profile-embedding-parent-guard.py')
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


class GuardProfileTrackingTests(unittest.TestCase):
    def test_before_and_after_both_transports_match_their_actual_process(self):
        with tempfile.TemporaryDirectory(prefix='agat-guard-smoke-') as folder:
            folder = Path(folder)
            for name in profile.TRANSPORTS:
                (folder / Path(name).name).write_bytes(subprocess.check_output(['git', 'show', f'{profile.BASELINE}:{name}'], cwd=ROOT))
            old = profile.load('guard_tracking_http', folder / 'embedding_http.py')
            with patch.dict(sys.modules, {'embedding_http': old}):
                old_session = profile.load('guard_tracking_session', folder / 'embedding_transport.py')
            for mode in ('isolated', 'session'):
                for guard in (False, True):
                    with self.subTest(mode=mode, guard=guard):
                        fixture = profile.support.Fixture()
                        identity = f'smoke-{mode}-{guard}'
                        fixture.entered[identity] = threading.Event()
                        try:
                            with patch.object(profile.model, 'BASE', fixture.url.removesuffix('/v1')), \
                                    patch.object(profile.model, 'MODEL', 'synthetic-768'), \
                                    patch.object(profile.model, 'inputs', return_value=[f'{identity}/success/0']), \
                                    patch.dict(os.environ, {'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'}):
                                phase, rows = profile.run_phase({'id': identity, 'mode': mode, 'guard': guard,
                                    'concurrency': 1, 'calls': 1, 'batch': 1},
                                    *((profile.embedding_http, profile.embedding_transport) if guard else (old, old_session)))
                            self.assertEqual(profile.support.sha(profile.support.encoded(rows[0][1])), fixture.expected[1, 768])
                            self.assertEqual(len(phase['children']), 1)
                            child = phase['children'][0]
                            self.assertEqual(child['arguments'], (['--serve'] if mode == 'session' else []) +
                                             (['--parent-pid', str(os.getpid())] if guard else []))
                            self.assertEqual(child['pid'], rows[0][0]['helperPid'])
                            self.assertEqual(child['returncode'], 0)
                            self.assertTrue(child['stdinClosed'] and child['stdoutClosed'])
                            self.assertEqual(phase['before']['descriptors'], phase['afterClose']['descriptors'])
                        finally:
                            fixture.close()


if __name__ == '__main__':
    unittest.main()
