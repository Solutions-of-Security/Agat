"""Mutation tests for the full ordinary-worker RAG transport evidence."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / 'docs/qualification/local-decisions/performance/evidence/2026-09-27/embedding-worker-rag'
spec = importlib.util.spec_from_file_location('embedding_rag_verifier', ROOT / 'scripts/verify-embedding-rag.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class EmbeddingRagEvidenceTests(unittest.TestCase):
    @contextmanager
    def changed(self, mutation):
        files = {path.name: json.loads(path.read_text()) for path in EVIDENCE.glob('*.json')
                 if path.name in {'plan.json', 'workflow.json', 'launcher.json'} or path.name.endswith('.worker.json')}
        mutation(files)
        with tempfile.TemporaryDirectory(prefix='embedding-rag-replay-', dir=ROOT / 'docs') as temporary:
            directory = Path(temporary)
            def save(name):
                path = directory / name
                path.write_text(json.dumps(files[name], ensure_ascii=False, indent=2) + '\n')
                return hashlib.sha256(path.read_bytes()).hexdigest()
            plan_sha = save('plan.json')
            files['workflow.json']['planSha256'] = files['launcher.json']['planSha256'] = plan_sha
            for block in files['workflow.json']['blocks']:
                name = block['workflow']['phase']['id'] + '.worker.json'
                digest = save(name)
                block['probeSha256'] = files['launcher.json']['probeSha256'][name] = digest
            files['launcher.json']['workflowSha256'] = save('workflow.json')
            save('launcher.json')
            yield directory

    def rejects(self, mutation):
        with self.changed(mutation) as directory:
            with self.assertRaises((AssertionError, KeyError, ValueError, TypeError)):
                verifier.verify(directory)

    def test_real_evidence_replays_exactly(self):
        self.assertEqual(verifier.verify(EVIDENCE), json.loads((EVIDENCE / 'replay.json').read_text()))

    def test_http_helper_scope_must_be_frozen_and_present_in_every_probe(self):
        self.rejects(lambda f: f['plan.json'].update(ownedHttpProbe='unknown'))
        self.rejects(lambda f: f['plan.json'].update(ownedHttpProbe='agat.worker.owned-http.v1'))
        self.rejects(lambda f: f['0_isolated.worker.json'].update(ownedHttp={}))

    def test_frozen_runtime_source_cannot_be_omitted_or_rehashed(self):
        self.rejects(lambda f: f['plan.json']['sourceSha256'].pop('workers/embedding_transport.py'))
        self.rejects(lambda f: f['plan.json']['sourceSha256'].update({'workers/embedding_transport.py': '0' * 64}))

    def test_fixture_weights_and_order_remain_fixed(self):
        self.rejects(lambda f: f['plan.json']['fixture']['rag']['sources'][0].update(content='changed'))
        self.rejects(lambda f: f['plan.json']['models'].update({'qwen3:8b': '0' * 64}))
        self.rejects(lambda f: f['plan.json']['blocks'][0].update(transport='session'))

    def test_incomplete_block_or_unexpected_shadow_is_rejected(self):
        self.rejects(lambda f: f['workflow.json']['blocks'][0]['workflow'].update(status='incomplete'))
        self.rejects(lambda f: f['workflow.json']['blocks'][0]['workflow']['decisionCalls'].append({}))
        self.rejects(lambda f: f['workflow.json'].update(routingEnabled=True))

    def test_primary_output_and_prompt_changes_are_detected(self):
        self.rejects(lambda f: f['workflow.json']['blocks'][0]['workflow']['primaryCalls'][0].update(output='changed'))
        self.rejects(lambda f: f['workflow.json']['blocks'][0]['workflow']['primaryCalls'][0].update(messagesSha256='0' * 64))

    def test_retrieval_source_query_and_citation_binding_is_checked(self):
        def stage(files):
            return files['workflow.json']['blocks'][0]['workflow']['workflows'][0]['stages'][0]
        self.rejects(lambda f: stage(f)['retrieval']['hits'][0].update(chunkSha256='0' * 64))
        self.rejects(lambda f: stage(f)['retrieval']['queries'][0].update(vectorSha256='0' * 64))
        self.rejects(lambda f: stage(f)['retrieval'].update(bothSourcesCited=False))

    def test_duplicate_proxy_requests_and_worker_input_drift_fail(self):
        self.rejects(lambda f: f['workflow.json']['blocks'][0]['workflow']['embeddingCalls'].append(
            dict(f['workflow.json']['blocks'][0]['workflow']['embeddingCalls'][0])))
        self.rejects(lambda f: f['0_isolated.worker.json']['calls'][0]['inputSha256'].__setitem__(0, '0' * 64))

    def test_helper_leak_or_missing_inventory_cannot_be_hidden(self):
        self.rejects(lambda f: f['1_session.worker.json']['children'][0].update(returncode=None))
        self.rejects(lambda f: f['1_session.worker.json']['children'][0].update(stdoutClosed=False))
        self.rejects(lambda f: f['0_isolated.worker.json']['children'].pop())

    def test_session_reuse_cannot_outlive_its_owner_or_close_early(self):
        self.rejects(lambda f: f['1_session.worker.json']['requests'][0].update(returncode=0))
        self.rejects(lambda f: f['1_session.worker.json']['requests'][0].update(callerStartedNs=1))
        self.rejects(lambda f: f['1_session.worker.json']['retired'][0].update(
            atNs=f['1_session.worker.json']['calls'][0]['startedNs']))

    def test_queue_ownership_cannot_overlap_on_one_helper(self):
        def corrupt(files):
            probe = files['1_session.worker.json']
            first = probe['requests'][0]
            second = next(row for row in probe['requests'] if row['pid'] != first['pid'])
            second['pid'] = first['pid']
        self.rejects(corrupt)

    def test_unknown_rss_process_bad_memory_and_nonquiescent_worker_fail(self):
        self.rejects(lambda f: f['1_session.worker.json']['samples'][5]['helperRssBytes'].update({'1': 4096}))
        self.rejects(lambda f: f['1_session.worker.json']['samples'][5].update(workerRssBytes=float('nan')))
        self.rejects(lambda f: f['1_session.worker.json'].update(fdAfter=f['1_session.worker.json']['fdBefore'] + 1))
        self.rejects(lambda f: f['1_session.worker.json']['liveThreads'].append('embedding-session-deadline'))

    def test_vector_drift_cannot_be_hidden_by_rebinding_retrieval(self):
        def corrupt(files):
            workflow = files['workflow.json']['blocks'][0]['workflow']
            vector = workflow['workflows'][0]['stages'][0]['retrieval']['queries'][0]['vectorSha256']
            for call in workflow['embeddingCalls']:
                call['vectorSha256'] = ['0' * 64 if value == vector else value for value in call['vectorSha256']]
            for run in workflow['workflows']:
                for stage in run['stages']:
                    query = stage['retrieval']['queries'][0]
                    if query['vectorSha256'] == vector:
                        query['vectorSha256'] = '0' * 64
        self.rejects(corrupt)

    def test_owned_model_server_and_worker_cleanup_must_be_complete(self):
        self.rejects(lambda f: f['launcher.json']['remainingOwnedPids'].append(f['launcher.json']['ollamaPid']))
        self.rejects(lambda f: f['launcher.json']['modelsAfterUnload']['models'].append({'name': 'qwen3:8b'}))
        self.rejects(lambda f: f['launcher.json']['ownedPids'].remove(f['1_session.worker.json']['pid']))


if __name__ == '__main__':
    unittest.main()
