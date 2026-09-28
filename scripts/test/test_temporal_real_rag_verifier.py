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
    evidence = EVIDENCE

    @contextmanager
    def changed(self, mutate):
        names = ['plan.json', 'launcher.json', 'isolated.json', 'session.json']
        files = {name: json.loads((self.evidence / name).read_text()) for name in names}
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
                if 'shadowJournalSha256' in files['launcher.json']:
                    journal = f'{transport}.shadow.jsonl'
                    data = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in files[name]['decisionCalls']).encode()
                    (directory / journal).write_bytes(data)
                    files['launcher.json']['shadowJournalSha256'][journal] = hashlib.sha256(data).hexdigest()
            save('launcher.json')
            (directory / 'tests.log').write_bytes((self.evidence / 'tests.log').read_bytes())
            yield directory

    def rejects(self, mutate):
        with self.changed(mutate) as directory:
            with self.assertRaises((AssertionError, ValueError, KeyError, TypeError)):
                verifier.verify(directory)

    def test_actual_evidence_reproduces_exact_summary(self):
        self.assertEqual(verifier.verify(self.evidence), json.loads((self.evidence / 'verification.json').read_text()))

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


class TemporalRealShadowEvidenceTests(TemporalRealRagEvidenceTests):
    evidence = ROOT / 'docs/qualification/local-decisions/performance/evidence/2026-09-28/temporal-real-shadow/run-warm'

    def test_frozen_source_weights_and_experiment_scope(self):
        self.rejects(lambda f: f['plan.json']['sourceSha256'].pop('decision_runtime/isolated.py'))
        self.rejects(lambda f: f['plan.json']['models'].update({'qwen3:8b': '0' * 64}))
        self.rejects(lambda f: f['plan.json'].update(shadow=False))
        self.rejects(lambda f: f['plan.json']['decision']['runtime']['packages'].update(mlx='changed'))
        self.rejects(lambda f: f['plan.json']['decision']['manifest'].update(artifactSha256='0' * 64))

    def change_answer(self, files, mutation):
        phase = files['isolated.json']; call = phase['decisionCalls'][0]
        mutation(call['result'])
        observation = next(row for row in phase['trace']['decisionObservations'] if row['stageId'] == call['stageId'])
        observation['observation'].update(result=call['result'], status=call['result']['status'], reason=call['result']['reason'])
        phase['shadowRecovery']['firstAcceptedObservationSha256'] = verifier.sha(verifier.compact(observation))

    def test_typed_shadow_distribution_and_input_binding_survive_rehashing(self):
        self.rejects(lambda f: self.change_answer(f, lambda r: r['distribution'][0].update(probability=.123)))
        self.rejects(lambda f: self.change_answer(f, lambda r: r.update(inputSha256='0' * 64)))
        self.rejects(lambda f: self.change_answer(f, lambda r: r.update(generatedTokens=1)))
        self.rejects(lambda f: self.change_answer(f, lambda r: r.update(selectedOptionId='missing')))

    def test_shadow_request_state_order_and_primary_fallback(self):
        self.rejects(lambda f: f['isolated.json']['decisionCalls'][0]['request'].update(state='changed'))
        self.rejects(lambda f: f['isolated.json']['decisionCalls'][0]['request']['options'].reverse())
        self.rejects(lambda f: f['isolated.json']['shadowRecovery'].update(primaryFallbackPreserved=False))

    def test_shadow_is_never_recomputed_during_held_primary_recovery(self):
        self.rejects(lambda f: f['isolated.json']['shadowRecovery'].update(decisionCallsBeforeRelease=2))
        self.rejects(lambda f: f['isolated.json']['decisionCalls'].append(f['isolated.json']['decisionCalls'][0]))
        self.rejects(lambda f: f['isolated.json']['shadowRecovery'].update(firstAcceptedObservationSha256='0' * 64))

    def test_shadow_warmup_health_and_cleanup_are_bound(self):
        self.rejects(lambda f: f['launcher.json']['shadowRuntime'].update(exitCode=75))
        self.rejects(lambda f: f['launcher.json']['shadowRuntime']['after'].update(status='unavailable'))
        self.rejects(lambda f: f['launcher.json']['shadowRuntime']['warmup']['result'].update(status='error'))
        self.rejects(lambda f: f['launcher.json']['ownedPids'].remove(f['launcher.json']['shadowRuntime']['pid']))


