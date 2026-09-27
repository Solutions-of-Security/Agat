"""Offline replay and mutation checks for the real model persistent-session experiment."""
from contextlib import contextmanager
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / 'docs/qualification/local-decisions/performance/evidence/2026-09-27/embedding-session-model'
spec = importlib.util.spec_from_file_location('session_model_verifier', ROOT / 'scripts/verify-embedding-session-model.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class SessionModelEvidenceTests(unittest.TestCase):
    @contextmanager
    def changed(self, mutation):
        with tempfile.TemporaryDirectory(prefix='model-transport-replay-', dir=ROOT / 'docs') as folder:
            target = Path(folder)
            shutil.copytree(EVIDENCE, target, dirs_exist_ok=True)
            plan = json.loads((target / 'plan.json').read_text())
            result = json.loads((target / 'result.json').read_text())
            mutation(plan, result, target)
            (target / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
            result['planSha256'] = hashlib.sha256((target / 'plan.json').read_bytes()).hexdigest()
            (target / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
            yield target

    def rejects(self, mutation):
        with self.changed(mutation) as directory:
            with self.assertRaises((AssertionError, ValueError, KeyError)):
                verifier.verify(directory)

    def test_real_vectors_and_timings_replay_exactly(self):
        self.assertEqual(verifier.verify(EVIDENCE), json.loads((EVIDENCE / 'replay.json').read_text()))

    def test_failure_or_missing_call_is_not_a_completed_experiment(self):
        self.rejects(lambda _p, r, _d: r.update(status='incomplete'))
        self.rejects(lambda _p, r, _d: r['phases'][-1]['rows'].pop())

    def test_model_and_loopback_endpoint_cannot_be_relabelled(self):
        self.rejects(lambda p, _r, _d: p.update(modelDigest='0' * 64))
        self.rejects(lambda p, _r, _d: p.update(endpoint='https://example.com/v1'))

    def test_tolerance_cannot_be_loosened_after_observation(self):
        self.rejects(lambda p, _r, _d: p.update(maxComponentDifference=.1))
        self.rejects(lambda p, _r, _d: p.update(maxCosineDistance=.1))

    def test_metadata_changes_and_rehashed_inputs_are_rejected(self):
        self.rejects(lambda _p, r, _d: r['metadataAfter'].update(version='changed'))
        self.rejects(lambda p, _r, _d: p['cases']['b1-0'].__setitem__(0, 'Different input'))

    def test_compressed_and_decoded_vector_hashes_are_both_checked(self):
        self.rejects(lambda _p, r, _d: next(iter(r['vectorFiles'].values())).update(sha256='0' * 64))
        self.rejects(lambda _p, r, _d: next(iter(r['vectorFiles'].values())).update(decodedBytes=1))

    def test_rehashed_valid_vector_changes_still_violate_paired_contract(self):
        def corrupt(_plan, result, directory):
            row = result['phases'][1]['rows'][0]
            old = directory / 'vectors' / f"{row['vectorSha256']}.json.gz"
            vectors = json.loads(gzip.decompress(old.read_bytes()))
            vectors[0][0] += .01
            raw = json.dumps(vectors, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
            digest = hashlib.sha256(raw).hexdigest()
            data = gzip.compress(raw, mtime=0)
            (directory / 'vectors' / f'{digest}.json.gz').write_bytes(data)
            result['vectorFiles'][digest] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'decodedBytes': len(raw)}
            row['vectorSha256'] = digest
        self.rejects(corrupt)

    def test_transport_cleanup_and_actual_overlap_are_checked(self):
        self.rejects(lambda _p, r, _d: r['phases'][1]['children'][0].update(returncode=None))
        self.rejects(lambda _p, r, _d: r['phases'][1]['children'][0].update(stdinClosed=False))
        def overlap(_plan, result, _directory):
            rows = result['phases'][4]['rows']
            rows[1]['startedNs'] = rows[0]['startedNs']
        self.rejects(overlap)

    def test_abba_order_and_warmup_exclusion_are_pinned(self):
        self.rejects(lambda p, _r, _d: p['phases'][0].update(measured=True))
        self.rejects(lambda p, _r, _d: p['phases'][4].update(mode='session'))

    def test_wrong_owner_and_readiness_after_request_fail(self):
        self.rejects(lambda _p, r, _d: r['phases'][9]['rows'][0].update(helperPid=r['phases'][9]['rows'][1]['helperPid']))
        self.rejects(lambda _p, r, _d: r['phases'][1]['readiness'][0].update(finishedNs=r['phases'][1]['rows'][0]['startedNs']))

    def test_unaccounted_live_helper_memory_and_descriptors_fail(self):
        self.rejects(lambda _p, r, _d: r['phases'][1]['ready']['helperRssBytes'].update({'1': 1000}))
        self.rejects(lambda _p, r, _d: r['phases'][1]['afterCalls'].update(helperRssBytes={}))
        self.rejects(lambda _p, r, _d: r['phases'][1]['afterClose'].update(descriptors=r['phases'][1]['before']['descriptors'] + 1))

    def test_current_or_invented_source_cannot_replace_measured_commit(self):
        self.rejects(lambda p, _r, _d: p['sourceSha256'].update({'workers/embedding_http.py': '0' * 64}))


if __name__ == '__main__':
    unittest.main()
