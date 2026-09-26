#!/usr/bin/env python3
"""Reproduce an exact arithmetic baseline for the already measured authored workflow fixture."""

import argparse
import hashlib
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import read_json, sealed, verify_seal, write_new
from decision_runtime.contracts import fingerprint
from scripts.lib.report_arithmetic import INPUT_SCHEMA, RESULT_SCHEMA

RECORD = re.compile(r'\[([A-Z][A-Z0-9]{0,7})\] ([А-Яа-яЁё -]{1,64}): ([0-9]{1,10}) заявок, '
                    r'([0-9]{1,13}(?:\.[0-9]{1,6})?) часов обработки, ([0-9]{1,10}) отклонений\.')


def require(value, message):
    if not value: raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args()
    require(args.evidence_dir.resolve().is_relative_to((ROOT / 'docs').resolve()), 'Evidence belongs under docs')
    paths = {key: args.evidence_dir / f'arithmetic-{key}.{suffix}' for key, suffix in
             [('plan', 'json'), ('input', 'json'), ('result', 'json'), ('report', 'md'), ('verification', 'json')]}
    require(not any(p.exists() for p in paths.values()), 'Use new evidence paths')
    fixture = read_json(args.fixture)
    require(fixture['schemaVersion'] == 'agat.decision.workflow-fixture.v1'
            and fixture['provenance'] == 'authored-synthetic; repeated throughput input, not independent quality cases',
            'This probe only maps the preexisting authored fixture format')
    source = fixture['input']
    matches = list(RECORD.finditer(source))
    require(len(matches) == 2 and [m.group(1) for m in matches] == ['A', 'B'], 'Exactly two authored records are required')
    rows = [{'sourceId': m.group(1), 'period': m.group(2), 'requests': int(m.group(3)),
             'processingHours': m.group(4), 'exceptions': int(m.group(5))} for m in matches]
    # This diagnostic has predeclared expected arithmetic for the one existing
    # fixture. The reusable calculator accepts other validated records.
    require([(r['requests'], r['processingHours'], r['exceptions']) for r in rows] == [(100, '400', 8), (120, '360', 6)],
            'Fixture changed; author a separate predeclared expected-results probe')
    data = {'schemaVersion': INPUT_SCHEMA, 'sourceSha256': hashlib.sha256(source.encode()).hexdigest(), 'periods': rows}
    expected = {'requests.relativeChangePercent': ['20', '1'], 'processingHours.relativeChangePercent': ['-10', '1'],
                'meanHoursPerRequest.first': ['4', '1'], 'meanHoursPerRequest.second': ['3', '1'],
                'meanHoursPerRequest.relativeChangePercent': ['-25', '1'], 'exceptionRatePercent.first': ['8', '1'],
                'exceptionRatePercent.second': ['5', '1'], 'exceptionRatePercent.change': ['-3', '1'],
                'exceptionRatePercent.relativeChangePercent': ['-75', '2']}
    files = ['scripts/lib/report_arithmetic.py', 'scripts/calculate-report-metrics.py', 'scripts/check-report-arithmetic.py']
    plan = sealed({'schemaVersion': 'agat.report.arithmetic-plan.v1', 'createdAt': datetime.now(timezone.utc).isoformat(),
                   'fixtureSha256': hashlib.sha256(args.fixture.read_bytes()).hexdigest(), 'sourceSha256': data['sourceSha256'],
                   'inputSha256': fingerprint(data), 'input': data, 'sourceMapping': 'fixed_authored_record_template',
                   'sourceSpans': [{'start': m.start(), 'end': m.end(), 'quote': m.group(0)} for m in matches],
                   'expectedExact': expected, 'displayDecimalPlaces': 3, 'routingEnabled': False,
                   'files': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in files},
                   'limitations': ['One old synthetic workflow input with mechanically mapped numbers, not natural-language extraction quality.',
                                   'The expected figures were known from prior workflow analysis; this is a regression baseline.']})
    write_new(paths['plan'], plan); write_new(paths['input'], data)
    completed = subprocess.run([sys.executable, str(ROOT / 'scripts/calculate-report-metrics.py'), '--input', str(paths['input']),
                               '--output', str(paths['result']), '--markdown', str(paths['report'])],
                              cwd=ROOT, text=True, capture_output=True, timeout=10, check=True)
    result = verify_seal(read_json(paths['result']), RESULT_SCHEMA)
    require(result['input'] == data and result['inputSha256'] == fingerprint(data) and result['status'] == 'calculated', 'Calculation not bound')
    require(result['sourceValuesVerified'] is False and result['qualifiedForRouting'] is False and result['routingEnabled'] is False,
            'Arithmetic cannot grant source truth or workflow authority')
    for key, values in expected.items():
        metric, part = key.split('.')
        quantity = result['metrics'][metric][part]
        require(quantity['status'] == 'defined' and quantity['exact'] == dict(zip(('numerator', 'denominator'), values)),
                'Incorrect exact result')
    require(result['metrics']['exceptionRatePercent']['change']['unit'] == 'percentage_points', 'Rate change lost its unit')
    text = paths['report'].read_text()
    for line in ('| Среднее время на заявку | 4.000 ч | 3.000 ч | -25.000% |',
                 '| Доля отклонений | 8.000% | 5.000% | -3.000 п.п. |'):
        require(line in text, 'Rendered report changed verified arithmetic')
    report = sealed({'schemaVersion': 'agat.report.arithmetic-verification.v1', 'createdAt': datetime.now(timezone.utc).isoformat(),
                     'status': 'verified', 'planSha256': plan['sha256'], 'resultSha256': result['sha256'],
                     'sourceSha256': data['sourceSha256'], 'markdownSha256': hashlib.sha256(paths['report'].read_bytes()).hexdigest(),
                     'sourceMapping': 'exact_matches_within_the_authored_fixture', 'verifiedExactQuantities': len(expected),
                     'cliExitCode': completed.returncode, 'routingEnabled': False, 'qualifiedForRouting': False,
                     'limitations': plan['limitations'] + ['No model generation, workflow route or production process was changed.',
                        'The calculator needs structured values; this is not an end-to-end semantic competitor to a language model.']})
    write_new(paths['verification'], report)
    print(report['status'], f"{len(expected)} exact quantities; report: {paths['report']}")


if __name__ == '__main__': main()
