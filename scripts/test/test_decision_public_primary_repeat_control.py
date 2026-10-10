"""Actual A/A coordinator/worker; models are fixtures and never confer capacity."""
import copy
import hashlib
import json
import shutil
import subprocess
import unittest
from unittest.mock import patch

from scripts.lib import decision_public_primary_repeat_control as diagnostic
from scripts.test import test_decision_public_real_primary as shared


@unittest.skipUnless(shutil.which('node') and (shared.ROOT/'node_modules/tsx').exists(), 'Requires Node diagnostic dependencies')
class RepeatControlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): shared.MatchedWorkflowTest.setUpClass.__func__(cls, suite=diagnostic)
    mutate_rows = shared.MatchedWorkflowTest.mutate_rows

    def check(self, artifacts=None, recipe=None, driver=None, protocol=None):
        return diagnostic.verify_inventory(self.context, protocol or diagnostic.PROTOCOL, recipe or self.recipe, driver or self.driver, artifacts or self.artifacts)

    def test_full_AA_inputs_identical_requests_outputs_no_shadow_and_actual_overlap(self):
        v = self.check(); self.assertEqual(v['actualWorkflows'], 12); self.assertEqual(v['primaryOutputsPreserved'], 12)
        self.assertEqual(v['matchedPrimaryRequests'], 6); self.assertEqual(v['matchedOutputPairs'], 6)
        self.assertEqual(v['nativeCaseCalls'], 0); self.assertEqual(v['durableShadowReturns'], 0)
        self.assertEqual(len(self.native_calls), 0); self.assertEqual(len(self.primary_calls), 12)
        self.assertEqual(v['actualTwoSlotPrimaryWitnesses'], 6); self.assertGreater(v['nativePrimaryHttpOverlapMs']['min'], 0)
        self.assertEqual(v['nativeBackgroundSnapshots'], 6); self.assertFalse(v['classificationAccuracyMeasured'])
        self.assertFalse(v['causalOverheadEstablished']); self.assertFalse(v['primaryConcurrencyCapacityQualified'])

    def test_async_completion_order_allowed_but_duplicate_primary_rejected(self):
        self.assertEqual(self.check(self.mutate_rows('primary-http.jsonl', lambda rows: rows.reverse()))['actualWorkflows'], 12)
        with self.assertRaises(ValueError): self.check(self.mutate_rows('primary-http.jsonl', lambda rows: rows.append(copy.deepcopy(rows[0]))))

    def test_different_valid_outputs_are_observed_without_failure_or_lucky_retry(self):
        artifacts = copy.deepcopy(self.artifacts); driver = copy.deepcopy(self.driver)
        rows = diagnostic.journal(artifacts['primary-http.jsonl']); target = next(r for r in rows if r['index'] == 0 and r['condition'] == 'repeatB')
        output = 'OTHER_VALID_PRIMARY_OUTPUT'; output_sha = hashlib.sha256(output.encode()).hexdigest()
        native = json.loads(target['nativeResponseBody']); native['message']['content'] = output
        translated = json.loads(target['responseBody']); translated['choices'][0]['message']['content'] = output
        for key, value in (('nativeResponseBody', native), ('responseBody', translated)):
            target[key] = shared.encoded(value).decode(); target[key+'Sha256'] = hashlib.sha256(target[key].encode()).hexdigest()
        target['outputSha256'] = output_sha; artifacts['primary-http.jsonl'] = b''.join(shared.encoded(r) for r in rows)
        route = next(r for r in driver['routes'] if r['index'] == 0 and r['condition'] == 'repeatB')
        trace = json.loads(artifacts[route['traceFile']]); next(s for s in trace['run']['stages'] if s['processNodeId'] == 'agent')['output'] = output
        artifacts[route['traceFile']] = shared.encoded(trace); route.update(outputSha256=output_sha, traceFileSha256=hashlib.sha256(artifacts[route['traceFile']]).hexdigest())
        artifacts['workflow-routes.jsonl'] = b''.join(shared.encoded(r) for r in driver['routes'])
        cohort = json.loads(artifacts['cohort.http.json'])
        artifacts['cohort.http.json'] = shared.encoded(cohort)
        census = diagnostic.journal(artifacts['cohort-http.jsonl']); census[0]['bodySha256'] = hashlib.sha256(artifacts['cohort.http.json']).hexdigest()
        artifacts['cohort-http.jsonl'] = b''.join(shared.encoded(r) for r in census)
        leases = diagnostic.journal(artifacts['coordinator-http.jsonl'])
        for row in leases:
            if row['runId'] == route['runId'] and row['path'].endswith('/complete'):
                body = json.loads(row['requestBody']); body['output'] = output; row['requestBody'] = shared.encoded(body).decode()
                row['requestBodySha256'] = hashlib.sha256(row['requestBody'].encode()).hexdigest()
        artifacts['coordinator-http.jsonl'] = b''.join(shared.encoded(r) for r in leases)
        v = self.check(artifacts, driver=driver); self.assertEqual(v['differentOutputIndices'], [0]); self.assertEqual(v['matchedOutputPairs'], 5)
        self.assertEqual(v['retryCount'], 0)

    def test_hidden_shadow_intent_or_case_score_cannot_pass(self):
        with self.assertRaises(ValueError): self.check(self.mutate_rows('coordinator-http.jsonl', lambda rows: rows[0].update(path='/api/v1/leases/'+rows[0]['leaseId']+'/decision-shadow/intent')))
        with self.assertRaises(ValueError): self.check(self.mutate_rows('decision-http.jsonl', lambda rows: rows.append({'hidden': True})))
        driver = copy.deepcopy(self.driver); driver['decisionCalls'] = 1
        with self.assertRaises(ValueError): self.check(driver=driver)

    def test_background_hidden_native_call_and_restart_rejected_after_rehash(self):
        for change in (lambda raw: raw.replace('outcome="ok"} 2', 'outcome="ok"} 3'),
            lambda raw: raw.replace('agat_decision_server_start_time_seconds ', 'agat_decision_server_start_time_seconds 1')):
            def mutate(rows):
                rows[-1]['metricsRaw'] = change(rows[-1]['metricsRaw'])
                rows[-1]['metricsSha256'] = hashlib.sha256(rows[-1]['metricsRaw'].encode()).hexdigest()
            with self.assertRaises(ValueError): self.check(self.mutate_rows('native-background-metrics.jsonl', mutate))

    def test_same_graph_process_and_version_are_required(self):
        for key, value in (('processId', 'another-process'), ('version', 2), ('graphFileSha256', '0'*64)):
            recipe = copy.deepcopy(self.recipe); recipe['processes']['repeatB'][key] = value
            with self.assertRaises(ValueError): self.check(recipe=recipe)

    def test_graph_cannot_enable_shadow_even_after_updating_recipe_pin(self):
        artifacts = copy.deepcopy(self.artifacts); recipe = copy.deepcopy(self.recipe)
        graph = json.loads(artifacts['graph-control.json']); graph['nodes'][1]['config']['decisionShadow'] = {'mode': 'shadow'}
        artifacts['graph-control.json'] = shared.encoded(graph)
        for condition in diagnostic.PROTOCOL['conditions']:
            recipe['processes'][condition]['graphFileSha256'] = hashlib.sha256(artifacts['graph-control.json']).hexdigest()
        with self.assertRaises(ValueError): self.check(artifacts, recipe=recipe)

    def test_duplicate_census_run_or_missing_projection_cannot_hide_accounting(self):
        for mutate in (lambda c: c['traces'].append(copy.deepcopy(c['traces'][0])),
            lambda c: c['traces'][0].pop('decisionCallerAccounting')):
            artifacts = copy.deepcopy(self.artifacts); cohort = json.loads(artifacts['cohort.http.json']); mutate(cohort)
            artifacts['cohort.http.json'] = shared.encoded(cohort)
            rows = diagnostic.journal(artifacts['cohort-http.jsonl']); rows[0]['bodySha256'] = hashlib.sha256(artifacts['cohort.http.json']).hexdigest()
            artifacts['cohort-http.jsonl'] = b''.join(shared.encoded(r) for r in rows)
            with self.assertRaises(ValueError): self.check(artifacts)

    def test_condition_leaking_prompt_shortened_input_or_changed_seed_rejected(self):
        def mutate(rows, change):
            row = rows[0]; native = json.loads(row['nativeRequestBody']); change(native)
            row['nativeRequestBody'] = shared.encoded(native).decode(); row['nativeRequestBodySha256'] = hashlib.sha256(row['nativeRequestBody'].encode()).hexdigest()
        for change in (lambda n: n['messages'][1].update(content='repeatA '+n['messages'][1]['content']),
            lambda n: n['messages'][1].update(content='shortened'), lambda n: n['options'].update(seed=1)):
            with self.assertRaises(ValueError): self.check(self.mutate_rows('primary-http.jsonl', lambda rows: mutate(rows, change)))

    def test_lost_original_batch_missing_overlap_or_answered_witness_rejected(self):
        with self.assertRaises(ValueError): self.check(self.mutate_rows('paired-batches.jsonl', lambda rows: rows.pop()))
        def serial(rows):
            pair = [r for r in rows if r['pair'] == 0 and r['condition'] == 'repeatA']; pair[1]['nativeStartedAt'] = pair[0]['completedAt']
        with self.assertRaises(ValueError): self.check(self.mutate_rows('primary-http.jsonl', serial))
        def answered(rows):
            item = rows[0]['traces'][0]; trace = json.loads(item['body']); trace['run']['status'] = 'completed'
            item['body'] = shared.encoded(trace).decode(); item['bodySha256'] = hashlib.sha256(item['body'].encode()).hexdigest()
        with self.assertRaises(ValueError): self.check(self.mutate_rows('paired-primary-witnesses.jsonl', answered))

    def test_actual_lease_run_stage_binding_and_completion_required(self):
        for key, value in (('stageId', 'foreign-stage'), ('nodeId', 'foreign-worker'), ('leaseId', 'foreign-lease'), ('runId', 'foreign-run')):
            with self.assertRaises(ValueError): self.check(self.mutate_rows('coordinator-http.jsonl', lambda rows: rows[0].update({key: value})))
        with self.assertRaises(ValueError): self.check(self.mutate_rows('coordinator-http.jsonl', lambda rows: next(r for r in rows if r['path'].endswith('/complete')).update(httpStatus=400)))

    def test_reordered_denominator_and_posthoc_budget_or_equality_requirement_rejected(self):
        with self.assertRaises(ValueError): self.check(self.mutate_rows('workflow-routes.jsonl', lambda rows: rows.reverse()))
        for key, value in (('workerConcurrency', 1), ('primaryNumParallel', 1), ('primaryContextLength', 8192), ('outputEqualityRequired', True), ('retryCount', 1)):
            protocol = copy.deepcopy(diagnostic.PROTOCOL); protocol[key] = value
            with self.assertRaises(ValueError): self.check(protocol=protocol)

    def test_actual_authenticated_census_and_zero_shadow_trace_required(self):
        with self.assertRaises(ValueError): self.check(self.mutate_rows('cohort-http.jsonl', lambda rows: rows[0].update(unauthenticatedStatus=200)))
        artifacts = copy.deepcopy(self.artifacts); route = self.driver['routes'][0]; trace = json.loads(artifacts[route['traceFile']])
        trace['decisionObservations'].append({'unexpected': True}); artifacts[route['traceFile']] = shared.encoded(trace)
        driver = copy.deepcopy(self.driver); driver['routes'][0]['traceFileSha256'] = hashlib.sha256(artifacts[route['traceFile']]).hexdigest()
        artifacts['workflow-routes.jsonl'] = b''.join(shared.encoded(r) for r in driver['routes'])
        with self.assertRaises(ValueError): self.check(artifacts, driver=driver)


