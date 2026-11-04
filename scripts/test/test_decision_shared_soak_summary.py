import copy
import hashlib
import json
import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from decision_runtime.artifacts import read_json, sealed, verify_seal, write_new
from decision_runtime.contracts import fingerprint
from scripts.lib.decision_shared_load import summarize
from scripts.lib.decision_shared_soak import shared_soak
from scripts.lib.decision_shared_soak_summary import SCHEMA, public_summary
from scripts.lib.decision_shared_soak_verification import SOURCES, verify_measurements
from scripts.test.test_decision_baselines import dataset
from scripts.test.test_decision_performance import Fixture
from scripts.test.test_decision_shared_soak import SyntheticClock, TimedBlocks

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / 'scripts/summarize-decision-shared-soak.py'


def evidence(directory, *, varied=False):
    clock = SyntheticClock(); delegate = TimedBlocks(clock); journal = []

    def probe(*args, **kwargs):
        events = []; observer = kwargs.pop('pair_observer'); before = clock.now
        report = delegate(*args, **kwargs, pair_observer=lambda *event: events.append(event))
        if varied:
            factor = 10 if delegate.calls == 1 else 1
            old_warmup = report['warmup'][-1]['finishedMs']; warmup_ms = 1501 * len(report['warmup'])
            for index, row in enumerate(report['warmup']):
                row.update(startedMs=index * 1501, finishedMs=(index + 1) * 1501, wallMs=1501)
            for phase in report['phases']:
                for row in phase['rows']:
                    row.update(startedMs=warmup_ms + (row['startedMs'] - old_warmup) * factor,
                               finishedMs=warmup_ms + (row['finishedMs'] - old_warmup) * factor,
                               wallMs=row['wallMs'] * factor)
                for pair in phase['pairs']:
                    pair['elapsedMs'] *= factor; pair['requestOverlapMs'] *= factor
                phase['summary'] = {kind: summarize([r for r in phase['rows'] if r['kind'] == kind])
                                    for kind in ('decision', 'primary')}
            report['elapsedMs'] = warmup_ms + (report['elapsedMs'] - old_warmup) * factor
            clock.now = before + report['elapsedMs'] / 1000
            report = sealed({k: v for k, v in report.items() if k != 'sha256'})
        for event in events: observer(*event)
        return report

    data = dataset(); profile = Fixture().engine.profile(); duration = 16 if varied else 3
    result = shared_soak(data, 'http://127.0.0.1:1', lambda: None, directory, profile,
                         duration_s=duration, max_blocks=4, probe=probe, clock=clock, journal=journal.append)
    blocks = [read_json(directory / entry['path']) for entry in result['blocks']]
    plan = sealed({'schemaVersion': 'agat.decision.shared-soak-plan.v1', 'implementationCommit': 'a' * 40,
                   'qualification': 'not_assessed', 'routingEnabled': False, 'durationSeconds': duration,
                   'maxBlocks': 4, 'profileSha256': fingerprint(profile), 'datasetSha256': data['sha256'],
                   'primaryDigest': '1' * 64, 'sourceFiles': blocks[0]['harnessFiles']})
    launcher = sealed({'schemaVersion': 'agat.decision.shared-soak-launcher.v1', 'status': 'observed',
                       'qualification': 'not_assessed', 'routingEnabled': False, 'failureType': None,
                       'planSha256': plan['sha256'], 'resultSha256': result['sha256'], 'implementationCommit': 'a' * 40,
                       'serviceOwnership': 'caller', 'servicesStoppedOrRestarted': False})
    proof = verify_measurements(plan, launcher, result, blocks, journal, data, profile)
    return plan, launcher, result, blocks, journal, data, profile, proof


