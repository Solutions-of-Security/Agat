import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from decision_runtime.artifacts import read_json, sealed, write_new
from scripts.lib import report_snapshot as snapshots
from scripts.lib.report_process import prepare_process
from scripts.test.test_report_request_records import mapping, source

ROOT = Path(__file__).resolve().parents[2]


def recipe():
    plan = mapping(source())
    return {'schemaVersion': snapshots.RECIPE_SCHEMA, 'name': 'Снимок отчёта по заявкам', 'decimalPlaces': 3,
            'table': {'firstLine': 2, 'through': 'end_of_file'},
            'mapping': {key: value for key, value in plan.items() if key not in ('expectedSha256', 'table')}}


class ReportSnapshotTest(unittest.TestCase):
    def test_capture_new_changed_and_unchanged_exports_without_overwriting_history(self):
        with tempfile.TemporaryDirectory(prefix='.report-snapshot-', dir=ROOT / 'docs') as folder:
            base = Path(folder); csv = base / 'live.csv'; csv.write_bytes(source())
            raw_recipe = recipe(); before = copy.deepcopy(raw_recipe)
            receipt, files = snapshots.prepare_snapshot(csv, raw_recipe)
            self.assertEqual(raw_recipe, before)
            self.assertEqual(receipt['sourceRead']['matchingReads'], 2)
            self.assertEqual(receipt['comparison']['status'], 'initial')
            first = base / 'first'; snapshots.write_snapshot(first, receipt, files)
            self.assertEqual(snapshots.verify_snapshot(first), receipt)
            previous = read_json(first / 'bundle.json'); previous_bytes = (first / 'bundle.json').read_bytes()
            unchanged, files = snapshots.prepare_snapshot(csv, raw_recipe, previous=previous)
            self.assertEqual(unchanged['status'], 'unchanged'); self.assertIsNone(files)
            csv.write_bytes(source() + b'2026-07,NEW,0.5,0\n')
            changed, files = snapshots.prepare_snapshot(csv, raw_recipe, previous=previous)
            second = base / 'second'; snapshots.write_snapshot(second, changed, files)
            self.assertEqual(snapshots.verify_snapshot(second, previous=previous), changed)
            comparison = changed['comparison']
            self.assertEqual(comparison['previousBundleSha256'], previous['sha256'])
            self.assertTrue(comparison['sourceBytesChanged']); self.assertTrue(comparison['tableBytesChanged'])
            self.assertTrue(comparison['aggregateValuesChanged'])
            self.assertEqual(comparison['after']['periods'][0]['requests'], 3)
            self.assertEqual(comparison['after']['periods'][0]['processingHours'], '4.5')
            self.assertEqual(json.loads(files['mapping.json'])['table']['lastLine'], 9)
            self.assertEqual((first / 'bundle.json').read_bytes(), previous_bytes)
            with self.assertRaisesRegex(ValueError, 'Previous bundle is required'): snapshots.verify_snapshot(second)
            with self.assertRaises(FileExistsError): snapshots.write_snapshot(first, changed, files)

    def test_preamble_or_equal_duplicate_changes_are_new_sources_even_when_totals_match(self):
        original = source(); plan = recipe()
        previous = prepare_process(original, mapping(original), plan['name'])
        with tempfile.TemporaryDirectory() as folder:
            csv = Path(folder) / 'live.csv'
            for raw, table_changed in [(original.replace('Учебные записи'.encode(), b'Updated export'), False),
                                       (original + b'2026-07,001,1.50,1\n', True)]:
                csv.write_bytes(raw)
                receipt, files = snapshots.prepare_snapshot(csv, plan, previous=previous)
                self.assertEqual(receipt['comparison']['status'], 'changed')
                self.assertEqual(receipt['comparison']['tableBytesChanged'], table_changed)
                self.assertFalse(receipt['comparison']['aggregateValuesChanged'])
                self.assertEqual(files['source.csv'], raw)

    def test_read_rejects_in_place_changes_and_path_replacement_between_reads(self):
        read = snapshots._read_bytes
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder); csv = base / 'live.csv'; replacement = base / 'replacement.csv'
            for mode in ('rewrite', 'replace', 'same_size'):
                csv.write_bytes(source()); replacement.write_bytes(source() + b'2026-07,NEW,1,0\n')
                calls = 0
                def mutate(descriptor):
                    nonlocal calls
                    result = read(descriptor); calls += 1
                    if calls == 1:
                        if mode == 'replace': os.replace(replacement, csv)
                        elif mode == 'same_size': csv.write_bytes(source().replace(b'1.5', b'1.6'))
                        else: csv.write_bytes(source() + b'2026-07,NEW,1,0\n')
                    return result
                with self.subTest(mode=mode), patch.object(snapshots, '_read_bytes', side_effect=mutate):
                    with self.assertRaisesRegex(ValueError, 'changed during capture'): snapshots.read_stable_source(csv)

    def test_symlink_fifo_directory_empty_and_oversized_inputs_do_not_block_or_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder); csv = base / 'source.csv'; csv.write_bytes(source())
            link = base / 'link.csv'; link.symlink_to(csv)
            fifo = base / 'pipe.csv'; os.mkfifo(fifo)
            empty = base / 'empty.csv'; empty.write_bytes(b'')
            oversized = base / 'large.csv'; oversized.write_bytes(b'x' * (snapshots.MAX_SOURCE_BYTES + 1))
            for path in (link, fifo, base, empty, oversized):
                with self.subTest(path=path), self.assertRaises((ValueError, OSError)): snapshots.read_stable_source(path)

    def test_two_content_reads_must_match_even_when_metadata_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            csv = Path(folder) / 'source.csv'; csv.write_bytes(source())
            with patch.object(snapshots, '_read_bytes', side_effect=[source(), source().replace(b'1.5', b'1.6')]):
                with self.assertRaisesRegex(ValueError, 'changed during capture'): snapshots.read_stable_source(csv)

    def test_process_limit_rejects_large_provenance_without_a_partial_snapshot(self):
        with tempfile.TemporaryDirectory(prefix='.report-snapshot-limit-', dir=ROOT / 'docs') as folder:
            base = Path(folder); csv = base / 'source.csv'
            csv.write_bytes(source() + b'2026-07,001,1.5,1\n' * 100)
            config = base / 'recipe.json'; write_new(config, recipe())
            output = base / 'output'
            run = subprocess.run([sys.executable, str(ROOT / 'scripts/prepare-report-snapshot.py'), '--source', str(csv),
                                  '--recipe', str(config), '--output-dir', str(output)], capture_output=True, text=True, timeout=10)
            self.assertNotEqual(run.returncode, 0); self.assertIn('template limit', run.stderr)
            self.assertFalse(output.exists())

    def test_recipe_remains_explicit_and_rejects_unknown_periods_footers_and_conflicting_duplicates(self):
        original = source(); previous = prepare_process(original, mapping(original), recipe()['name'])
        with tempfile.TemporaryDirectory() as folder:
            csv = Path(folder) / 'live.csv'
            for raw in [original + b'footer\n', original + b'2026-09,X,1,0\n', original + b'2026-07,001,99,1\n']:
                csv.write_bytes(raw)
                with self.assertRaises(ValueError): snapshots.prepare_snapshot(csv, recipe())
            csv.write_bytes(original)
            for kind in ('periods', 'identity', 'precision', 'name'):
                plan = recipe()
                if kind == 'periods': plan['mapping']['periods'].reverse()
                elif kind == 'identity': plan['mapping']['population']['id'] = 'other-population'
                elif kind == 'precision': plan['decimalPlaces'] = 2
                else: plan['name'] = 'Иное назначение'
                with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'different recipe'):
                    snapshots.prepare_snapshot(csv, plan, previous=previous)
            for plan in [dict(recipe(), schemaVersion='unknown'), dict(recipe(), decimalPlaces=True),
                         dict(recipe(), table={'firstLine': 2, 'through': 'detect'}), dict(recipe(), extra=True)]:
                with self.assertRaises(ValueError): snapshots.prepare_snapshot(csv, plan)

    def test_resealed_comparison_and_artifact_changes_fail_rebuild(self):
        original = source(); previous = prepare_process(original, mapping(original), recipe()['name'])
        with tempfile.TemporaryDirectory(prefix='.report-snapshot-', dir=ROOT / 'docs') as folder:
            base = Path(folder); csv = base / 'live.csv'; csv.write_bytes(original + b'2026-07,NEW,0.5,0\n')
            receipt, files = snapshots.prepare_snapshot(csv, recipe(), previous=previous)
            for mode in ('comparison', 'file', 'claim', 'scope'):
                directory = base / mode; snapshots.write_snapshot(directory, receipt, files)
                forged = copy.deepcopy(receipt); forged.pop('sha256')
                if mode == 'comparison': forged['comparison']['after']['counts']['uniqueRequestPeriods'] = 999
                elif mode == 'claim': forged['humanReviewed'] = True
                elif mode == 'scope': forged['recipe']['mapping']['population']['id'] = 'changed'
                else:
                    raw = b'Invented report\n'; (directory / 'report.md').write_bytes(raw)
                    forged['files']['report.md'] = {'sha256': snapshots.sha(raw), 'sizeBytes': len(raw)}
                (directory / 'snapshot.json').write_text(json.dumps(sealed(forged)))
                with self.subTest(mode=mode), self.assertRaises(ValueError): snapshots.verify_snapshot(directory, previous=previous)

    def test_cli_generates_verified_snapshot_then_reports_unchanged_without_new_directory(self):
        with tempfile.TemporaryDirectory(prefix='.report-snapshot-cli-', dir=ROOT / 'docs') as folder:
            base = Path(folder); csv = base / 'live.csv'; csv.write_bytes(source())
            config = base / 'recipe.json'; write_new(config, recipe())
            command = [sys.executable, str(ROOT / 'scripts/prepare-report-snapshot.py'), '--source', str(csv), '--recipe', str(config)]
            first = base / 'first'
            completed = subprocess.run(command + ['--output-dir', str(first)], capture_output=True, text=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(len(list(first.iterdir())), 7)
            original = (first / 'snapshot.json').read_bytes()
            verify = [sys.executable, str(ROOT / 'scripts/verify-report-snapshot.py'), '--snapshot', str(first)]
            completed = subprocess.run(verify, capture_output=True, text=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            unchanged = base / 'unchanged'
            run = subprocess.run(command + ['--output-dir', str(unchanged), '--previous-bundle', str(first / 'bundle.json')],
                                 capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr); self.assertIn('unchanged', run.stdout); self.assertFalse(unchanged.exists())
            self.assertNotEqual(subprocess.run(command + ['--output-dir', str(first)], capture_output=True, timeout=10).returncode, 0)
            self.assertEqual((first / 'snapshot.json').read_bytes(), original)
            csv.write_bytes(source() + b'2026-07,001,9,1\n')
            invalid = base / 'invalid'
            self.assertNotEqual(subprocess.run(command + ['--output-dir', str(invalid)], capture_output=True, timeout=10).returncode, 0)
            self.assertFalse(invalid.exists())


if __name__ == '__main__':
    unittest.main()
