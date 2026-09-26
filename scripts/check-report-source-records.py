#!/usr/bin/env python3
"""Exercise the pinned CSV-to-arithmetic CLI on the existing authored report source."""

import argparse
import hashlib
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import read_json, sealed, verify_seal, write_new
from decision_runtime.contracts import fingerprint
from scripts.lib.report_source_records import MAPPING_SCHEMA, BINDING_SCHEMA, RESULT_SCHEMA, UNITS, DECLARATIONS


def require(value, message):
    if not value: raise ValueError(message)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args()
    require(args.evidence_dir.resolve().is_relative_to(ROOT / 'docs'), 'Evidence belongs under docs')
    paths = {name: args.evidence_dir / f'source-records-{name}.{extension}' for name, extension in
             [('plan', 'json'), ('source', 'csv'), ('mapping', 'json'), ('result', 'json'), ('report', 'md'), ('verification', 'json')]}
    require(not any(path.exists() for path in paths.values()), 'Use new experiment paths')
    fixture = read_json(args.fixture)
    require(fixture['schemaVersion'] == 1 and fixture['classification'] == 'synthetic-public', 'Only the authored qualification fixture is expected')
    candidates = [source for source in fixture['sources'] if source['uri'] == 'urn:agat:internal-report:v1:requests']
    require(len(candidates) == 1, 'Expected one source')
    source = candidates[0]['content'].encode('utf-8')
    lines = source.splitlines(keepends=True)
    expected_rows = [b'2026-07,100,400,8\n', b'2026-08,120,360,6\n']
    require(len(lines) == 5 and lines[2] == b'period,requests,total_resolution_hours,reopened\n'
            and lines[3:] == expected_rows, 'Authored source changed; declare a separate diagnostic')
    mapping = {'schemaVersion': MAPPING_SCHEMA, 'sourceId': 'support-demo', 'sourceUri': candidates[0]['uri'],
               'expectedSha256': sha(source), 'table': {'firstLine': 3, 'lastLine': 5},
               'columns': {'period': 'period', 'requests': 'requests', 'processingHours': 'total_resolution_hours', 'exceptions': 'reopened'},
               'periods': ['2026-07', '2026-08'], 'units': UNITS,
               'population': {'id': 'support-demo-counted-requests', **DECLARATIONS}}
    expected = {'requests.relativeChangePercent': ['20', '1'], 'processingHours.relativeChangePercent': ['-10', '1'],
                'meanHoursPerRequest.first': ['4', '1'], 'meanHoursPerRequest.second': ['3', '1'],
                'meanHoursPerRequest.relativeChangePercent': ['-25', '1'], 'exceptionRatePercent.first': ['8', '1'],
                'exceptionRatePercent.second': ['5', '1'], 'exceptionRatePercent.change': ['-3', '1'],
                'exceptionRatePercent.relativeChangePercent': ['-75', '2']}
    names = ['scripts/lib/report_source_records.py', 'scripts/calculate-source-report.py', 'scripts/check-report-source-records.py',
             'scripts/lib/report_arithmetic.py', 'scripts/test/test_report_source_records.py']
    plan = sealed({'schemaVersion': 'agat.report.source-records-plan.v1', 'createdAt': datetime.now(timezone.utc).isoformat(),
                   'fixtureSha256': sha(args.fixture.read_bytes()), 'sourceSha256': sha(source), 'mapping': mapping,
                   'expectedExact': expected, 'populationDeclarations': 'authored diagnostic assumptions; the fixture does not independently establish per-request deduplication',
                   'files': {name: sha((ROOT / name).read_bytes()) for name in names}, 'routingEnabled': False,
                   'limitations': ['This is the existing synthetic SUPPORT-DEMO source, not an independent business dataset.',
                                   'No real-case qualification or production process change; no model call or automatic approval.']})
    write_new(paths['plan'], plan)
    with paths['source'].open('xb') as handle: handle.write(source)
    write_new(paths['mapping'], mapping)
    command = [sys.executable, str(ROOT / 'scripts/calculate-source-report.py'), '--source', str(paths['source']),
               '--mapping', str(paths['mapping']), '--output', str(paths['result']), '--markdown', str(paths['report'])]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=10, check=True)
    report = verify_seal(read_json(paths['result']), RESULT_SCHEMA)
    binding = verify_seal(report['binding'], BINDING_SCHEMA)
    calculation = verify_seal(report['calculation'], 'agat.report.operations-result.v1')
    require(report['status'] == 'calculated' and binding['mapping'] == mapping
            and binding['mappingSha256'] == fingerprint(mapping) and binding['source']['sha256'] == sha(source), 'Incorrect source binding')
    for row, expected_row, line_number in zip(binding['records'], expected_rows, [4, 5]):
        begin = sum(len(line) for line in lines[:line_number - 1])
        require(row['firstLine'] == row['lastLine'] == line_number and row['byteStart'] == begin
                and row['byteEnd'] == begin + len(expected_row)
                and source[row['byteStart']:row['byteEnd']] == expected_row == row['rawRecord'].encode()
                and row['rawRecordSha256'] == sha(expected_row), 'Raw row/byte evidence mismatch')
        cells = dict(zip(['period', 'requests', 'total_resolution_hours', 'reopened'], expected_row.decode().strip().split(',')))
        require(row['cells'] == cells, 'Cell binding mismatch')
    require(calculation['input'] == binding['arithmeticInput'] and calculation['sourceSha256'] == sha(source)
            and calculation['inputSha256'] == binding['arithmeticInputSha256'], 'Calculator input detached from source')
    for key, values in expected.items():
        metric, part = key.split('.')
        require(calculation['metrics'][metric][part]['exact'] == dict(zip(['numerator', 'denominator'], values)), 'Incorrect exact metric')
    for value in (report, binding):
        require(all(value[key] is False for key in ('sourceTruthVerified', 'populationAlignmentVerified', 'qualifiedForRouting', 'routingEnabled')), 'Unexpected acceptance')
    require(calculation['sourceValuesVerified'] is False, 'Calculator cannot promote source truth')
    rendered = paths['report'].read_text()
    require('| Среднее время на заявку | 4.000 ч | 3.000 ч | -25.000% |' in rendered
            and '| Доля отклонений | 8.000% | 5.000% | -3.000 п.п. |' in rendered
            and sha(source) in rendered and 'reopened' in rendered, 'Report missing arithmetic or source mapping')
    verification = sealed({'schemaVersion': 'agat.report.source-records-verification.v1', 'createdAt': datetime.now(timezone.utc).isoformat(),
                           'status': 'verified_diagnostic_only', 'planSha256': plan['sha256'], 'resultSha256': report['sha256'],
                           'sourceSha256': sha(source), 'mappingSha256': fingerprint(mapping), 'verifiedRows': 2, 'verifiedExactQuantities': len(expected),
                           'markdownSha256': sha(paths['report'].read_bytes()), 'cliExitCode': completed.returncode,
                           'routingEnabled': False, 'qualifiedForRouting': False,
                           'checks': {'rawSourceAndSpansBound': True, 'explicitCellsMatch': True, 'calculatorBoundToSource': True,
                                      'allPredeclaredExactValues': True, 'sourceTruthAndPopulationNotAsserted': True}, 'limitations': plan['limitations']})
    write_new(paths['verification'], verification)
    print(verification['status'], '2 source rows, 9 exact quantities; no production workflow changed')


if __name__ == '__main__': main()
