import copy
import hashlib
import json
import random
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from decision_runtime.artifacts import sealed, write_new
from scripts.lib.report_request_records import (
    DECLARATIONS, DEDUPLICATION, MAPPING_SCHEMA, MAX_RECORDS, MAX_SOURCE_BYTES, UNITS,
    aggregate_records, calculate_requests, request_markdown,
)

ROOT = Path(__file__).resolve().parents[2]
HEADER = 'period,request_id,hours,reopened\n'
ROWS = ['2026-07,001,1.5,1', '2026-08,001,1,0', '2026-07,1,2.5,0',
        '2026-07,001,1.500000,1', '2026-08,A-2,2,1', '2026-08,001,1.0,0']


def source(rows=ROWS):
    return ('Учебные записи\n' + HEADER + '\n'.join(rows) + '\n').encode()


def mapping(raw):
    return {'schemaVersion': MAPPING_SCHEMA, 'sourceId': 'requests', 'sourceUri': 'urn:agat:request-fixture',
            'expectedSha256': hashlib.sha256(raw).hexdigest(), 'table': {'firstLine': 2, 'lastLine': len(raw.splitlines())},
            'columns': {'period': 'period', 'requestId': 'request_id', 'processingHours': 'hours', 'hasException': 'reopened'},
            'periods': ['2026-07', '2026-08'], 'units': dict(UNITS), 'population': {'id': 'demo', **DECLARATIONS},
            'deduplication': copy.deepcopy(DEDUPLICATION)}


