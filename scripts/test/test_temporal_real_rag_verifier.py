"""Reject rehashed corruption of actual real-model/Temporal/RLS evidence."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / 'docs/qualification/local-decisions/performance/evidence/2026-09-28/temporal-real-rag/run-final'
spec = importlib.util.spec_from_file_location('temporal_real_rag_verifier', ROOT / 'scripts/verify-temporal-real-rag.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class TemporalRealRagEvidenceTests(unittest.TestCase):
    @contextmanager
    def changed(self, mutate):
        names = ['plan.json', 'launcher.json', 'isolated.json', 'session.json']
        files = {name: json.loads((EVIDENCE / name).read_text()) for name in names}
        mutate(files)
        with tempfile.TemporaryDirectory(prefix='temporal-real-replay-', dir=ROOT / 'docs') as temporary:
            directory = Path(temporary)
            def save(name):
                data = (json.dumps(files[name], ensure_ascii=False, indent=2) + '\n').encode()
                (directory / name).write_bytes(data)
                return hashlib.sha256(data).hexdigest()
            plan_sha = save('plan.json')
            files['launcher.json']['planSha256'] = plan_sha
            for transport in ('isolated', 'session'):
                name = f'{transport}.json'
                files[name]['planSha256'] = plan_sha
                files['launcher.json']['phaseSha256'][name] = save(name)
            save('launcher.json')
            (directory / 'tests.log').write_bytes((EVIDENCE / 'tests.log').read_bytes())
            yield directory

    def rejects(self, mutate):
        with self.changed(mutate) as directory:
            with self.assertRaises((AssertionError, ValueError, KeyError, TypeError)):
                verifier.verify(directory)

    def test_actual_evidence_reproduces_exact_summary(self):
        self.assertEqual(verifier.verify(EVIDENCE), json.loads((EVIDENCE / 'verification.json').read_text()))

    def test_frozen_source_weights_and_experiment_scope(self):
        self.rejects(lambda f: f['plan.json']['sourceSha256'].pop('workers/embedding_http.py'))
        self.rejects(lambda f: f['plan.json']['models'].update({'qwen3:8b': '0' * 64}))
        self.rejects(lambda f: f['plan.json'].update(shadow=True))
        self.rejects(lambda f: f['plan.json'].update(databaseIsolation='shared'))

    def test_outputs_prompts_and_extra_model_call(self):
        self.rejects(lambda f: f['isolated.json']['primaryCalls'][0].update(output='changed'))
        self.rejects(lambda f: f['isolated.json']['primaryCalls'][0].update(messagesSha256='0' * 64))
        self.rejects(lambda f: f['isolated.json']['primaryCalls'].append(f['isolated.json']['primaryCalls'][0]))

    def test_retrieval_source_query_citation_and_lease_input(self):
        def retrieval(files):
            return next(row for row in files['isolated.json']['trace']['events'] if row['type'] == 'knowledge.retrieved')['data']
        self.rejects(lambda f: retrieval(f)['hits'][0].update(content='changed'))
        self.rejects(lambda f: retrieval(f)['queries'][0].update(vectorSha256='0' * 64))
        self.rejects(lambda f: f['isolated.json']['retrieval'][0].update(bothSourcesCited=False))
        self.rejects(lambda f: next(row for row in f['isolated.json']['trace']['run']['stages'] if row['processNodeId'] == 'analyze').update(input='changed'))

    def test_worker_restart_must_happen_while_response_is_held(self):
        self.rejects(lambda f: f['isolated.json']['recovery'].update(restoredAtMs=f['isolated.json']['primaryCalls'][1]['finishedMs'] + 1))
        self.rejects(lambda f: f['isolated.json']['recovery'].update(firstAcceptedStageSha256='0' * 64))
        self.rejects(lambda f: f['isolated.json']['children'][1].update(signal=None, exitCode=0))

    def test_history_retry_identity_run_and_tick_binding(self):
        self.rejects(lambda f: f['isolated.json'].update(workflowRunId='changed'))
        def no_retry(files):
            for event in files['isolated.json']['history']['events']:
                if 'activityTaskStartedEventAttributes' in event:
                    event['activityTaskStartedEventAttributes']['attempt'] = 1
        self.rejects(no_retry)
        self.rejects(lambda f: f['isolated.json']['recovery'].update(secondIdentity='unknown-worker'))
        self.rejects(lambda f: f['isolated.json']['ticks'][0].update(dropped=False))
        self.rejects(lambda f: f['isolated.json']['ticks'][-1]['response'].update(status='failed'))

    def test_rls_and_shadow_are_not_optional(self):
        self.rejects(lambda f: f['isolated.json']['database']['visibility'].update(foreign=[1, 3, 2]))
        self.rejects(lambda f: f['isolated.json']['database'].update(releaseRegistryDenied=False))
        self.rejects(lambda f: f['isolated.json']['trace']['decisionObservations'].append({}))

    def test_process_container_and_model_cleanup(self):
        self.rejects(lambda f: f['launcher.json']['remainingOwnedPids'].append(f['launcher.json']['ollamaPid']))
        self.rejects(lambda f: f['launcher.json']['modelsAfterUnload']['models'].append({'name': 'qwen3:8b'}))
        self.rejects(lambda f: f['launcher.json']['ownedPids'].remove(f['isolated.json']['children'][0]['pid']))
        self.rejects(lambda f: f['launcher.json']['containers'].pop(next(iter(f['launcher.json']['containers']))))

    def test_failed_or_missing_phase_is_not_a_success(self):
        self.rejects(lambda f: f['session.json'].update(status='fail'))
        self.rejects(lambda f: f['session.json']['checks'].pop('nativeReplayWithoutSideEffects'))


if __name__ == '__main__':
    unittest.main()