class SharedSummaryTest(unittest.TestCase):
    def test_verified_summary_uses_an_explicit_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan, _launcher, result, blocks, _journal, _data, _profile, proof = evidence(Path(tmp))
            proof['counts']['hostPath'] = 'PRIVATE HOST PATH'
            proof = sealed({k: v for k, v in proof.items() if k != 'sha256'})
            summary = public_summary(plan, result, proof, blocks); verify_seal(summary, SCHEMA)
            self.assertEqual((summary['counts']['measuredCalls'], summary['counts']['warmupCalls']), (32, 8))
            self.assertEqual((summary['models']['decision']['calls'], summary['models']['primary']['calls']), (16, 16))
            self.assertFalse(summary['routingEnabled']); self.assertEqual(summary['qualification'], 'not_assessed')
            encoded = json.dumps(summary)
            for private in ('PRIVATE HOST PATH', 'Input only', 'GOLD NEVER SENT', 'expectedOptionId',
                            'caseId', 'inputSha256', 'decisionSha256', 'primaryResidence', 'harnessFiles', 'pid'):
                self.assertNotIn(private, encoded)

    def test_pooled_percentiles_exclude_warmup_and_are_not_average_block_quantiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan, _launcher, result, blocks, _journal, _data, _profile, proof = evidence(Path(tmp), varied=True)
            summary = public_summary(plan, result, proof, blocks)
            self.assertEqual((summary['measuredMs'], summary['nonMeasuredWallMs']), (16500, 12008))
            self.assertEqual(summary['models']['decision']['wallMs']['p95'], 1000)
            self.assertEqual(summary['separateWarmup']['decision']['wallMs']['p95'], 1501)
            mean = sum(block['models']['decision']['wallMs']['p95'] for block in summary['blocks']) / 2
            self.assertNotEqual(summary['models']['decision']['wallMs']['p95'], mean)
            self.assertEqual(summary['phases']['overlapping_pair']['primary']['wallMs']['max'], 1500)

    def test_wrong_proof_missing_blocks_and_resealed_changed_block_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan, _launcher, result, blocks, _journal, _data, _profile, proof = evidence(Path(tmp))
            for field, value in (('resultSha256', '0' * 64), ('planSha256', '0' * 64),
                                 ('status', 'failed'), ('routingEnabled', True)):
                wrong = sealed({**{k: v for k, v in proof.items() if k != 'sha256'}, field: value})
                with self.assertRaisesRegex(ValueError, 'matching verified experiment'):
                    public_summary(plan, result, wrong, blocks)
            with self.assertRaisesRegex(ValueError, 'Missing summary blocks'):
                public_summary(plan, result, proof, blocks[:-1])
            changed = copy.deepcopy(blocks)
            changed[0] = sealed({**{k: v for k, v in changed[0].items() if k != 'sha256'}, 'elapsedMs': 0})
            with self.assertRaisesRegex(ValueError, 'block checksum changed'):
                public_summary(plan, result, proof, changed)

    def cli_evidence(self, root):
        entry = runpy.run_path(str(CLI)); directory = root / 'docs/private/run'; directory.mkdir(parents=True)
        plan, launcher, result, blocks, journal, data, profile, _proof = evidence(directory)
        names = set(SOURCES) | set(entry['SOURCES'])
        for name in names:
            target = root / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes((ROOT / name).read_bytes())
        write_new(root / 'docs/development.json', data); write_new(root / 'docs/profile.json', profile)
        names.update(('docs/development.json', 'docs/profile.json'))
        subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True)
        subprocess.run(['git', 'add', *sorted(names)], cwd=root, check=True, capture_output=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.com',
                        'commit', '-qm', 'Fixture analysis and measured sources'], cwd=root, check=True, capture_output=True)
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
        plan = sealed({**{k: v for k, v in plan.items() if k != 'sha256'}, 'implementationCommit': commit,
                       'datasetPath': 'docs/development.json', 'profilePath': 'docs/profile.json',
                       'sourceFiles': {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                                       for name in SOURCES | {'docs/development.json', 'docs/profile.json'}}})
        raw = ''.join(json.dumps(event, sort_keys=True) + '\n' for event in journal).encode()
        (directory / 'pairs.jsonl').write_bytes(raw)
        launcher = sealed({**{k: v for k, v in launcher.items() if k != 'sha256'}, 'implementationCommit': commit,
                           'planSha256': plan['sha256'], 'journalSha256': hashlib.sha256(raw).hexdigest()})
        write_new(directory / 'plan.json', plan); write_new(directory / 'launcher.json', launcher)
        write_new(directory / 'result.json', result)
        return entry, directory, root / 'docs/public/summary.json', commit

    def test_cli_repeats_verification_and_binds_committed_analysis_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); entry, directory, output, commit = self.cli_evidence(root)
            (directory / 'verification.json').write_text('{"status":"fake saved label"}')
            with patch.dict(entry['main'].__globals__, {'ROOT': root}):
                self.assertEqual(entry['main']([str(directory), '--output', str(output)]), 0)
            summary = read_json(output); verify_seal(summary, SCHEMA)
            self.assertEqual(summary['analysisCommit'], commit)
            self.assertEqual(set(summary['analysisSourceSha256']), set(entry['SOURCES']))

    def test_cli_corrupt_journal_or_dirty_analysis_creates_no_export(self):
        for fault in ('journal', 'source', 'failed_launcher'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); entry, directory, output, _commit = self.cli_evidence(root)
                if fault == 'journal': (directory / 'pairs.jsonl').write_text('')
                elif fault == 'source': (root / entry['SOURCES'][0]).write_text('# dirty analysis')
                else:
                    launcher = read_json(directory / 'launcher.json')
                    write_new(directory / 'failed.json', sealed({**{k: v for k, v in launcher.items() if k != 'sha256'}, 'status': 'failed'}))
                    (directory / 'failed.json').replace(directory / 'launcher.json')
                with patch.dict(entry['main'].__globals__, {'ROOT': root}), self.assertRaises(ValueError):
                    entry['main']([str(directory), '--output', str(output)])
                self.assertFalse(output.exists())

    def test_cli_rejects_public_input_existing_output_and_symlink_escape(self):
        entry = runpy.run_path(str(CLI))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); private = root / 'docs/private'; private.mkdir(parents=True)
            output = root / 'docs/existing.json'; output.write_text('keep')
            outside = root / 'outside'; outside.mkdir(); (private / 'escape').symlink_to(outside, target_is_directory=True)
            for source, destination in ((root / 'docs/public', root / 'docs/new.json'),
                                        (private, output), (private / 'escape', root / 'docs/new.json')):
                with patch.dict(entry['main'].__globals__, {'ROOT': root}), self.assertRaises(ValueError):
                    entry['main']([str(source), '--output', str(destination)])
            self.assertEqual(output.read_text(), 'keep')


if __name__ == '__main__': unittest.main()
