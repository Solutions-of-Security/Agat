"""Reject incomplete and consistently rehashed mutations of the real embedding profile."""
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
BASE = ROOT / 'docs/qualification/local-decisions/performance/evidence/2026-09-27'
EVIDENCE = BASE / 'embedding-http'
spec = importlib.util.spec_from_file_location('embedding_verifier', ROOT / 'scripts/verify-embedding-http-probe.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class EmbeddingHttpEvidenceTest(unittest.TestCase):
    @contextmanager
    def changed(self, mutation):
        with tempfile.TemporaryDirectory(prefix='embedding-http-verifier-', dir=ROOT / 'docs') as folder:
            target = Path(folder)
            shutil.copytree(EVIDENCE, target, dirs_exist_ok=True)
            files = {'plan': 'plan.json', 'launcher': 'launcher-result.json'}
            files.update({f'{kind}-{layout}': f'{kind}-{layout}.json' for kind in ('client', 'server', 'postgres-image')
                          for layout in ('shared', 'independent')})
            rows = {key: json.loads((target / name).read_text()) for key, name in files.items()}
            mutation(rows)
            def write(name, value):
                (target / name).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
            write('plan.json', rows['plan'])
            digest = hashlib.sha256((target / 'plan.json').read_bytes()).hexdigest()
            for key, name in files.items():
                if key.startswith(('client-', 'server-')):
                    rows[key]['planSha256'] = digest
                if key not in ('plan', 'launcher'):
                    write(name, rows[key])
            rows['launcher']['files'] = {name: hashlib.sha256((target / name).read_bytes()).hexdigest()
                                         for name in rows['launcher']['files']}
            write('launcher-result.json', rows['launcher'])
            yield target

    def rejects(self, mutation):
        with self.changed(mutation) as target:
            with self.assertRaises((AssertionError, ValueError)):
                verifier.verify(target)

    def test_real_profile_replays_exactly(self):
        self.assertEqual(verifier.verify(EVIDENCE), json.loads((BASE / 'embedding-http-replay.json').read_text()))

    def test_incomplete_run_and_unclean_shutdown_fail(self):
        self.rejects(lambda rows: rows['launcher'].update(status='incomplete'))
        self.rejects(lambda rows: rows['server-shared']['mains'][0].update(code=1))
        self.rejects(lambda rows: rows['launcher'].update(containersRemoved=False))

    def test_payload_and_saved_vectors_must_match(self):
        for key, value in [('dimensions', 3), ('vectorSha256', '0' * 64), ('contentSha256', '0' * 64),
                           ('readyEvents', 2), ('jobStatus', 'running'), ('failures', 1)]:
            with self.subTest(field=key):
                self.rejects(lambda rows: rows['server-shared']['phases'][2]['jobs'][0].update({key: value}))

    def test_independent_collections_cannot_be_relabelled(self):
        def mutate(rows):
            for kind in ('client', 'server'):
                jobs = rows[f'{kind}-independent']['phases'][2]['jobs']
                jobs[1]['collection'] = jobs[0]['collection']
        self.rejects(mutate)

    def test_duplicate_document_results_do_not_count_as_complete(self):
        def mutate(rows):
            first = rows['client-shared']['phases'][2]['jobs'][0]['document']
            for kind in ('client', 'server'):
                rows[f'{kind}-shared']['phases'][2]['jobs'][1]['document'] = first
            rows['client-shared']['phases'][2]['completions'][1]['identity'] = first
        self.rejects(mutate)

    def test_measured_concurrency_is_replayed_from_request_intervals(self):
        def mutate(rows):
            for response in rows['client-shared']['phases'][2]['completions']:
                response.update(startedMs=0, finishedMs=response['wallMs'])
        self.rejects(mutate)

    def test_control_status_is_not_replaced_by_a_valid_flag(self):
        self.rejects(lambda rows: rows['client-shared']['phases'][1]['healthProbes'][0]['response'].update(status=503, valid=True))
        self.rejects(lambda rows: rows['client-shared']['phases'][1]['renewalProbes'][0]['response'].update(status=404, valid=True))

    def test_control_probe_gaps_cannot_be_hidden(self):
        self.rejects(lambda rows: rows['client-shared']['phases'][1].update(healthProbes=[]))
        self.rejects(lambda rows: rows['client-shared']['phases'][1]['healthProbes'][1].update(tick=5))

    def test_renewal_must_preserve_a_valid_deadline(self):
        for value in ('not-a-date', '2000-01-01T00:00:00.000Z'):
            self.rejects(lambda rows: rows['server-shared']['phases'][1]['renewals'][0].update(finalExpiry=value))

    def test_each_main_requires_real_maintenance_under_load(self):
        def mutate(rows):
            for phase in rows['server-shared']['phases'][2:]:
                phase['metrics'][1]['maintenance'] = []
        self.rejects(mutate)

    def test_static_protocol_and_pool_budget_cannot_change(self):
        self.rejects(lambda rows: rows['plan'].update(iterations=1))
        self.rejects(lambda rows: rows['plan']['poolBudget'].update(eachMainPoolMaxPerRole=1))
        self.rejects(lambda rows: rows['plan']['sourceSha256'].pop('scripts/serve-embedding-http-probe.ts'))

    def test_both_layouts_require_same_postgres_image(self):
        self.rejects(lambda rows: rows['postgres-image-independent'].update(imageId='sha256:' + '0' * 64))

    def test_python_optimization_cannot_disable_validation(self):
        result = subprocess.run([sys.executable, '-O', str(ROOT / 'scripts/verify-embedding-http-probe.py'),
                                 '--evidence-dir', str(EVIDENCE)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires Python assertions', result.stderr)


if __name__ == '__main__':
    unittest.main()