class TemporalShadowRuntimeRecoveryEvidenceTests(TemporalRealShadowEvidenceTests):
    evidence = ROOT / 'docs/qualification/local-decisions/performance/evidence/2026-09-28/temporal-shadow-runtime-recovery/run-journal'

    def change_event(self, files, index, mutation):
        event = files['launcher.json']['shadowRecovery']['events'][index]
        mutation(event)
        files[event['transport'] + '.json']['runtimeRecovery']['controlCalls'][index % 2]['response'] = event

    def test_frozen_control_protocol_sources_and_warmup_count(self):
        self.rejects(lambda f: f['plan.json']['sourceSha256'].pop('scripts/lib/temporal_shadow_control.py'))
        self.rejects(lambda f: f['plan.json']['shadowRecovery']['actions'].reverse())
        self.rejects(lambda f: f['plan.json']['decision'].update(warmupCalls=1))

    def test_physical_owner_failure_and_child_retirement(self):
        self.rejects(lambda f: self.change_event(f, 0, lambda e: e.update(exitCode=0)))
        self.rejects(lambda f: self.change_event(f, 0, lambda e: e.update(remainingAfterKill=[e['oldPid']])))
        self.rejects(lambda f: self.change_event(f, 0, lambda e: e.update(ownedBeforeKill=[e['oldPid']])))
        self.rejects(lambda f: f['launcher.json']['shadowRecovery']['runtimes'][0].update(exitCode=130))

    def test_restart_owns_new_ready_runtime_and_same_profile(self):
        self.rejects(lambda f: self.change_event(f, 1, lambda e: e.update(newPid=e['oldPid'])))
        self.rejects(lambda f: self.change_event(f, 1, lambda e: e.update(profileSha256='0' * 64)))
        self.rejects(lambda f: f['launcher.json']['shadowRecovery']['runtimes'][1]['warmup']['result'].update(inputSha256='0' * 64))
        self.rejects(lambda f: f['launcher.json']['shadowRecovery']['runtimes'][1].update(readyMs=1))
        self.rejects(lambda f: f['launcher.json']['shadowRecovery'].update(closed=False))

    def test_control_ack_is_bound_to_phase_instance_and_launcher(self):
        self.rejects(lambda f: self.change_event(f, 0, lambda e: e.update(instanceId='changed')))
        self.rejects(lambda f: f['isolated.json']['runtimeRecovery']['controlCalls'][0]['response'].update(oldPid=1))
        self.rejects(lambda f: f['isolated.json']['runtimeRecovery']['controlCalls'].reverse())

    def test_unavailability_has_no_inference_result_and_primary_is_preserved(self):
        self.rejects(lambda f: f['isolated.json']['decisionCalls'][1].update(result=f['isolated.json']['decisionCalls'][0]['result']))
        self.rejects(lambda f: f['isolated.json']['decisionCalls'][1].update(transportError='ECONNRESET'))
        def change_fallback(files):
            phase = files['isolated.json']
            observation = next(row for row in phase['trace']['decisionObservations']
                               if row['stageId'] == phase['decisionCalls'][1]['stageId'])
            observation['observation']['reason'] = 'inference_timeout'
            phase['runtimeRecovery']['fallbackSnapshot']['observationSha256'] = verifier.sha(verifier.compact(observation))
        self.rejects(change_fallback)
        self.rejects(lambda f: f['isolated.json']['runtimeRecovery']['fallbackSnapshot'].update(stageSha256='0' * 64))

    def test_recovery_occurs_only_after_fallback_and_while_third_response_is_held(self):
        self.rejects(lambda f: f['isolated.json']['runtimeRecovery']['controlCalls'][0].update(startedMs=1))
        self.rejects(lambda f: f['isolated.json']['runtimeRecovery']['fallbackSnapshot'].update(recordedMs=1))
        self.rejects(lambda f: f['isolated.json']['runtimeRecovery']['fallbackSnapshot'].update(
            thirdResponseReleasedMs=f['isolated.json']['primaryCalls'][2]['finishedMs'] + 1))

    def test_journal_corruption_rejected_even_after_digest_is_updated(self):
        with self.changed(lambda f: None) as directory:
            journal = directory / 'isolated.shadow.jsonl'
            journal.write_text(journal.read_text().splitlines()[0] + '\n')
            file = directory / 'launcher.json'; launcher = json.loads(file.read_text())
            launcher['shadowJournalSha256'][journal.name] = hashlib.sha256(journal.read_bytes()).hexdigest()
            file.write_text(json.dumps(launcher))
            with self.assertRaises(AssertionError): verifier.verify(directory)


if __name__ == '__main__':
    unittest.main()
