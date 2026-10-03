import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from decision_runtime.artifacts import sealed, write_new
from decision_runtime.contracts import fingerprint
from scripts.lib.decision_endurance import endurance
from scripts.lib.decision_soak import continuous_soak, validate_plan
from scripts.lib.decision_soak_verification import LAUNCHER_SCHEMA, PLAN_SCHEMA, verify_run
from scripts.test.test_decision_baselines import dataset
from scripts.test.test_decision_endurance import ClockFixture


class SoakFixture:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.fixture = ClockFixture()
        self.fixture.engine.backend.identity = {**self.fixture.engine.backend.identity, 'artifactSha256': '2' * 64}
        self.profile = self.fixture.engine.profile()
        self.profile_sha = fingerprint(self.profile)
        self.journal = []
        self.memories = []
        self.pids = [123, 124]
        self.memory_calls = 0

    def memory(self):
        self.memory_calls += 1
        return {'kind': 'ps-rss', 'available': True, 'processes': [
            {'pid': pid, 'parentPid': 1, 'rssBytes': 1024, 'processStart': 'fixture', 'available': True}
            for pid in self.pids]}

    def probe(self, data, url, **kwargs):
        self.memories.append(kwargs['memory_sampler'])
        return endurance(data, url, health_transport=self.fixture.health, client=self.fixture,
                         clock=self.fixture.clock, **kwargs)

    def run(self, **kwargs):
        return continuous_soak(dataset(), 'http://127.0.0.1:1', self.directory, self.profile_sha,
            duration_s=4, block_s=2, process_pids=self.pids, journal=self.journal.append,
            probe=self.probe, clock=self.fixture.clock, memory_sampler=self.memory, **kwargs)

    def evidence(self):
        report = self.run()
        plan = sealed({'schemaVersion': PLAN_SCHEMA, 'implementationCommit': '1' * 40,
            'profile': self.profile, 'profileSha256': self.profile_sha, 'datasetSha256': dataset()['sha256'],
            'model': self.profile['model'], 'durationSeconds': 4, 'blockSeconds': 2,
            'sourceSha256': json.loads((self.directory / 'block-001.json').read_text())['harnessFiles'],
            'qualification': 'not_assessed', 'routingEnabled': False})
        write_new(self.directory / 'plan.json', plan)
        (self.directory / 'runtime.log').write_text('fixture runtime')
        (self.directory / 'observations.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in self.journal))
        launcher = sealed({'schemaVersion': LAUNCHER_SCHEMA, 'status': 'pass', 'failure': None, 'cancelled': False,
            'cleanupErrors': [], 'remainingOwnedPids': [], 'ownedPids': self.pids, 'observed': report,
            'planSha256': hashlib.sha256((self.directory / 'plan.json').read_bytes()).hexdigest(),
            'logSha256': {name: hashlib.sha256((self.directory / name).read_bytes()).hexdigest()
                         for name in ('runtime.log', 'observations.jsonl')},
            'qualification': 'not_assessed', 'routingEnabled': False})
        return plan, launcher


class SoakTest(unittest.TestCase):
    def test_continuous_blocks_share_identity_and_only_first_block_warms_up(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = SoakFixture(directory); report = fixture.run()
            self.assertEqual((report['status'], report['attempts'], report['separateWarmupCalls']), ('observed', 4, 3))
            self.assertEqual(report['measuredMs'], 4000)
            self.assertEqual(fixture.memories[0], fixture.memories[1])
            self.assertEqual(len(fixture.journal), 7)
            self.assertTrue((Path(directory) / 'block-002.json').exists())

    def test_cancel_saves_partial_block_and_does_not_start_another(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = SoakFixture(directory)
            report = fixture.run(cancel_requested=lambda: fixture.fixture.calls >= 4)
            self.assertEqual((report['status'], report['stoppedReason']), ('degraded', 'cancelled'))
            self.assertEqual((report['attempts'], len(report['blocks']), fixture.fixture.calls), (1, 1, 4))
            self.assertFalse((Path(directory) / 'block-002.json').exists())

    def test_cross_block_decision_drift_stops_after_first_changed_call(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = SoakFixture(directory)
            def change(index, _report):
                if index == 0:
                    fixture.fixture.engine.backend.logits = [0, 3]
            report = fixture.run(progress=change)
            self.assertEqual(report['stoppedReason'], 'decision_changed_across_blocks')
            self.assertEqual((report['attempts'], fixture.fixture.calls), (3, 6))
            self.assertEqual(report['crossBlockDecisionChanges'], ['one'])

    def test_invalid_or_holdout_plan_never_starts_probe(self):
        data = dataset(); data.pop('sha256'); data['cases'][0]['split'] = 'holdout'
        with self.assertRaises(ValueError): validate_plan(sealed(data), 4, 2)
        for duration, block in [(7201, 900), (7200, 1801), (7200, 1), (True, 2), (4, 0)]:
            with self.assertRaises(ValueError): validate_plan(dataset(), duration, block)

    def test_completed_checkpoint_survives_later_write_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = SoakFixture(directory)
            def full(row):
                if row['block'] == 1:
                    raise OSError(28, 'No space left on device')
                fixture.journal.append(row)
            with self.assertRaises(OSError):
                continuous_soak(dataset(), 'http://127.0.0.1:1', fixture.directory, fixture.profile_sha,
                    duration_s=4, block_s=2, process_pids=fixture.pids, journal=full,
                    probe=fixture.probe, clock=fixture.fixture.clock, memory_sampler=fixture.memory)
            self.assertTrue((Path(directory) / 'block-001.json').exists())
            self.assertFalse((Path(directory) / 'block-002.json').exists())

    def test_offline_verifier_accepts_complete_conserved_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = SoakFixture(directory); plan, launcher = fixture.evidence()
            result = verify_run(Path(directory), plan, launcher, dataset())
            self.assertEqual((result['status'], result['attempts'], result['blocks']), ('pass', 4, 2))

    def test_resealed_short_duration_or_unknown_cleanup_cannot_pass(self):
        for mutate in [lambda run: run['observed'].update(measuredMs=3999),
                       lambda run: run.update(remainingOwnedPids=None),
                       lambda run: run.update(cancelled=True),
                       lambda run: run['observed'].update(attempts=999),
                       lambda run: run['observed'].update(qualifiedForRouting=True)]:
            with tempfile.TemporaryDirectory() as directory:
                fixture = SoakFixture(directory); plan, launcher = fixture.evidence()
                mutate(launcher)
                launcher['observed'] = sealed({k: v for k, v in launcher['observed'].items() if k != 'sha256'})
                launcher = sealed({k: v for k, v in launcher.items() if k != 'sha256'})
                with self.assertRaises(ValueError): verify_run(Path(directory), plan, launcher, dataset())

    def test_resealed_process_replacement_or_missing_journal_cannot_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = SoakFixture(directory); plan, launcher = fixture.evidence()
            path = Path(directory) / 'block-002.json'; block = json.loads(path.read_text())
            block['samples'][0]['memory']['processes'][0]['processStart'] = 'replaced'
            block = sealed({k: v for k, v in block.items() if k != 'sha256'})
            path.write_text(json.dumps(block)); launcher['observed']['blocks'][1]['sha256'] = block['sha256']
            launcher['observed'] = sealed({k: v for k, v in launcher['observed'].items() if k != 'sha256'})
            launcher = sealed({k: v for k, v in launcher.items() if k != 'sha256'})
            with self.assertRaisesRegex(ValueError, 'process replaced'): verify_run(Path(directory), plan, launcher, dataset())
        with tempfile.TemporaryDirectory() as directory:
            fixture = SoakFixture(directory); plan, launcher = fixture.evidence()
            path = Path(directory) / 'observations.jsonl'; path.write_text('')
            launcher['logSha256']['observations.jsonl'] = hashlib.sha256(b'').hexdigest()
            launcher = sealed({k: v for k, v in launcher.items() if k != 'sha256'})
            with self.assertRaisesRegex(ValueError, 'journal differs'): verify_run(Path(directory), plan, launcher, dataset())


if __name__ == '__main__': unittest.main()
