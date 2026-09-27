"""Replay real evidence and reject semantically invalid, consistently rehashed copies."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / 'docs/qualification/local-decisions/performance/evidence/2026-09-27'
SYNC, ISOLATED = EVIDENCE / 'retrieval-main-sync', EVIDENCE / 'retrieval-main-isolated'
spec = importlib.util.spec_from_file_location('rag_http_verifier', ROOT / 'scripts/verify-rag-http-probe.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


class MainHttpEvidenceTest(unittest.TestCase):
    @contextmanager
    def changed(self, mutation):
        with tempfile.TemporaryDirectory(prefix='rag-http-verifier-', dir=ROOT / 'docs') as folder:
            target = Path(folder)
            shutil.copytree(ISOLATED, target, dirs_exist_ok=True)
            files = {'plan': 'plan.json', 'client': 'client-postgresql.json', 'server': 'server-postgresql.json', 'launcher': 'launcher-result.json'}
            records = {key: read(target / name) for key, name in files.items()}
            mutation(records)
            write(target / files['plan'], records['plan'])
            digest = hashlib.sha256((target / files['plan']).read_bytes()).hexdigest()
            for key in ('client', 'server'):
                records[key]['planSha256'] = digest
                write(target / files[key], records[key])
            # Rebuild transport checksums so rejection must come from the contract.
            records['launcher']['files'] = {name: hashlib.sha256((target / name).read_bytes()).hexdigest()
                                            for name in records['launcher']['files']}
            write(target / files['launcher'], records['launcher'])
            yield target

    def test_main_pair_replays_exactly(self):
        self.assertEqual(verifier.compare(SYNC, ISOLATED), read(EVIDENCE / 'retrieval-main-comparison.json'))

    def test_historical_handler_pair_replays_unchanged(self):
        self.assertEqual(verifier.compare(EVIDENCE / 'retrieval-executor-sync', EVIDENCE / 'retrieval-executor-isolated'),
                         read(EVIDENCE / 'retrieval-executor-comparison.json'))

    def test_main_cannot_be_relabelled_as_handler(self):
        with self.changed(lambda rows: rows['plan'].pop('coordinatorEntry')) as target:
            with self.assertRaises(AssertionError):
                verifier.verify(target)

    def test_health_reports_the_real_mode_and_admission(self):
        for key, value in [('execution', 'sync'), ('accepting', False)]:
            with self.subTest(field=key):
                def mutate(rows):
                    rows['client']['phases'][1]['healthProbes'][0]['response']['knowledgeSearch'][key] = value
                with self.changed(mutate) as target:
                    with self.assertRaises(AssertionError):
                        verifier.verify(target)

    def test_runtime_must_be_a_separate_cleanly_exited_process(self):
        for case in ('helper-pid', 'failed-exit'):
            with self.subTest(case=case):
                def mutate(rows):
                    if case == 'helper-pid':
                        pid = rows['launcher']['children'][0]['pid']
                        rows['server']['mainRuntime']['pid'] = rows['client']['runtimePid'] = pid
                    else:
                        rows['server']['mainRuntime']['code'] = 1
                with self.changed(mutate) as target:
                    with self.assertRaises(AssertionError):
                        verifier.verify(target)

    def test_real_maintenance_is_required(self):
        def mutate(rows):
            for phase in rows['server']['phases']:
                phase['maintenance'] = []
        with self.changed(mutate) as target:
            with self.assertRaises(AssertionError):
                verifier.verify(target)

    def test_pair_requires_same_build_and_pool_budget(self):
        for case in ('build', 'pool'):
            with self.subTest(case=case):
                def mutate(rows):
                    if case == 'build':
                        rows['plan']['compiledSha256']['apps/coordinator/dist/server.js'] = '0' * 64
                    else:
                        rows['plan']['poolBudget']['mainPoolMaxPerRole'] = 3
                with self.changed(mutate) as target:
                    with self.assertRaises(AssertionError):
                        verifier.compare(SYNC, target)

    def test_main_cannot_be_compared_to_handler(self):
        with self.assertRaises(AssertionError):
            verifier.compare(EVIDENCE / 'retrieval-executor-sync', ISOLATED)

    def test_python_optimization_is_rejected(self):
        result = subprocess.run([sys.executable, '-O', str(ROOT / 'scripts/verify-rag-http-probe.py'),
                                 '--evidence-dir', str(ISOLATED)], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Evidence verification requires Python assertions', result.stderr)


if __name__ == '__main__':
    unittest.main()
