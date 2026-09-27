"""Portable mutation checks for the pinned model idle/resume experiment."""
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
EVIDENCE = ROOT / 'docs/qualification/local-decisions/performance/evidence/2026-09-27/embedding-idle-model'
spec = importlib.util.spec_from_file_location('idle_model_verifier', ROOT / 'scripts/verify-embedding-idle-model.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class IdleModelEvidenceTests(unittest.TestCase):
    @contextmanager
    def changed(self, mutation):
        with tempfile.TemporaryDirectory(prefix='idle-model-replay-', dir=ROOT / 'docs') as folder:
            target = Path(folder)
            shutil.copytree(EVIDENCE, target, dirs_exist_ok=True)
            plan = json.loads((target / 'plan.json').read_text())
            result = json.loads((target / 'result.json').read_text())
            mutation(plan, result, target)
            (target / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
            result['planSha256'] = verifier.support.sha((target / 'plan.json').read_bytes())
            (target / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
            yield target

    def rejects(self, mutation):
        with self.changed(mutation) as directory:
            with self.assertRaises((AssertionError, KeyError, ValueError)):
                verifier.verify(directory)

    def test_full_vectors_and_real_ownership_replay_exactly(self):
        self.assertEqual(verifier.verify(EVIDENCE), json.loads((EVIDENCE / 'replay.json').read_text()))

    def test_source_model_endpoint_and_tolerances_are_pinned(self):
        self.rejects(lambda p, _r, _d: p['sourceSha256'].update({'scripts/lib/embedding_idle_pool.py': '0' * 64}))
        self.rejects(lambda p, _r, _d: p.update(modelDigest='0' * 64))
        self.rejects(lambda p, _r, _d: p.update(endpoint='http://127.0.0.1:11434/v1'))
        self.rejects(lambda p, _r, _d: p.update(maxComponentDifference=.1))

    def test_abba_idle_window_and_warmup_cannot_change_after_measurement(self):
        self.rejects(lambda p, _r, _d: p['phases'][0].update(mode='retire'))
        self.rejects(lambda p, _r, _d: p['phases'][1].update(idleTimeoutSeconds=10))
        self.rejects(lambda _p, r, _d: r['phases'][1]['afterIdle'].update(atNs=r['phases'][1]['afterFirst']['atNs'] + 1))
        self.rejects(lambda _p, r, _d: r['warmups'].pop())

    def test_missing_or_wrong_vector_in_either_burst_fails(self):
        self.rejects(lambda _p, r, _d: r['phases'][-1]['rows'].pop())
        self.rejects(lambda _p, r, _d: r['phases'][1]['rows'][8].update(inputSha256='0' * 64))
        self.rejects(lambda _p, r, _d: next(iter(r['vectorFiles'].values())).update(decodedBytes=1))

    def test_rehashed_vector_change_still_fails_paired_comparison(self):
        def corrupt(_plan, result, directory):
            row = result['phases'][1]['rows'][8]
            values = json.loads(gzip.decompress((directory / 'vectors' / f"{row['vectorSha256']}.json.gz").read_bytes()))
            values[0][0] += .01
            raw = json.dumps(values, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
            digest = hashlib.sha256(raw).hexdigest()
            data = gzip.compress(raw, mtime=0)
            (directory / 'vectors' / f'{digest}.json.gz').write_bytes(data)
            result['vectorFiles'][digest] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'decodedBytes': len(raw)}
            row['vectorSha256'] = digest
        self.rejects(corrupt)

    def test_finite_components_with_overflowing_norm_are_rejected(self):
        def corrupt(_plan, result, directory):
            old = result['phases'][0]['rows'][1]['vectorSha256']
            raw = json.dumps([[1e200] * 768], separators=(',', ':')).encode()
            digest = hashlib.sha256(raw).hexdigest()
            data = gzip.compress(raw, mtime=0)
            (directory / 'vectors' / f'{digest}.json.gz').write_bytes(data)
            result['vectorFiles'][digest] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'decodedBytes': len(raw)}
            for phase in result['warmups'] + result['phases']:
                for row in phase['rows']:
                    if row['vectorSha256'] == old:
                        row['vectorSha256'] = digest
            del result['vectorFiles'][old]
            (directory / 'vectors' / f'{old}.json.gz').unlink()
        self.rejects(corrupt)

    def test_fake_idle_memory_or_missing_reap_cannot_hide_live_helpers(self):
        self.rejects(lambda _p, r, _d: r['phases'][0]['afterIdle'].update(helperRssBytes={}))
        self.rejects(lambda _p, r, _d: r['phases'][1]['afterIdle'].update(reapedPids=[]))
        self.rejects(lambda _p, r, _d: r['phases'][1].update(idleReaps=0))

    def test_restart_must_use_new_pid_and_guard_binding(self):
        self.rejects(lambda _p, r, _d: r['phases'][1]['rows'][8].update(helperPid=r['phases'][1]['rows'][0]['helperPid']))
        self.rejects(lambda _p, r, _d: r['phases'][1]['children'][1].update(arguments=['--serve']))
        self.rejects(lambda _p, r, _d: r['phases'][1]['children'][1].update(owner=r['phases'][1]['children'][0]['owner']))

    def test_transport_interval_and_birth_must_belong_to_actual_call(self):
        self.rejects(lambda _p, r, _d: r['phases'][0]['rows'][0].update(transportFinishedNs=r['phases'][0]['rows'][0]['startedNs']))
        self.rejects(lambda _p, r, _d: r['phases'][0]['children'][0].update(bornNs=r['phases'][0]['rows'][0]['finishedNs']))
        self.rejects(lambda _p, r, _d: r['phases'][0]['rows'][1].update(actor=1))

    def test_closed_resources_and_maintenance_are_required(self):
        self.rejects(lambda _p, r, _d: r['phases'][1]['children'][0].update(returncode=None))
        self.rejects(lambda _p, r, _d: r['phases'][1]['children'][0].update(stdinClosed=False))
        self.rejects(lambda _p, r, _d: r['phases'][1].update(remainingThreads=['maintenance']))
        self.rejects(lambda _p, r, _d: r['phases'][1].update(maintenanceFailure='OSError'))

    def test_unloaded_owned_server_and_complete_observation_are_required(self):
        self.rejects(lambda _p, r, _d: r.update(status='incomplete'))
        self.rejects(lambda _p, r, _d: r.update(remainingOwnedPids=[r['ollamaPid']]))
        self.rejects(lambda _p, r, _d: r.update(modelsAfterUnload={'models': [{'name': 'remaining'}]}))


if __name__ == '__main__':
    unittest.main()
