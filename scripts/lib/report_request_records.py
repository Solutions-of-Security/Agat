"""Aggregate explicitly mapped request-period records, rejecting conflicting duplicates."""

import csv
import hashlib
import io
import re
from fractions import Fraction
from pathlib import Path

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import fields, fingerprint
from scripts.lib.report_arithmetic import DECIMAL, INPUT_SCHEMA, LABEL, MAX_HOURS, calculate, markdown, rounded, validate_input

MAPPING_SCHEMA = 'agat.report.request-csv-mapping.v1'
BINDING_SCHEMA = 'agat.report.request-records.v1'
RESULT_SCHEMA = 'agat.report.request-calculation.v1'
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_LINES = 20000
MAX_RECORDS = 10000
UNITS = {'processingHours': 'hours', 'hasException': 'boolean_0_1'}
DECLARATIONS = {'recordScope': 'one_complete_request_per_period', 'hoursScope': 'same_counted_requests',
                'exceptionScope': 'boolean_per_counted_request', 'comparablePeriods': True}
DEDUPLICATION = {'key': ['period', 'requestId'], 'matching': 'typed_values', 'conflict': 'reject'}
UNVERIFIED = {'sourceTruthVerified': False, 'sourceCompletenessVerified': False, 'identitySemanticsVerified': False,
              'populationAlignmentVerified': False, 'qualifiedForRouting': False, 'routingEnabled': False}


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def validate_mapping(raw):
    value = fields(raw, {'schemaVersion', 'sourceId', 'sourceUri', 'expectedSha256', 'table', 'columns',
                         'periods', 'units', 'population', 'deduplication'})
    require(value['schemaVersion'] == MAPPING_SCHEMA, 'Unsupported request source mapping')
    require(isinstance(value['sourceId'], str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,15}', value['sourceId']), 'Invalid source ID')
    require(isinstance(value['sourceUri'], str) and len(value['sourceUri']) <= 512
            and re.fullmatch(r'(?:urn:|agat://|https://)[A-Za-z0-9_./:%?=&#+~-]+', value['sourceUri']), 'Invalid source URI')
    require(isinstance(value['expectedSha256'], str) and re.fullmatch(r'[a-f0-9]{64}', value['expectedSha256']), 'Expected SHA-256 is required')
    table = fields(value['table'], {'firstLine', 'lastLine'})
    require(all(type(v) is int for v in table.values()) and 1 <= table['firstLine'] < table['lastLine'] <= MAX_LINES,
            'Explicit inclusive line bounds required')
    columns = fields(value['columns'], {'period', 'requestId', 'processingHours', 'hasException'})
    require(all(isinstance(name, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name) for name in columns.values())
            and len(set(columns.values())) == 4, 'Four unique explicit column names required')
    require(isinstance(value['periods'], list) and len(value['periods']) == 2
            and all(isinstance(p, str) and LABEL.fullmatch(p) and p == p.strip() for p in value['periods'])
            and value['periods'][0] != value['periods'][1], 'Two explicit ordered periods required')
    require(value['units'] == UNITS, 'Unsupported/unspecified units; no implicit conversion')
    population = fields(value['population'], {'id', *DECLARATIONS})
    require(isinstance(population['id'], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', population['id']), 'Population ID required')
    require(all(type(population[k]) is type(v) and population[k] == v for k, v in DECLARATIONS.items()),
            'Declare complete request-period records, common populations and comparable periods')
    require(value['deduplication'] == DEDUPLICATION, 'Declare period/requestId keys, typed equality and rejection of conflicts')
    return {**value, 'table': dict(table), 'columns': dict(columns), 'periods': list(value['periods']),
            'units': dict(UNITS), 'population': dict(population),
            'deduplication': {**DEDUPLICATION, 'key': list(DEDUPLICATION['key'])}}


def _decimal(value):
    # Every input has at most six decimal places, so the sum is exactly representable here.
    text = rounded(value, 6).rstrip('0').rstrip('.')
    require(Fraction(text) == value, 'Aggregate hours cannot be represented exactly')
    return text


def aggregate_records(source: bytes, raw_mapping):
    mapping = validate_mapping(raw_mapping)
    require(type(source) is bytes and 0 < len(source) <= MAX_SOURCE_BYTES, 'Source must be bounded bytes')
    require(sha(source) == mapping['expectedSha256'], 'Source SHA-256 changed')
    try:
        text = source.decode('utf-8')
    except UnicodeDecodeError:
        raise ValueError('Source must be UTF-8') from None
    require('\x00' not in text, 'NUL in source')
    bom = 3 if text.startswith('\ufeff') else 0
    if bom:
        text = text[1:]
    lines = list(io.StringIO(text, newline=''))
    require(len(lines) <= MAX_LINES, 'Too many source lines')
    first, last = mapping['table']['firstLine'], mapping['table']['lastLine']
    require(last <= len(lines), 'CSV bounds exceed the source')
    offsets = [bom]
    for line in lines:
        offsets.append(offsets[-1] + len(line.encode('utf-8')))
    selected = ''.join(lines[first - 1:last])
    reader = csv.reader(io.StringIO(selected, newline=''), delimiter=',', quotechar='"', doublequote=True,
                        skipinitialspace=False, strict=True)
    parsed = []
    previous_line = 0
    try:
        for row in reader:
            require(len(parsed) <= MAX_RECORDS and len(row) == 4, 'Bounded complete four-cell records required')
            parsed.append((row, first + previous_line, first + reader.line_num - 1))
            previous_line = reader.line_num
    except csv.Error:
        raise ValueError('Invalid CSV syntax') from None
    require(len(parsed) >= 3, 'Header and records for both periods required')
    header = parsed[0][0]
    require(len(set(header)) == 4 and set(header) == set(mapping['columns'].values()), 'CSV header does not match unique mapped columns')
    records = []
    unique = {}
    for row, start, end in parsed[1:]:
        cells = dict(zip(header, row))
        values = {key: cells[column] for key, column in mapping['columns'].items()}
        require(values['period'] in mapping['periods'], f'Unknown period at source line {start}')
        require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', values['requestId']), f'Invalid request ID at source line {start}')
        require(DECIMAL.fullmatch(values['processingHours']), f'Hours must be bounded nonnegative decimal strings at source line {start}')
        hours = Fraction(values['processingHours'])
        require(hours <= MAX_HOURS, f'Hours exceed supported bounds at source line {start}')
        require(values['hasException'] in ('0', '1'), f'Exception flag must be 0 or 1 at source line {start}')
        record_id = f"{mapping['sourceId']}-L{start}"
        key = (values['period'], values['requestId'])
        metrics = (hours, int(values['hasException']))
        canonical = unique.get(key)
        if canonical is not None:
            require(canonical['metrics'] == metrics,
                    f"Conflicting duplicate at source lines {canonical['line']} and {start}; no record selected")
        else:
            unique[key] = {'metrics': metrics, 'sourceId': record_id, 'line': start}
        begin, finish = offsets[start - 1], offsets[end]
        quoted = source[begin:finish]
        records.append({'sourceId': record_id, 'firstLine': start, 'lastLine': end, 'byteStart': begin, 'byteEnd': finish,
                        'rawRecordSha256': sha(quoted), 'rawRecord': quoted.decode('utf-8'), 'cells': cells,
                        'values': values, 'included': canonical is None,
                        'duplicateOf': canonical['sourceId'] if canonical is not None else None})
    aggregation = []
    arithmetic = []
    for index, period in enumerate(mapping['periods'], 1):
        members = [record for record in records if record['values']['period'] == period]
        included = [record['sourceId'] for record in members if record['included']]
        require(included, f'No records for period {period}; missing data cannot imply zero requests')
        metrics = [record['metrics'] for (p, _), record in unique.items() if p == period]
        hours = sum((value[0] for value in metrics), Fraction(0))
        require(hours <= MAX_HOURS, f'Aggregate hours exceed supported bounds for period {period}')
        result = {'sourceId': f"{mapping['sourceId']}-P{index}", 'period': period, 'requests': len(included),
                  'processingHours': _decimal(hours), 'exceptions': sum(value[1] for value in metrics)}
        arithmetic.append(result)
        aggregation.append({**result, 'sourceRecordCount': len(members), 'includedRecordIds': included,
                            'duplicateRecordIds': [record['sourceId'] for record in members if not record['included']]})
    data = validate_input({'schemaVersion': INPUT_SCHEMA, 'sourceSha256': sha(source), 'periods': arithmetic})
    begin, finish = offsets[first - 1], offsets[last]
    return sealed({'schemaVersion': BINDING_SCHEMA, 'status': 'aggregated', 'mapping': mapping, 'mappingSha256': fingerprint(mapping),
                   'source': {'id': mapping['sourceId'], 'uri': mapping['sourceUri'], 'sha256': sha(source), 'byteLength': len(source),
                              'encoding': 'utf-8-sig' if bom else 'utf-8', 'tableByteStart': begin, 'tableByteEnd': finish,
                              'tableSha256': sha(source[begin:finish]), 'tableText': selected},
                   'records': records, 'aggregation': aggregation,
                   'counts': {'sourceRecords': len(records), 'uniqueRequestPeriods': len(unique), 'duplicateRecords': len(records) - len(unique)},
                   'arithmeticInput': data, 'arithmeticInputSha256': fingerprint(data),
                   'implementationSha256': sha(Path(__file__).read_bytes()), 'sourceBytesMatched': True, 'cellsMatchSelectedCsv': True,
                   'deduplicationApplied': True, **UNVERIFIED,
                   'limitations': ['The caller selects the source, table, columns, periods, units and identity scope.',
                                   'Only equal typed values for the same period/requestId are collapsed; conflicting values are rejected.',
                                   'IDs are exact case-sensitive strings; the same ID in different periods is counted once per period.',
                                   'Each row must describe a complete request-period, not an event or a partial duration.',
                                   'Source truth, completeness and the real-world meaning of IDs and population declarations are not verified.']})


def calculate_requests(source: bytes, mapping, *, decimal_places=3):
    binding = aggregate_records(source, mapping)
    result = calculate(binding['arithmeticInput'], decimal_places=decimal_places)
    return sealed({'schemaVersion': RESULT_SCHEMA, 'status': result['status'], 'binding': binding, 'calculation': result, **UNVERIFIED})


def request_markdown(report, source: bytes):
    verify_seal(report, RESULT_SCHEMA)
    expected = calculate_requests(source, report['binding']['mapping'], decimal_places=report['calculation']['rounding']['decimalPlaces'])
    require(expected == report, 'Request binding, deduplication or arithmetic result changed')
    binding = report['binding']
    mapping = binding['mapping']
    counts = binding['counts']
    lines = [markdown(report['calculation']).rstrip(), '', '## Расчёт по отдельным заявкам', '',
             f"Источник: `{mapping['sourceUri']}`. SHA-256 исходных байтов: `{binding['source']['sha256']}`.",
             f"Выбраны строки {mapping['table']['firstLine']}–{mapping['table']['lastLine']} включительно, считая с 1; "
             f"границы байтов: [{binding['source']['tableByteStart']}, {binding['source']['tableByteEnd']}).", '',
             f"Строк данных: {counts['sourceRecords']}; уникальных пар «период + ID заявки»: {counts['uniqueRequestPeriods']}; "
             f"одинаковых повторов исключено: {counts['duplicateRecords']}.",
             'Ключ дубля — период и ID заявки. Часы сравниваются как точные числа (1 и 1.0 равны), признак — как 0 или 1. '
             'Конфликтующие дубли останавливают расчёт; значения не выбираются по порядку строк.',
             'ID сравниваются посимвольно с учётом регистра: 001 и 1 различаются. Один ID в разных периодах учитывается в каждом периоде.', '',
             '| Группа | Период | Строк данных | Уникальных заявок | Исключено повторов |', '|---|---|---:|---:|---:|']
    lines += [f"| {row['sourceId']} | {row['period']} | {row['sourceRecordCount']} | {row['requests']} | {len(row['duplicateRecordIds'])} |"
              for row in binding['aggregation']]
    lines += ['', '| Поле | Столбец источника |', '|---|---|']
    lines += [f"| {key} | `{mapping['columns'][key]}` |" for key in ('period', 'requestId', 'processingHours', 'hasException')]
    lines += ['', f"Совокупность, заявленная в mapping: `{mapping['population']['id']}`.",
              f"«Отклонения» означают заявки со значением 1 в столбце `{mapping['columns']['hasException']}`; знаменатель — все уникальные заявки периода.",
              'Каждая строка должна содержать полные часы заявки за период и один булев признак; периоды должны быть сопоставимы. '
              'Это декларации mapping. События и отдельные интервалы работы требуют другого расчёта.',
              'Проверены исходные байты, выбранные ячейки, дубли по заданному ключу и арифметика. '
              'Полнота выгрузки, достоверность, смысл ID и принадлежность одной совокупности не подтверждены.',
              'Расчётный JSON содержит все исходные строки с byte offsets, SHA и ссылками duplicateOf на учтённую запись.', '']
    return '\n'.join(lines)
