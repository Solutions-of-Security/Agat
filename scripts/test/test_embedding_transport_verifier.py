"""Reject incomplete or consistently rehashed transport profiling evidence."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / 'docs/qualification/local-decisions/performance/evidence/2026-09-27/embedding-transport-profile'
spec = importlib.util.spec_from_file_location('transport_verifier', ROOT / 'scripts/verify-embedding-transport.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class TransportEvidenceTests(unittest.TestCase):
    @contextmanager
    def changed(self, mutation):
        plan = json.loads((EVIDENCE / 'plan.json').read_text())
        result = json.loads((EVIDENCE / 'result.json').read_text())
        mutation(plan, result)
        with tempfile.TemporaryDirectory(prefix='transport-replay-', dir=ROOT / 'docs') as folder:
            target = Path(folder)
            (target / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
            result['planSha256'] = hashlib.sha256((target / 'plan.json').read_bytes()).hexdigest()
            (target / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
            yield target

    def rejects(self, mutation):
        with self.changed(mutation) as target:
            with self.assertRaises((AssertionError, ValueError, KeyError)):
                verifier.verify(target)

    def test_complete_profile_replays_exactly(self):
        self.assertEqual(verifier.verify(EVIDENCE), json.loads((EVIDENCE / 'replay.json').read_text()))

    def test_incomplete_run_or_cleanup_fails(self):
        self.rejects(lambda _p, r: r.update(status='incomplete'))
        self.rejects(lambda _p, r: r.update(cleanup=False))

    def test_missing_phase_or_hidden_retry_fails(self):
        self.rejects(lambda _p, r: r['phases'].pop())
        self.rejects(lambda _p, r: r['server'].append(dict(r['server'][0])))

    def test_rehashed_plan_cannot_relabel_abba_order(self):
        self.rejects(lambda p, _r: p['phases'][8].update(mode='isolated'))

    def test_source_and_vector_hashes_are_independently_checked(self):
        self.rejects(lambda p, _r: p['sourceSha256'].update({'workers/embedding_http.py': '0' * 64}))
        self.rejects(lambda _p, r: r['phases'][0]['rows'][0].update(vectorSha256='0' * 64))

    def test_matching_client_and_server_input_tampering_is_rejected(self):
        def corrupt(_plan, result):
            result['phases'][0]['rows'][0]['inputSha256'] = '0' * 64
            result['server'][0]['inputSha256'] = '0' * 64
        self.rejects(corrupt)

    def test_open_pipe_or_unreaped_helper_is_rejected(self):
        self.rejects(lambda _p, r: r['phases'][1]['rows'][0]['children'][0].update(returncode=None))
        self.rejects(lambda _p, r: r['phases'][1]['rows'][0]['children'][0].update(stdoutClosed=False))

    def test_descriptor_growth_is_not_hidden(self):
        self.rejects(lambda _p, r: r['phases'][1].update(fdAfter=r['phases'][1]['fdBefore'] + 1))

    def test_late_cancellation_timestamp_is_rejected(self):
        def corrupt(_plan, result):
            row = result['phases'][-2]['rows'][0]
            row['cancelledNs'] = row['finishedNs'] + 1
        self.rejects(corrupt)

    def test_foreign_or_negative_memory_sample_is_rejected(self):
        self.rejects(lambda _p, r: r['phases'][0]['samples'][0].update(parentRssBytes=-1))
        self.rejects(lambda _p, r: r['phases'][0]['samples'][0]['helperRssBytes'].update({'1': 1000}))

    def test_actual_request_overlap_must_match_configured_concurrency(self):
        def corrupt(_plan, result):
            rows = result['phases'][8]['rows']
            rows[1]['startedNs'] = rows[0]['startedNs']
        self.rejects(corrupt)


if __name__ == '__main__':
    unittest.main()
