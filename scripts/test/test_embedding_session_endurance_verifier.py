"""Offline validation of sustained ownership, cancellations and resource evidence."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / 'docs/qualification/local-decisions/performance/evidence/2026-09-27/embedding-session-endurance'
spec = importlib.util.spec_from_file_location('session_endurance_verifier', ROOT / 'scripts/verify-embedding-session-endurance.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class SessionEnduranceEvidenceTests(unittest.TestCase):
    @contextmanager
    def changed(self, mutation):
        plan = json.loads((EVIDENCE / 'plan.json').read_text())
        result = json.loads((EVIDENCE / 'result.json').read_text())
        mutation(plan, result)
        with tempfile.TemporaryDirectory(prefix='session-endurance-replay-', dir=ROOT / 'docs') as directory:
            path = Path(directory)
            (path / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
            result['planSha256'] = hashlib.sha256((path / 'plan.json').read_bytes()).hexdigest()
            (path / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
            yield path

    def rejects(self, mutation):
        with self.changed(mutation) as directory:
            with self.assertRaises((AssertionError, ValueError, KeyError)):
                verifier.verify(directory)

    def test_recorded_control_and_recovery_replay_exactly(self):
        self.assertEqual(verifier.verify(EVIDENCE), json.loads((EVIDENCE / 'replay.json').read_text()))

    def test_incomplete_run_or_shorter_schedule_fails(self):
        self.rejects(lambda _p, r: r.update(status='incomplete'))
        self.rejects(lambda _p, r: r.update(scheduleFinishedNs=r['scheduleStartedNs'] + 119_000_000_000))
        self.rejects(lambda _p, r: r['rounds'].pop())

    def test_fault_schedule_and_measured_sources_are_frozen(self):
        self.rejects(lambda p, _r: p['faults']['19'].update(actor=3))
        self.rejects(lambda p, _r: p['sourceSha256'].update({'scripts/lib/embedding_session.py': '0' * 64}))

    def test_duplicate_http_and_late_cancel_timestamp_fail(self):
        self.rejects(lambda _p, r: r['server'].append(dict(r['server'][0])))
        self.rejects(lambda _p, r: r['rounds'][19]['rows'][0].update(cancelledNs=r['rounds'][19]['rows'][0]['finishedNs'] + 1))

    def test_same_rehashed_client_server_input_is_still_rejected(self):
        def corrupt(_p, result):
            row = result['rounds'][0]['rows'][0]
            row['inputSha256'] = '0' * 64
            next(observed for observed in result['server'] if observed['id'] == row['id'])['inputSha256'] = '0' * 64
        self.rejects(corrupt)
        self.rejects(lambda _p, r: r['rounds'][0]['rows'][0].update(vectorSha256='0' * 64))

    def test_cancelled_helper_must_be_reaped_before_recovery(self):
        self.rejects(lambda _p, r: r['rounds'][19]['rows'][0].update(helperReturncode=None))
        self.rejects(lambda _p, r: r['rounds'][19]['rows'][0].update(stdoutClosed=False))
        self.rejects(lambda _p, r: r['rounds'][19]['rows'][1].update(helperPid=r['rounds'][19]['rows'][0]['helperPid']))

    def test_foreign_owner_or_control_helper_replacement_fails(self):
        self.rejects(lambda _p, r: r['rounds'][0]['rows'][0].update(helperPid=r['rounds'][0]['rows'][1]['helperPid']))
        self.rejects(lambda _p, r: r['rounds'][19]['idle']['actorPids'].__setitem__(3, r['rounds'][19]['idle']['actorPids'][0]))

    def test_nonquiescent_threads_descriptors_and_unaccounted_rss_fail(self):
        self.rejects(lambda _p, r: r['rounds'][10]['idle'].update(guardThreads=1))
        self.rejects(lambda _p, r: r['rounds'][10]['idle'].update(descriptors=r['before']['descriptors'] + 9))
        self.rejects(lambda _p, r: r['rounds'][10]['idle']['helperRssBytes'].update({'1': 1000}))

    def test_final_cleanup_cannot_omit_helper_or_fixture(self):
        self.rejects(lambda _p, r: r['children'].pop())
        self.rejects(lambda _p, r: r.update(cleanup=False))
        self.rejects(lambda _p, r: r['afterClose'].update(descriptors=r['before']['descriptors'] + 1))


if __name__ == '__main__':
    unittest.main()
