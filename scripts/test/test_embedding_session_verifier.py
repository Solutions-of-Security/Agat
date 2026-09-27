"""Mutations must not turn missing ownership, vectors or cleanup into success."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / 'docs/qualification/local-decisions/performance/evidence/2026-09-27/embedding-session-prototype'
spec = importlib.util.spec_from_file_location('session_verifier', ROOT / 'scripts/verify-embedding-session.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class SessionEvidenceTests(unittest.TestCase):
    @contextmanager
    def changed(self, mutation):
        plan = json.loads((EVIDENCE / 'plan.json').read_text())
        result = json.loads((EVIDENCE / 'result.json').read_text())
        mutation(plan, result)
        with tempfile.TemporaryDirectory(prefix='session-replay-', dir=ROOT / 'docs') as directory:
            target = Path(directory)
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

    def test_missing_phase_or_unreported_retry_fails(self):
        self.rejects(lambda _p, r: r['phases'].pop())
        self.rejects(lambda _p, r: r['requests'].append(dict(r['requests'][0])))

    def test_rehashed_plan_still_rejects_changed_order_and_limits(self):
        self.rejects(lambda p, _r: p['phases'][1].update(mode='isolated'))
        self.rejects(lambda p, _r: p.update(sessionRequestBytes=4194304))

    def test_incomplete_or_source_mismatch_fails(self):
        self.rejects(lambda _p, r: r.update(status='incomplete'))
        self.rejects(lambda p, _r: p['sourceSha256'].update({'scripts/lib/embedding_session.py': '0' * 64}))

    def test_matching_client_server_input_and_wrong_vectors_fail(self):
        def corrupt(_p, result):
            result['phases'][0]['rows'][0]['inputSha256'] = '0' * 64
            identity = result['phases'][0]['rows'][0]['id']
            next(row for row in result['requests'] if row['id'] == identity)['inputSha256'] = '0' * 64
        self.rejects(corrupt)
        self.rejects(lambda _p, r: r['phases'][1]['rows'][0].update(vectorSha256='0' * 64))

    def test_wrong_session_owner_or_reuse_before_ready_fails(self):
        self.rejects(lambda _p, r: r['phases'][5]['rows'][0].update(helperPid=r['phases'][5]['rows'][1]['helperPid']))
        self.rejects(lambda _p, r: r['phases'][1]['warmup'][0].update(finishedNs=r['phases'][1]['rows'][0]['startedNs'] + 1))

    def test_unclosed_pipe_and_descriptor_leak_fail(self):
        self.rejects(lambda _p, r: r['phases'][1]['children'][0].update(stdoutClosed=False))
        self.rejects(lambda _p, r: r['phases'][1]['children'][0].update(returncode=None))
        self.rejects(lambda _p, r: r['phases'][1]['afterClose'].update(descriptors=r['phases'][1]['before']['descriptors'] + 1))

    def test_foreign_or_missing_live_helper_memory_fails(self):
        self.rejects(lambda _p, r: r['phases'][1]['ready']['helperRssBytes'].update({'1': 1000}))
        self.rejects(lambda _p, r: r['phases'][1]['afterCalls'].update(helperRssBytes={}))

    def test_overlapping_requests_on_one_session_fail(self):
        def overlap(_p, result):
            rows = result['phases'][1]['rows']
            rows[1]['startedNs'] = rows[0]['startedNs']
        self.rejects(overlap)


if __name__ == '__main__':
    unittest.main()
