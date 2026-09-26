"""Bind two explicitly mapped CSV records to exact source bytes before arithmetic."""

import csv
import hashlib
import io
import re
from pathlib import Path

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import fields, fingerprint
from scripts.lib.report_arithmetic import INPUT_SCHEMA, calculate, markdown, validate_input

MAPPING_SCHEMA = 'agat.report.csv-mapping.v1'
BINDING_SCHEMA = 'agat.report.source-records.v1'
RESULT_SCHEMA = 'agat.report.source-calculation.v1'
MAX_SOURCE_BYTES = 2 * 1024 * 1024
UNITS = {'requests': 'requests', 'processingHours': 'hours', 'exceptions': 'requests_with_exception'}
DECLARATIONS = {'hoursScope': 'same_counted_requests', 'exceptionScope': 'at_most_one_per_counted_request',
                'comparablePeriods': True}


def require(value, message):
    if not value: raise ValueError(message)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def validate_mapping(raw):
    value = fields(raw, {'schemaVersion', 'sourceId', 'sourceUri', 'expectedSha256', 'table', 'columns', 'periods', 'units', 'population'})
    require(value['schemaVersion'] == MAPPING_SCHEMA, 'Unsupported source mapping')
    require(isinstance(value['sourceId'], str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,15}', value['sourceId']), 'Invalid source ID')
    require(isinstance(value['sourceUri'], str) and len(value['sourceUri']) <= 512
            and re.fullmatch(r'(?:urn:|agat://|https://)[A-Za-z0-9_./:%?=&#+~-]+', value['sourceUri']), 'Invalid source URI')
    require(isinstance(value['expectedSha256'], str) and re.fullmatch(r'[a-f0-9]{64}', value['expectedSha256']), 'Expected SHA-256 is required')
    table = fields(value['table'], {'firstLine', 'lastLine'})
    require(all(type(v) is int for v in table.values()) and 1 <= table['firstLine'] < table['lastLine'] <= 20000, 'Explicit inclusive line bounds required')
    columns = fields(value['columns'], {'period', 'requests', 'processingHours', 'exceptions'})
    require(all(isinstance(name, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name) for name in columns.values())
            and len(set(columns.values())) == 4, 'Four unique explicit column names required')
    require(isinstance(value['periods'], list) and len(value['periods']) == 2
            and all(isinstance(p, str) and re.fullmatch(r'[\w -]{1,64}', p) and p == p.strip() for p in value['periods'])
            and value['periods'][0] != value['periods'][1], 'Two explicit ordered periods required')
    require(value['units'] == UNITS, 'Unsupported/unspecified units; no implicit conversion')
    population = fields(value['population'], {'id', *DECLARATIONS})
    require(isinstance(population['id'], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', population['id']), 'Population ID required')
    require(all(type(population[k]) is type(v) and population[k] == v for k, v in DECLARATIONS.items()),
            'Declare common populations, comparable periods and at most one exception per request')
    # Detach all nested data so a caller cannot mutate a result through its mapping.
    return {**value, 'table': dict(table), 'columns': dict(columns), 'periods': list(value['periods']),
            'units': dict(UNITS), 'population': dict(population)}


def bind_records(source: bytes, raw_mapping):
    mapping = validate_mapping(raw_mapping)
    require(type(source) is bytes and 0 < len(source) <= MAX_SOURCE_BYTES, 'Source must be bounded bytes')
    require(sha(source) == mapping['expectedSha256'], 'Source SHA-256 changed')
    try: text = source.decode('utf-8')
    except UnicodeDecodeError: raise ValueError('Source must be UTF-8') from None
    require('\x00' not in text, 'NUL in source')
    bom = 3 if text.startswith('\ufeff') else 0
    if bom: text = text[1:]
    # newline='' preserves CR/LF/CRLF and quoted CSV text, including byte spans.
    lines = list(io.StringIO(text, newline=''))
    require(len(lines) <= 20000, 'Too many source lines')
    first, last = mapping['table']['firstLine'], mapping['table']['lastLine']
    require(last <= len(lines), 'CSV bounds exceed the source')
    offsets = [bom]
    for line in lines: offsets.append(offsets[-1] + len(line.encode('utf-8')))
    selected = ''.join(lines[first - 1:last])
    reader = csv.reader(io.StringIO(selected, newline=''), delimiter=',', quotechar='"', doublequote=True,
                        skipinitialspace=False, strict=True)
    parsed = [];previous_line = 0
    try:
        for row in reader:
            require(len(parsed) < 3 and len(row) == 4, 'Exactly one header and two complete four-cell rows required')
            parsed.append((row, first + previous_line, first + reader.line_num - 1))
            previous_line = reader.line_num
    except csv.Error: raise ValueError('Invalid CSV syntax') from None
    require(len(parsed) == 3, 'Exactly two source records required')
    header = parsed[0][0]
    require(len(set(header)) == 4 and set(header) == set(mapping['columns'].values()), 'CSV header does not match unique mapped columns')
    records = [];bindings = []
    for row, start, end in parsed[1:]:
        cells = dict(zip(header, row));values = {key: cells[column] for key, column in mapping['columns'].items()}
        require(all(re.fullmatch(r'(?:0|[1-9][0-9]{0,9})', values[key]) for key in ('requests', 'exceptions')),
                'Counts must be canonical nonnegative integers, without formulas or missing values')
        record_id = f"{mapping['sourceId']}-L{start}"
        records.append({'sourceId': record_id, 'period': values['period'], 'requests': int(values['requests']),
                        'processingHours': values['processingHours'], 'exceptions': int(values['exceptions'])})
        begin, finish = offsets[start - 1], offsets[end]
        quoted = source[begin:finish]
        bindings.append({'sourceId': record_id, 'firstLine': start, 'lastLine': end, 'byteStart': begin, 'byteEnd': finish,
                         'rawRecordSha256': sha(quoted), 'rawRecord': quoted.decode('utf-8'), 'cells': cells})
    require([record['period'] for record in records] == mapping['periods'], 'Source periods duplicate, reordered or differ from the mapping')
    data = validate_input({'schemaVersion': INPUT_SCHEMA, 'sourceSha256': sha(source), 'periods': records})
    begin, finish = offsets[first - 1], offsets[last]
    return sealed({'schemaVersion': BINDING_SCHEMA, 'status': 'bound', 'mapping': mapping, 'mappingSha256': fingerprint(mapping),
                   'source': {'id': mapping['sourceId'], 'uri': mapping['sourceUri'], 'sha256': sha(source), 'byteLength': len(source),
                              'encoding': 'utf-8-sig' if bom else 'utf-8', 'tableByteStart': begin, 'tableByteEnd': finish,
                              'tableSha256': sha(source[begin:finish]), 'tableText': selected},
                   'records': bindings, 'arithmeticInput': data, 'arithmeticInputSha256': fingerprint(data),
                   'implementationSha256': sha(Path(__file__).read_bytes()), 'sourceBytesMatched': True, 'cellsMatchSelectedCsv': True,
                   'sourceTruthVerified': False, 'populationAlignmentVerified': False, 'qualifiedForRouting': False, 'routingEnabled': False,
                   'limitations': ['Values match the explicitly selected CSV rows; the caller selects columns, periods and the table range.',
                                   'Units and population scope are caller declarations, not established from source bytes.',
                                   'Two aggregate records only; no completeness, per-request deduplication or business-source truth verification.']})


def calculate_source(source: bytes, mapping, *, decimal_places=3):
    binding = bind_records(source, mapping)
    result = calculate(binding['arithmeticInput'], decimal_places=decimal_places)
    return sealed({'schemaVersion': RESULT_SCHEMA, 'status': result['status'], 'binding': binding, 'calculation': result,
                   'sourceTruthVerified': False, 'populationAlignmentVerified': False, 'qualifiedForRouting': False, 'routingEnabled': False})


def source_markdown(report, source: bytes):
    verify_seal(report, RESULT_SCHEMA)
    expected = calculate_source(source, report['binding']['mapping'], decimal_places=report['calculation']['rounding']['decimalPlaces'])
    require(expected == report, 'Source binding or arithmetic result changed')
    binding = report['binding'];mapping = binding['mapping']
    lines = [markdown(report['calculation']).rstrip(), '', '## Привязка к исходной CSV-таблице', '',
             f"Источник: `{mapping['sourceUri']}`. SHA-256 исходных байтов: `{binding['source']['sha256']}`.",
             f"Таблица: строки {mapping['table']['firstLine']}–{mapping['table']['lastLine']} включительно, считая с 1. "
             f"Границы байтов: [{binding['source']['tableByteStart']}, {binding['source']['tableByteEnd']}).", '',
             '| Поле расчёта | Столбец источника |', '|---|---|']
    lines += [f"| {key} | `{mapping['columns'][key]}` |" for key in ('period', 'requests', 'processingHours', 'exceptions')]
    lines += ['', f"Совокупность, заявленная в mapping: `{mapping['population']['id']}`.",
              'Единицы и условия заданы пользователем mapping: число заявок, часы для этих же заявок, не более одного отклонения на заявку; периоды сопоставимы.',
              f"«Отклонения» в таблице расчёта означают выбранный столбец `{mapping['columns']['exceptions']}` в рамках этих заявленных условий.",
              'Проверены совпадение SHA, ячейки и арифметика. Достоверность, полнота и принадлежность чисел одной совокупности не подтверждены.', '',
              '| Запись | Строки источника | SHA-256 исходной строки |', '|---|---|---|']
    lines += [f"| {row['sourceId']} | {row['firstLine']}–{row['lastLine']} | `{row['rawRecordSha256']}` |" for row in binding['records']]
    return '\n'.join(lines) + '\n'
