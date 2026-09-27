"""Reject internally rehashed but semantically corrupted worker model evidence."""
from contextlib import contextmanager
import gzip
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / 'docs/qualification/local-decisions/performance/evidence/2026-09-28/embedding-worker-idle-model'
spec = importlib.util.spec_from_file_location('worker_idle_verifier', ROOT / 'scripts/verify-embedding-worker-idle.py')
verifier = importlib.util.module_from_spec(spec); spec.loader.exec_module(verifier)


class WorkerIdleEvidenceTests(unittest.TestCase):
    @contextmanager
    def changed(self, mutate):
        with tempfile.TemporaryDirectory(prefix='worker-idle-replay-', dir=ROOT / 'docs') as folder:
            target = Path(folder); shutil.copytree(EVIDENCE, target, dirs_exist_ok=True)
            plan = json.loads((target / 'plan.json').read_text()); result = json.loads((target / 'result.json').read_text())
            mutate(plan, result, target)
            (target / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
            result['planSha256'] = verifier.support.sha((target / 'plan.json').read_bytes())
            (target / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
            yield target

    def rejects(self, mutate):
        with self.changed(mutate) as target, self.assertRaises((AssertionError, ValueError, KeyError, StopIteration)):
            verifier.verify(target)

    def change_file(self, result, directory, kind, mutate, phase='b1-1'):
        name = f'{phase}.{kind}.json.gz'; path = directory / name
        value = json.loads(gzip.decompress(path.read_bytes())); mutate(value)
        raw = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode(); data = gzip.compress(raw, mtime=0)
        path.write_bytes(data)
        result['files'][name] = {'sha256': verifier.support.sha(data), 'bytes': len(data), 'decodedBytes': len(raw)}

    def probe_rejects(self, mutate):
        self.rejects(lambda _p, r, d: self.change_file(r, d, 'worker', mutate))

    def store_rejects(self, mutate):
        self.rejects(lambda _p, r, d: self.change_file(r, d, 'store', mutate))

    def test_actual_full_vectors_store_and_ownership_match_replay(self):
        self.assertEqual(verifier.verify(EVIDENCE), json.loads((EVIDENCE / 'replay.json').read_text()))

    def test_source_model_thresholds_and_design_are_pinned(self):
        self.rejects(lambda p, _r, _d: p['sourceSha256'].update({'workers/embedding_transport.py': '0' * 64}))
        self.rejects(lambda p, _r, _d: p.update(modelDigest='0' * 64))
        self.rejects(lambda p, _r, _d: p.update(maxComponentDifference=1))
        self.rejects(lambda p, _r, _d: p['phases'][1].update(idleTimeout=0))

    def test_missing_calls_and_edited_bytes_are_not_accepted(self):
        self.probe_rejects(lambda value: value['calls'].pop())
        self.rejects(lambda _p, r, _d: next(iter(r['files'].values())).update(sha256='0' * 64))

    def test_rehashed_call_completion_and_database_still_need_model_reference(self):
        def mutate(_plan, result, directory):
            selected = {}
            def change_probe(probe):
                call = probe['calls'][2]
                call['vectors'][0][0] += .01
                probe['completions'][2]['body']['embeddings'][0]['embedding'] = call['vectors'][0]
                selected.update(inputs=call['inputs'], vectors=call['vectors'])
            def change_store(saved):
                document = next(d for d in saved['rounds'][1] if [c['content'] for c in d['chunks']] == selected['inputs'])
                document['chunks'][0]['embedding_json'] = json.dumps(selected['vectors'][0])
            self.change_file(result, directory, 'worker', change_probe)
            self.change_file(result, directory, 'store', change_store)
        self.rejects(mutate)

    def test_document_vector_and_provenance_are_independently_checked(self):
        self.store_rejects(lambda value: value['rounds'][1][0]['chunks'][0].update(embedding_json='[1,0]'))
        self.store_rejects(lambda value: value['rounds'][1][0]['chunks'][0].update(char_start=1))
        self.store_rejects(lambda value: value['rounds'][1][0]['document'].update(content_sha256='0' * 64))
        self.store_rejects(lambda value: value['rounds'][1][0]['job'].update(status='running'))

    def test_terminal_lease_ids_status_and_ready_events_cannot_be_reused(self):
        self.probe_rejects(lambda value: value['completions'][2].update(path=value['completions'][0]['path']))
        self.probe_rejects(lambda value: value['completions'][2].update(status=503))
        def drop_ready(value):
            value['events'] = [e for e in value['events'] if e['type'] != 'knowledge.document.ready']
        self.store_rejects(drop_ready)

    def test_helper_birth_and_post_idle_ownership_are_required(self):
        self.probe_rejects(lambda value: value['children'][1].update(bornNs=value['calls'][2]['finishedNs']))
        self.probe_rejects(lambda value: value['calls'][2].update(helperPid=value['calls'][0]['helperPid']))

    def test_idle_memory_and_reap_evidence_must_agree(self):
        self.probe_rejects(lambda value: value['snapshots']['afterIdle'].update(helperRssBytes=value['snapshots']['afterFirst']['helperRssBytes']))
        self.probe_rejects(lambda value: value['snapshots']['afterIdle'].update(reaped=[]))
        self.probe_rejects(lambda value: value['retired'][0].update(atNs=value['snapshots']['afterSecond']['atNs']))

    def test_closed_pipes_descriptors_and_threads_are_required(self):
        self.probe_rejects(lambda value: value['children'][0].update(stdinClosed=False))
        self.probe_rejects(lambda value: value.update(fdAfter=value['fdBefore'] + 1))
        self.probe_rejects(lambda value: value.update(liveThreads=['embedding-idle-maintenance']))
        self.probe_rejects(lambda value: value.update(controlErrors=['OSError']))

    def test_finite_components_with_overflowing_norm_fail(self):
        self.probe_rejects(lambda value: value['calls'][0].update(vectors=[[1e200] * 768]))

    def test_idle_window_and_call_lease_order_cannot_be_shortened(self):
        self.probe_rejects(lambda value: value['snapshots']['afterIdle'].update(atNs=value['snapshots']['afterFirst']['atNs'] + 1))
        self.probe_rejects(lambda value: value['calls'][1].update(startedNs=value['calls'][0]['startedNs']))
        self.probe_rejects(lambda value: value['completions'][0].update(atNs=value['calls'][0]['startedNs']))

    def test_owned_server_must_unload_and_every_phase_must_complete(self):
        self.rejects(lambda _p, r, _d: r.update(remainingOwnedPids=[r['ollamaPid']]))
        self.rejects(lambda _p, r, _d: r.update(modelsAfterUnload={'models': [1]}))
        self.rejects(lambda _p, r, _d: r['progress'].pop())
        self.rejects(lambda _p, r, _d: r.update(nodeExitCode=1))


if __name__ == '__main__':
    unittest.main()