@unittest.skipUnless(shutil.which('node') and (shared.ROOT/'node_modules/tsx').exists(), 'Requires Node diagnostic dependencies')
class RepeatReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): shared.ReplayVerificationTest.setUpClass.__func__(cls, suite=diagnostic, actor=RepeatControlTest)
    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()
    replay = shared.ReplayVerificationTest.replay

    def test_replay_bound_sources_zero_native_cases_and_no_model_network(self):
        original = subprocess.Popen
        def git_only(args, *positional, **kwargs):
            if args[0] != 'git': raise AssertionError('Replay attempted model/worker')
            return original(args, *positional, **kwargs)
        with patch('subprocess.Popen', side_effect=git_only), patch('socket.create_connection', side_effect=AssertionError('Replay attempted network')):
            v = self.replay()
        self.assertEqual(v['status'], 'pass'); self.assertEqual(v['nativeCalls'], 2); self.assertEqual(v['primaryScoringCalls'], 12)
        self.assertEqual(v['modelCallsDuringVerification'], 0)

    def test_unpinned_source_unknown_cleanup_and_physical_extra_native_rejected(self):
        plan = copy.deepcopy(self.plan); plan['sourceFiles'].pop(diagnostic.DRIVER_PATH)
        with self.assertRaises(ValueError): self.replay(plan=plan)
        for mutate in (lambda r: r.update(remainingOwnedPids=None), lambda r: r['samples'][-1]['counters'].update(ok=99)):
            result = copy.deepcopy(self.result); mutate(result)
            with self.assertRaises(ValueError): self.replay(result=result)

    def test_real_runner_context_and_parallelism_and_background_origin_required(self):
        artifacts = copy.deepcopy(self.artifacts); artifacts['primary.log'] = artifacts['primary.log'].replace(b'n_seq_max = 2', b'n_seq_max = 1')
        result = copy.deepcopy(self.result); result['artifactSha256']['primary.log'] = hashlib.sha256(artifacts['primary.log']).hexdigest()
        with self.assertRaises(ValueError): self.replay(result=result, artifacts=artifacts)
        result = copy.deepcopy(self.result); result['samples'][1]['serverStart'] += 1
        with self.assertRaises(ValueError): self.replay(result=result)

    def test_posthoc_output_equality_and_extra_native_warmup_cannot_pass_replay(self):
        plan = copy.deepcopy(self.plan); plan['protocol']['outputEqualityRequired'] = True
        with self.assertRaises(ValueError): self.replay(plan=plan)
        result = copy.deepcopy(self.result); result['warmup'].append(copy.deepcopy(result['warmup'][0]))
        with self.assertRaises(ValueError): self.replay(result=result)


class RepeatOrderTest(unittest.TestCase):
    def test_odd_whole_inventory_retains_singleton_in_both_repeats(self):
        schedule = diagnostic.batches(49)
        self.assertEqual(len(schedule), 50); self.assertEqual(schedule[-2:], [(24, 'repeatA', [48]), (24, 'repeatB', [48])])
        self.assertEqual(schedule[2:4], [(1, 'repeatB', [2, 3]), (1, 'repeatA', [2, 3])])
        self.assertEqual(len(set(diagnostic.route_order(49))), 98)


if __name__ == '__main__': unittest.main()
