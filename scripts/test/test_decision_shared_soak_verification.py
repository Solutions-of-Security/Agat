import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from decision_runtime.artifacts import sealed
from scripts.lib.decision_shared_soak import shared_soak
from scripts.lib.decision_shared_soak_verification import SCHEMA, verify_measurements
from scripts.test.test_decision_baselines import dataset
from scripts.test.test_decision_performance import Fixture
from scripts.test.test_decision_shared_soak import SyntheticClock, TimedBlocks

ROOT = Path(__file__).resolve().parents[2]


class VerificationTest(unittest.TestCase):
    def fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = SyntheticClock(); journal = []; data = dataset(); profile = Fixture().engine.profile()
            result = shared_soak(data, 'http://127.0.0.1:1', lambda: None, Path(tmp), profile,
                                 duration_s=3, max_blocks=4, probe=TimedBlocks(clock), clock=clock, journal=journal.append)
            reports = [json.loads((Path(tmp) / e['path']).read_text()) for e in result['blocks']]
        plan = sealed({'schemaVersion': 'agat.decision.shared-soak-plan.v1', 'implementationCommit': 'a' * 40,
                       'qualification': 'not_assessed', 'routingEnabled': False, 'durationSeconds': 3,
                       'maxBlocks': 4, 'profileSha256': hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                       'datasetSha256': data['sha256'], 'primaryDigest': '1' * 64,
                       'sourceFiles': reports[0]['harnessFiles']})
        launcher = sealed({'schemaVersion': 'agat.decision.shared-soak-launcher.v1', 'status': 'observed',
                           'qualification': 'not_assessed', 'routingEnabled': False, 'failureType': None,
                           'planSha256': plan['sha256'], 'resultSha256': result['sha256'], 'implementationCommit': 'a' * 40,
                           'serviceOwnership': 'caller', 'servicesStoppedOrRestarted': False})
        return plan, launcher, result, reports, journal, data, profile

    def reseal(self, values):
        plan, launcher, result, reports, *_ = values
        for report in reports:
            digest = sealed({k: v for k, v in report.items() if k != 'sha256'})['sha256']; report['sha256'] = digest
        for entry, report in zip(result['blocks'], reports): entry['sha256'] = report['sha256']
        result['sha256'] = sealed({k: v for k, v in result.items() if k != 'sha256'})['sha256']
        plan['sha256'] = sealed({k: v for k, v in plan.items() if k != 'sha256'})['sha256']
        launcher.update(planSha256=plan['sha256'], resultSha256=result['sha256'])
        launcher['sha256'] = sealed({k: v for k, v in launcher.items() if k != 'sha256'})['sha256']

    def reject(self, mutate):
        values = self.fixture(); mutate(values); self.reseal(values)
        with self.assertRaises(ValueError): verify_measurements(*values)

    def test_complete_fixture_is_verified_with_independent_counts(self):
        proof = verify_measurements(*self.fixture())
        self.assertEqual(proof['schemaVersion'], SCHEMA)
        self.assertEqual((proof['status'], proof['measuredMs'], proof['counts']['measuredCalls']), ('verified', 3000, 32))
        self.assertEqual(proof['counts']['overlappingPairs'], 4)
        self.assertFalse(proof['routingEnabled'])

    def test_resealed_request_count_cannot_hide_dropped_rows(self):
        self.reject(lambda v: v[2].update(measuredAttempts=31))

    def test_missing_journal_pair_is_not_accepted_by_matching_report_hashes(self):
        self.reject(lambda v: v[4].pop())

    def test_resealed_percentile_is_recomputed_from_raw_rows(self):
        self.reject(lambda v: v[3][0]['phases'][0]['summary']['decision']['computedWallMs'].update(p95=99))

    def test_unmet_duration_cannot_be_promoted_with_coherent_plan_and_launcher(self):
        def mutate(v):
            v[0]['durationSeconds'] = 4; v[2]['plan']['durationSeconds'] = 4
        self.reject(mutate)

    def test_busy_row_cannot_become_a_time_budget_boundary(self):
        def mutate(v):
            row = v[3][-1]['phases'][-1]['rows'][-1]; row.update(status='unavailable', reason='busy')
            v[3][-1].update(status='degraded', stoppedReason='time_budget')
            v[2]['blocks'][-1].update(status='degraded', stoppedReason='time_budget')
        self.reject(mutate)

    def test_model_identity_drift_is_rejected_even_when_both_blocks_claim_stable(self):
        self.reject(lambda v: v[3][-1]['primary'].update(digest='2' * 64))

    def test_wrong_http_overlap_and_wrong_input_are_rejected(self):
        self.reject(lambda v: v[3][0]['phases'][3]['pairs'][0].update(requestOverlapMs=0))
        self.reject(lambda v: v[3][0]['phases'][0]['rows'][0].update(inputSha256='f' * 64))

    def test_launcher_failure_and_changed_harness_are_rejected(self):
        self.reject(lambda v: v[1].update(status='failed', failureType='SourceChanged'))
        self.reject(lambda v: v[3][0]['harnessFiles'].update({'scripts/lib/decision_shared_load.py': '0' * 64}))

    def test_time_budget_label_requires_actual_elapsed_budget(self):
        def mutate(v):
            v[3][-1].update(status='degraded', stoppedReason='time_budget')
            v[2]['blocks'][-1].update(status='degraded', stoppedReason='time_budget')
        self.reject(mutate)

    def test_forward_row_chronology_and_nonempty_harness_binding_are_required(self):
        def move(v):
            row=v[3][0]['phases'][-1]['rows'][-1]
            row.update(startedMs=10,finishedMs=110,wallMs=100)
        self.reject(move)
        self.reject(lambda v: v[3][0].update(harnessFiles={}))


if __name__ == '__main__': unittest.main()