class RequestRecordsTest(unittest.TestCase):
    def test_typed_duplicates_preserve_ids_period_scope_and_exact_provenance(self):
        raw = source(); plan = mapping(raw); before = copy.deepcopy(plan)
        report = calculate_requests(raw, plan); binding = report['binding']
        self.assertEqual(plan, before)
        self.assertEqual(binding['counts'], {'sourceRecords': 6, 'uniqueRequestPeriods': 4, 'duplicateRecords': 2})
        self.assertEqual(binding['arithmeticInput']['periods'], [
            {'sourceId': 'requests-P1', 'period': '2026-07', 'requests': 2, 'processingHours': '4', 'exceptions': 1},
            {'sourceId': 'requests-P2', 'period': '2026-08', 'requests': 2, 'processingHours': '3', 'exceptions': 1}])
        self.assertEqual([r['duplicateOf'] for r in binding['records']], [None, None, None, 'requests-L3', None, 'requests-L4'])
        self.assertEqual(binding['aggregation'][0]['includedRecordIds'], ['requests-L3', 'requests-L5'])
        self.assertEqual(binding['aggregation'][1]['duplicateRecordIds'], ['requests-L8'])
        self.assertEqual(report['calculation']['metrics']['meanHoursPerRequest']['relativeChangePercent']['exact'],
                         {'numerator': '-25', 'denominator': '1'})
        self.assertEqual(report['calculation']['metrics']['exceptionRatePercent']['first']['exact'], {'numerator': '50', 'denominator': '1'})
        for record in binding['records']:
            selected = raw[record['byteStart']:record['byteEnd']]
            self.assertEqual(selected, record['rawRecord'].encode())
            self.assertEqual(hashlib.sha256(selected).hexdigest(), record['rawRecordSha256'])
        for key in ('sourceTruthVerified', 'sourceCompletenessVerified', 'identitySemanticsVerified', 'populationAlignmentVerified',
                    'routingEnabled', 'qualifiedForRouting'):
            self.assertFalse(report[key]); self.assertFalse(binding[key])
        plan['columns']['requestId'] = 'changed'; plan['deduplication']['key'].reverse()
        self.assertEqual(binding['mapping']['columns']['requestId'], 'request_id')
        self.assertEqual(binding['mapping']['deduplication']['key'], ['period', 'requestId'])

    def test_conflicting_hours_or_exception_are_rejected_even_if_first_or_last_looks_plausible(self):
        for duplicate in ['2026-07,001,2,1', '2026-07,001,1.5,0']:
            for rows in [ROWS + [duplicate], [duplicate] + ROWS]:
                raw = source(rows)
                with self.subTest(duplicate=duplicate), self.assertRaisesRegex(ValueError, 'Conflicting duplicate at source lines'):
                    calculate_requests(raw, mapping(raw))

    def test_order_and_identical_repetitions_do_not_change_metrics_against_integer_oracle(self):
        rng = random.Random(8421)
        for case in range(25):
            unique = []; totals = []
            for period in ('2026-07', '2026-08'):
                count = rng.randint(1, 12); micros = []; exceptions = []
                for index in range(count):
                    value = rng.randint(0, 9_999_999); flag = rng.randint(0, 1)
                    micros.append(value); exceptions.append(flag)
                    unique.append(f'{period},ID-{index},{value // 1_000_000}.{value % 1_000_000:06d},{flag}')
                totals.append((count, sum(micros), sum(exceptions)))
            rows = unique + rng.choices(unique, k=rng.randint(1, 20)); rng.shuffle(rows)
            raw = source(rows); result = calculate_requests(raw, mapping(raw))
            for actual, expected in zip(result['binding']['arithmeticInput']['periods'], totals):
                self.assertEqual((actual['requests'], Fraction(actual['processingHours']), actual['exceptions']),
                                 (expected[0], Fraction(expected[1], 1_000_000), expected[2]), case)
            clean = source(unique)
            self.assertEqual(result['calculation']['metrics'], calculate_requests(clean, mapping(clean))['calculation']['metrics'])

    def test_bom_crlf_quoted_reordered_columns_and_no_trailing_newline_preserve_bytes(self):
        raw = '\ufeffПреамбула\r\nreopened,hours,request_id,period\r\n"1","0.1","001","2026-07"\r\n0,0.2,001,2026-08'.encode()
        report = calculate_requests(raw, mapping(raw)); binding = report['binding']
        self.assertEqual(binding['source']['encoding'], 'utf-8-sig')
        self.assertEqual(raw[binding['source']['tableByteStart']:binding['source']['tableByteEnd']].decode(), binding['source']['tableText'])
        self.assertEqual(binding['records'][0]['rawRecord'], '"1","0.1","001","2026-07"\r\n')
        self.assertEqual(binding['records'][1]['byteEnd'], len(raw))
        self.assertEqual(report['calculation']['metrics']['meanHoursPerRequest']['first']['exact'], {'numerator': '1', 'denominator': '10'})
        first = ('\ufeff' + HEADER + '2026-07,A,0,0\n2026-08,A,0,0').encode()
        plan = mapping(first); plan['table']['firstLine'] = 1
        binding = aggregate_records(first, plan)
        self.assertEqual(binding['source']['tableByteStart'], 3)
        self.assertEqual(binding['arithmeticInput']['periods'][0]['processingHours'], '0')

    def test_missing_period_is_not_zero_and_unknown_period_is_not_filtered(self):
        for rows in [[ROWS[0], ROWS[2]], [ROWS[0], '2026-09,other,1,0'], []]:
            raw = source(rows)
            with self.assertRaises(ValueError): calculate_requests(raw, mapping(raw))
        raw = source(); plan = mapping(raw); plan['periods'].reverse()
        result = calculate_requests(raw, plan)
        self.assertEqual(result['calculation']['input']['periods'][0]['period'], '2026-08')
        self.assertEqual(result['calculation']['metrics']['meanHoursPerRequest']['relativeChangePercent']['exact'],
                         {'numerator': '100', 'denominator': '3'})

    def test_invalid_cells_are_not_coerced_or_normalized(self):
        mutations = {'request_id': ['', ' 001', '001 ', 'a/b', '=1+2', '{{ input }}', 'a' * 65],
                     'hours': ['', '-1', '1e2', 'NaN', '01', '0.0000001', '1000000000000.000001', '=2'],
                     'reopened': ['', 'true', '2', '01', '0.0', ' 1']}
        indices = {'request_id': 1, 'hours': 2, 'reopened': 3}
        for field, values in mutations.items():
            for value in values:
                row = ROWS[0].split(','); row[indices[field]] = value
                raw = source([','.join(row), ROWS[1]])
                with self.subTest(field=field, value=value), self.assertRaises(ValueError): aggregate_records(raw, mapping(raw))
        raw = source(['2026-07,A,1000000000000,0', '2026-07,B,0.000001,0', ROWS[1]])
        with self.assertRaisesRegex(ValueError, 'Aggregate hours'): aggregate_records(raw, mapping(raw))

    def test_units_population_identity_and_duplicate_policy_are_explicit(self):
        raw = source()
        mutations = [('units', {**UNITS, 'processingHours': 'minutes'}), ('units', {**UNITS, 'hasException': 'event_count'}),
                     ('population', {'id': 'demo', **DECLARATIONS, 'comparablePeriods': 1}),
                     ('population', {'id': 'demo', **DECLARATIONS, 'recordScope': 'event'}),
                     ('deduplication', {**DEDUPLICATION, 'key': ['requestId']}),
                     ('deduplication', {**DEDUPLICATION, 'conflict': 'last'}),
                     ('sourceId', 'bad/name'), ('sourceUri', 'https://example.com/`[bad]'), ('approved', True)]
        for key, value in mutations:
            plan = mapping(raw); plan[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): aggregate_records(raw, plan)

    def test_header_range_csv_syntax_and_record_limits_fail_without_silent_repair(self):
        raw = source()
        for value in [raw.replace(b'reopened', b'hours'), raw.replace(b'request_id', b' request_id'),
                      raw.replace(b'2026-07,001,1.5,1', b'2026-07,001,1.5'),
                      raw.replace(b'2026-07,001,1.5,1', b'2026-07,001,1.5,1,extra'),
                      raw.replace(b'2026-07,001,1.5,1', b'"2026-07,001,1.5,1'),
                      raw.replace(b'2026-07,001,1.5,1', b'')]:
            with self.assertRaises(ValueError): aggregate_records(value, mapping(value))
        for first, last in [(0, 8), (True, 8), (2, 2), (2, 9), (1, 8)]:
            plan = mapping(raw); plan['table'] = {'firstLine': first, 'lastLine': last}
            with self.assertRaises(ValueError): aggregate_records(raw, plan)
        raw = source([ROWS[0]] * (MAX_RECORDS - 1) + [ROWS[1]])
        self.assertEqual(aggregate_records(raw, mapping(raw))['counts']['sourceRecords'], MAX_RECORDS)
        raw += (ROWS[1] + '\n').encode()
        with self.assertRaisesRegex(ValueError, 'Bounded complete'): aggregate_records(raw, mapping(raw))

    def test_changed_hash_invalid_encoding_nul_and_oversized_source_are_rejected(self):
        raw = source()
        with self.assertRaisesRegex(ValueError, 'SHA-256'): aggregate_records(b'changed\n' + raw, mapping(raw))
        for value in [b'\xff\n' + raw, raw + b'\x00', b'x' * (MAX_SOURCE_BYTES + 1)]:
            with self.assertRaises(ValueError): aggregate_records(value, mapping(value))

    def test_rendering_recomputes_source_deduplication_provenance_and_arithmetic(self):
        raw = source(); report = calculate_requests(raw, mapping(raw))
        md = request_markdown(report, raw)
        self.assertIn('одинаковых повторов исключено: 2', md)
        self.assertIn('| Среднее время на заявку | 2.000 ч | 1.500 ч | -25.000% |', md)
        self.assertEqual(md, request_markdown(json.loads(json.dumps(report)), raw))
        for field in ('duplicate', 'offset', 'counts', 'aggregate', 'calculation', 'claim'):
            forged = copy.deepcopy(report); forged.pop('sha256')
            if field == 'duplicate': forged['binding']['records'][3]['duplicateOf'] = 'requests-L5'
            elif field == 'offset': forged['binding']['records'][0]['byteStart'] += 1
            elif field == 'counts': forged['binding']['counts']['duplicateRecords'] = 0
            elif field == 'aggregate': forged['binding']['aggregation'][0]['includedRecordIds'].append('requests-L6')
            elif field == 'calculation': forged['calculation']['metrics']['requests']['first']['display'] = '3.000'
            else: forged['sourceTruthVerified'] = True
            with self.subTest(field=field), self.assertRaises(ValueError): request_markdown(sealed(forged), raw)
        with self.assertRaises(ValueError): request_markdown(report, raw.replace(b'1.5', b'1.6'))

    def test_cli_writes_reviewable_files_and_rejects_conflict_without_outputs_or_overwrite(self):
        with tempfile.TemporaryDirectory(prefix='.request-records-test-', dir=ROOT / 'docs') as directory:
            base = Path(directory); csv_path = base / 'requests.csv'; csv_path.write_bytes(source())
            config = base / 'mapping.json'; write_new(config, mapping(csv_path.read_bytes()))
            output = base / 'result.json'; md = base / 'report.md'
            command = [sys.executable, str(ROOT / 'scripts/calculate-request-report.py'), '--source', str(csv_path),
                       '--mapping', str(config), '--output', str(output), '--markdown', str(md)]
            run = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('4 unique request-periods; 2 duplicates excluded', run.stdout)
            self.assertIn('одинаковых повторов исключено: 2', md.read_text())
            original = output.read_bytes()
            self.assertNotEqual(subprocess.run(command, capture_output=True, timeout=10).returncode, 0)
            self.assertEqual(output.read_bytes(), original)
            output.unlink(); md.unlink(); config.unlink()
            csv_path.write_bytes(source(ROWS + ['2026-07,001,9,1']))
            write_new(config, mapping(csv_path.read_bytes()))
            run = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(run.returncode, 0); self.assertIn('Conflicting duplicate', run.stderr)
            self.assertFalse(output.exists()); self.assertFalse(md.exists())


if __name__ == '__main__':
    unittest.main()
