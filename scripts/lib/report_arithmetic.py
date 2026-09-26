"""Exact arithmetic for two explicitly supplied operational records, without NLP."""

from __future__ import annotations

import hashlib
import re
from fractions import Fraction
from pathlib import Path

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import fields, fingerprint

INPUT_SCHEMA = 'agat.report.operations-input.v1'
RESULT_SCHEMA = 'agat.report.operations-result.v1'
MAX_COUNT = 1_000_000_000
MAX_HOURS = 1_000_000_000_000
DECIMAL = re.compile(r'(?:0|[1-9][0-9]{0,12})(?:\.[0-9]{1,6})?')
LABEL = re.compile(r'[\w -]{1,64}', flags=re.UNICODE)


def _require(value, message):
    if not value:
        raise ValueError(message)


def validate_input(raw):
    data = fields(raw, {'schemaVersion', 'sourceSha256', 'periods'})
    _require(data['schemaVersion'] == INPUT_SCHEMA, 'Unsupported arithmetic input schema')
    _require(isinstance(data['sourceSha256'], str) and re.fullmatch(r'[a-f0-9]{64}', data['sourceSha256']), 'Missing source hash')
    _require(isinstance(data['periods'], list) and len(data['periods']) == 2, 'Exactly two ordered periods are required')
    records = []
    for row in data['periods']:
        row = fields(row, {'period', 'sourceId', 'requests', 'processingHours', 'exceptions'})
        _require(isinstance(row['period'], str) and LABEL.fullmatch(row['period']) and row['period'].strip() == row['period'],
                 'Invalid period label')
        _require(isinstance(row['sourceId'], str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,31}', row['sourceId']), 'Invalid source reference')
        _require(type(row['requests']) is int and 0 <= row['requests'] <= MAX_COUNT, 'Invalid request count')
        _require(type(row['exceptions']) is int and 0 <= row['exceptions'] <= row['requests'], 'Invalid exception count')
        hours = row['processingHours']
        _require(isinstance(hours, str) and DECIMAL.fullmatch(hours), 'Hours must be a bounded nonnegative decimal string')
        parsed = Fraction(hours)
        _require(parsed <= MAX_HOURS, 'Hours exceed supported bounds')
        _require(row['requests'] != 0 or parsed == 0, 'Nonzero processing hours cannot belong to zero counted requests')
        records.append({**row})
    _require(records[0]['period'] != records[1]['period'], 'Period labels must differ')
    return {'schemaVersion': INPUT_SCHEMA, 'sourceSha256': data['sourceSha256'], 'periods': records}


def rounded(value: Fraction, places: int):
    """Round rationals directly, ties to even; never pass through binary float."""
    _require(type(places) is int and 0 <= places <= 6, 'Display precision must be between zero and six')
    whole, remainder = divmod(abs(value.numerator) * 10 ** places, value.denominator)
    if 2 * remainder > value.denominator or (2 * remainder == value.denominator and whole % 2):
        whole += 1
    sign = '-' if value < 0 and whole else ''
    if places == 0:
        return sign + str(whole)
    digits = str(whole).zfill(places + 1)
    return sign + digits[:-places] + '.' + digits[-places:]


def quantity(value, unit, places, reason=None):
    if value is None:
        _require(reason in ('zero_requests', 'zero_baseline', 'undefined_period_metric'), 'Undefined quantity needs a reason')
        return {'status': 'undefined', 'reason': reason, 'unit': unit, 'exact': None, 'display': None}
    value = Fraction(value)
    return {'status': 'defined', 'unit': unit, 'exact': {'numerator': str(value.numerator), 'denominator': str(value.denominator)},
            'display': rounded(value, places)}


def _metric(values, unit, change_unit, places, *, percent_change=True):
    first, second = values
    absolute = None if first is None or second is None else second - first
    metric = {'first': quantity(first, unit, places, 'zero_requests'),
              'second': quantity(second, unit, places, 'zero_requests'),
              'change': quantity(absolute, change_unit, places, 'undefined_period_metric')}
    if percent_change:
        relative = None if absolute is None or first == 0 else absolute / first * 100
        metric['relativeChangePercent'] = quantity(relative, 'percent', places,
                                                   'undefined_period_metric' if absolute is None else 'zero_baseline')
    return metric


def calculate(raw, *, decimal_places=3):
    _require(type(decimal_places) is int and 0 <= decimal_places <= 6, 'Invalid display precision')
    data = validate_input(raw)
    counts = [Fraction(r['requests']) for r in data['periods']]
    hours = [Fraction(r['processingHours']) for r in data['periods']]
    exceptions = [Fraction(r['exceptions']) for r in data['periods']]
    means = [h / n if n else None for h, n in zip(hours, counts)]
    rates = [e / n * 100 if n else None for e, n in zip(exceptions, counts)]
    metrics = {'requests': _metric(counts, 'requests', 'requests', decimal_places),
               'processingHours': _metric(hours, 'hours', 'hours', decimal_places),
               'meanHoursPerRequest': _metric(means, 'hours_per_request', 'hours_per_request', decimal_places),
               'exceptionRatePercent': _metric(rates, 'percent', 'percentage_points', decimal_places)}
    return sealed({'schemaVersion': RESULT_SCHEMA, 'status': 'partial' if any(q['status'] == 'undefined'
                       for metric in metrics.values() for q in metric.values()) else 'calculated',
                   'input': data, 'inputSha256': fingerprint(data), 'sourceSha256': data['sourceSha256'],
                   'implementationSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   'arithmetic': 'exact_rational', 'rounding': {'mode': 'half_even', 'decimalPlaces': decimal_places},
                   'sourceValuesVerified': False, 'qualifiedForRouting': False, 'routingEnabled': False,
                   'metrics': metrics,
                   'formulas': {'meanHoursPerRequest': 'processingHours / requests',
                                'exceptionRatePercent': 'exceptions / requests * 100',
                                'relativeChangePercent': '(second - first) / first * 100',
                                'percentagePointChange': 'second_rate_percent - first_rate_percent'},
                   'limitations': ['Arithmetic is conditional on supplied records; extraction, source truth and population alignment are not verified.',
                                   'Exceptions count requests with an exception, at most once per counted request.',
                                   'Period order is explicit input order; no date inference, causal inference or semantic classification.',
                                   'Display values are rounded; exact fractions remain in the machine result.']})


def markdown(report):
    """Render only a reproducible calculator result; no generated claims are spliced in."""
    from decision_runtime.artifacts import verify_seal
    verify_seal(report, RESULT_SCHEMA)
    expected = calculate(report['input'], decimal_places=report['rounding']['decimalPlaces'])
    _require(expected == report, 'Arithmetic result changed or belongs to another implementation')
    first, second = report['input']['periods']
    def text(q, suffix='', signed=False):
        if q['status'] == 'undefined':
            return 'не определено'
        positive = int(q['exact']['numerator']) > 0
        shown = q['exact']['numerator'] if q['unit'] == 'requests' else q['display']
        return ('+' if signed and positive else '') + shown + suffix
    metrics = report['metrics']
    lines = ['# Расчёт операционных показателей', '',
             f'Исходные данные [{first["sourceId"]}]: {first["requests"]} заявок, {first["processingHours"]} ч обработки, {first["exceptions"]} отклонений.',
             f'Исходные данные [{second["sourceId"]}]: {second["requests"]} заявок, {second["processingHours"]} ч обработки, {second["exceptions"]} отклонений.', '',
             f'| Показатель | {first["period"]} [{first["sourceId"]}] | {second["period"]} [{second["sourceId"]}] | Изменение |',
             '|---|---:|---:|---:|']
    for key, label, suffix in [('requests', 'Число заявок', ''), ('processingHours', 'Общее время обработки', ' ч'),
                               ('meanHoursPerRequest', 'Среднее время на заявку', ' ч'), ('exceptionRatePercent', 'Доля отклонений', '%')]:
        metric = metrics[key]
        change = (text(metric['change'], ' п.п.', True) if key == 'exceptionRatePercent'
                  else text(metric['relativeChangePercent'], '%', True))
        lines.append(f'| {label} | {text(metric["first"], suffix)} | {text(metric["second"], suffix)} | {change} |')
    lines += ['', f'Отображение округлено до {report["rounding"]["decimalPlaces"]} знаков после запятой; точные значения сохранены в расчётном JSON.',
              'Среднее время = часы обработки / число заявок. Доля отклонений = заявки с отклонением / все заявки × 100%.',
              'Изменение в процентах = (новое − прежнее) / прежнее × 100%; процентные пункты — разность двух долей в процентах.', '',
              'При нулевом числе заявок среднее и доля не определены; относительное изменение от нулевой базы также не определено.',
              'Причины изменений по этим данным установить нельзя. Расчёт не подтверждает полноту и достоверность исходных записей.', '']
    return '\n'.join(lines)
