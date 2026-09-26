import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from decision_runtime.artifacts import sealed, write_new
from scripts.lib.report_source_records import MAX_SOURCE_BYTES, MAPPING_SCHEMA, UNITS, DECLARATIONS, bind_records, calculate_source, source_markdown

ROOT = Path(__file__).resolve().parents[2]
TEXT = 'Учебные данные\nperiod,requests,hours,exceptions\n2026-07,100,400,8\n2026-08,120,360,6\n'


def mapping(source):
    return {'schemaVersion': MAPPING_SCHEMA, 'sourceId': 'records', 'sourceUri': 'urn:agat:report-fixture',
            'expectedSha256': hashlib.sha256(source).hexdigest(), 'table': {'firstLine': 2, 'lastLine': 4},
            'columns': {'period': 'period', 'requests': 'requests', 'processingHours': 'hours', 'exceptions': 'exceptions'},
            'periods': ['2026-07', '2026-08'], 'units': dict(UNITS), 'population': {'id': 'fixture', **DECLARATIONS}}


class SourceRecordsTest(unittest.TestCase):
    def test_utf8_byte_spans_and_exact_cells_feed_calculation_without_source_truth_claim(self):
        raw = TEXT.encode();plan = mapping(raw);before = copy.deepcopy(plan)
        report = calculate_source(raw, plan);binding = report['binding']
        self.assertEqual(plan, before)
        self.assertEqual(binding['arithmeticInput']['periods'][1]['processingHours'], '360')
        for record in binding['records']:
            selected = raw[record['byteStart']:record['byteEnd']]
            self.assertEqual(selected.decode(), record['rawRecord'])
            self.assertEqual(hashlib.sha256(selected).hexdigest(), record['rawRecordSha256'])
        self.assertEqual(binding['records'][0]['byteStart'], len('\n'.join(TEXT.split('\n')[:2]).encode()) + 1)
        self.assertEqual(report['calculation']['metrics']['meanHoursPerRequest']['relativeChangePercent']['exact'],
                         {'numerator': '-25', 'denominator': '1'})
        self.assertEqual(report['calculation']['metrics']['exceptionRatePercent']['change']['unit'], 'percentage_points')
        self.assertTrue(binding['cellsMatchSelectedCsv']);self.assertTrue(binding['sourceBytesMatched'])
        for key in ('sourceTruthVerified', 'populationAlignmentVerified', 'routingEnabled', 'qualifiedForRouting'):
            self.assertFalse(report[key]);self.assertFalse(binding[key])
        self.assertFalse(report['calculation']['sourceValuesVerified'])
        plan['columns']['requests'] = 'changed'
        self.assertEqual(binding['mapping']['columns']['requests'], 'requests')

    def test_pinned_source_change_is_rejected_even_when_the_table_is_unchanged(self):
        raw = TEXT.encode()
        with self.assertRaisesRegex(ValueError, 'SHA-256'): bind_records(b'changed preamble\n' + raw.split(b'\n', 1)[1], mapping(raw))

    def test_crlf_bom_quoted_cells_and_reordered_columns_keep_exact_raw_spans(self):
        text = '\ufeffПреамбула\r\nexceptions,hours,period,requests\r\n"8","400","2026-07","100"\r\n6,360,2026-08,120'
        raw = text.encode();result = bind_records(raw, mapping(raw))
        self.assertEqual(result['source']['encoding'], 'utf-8-sig')
        self.assertEqual(result['records'][0]['rawRecord'], '"8","400","2026-07","100"\r\n')
        self.assertEqual(raw[result['source']['tableByteStart']:result['source']['tableByteEnd']].decode(), result['source']['tableText'])
        self.assertEqual(result['records'][1]['byteEnd'], len(raw))
        self.assertEqual(result['arithmeticInput']['periods'][0]['requests'], 100)

    def test_header_range_extra_records_and_wrong_width_are_not_silently_repaired(self):
        for text in [TEXT.replace('period,requests,hours,exceptions', 'period,requests,hours,hours'),
                     TEXT.replace('period,requests,hours,exceptions', 'period, requests,hours,exceptions'),
                     TEXT.replace('2026-07,100,400,8', '2026-07,100,400'),
                     TEXT.replace('2026-07,100,400,8', '2026-07,100,400,8,extra'),
                     TEXT + '2026-09,2,4,0\n', TEXT.replace('2026-07,100,400,8', '')]:
            raw = text.encode();plan = mapping(raw)
            if text.endswith('2026-09,2,4,0\n'): plan['table']['lastLine'] = 5
            with self.subTest(text=text), self.assertRaises(ValueError): bind_records(raw, plan)
        for first, last in [(0, 4), (True, 4), (2, 2), (2, 5), (1, 4)]:
            raw = TEXT.encode();plan = mapping(raw);plan['table'] = {'firstLine': first, 'lastLine': last}
            with self.assertRaises(ValueError): bind_records(raw, plan)

    def test_period_duplicates_unexpected_periods_and_swapped_order_require_explicit_mapping(self):
        for text in [TEXT.replace('2026-08', '2026-07'), TEXT.replace('2026-08', '2026-09'),
                     '\n'.join(TEXT.split('\n')[:2] + [TEXT.split('\n')[3], TEXT.split('\n')[2], ''])]:
            raw = text.encode()
            with self.assertRaises(ValueError): bind_records(raw, mapping(raw))
        raw = '\n'.join(TEXT.split('\n')[:2] + [TEXT.split('\n')[3], TEXT.split('\n')[2], '']).encode()
        plan = mapping(raw);plan['periods'].reverse()
        self.assertEqual(bind_records(raw, plan)['arithmeticInput']['periods'][0]['requests'], 120)

    def test_missing_formula_locale_float_and_out_of_population_counts_fail(self):
        for row in ['2026-07,,400,8', '2026-07,=SUM(100),400,8', '2026-07,100.0,400,8', '2026-07,01,400,0',
                    '2026-07,100,"400,5",8', '2026-07,100,4e2,8', '2026-07,100,400,101', '2026-07,0,400,0',
                    '2026-07,100,-400,8', '2026-07,100,400,', '2026-07,100,NaN,8', '2026-07,100,400,1.0']:
            raw = TEXT.replace('2026-07,100,400,8', row).encode()
            with self.subTest(row=row), self.assertRaises(ValueError): calculate_source(raw, mapping(raw))

    def test_zero_population_remains_undefined_and_fractional_hours_remain_exact(self):
        raw = TEXT.replace('2026-07,100,400,8', '2026-07,0,0,0').replace('2026-08,120,360,6', '2026-08,3,0.1,1').encode()
        report = calculate_source(raw, mapping(raw))
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['calculation']['metrics']['meanHoursPerRequest']['second']['exact'], {'numerator': '1', 'denominator': '30'})
        self.assertIn('не определено', source_markdown(report, raw))

    def test_units_population_declarations_and_unknown_keys_are_strict(self):
        raw = TEXT.encode()
        for field, value in [('units', {**UNITS, 'processingHours': 'minutes'}), ('units', {**UNITS, 'exceptions': 'events'}),
                             ('population', {'id': 'x', **DECLARATIONS, 'comparablePeriods': 1}),
                             ('population', {'id': 'x', **DECLARATIONS, 'exceptionScope': 'repeatable_events'}),
                             ('sourceUri', 'https://example.com/`[bad]'), ('sourceId', '../bad'), ('humanApproved', True)]:
            plan = mapping(raw);plan[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): bind_records(raw, plan)
        plan = mapping(raw);plan['columns']['processingHours'] = 'requests'
        with self.assertRaises(ValueError): bind_records(raw, plan)

    def test_bad_encoding_binary_oversized_and_broken_quoted_csv_fail(self):
        for raw in [b'\xff', TEXT.encode() + b'\x00', b'x' * (MAX_SOURCE_BYTES + 1),
                    TEXT.replace('2026-07,100,400,8', '"2026-07,100,400,8').encode()]:
            with self.assertRaises(ValueError): bind_records(raw, mapping(raw))

    def test_source_report_rechecks_arithmetic_and_resealed_cell_provenance_tampering(self):
        raw = TEXT.encode();report = calculate_source(raw, mapping(raw))
        self.assertIn('CSV-таблице', source_markdown(report, raw))
        self.assertEqual(source_markdown(report, raw), source_markdown(json.loads(json.dumps(report, sort_keys=True)), raw))
        for change in ['cell', 'offset', 'calculation']:
            forged = copy.deepcopy(report)
            if change == 'cell': forged['binding']['records'][0]['cells']['hours'] = '401'
            elif change == 'offset': forged['binding']['records'][0]['byteStart'] += 1
            else: forged['calculation']['metrics']['meanHoursPerRequest']['first']['display'] = '40.000'
            forged.pop('sha256')
            with self.subTest(change=change), self.assertRaises(ValueError): source_markdown(sealed(forged), raw)
        with self.assertRaises(ValueError): source_markdown(report, raw.replace(b'400', b'401'))

    def test_cli_source_to_report_and_invalid_source_cannot_create_partial_or_overwrite_evidence(self):
        with tempfile.TemporaryDirectory(prefix='.source-records-test-', dir=ROOT / 'docs') as directory:
            base = Path(directory);source = base / 'source.csv';source.write_bytes(TEXT.encode())
            config = base / 'mapping.json';write_new(config, mapping(source.read_bytes()))
            output = base / 'report.json';md = base / 'report.md'
            command = [sys.executable, str(ROOT / 'scripts/calculate-source-report.py'), '--source', str(source), '--mapping', str(config),
                       '--output', str(output), '--markdown', str(md)]
            run = subprocess.run(command, capture_output=True, text=True, timeout=5)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('| Среднее время на заявку | 4.000 ч | 3.000 ч | -25.000% |', md.read_text())
            original = output.read_bytes()
            self.assertNotEqual(subprocess.run(command, capture_output=True, timeout=5).returncode, 0)
            self.assertEqual(output.read_bytes(), original)
            output.unlink();md.unlink();source.write_text(TEXT.replace('400', '401'))
            self.assertNotEqual(subprocess.run(command, capture_output=True, timeout=5).returncode, 0)
            self.assertFalse(output.exists());self.assertFalse(md.exists())


if __name__ == '__main__': unittest.main()
