import json
from pathlib import Path
import tempfile
import unittest

from decision_runtime.artifacts import sealed, verify_seal
from scripts.lib.decision_soak_summary import SCHEMA, public_summary
from scripts.lib.decision_soak_verification import verify_run
from scripts.test.test_decision_baselines import dataset
from scripts.test.test_decision_soak import SoakFixture


def evidence(directory, fixture=None):
    fixture = fixture or SoakFixture(directory)
    plan, launcher = fixture.evidence()
    verification = verify_run(Path(directory), plan, launcher, dataset())
    blocks = [json.loads((Path(directory) / item['path']).read_text()) for item in launcher['observed']['blocks']]
    return plan, launcher, verification, blocks


class PublicSummaryTest(unittest.TestCase):
    def test_verified_summary_excludes_private_host_pid_source_and_gold(self):
        with tempfile.TemporaryDirectory() as directory:
            result = public_summary(*evidence(directory))
            verify_seal(result, SCHEMA)
            self.assertEqual((result['summary']['attempts'], result['separateWarmupCalls']), (4, 3))
            raw = json.dumps(result)
            for field in ['ownedPids', 'processStart', 'rssBytes', 'parentPid', 'harnessFiles',
                          'Input only', 'GOLD NEVER SENT', 'expectedOptionId']:
                self.assertNotIn(field, raw)
            self.assertFalse(result['routingEnabled'])
            self.assertEqual(result['qualification'], 'not_assessed')

    def test_pooled_p95_is_not_mean_of_block_percentiles_or_warmup(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = SoakFixture(directory)
            original_probe, original_decide = fixture.probe, fixture.fixture.decide
            block_index = [0]
            def probe(data, url, **kwargs):
                index = block_index[0]; block_index[0] += 1
                def timed(shadow):
                    outcome = original_decide(shadow)
                    if index == 0:
                        fixture.fixture.now -= .9
                    return outcome
                fixture.fixture.decide = timed
                return original_probe(data, url, **kwargs)
            fixture.probe = probe
            result = public_summary(*evidence(directory, fixture))
            self.assertNotEqual(result['blocks'][0]['attempts'], result['blocks'][1]['attempts'])
            self.assertEqual(result['summary']['scoredWallMs']['p95'], 1000)
            mean = sum(block['scoredWallMs']['p95'] for block in result['blocks']) / 2
            self.assertNotEqual(result['summary']['scoredWallMs']['p95'], mean)
            self.assertEqual(result['summary']['attempts'], sum(b['attempts'] for b in result['blocks']))

    def test_resealed_wrong_verification_and_missing_blocks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            plan, launcher, verification, blocks = evidence(directory)
            with self.assertRaisesRegex(ValueError, 'Missing summary blocks'):
                public_summary(plan, launcher, verification, blocks[:-1])
            verification['launcherSha256'] = '0' * 64
            verification = sealed({k: v for k, v in verification.items() if k != 'sha256'})
            with self.assertRaisesRegex(ValueError, 'matching verified experiment'):
                public_summary(plan, launcher, verification, blocks)


if __name__ == '__main__': unittest.main()
