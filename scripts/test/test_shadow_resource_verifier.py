"""Synthetic counters only; no host resource measurements are test fixtures."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('resource_verifier', ROOT / 'scripts/verify-temporal-real-rag.py')
verifier = importlib.util.module_from_spec(spec); spec.loader.exec_module(verifier)
from scripts.lib.shadow_resource_sample import PLAN


def fixture():
    owned = {10, 11, 20, 30, 31}
    groups = [{}, {'shadow': [10, 11]}, {'shadow': [10, 11], 'ollama': [20]},
        *[{'shadow': [10, 11], 'ollama': [20], 'workload': [pid]} for pid in (30, 30, 31, 31, 31)],
        {'shadow': [10, 11], 'ollama': [20]}, {}]
    phases = ['before_models', 'after_shadow_warmup', 'after_ollama_warmup',
              'isolated', 'isolated', 'session', 'session', 'session', 'before_cleanup', 'after_cleanup']
    samples = []
    for index, (phase, group) in enumerate(zip(phases, groups, strict=True)):
        samples.append({'phase': phase, 'startedMs': index * 10 + 1, 'finishedMs': index * 10 + 2,
            'groups': group, 'exitedDuringSample': [], 'processes': [
                {'pid': pid, 'startTicks': 1, 'userTicks': index + 1, 'systemTicks': index + 1,
                 'rssBytes': pid * 1024, 'footprintBytes': pid * 2048} for pid in sorted(set().union(*map(set, group.values())))],
            'pressureDispatchLevel': 1, 'vm': {'pageSizeBytes': 4096, 'pages': {key: 7 for key in
                ('Pages free', 'Pages active', 'Pages inactive', 'Pages wired down', 'Pages occupied by compressor',
                 'Pages stored in compressor', 'Pageins', 'Pageouts', 'Swapins', 'Swapouts', 'Compressions', 'Decompressions')}},
            'swap': {'totalBytes': 4 * 1024**2, 'usedBytes': 1024**2, 'freeBytes': 3 * 1024**2}})
    return {'resources': deepcopy(PLAN)}, {'ollamaPid': 20, 'workloads': [{'pid': 30}, {'pid': 31}],
        'shadowRecovery': {'runtimes': [{'pid': 10}]}, 'resources': {'schema': 'agat.shadow.resources.v1',
            'timebase': {'numer': 1, 'denom': 1}, 'ownedPids': sorted(owned), 'errors': [], 'samples': samples}}, owned


class ResourceVerifierTests(unittest.TestCase):
    def rejects(self, mutate):
        plan, launcher, owned = fixture(); mutate(plan, launcher)
        with self.assertRaises(AssertionError): verifier.verify_resources(plan, launcher, owned, 200)

    def test_synthetic_observation_summary(self):
        plan, launcher, owned = fixture(); result = verifier.verify_resources(plan, launcher, owned, 200)
        self.assertEqual(result['samples'], 10); self.assertEqual(result['ownedSampledPids'], 5)
        self.assertEqual(result['maxObservedShadowRssBytes'], 21 * 1024)
        self.assertEqual(result['causalConclusion'], 'not_established')

    def test_units_and_pressure_are_not_reinterpreted(self):
        self.rejects(lambda p, l: l['resources']['samples'][0].update(pressureDispatchLevel=0))
        self.rejects(lambda p, l: l['resources']['samples'][0]['vm'].update(pageSizeBytes=8192))
        self.rejects(lambda p, l: l['resources']['samples'][0]['swap'].update(usedBytes=2**60))
        self.rejects(lambda p, l: l['resources']['samples'][1]['processes'][0].update(rssBytes=-1))

    def test_process_scope_and_known_roots_are_required(self):
        self.rejects(lambda p, l: p['resources'].update(processScope='all_processes'))
        self.rejects(lambda p, l: l['resources']['ownedPids'].append(1))
        self.rejects(lambda p, l: l['resources']['samples'][1]['processes'][0].update(pid=1))
        self.rejects(lambda p, l: l['shadowRecovery']['runtimes'][0].update(pid=99))

    def test_same_process_cpu_counters_cannot_go_backwards(self):
        self.rejects(lambda p, l: l['resources']['samples'][2]['processes'][0].update(userTicks=0))
        self.rejects(lambda p, l: l['resources']['timebase'].update(denom=0))

    def test_cleanup_time_and_sampling_errors_are_not_hidden(self):
        self.rejects(lambda p, l: l['resources']['samples'][-1].update(phase='before_cleanup'))
        self.rejects(lambda p, l: l['resources']['samples'][2].update(startedMs=1))
        self.rejects(lambda p, l: l['resources']['errors'].append({'type': 'PermissionError'}))

    def test_exit_race_must_account_for_the_missing_sample(self):
        plan, launcher, owned = fixture(); row = launcher['resources']['samples'][1]
        row['processes'].pop(); row['exitedDuringSample'] = [11]
        verifier.verify_resources(plan, launcher, owned, 200)
        row['exitedDuringSample'] = []
        with self.assertRaises(AssertionError): verifier.verify_resources(plan, launcher, owned, 200)

    def test_cli_refuses_resource_artifacts_outside_private_docs_before_loading_models(self):
        directory = ROOT / 'docs/forbidden-resource-evidence'
        result = subprocess.run([__import__('sys').executable, 'scripts/run-temporal-real-rag.py', '--evidence-dir', str(directory),
            '--shadow-python', 'missing', '--shadow-manifest', 'missing', '--shadow-recovery', '--shadow-resources'],
            cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Resource diagnostics must stay under ignored docs/private', result.stderr)
        self.assertFalse(directory.exists())


if __name__ == '__main__': unittest.main()
