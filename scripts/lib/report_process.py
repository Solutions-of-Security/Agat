"""Prepare an immutable CSV calculation for Agat's existing approval/artifact steps."""

import hashlib
import json
import re
from pathlib import Path

from decision_runtime.artifacts import sealed, verify_seal
from scripts.lib.report_request_records import MAPPING_SCHEMA as REQUEST_MAPPING_SCHEMA, calculate_requests, request_markdown
from scripts.lib.report_source_records import calculate_source, source_markdown

SCHEMA = 'agat.report.process-bundle.v1'


def require(value, message):
    if not value: raise ValueError(message)


def prepare_process(source: bytes, mapping, name: str, *, decimal_places=3):
    require(isinstance(name, str) and name == name.strip() and 0 < len(name.encode('utf-16-le')) // 2 <= 80
            and not re.search(r'[\x00-\x1f\x7f]', name), 'Process name must contain 1 to 80 characters without control characters')
    require(isinstance(mapping, dict), 'Explicit source mapping required')
    if mapping.get('schemaVersion') == REQUEST_MAPPING_SCHEMA:
        calculation = calculate_requests(source, mapping, decimal_places=decimal_places)
        report = request_markdown(calculation, source)
    else:
        calculation = calculate_source(source, mapping, decimal_places=decimal_places)
        report = source_markdown(calculation, source)
    calculation_json = json.dumps(calculation, ensure_ascii=False, sort_keys=True, indent=2) + '\n'
    payload = {'markdown': report, 'source': source.decode('utf-8'), 'calculationJson': calculation_json}
    # A literal JSON string is used as a template. Escape source-provided opening
    # delimiters so {{ input }} in a CSV preamble cannot execute as a template.
    # json.* substitution is one pass; decoded source text is never templated again.
    template = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(',', ':')).replace('{{', r'\u007b\u007b')
    require('{{' not in template and len(template) <= 100_000, 'Prepared report exceeds the process template limit; source cannot be truncated')
    require(json.loads(template) == payload, 'Literal payload encoding changed the source')
    nodes = []

    def node(id, type, label, config=None):
        nodes.append({'id': id, 'type': type, 'name': label, 'position': {'x': len(nodes) * 260, 'y': 120}, 'config': config or {}})

    node('start', 'start', 'Подготовленный расчёт')
    node('records', 'transform', 'Закреплённые данные отчёта', {'template': template})
    node('report', 'transform', 'Отчёт для проверки', {'template': '{{ json.markdown }}'})
    node('approval', 'approval', 'Решение владельца отчёта', {'approvalMessage':
         'Проверьте CSV, строки, периоды, единицы и совокупность. '
         'Подтверждение сохранит отчёт, CSV и расчётный JSON. '
         'При ошибке отклоните; достоверность источника требует проверки.'})
    node('report-file', 'artifact', 'Сохранить отчёт', {'artifactName': 'source-report.md',
         'artifactMediaType': 'text/markdown; charset=utf-8', 'artifactContent': '{{ lastOutput }}'})
    node('source-records', 'transform', 'Исходные записи и вычисления', {'template': template})
    node('source-file', 'artifact', 'Сохранить исходный CSV', {'artifactName': 'source.csv',
         'artifactMediaType': 'text/csv; charset=utf-8', 'artifactContent': '{{ json.source }}'})
    node('calculation-file', 'artifact', 'Сохранить расчётный JSON', {'artifactName': 'calculation.json',
         'artifactMediaType': 'application/json; charset=utf-8', 'artifactContent': '{{ json.calculationJson }}'})
    node('result', 'transform', 'Итоговый отчёт', {'template': '{{ json.markdown }}'})
    node('end', 'end', 'Отчёт сохранён')
    graph = {'nodes': nodes, 'edges': [{'id': f'edge-{index}', 'source': previous['id'], 'target': following['id'], 'branch': 'default'}
                                     for index, (previous, following) in enumerate(zip(nodes, nodes[1:]), 1)],
             'allowPartialStart': False, 'mcpToolAllowlist': []}
    artifacts = {'source-report.md': report.encode('utf-8'), 'source.csv': source, 'calculation.json': calculation_json.encode('utf-8')}
    return sealed({'schemaVersion': SCHEMA, 'status': 'prepared_requires_review',
                   'source': {'sha256': hashlib.sha256(source).hexdigest(), 'text': source.decode('utf-8')},
                   'mapping': calculation['binding']['mapping'], 'calculation': calculation,
                   'process': {'name': name, 'description': 'Подготовленный расчёт по закреплённому CSV. '
                               'Новые исходные данные требуют новой подготовки; сохранение — после решения владельца отчёта.', 'graph': graph},
                   'artifacts': {name: {'sha256': hashlib.sha256(content).hexdigest(), 'sizeBytes': len(content)} for name, content in artifacts.items()},
                   'implementationSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   'sourceTruthVerified': False, 'populationAlignmentVerified': False, 'qualifiedForRouting': False, 'routingEnabled': False,
                   'limitations': ['Calculation happens at preparation time; run input cannot replace the pinned source or numbers.',
                                   'A published graph snapshot carries the prepared records; changing graph/source requires a new preparation and review.',
                                   'Approval is an operator decision, not an automatic source truth, completeness or population verification.',
                                   'The existing 100000-character template limit applies after literal JSON escaping; no source truncation.']})


def verify_process(bundle):
    verify_seal(bundle, SCHEMA)
    expected = prepare_process(bundle['source']['text'].encode('utf-8'), bundle['mapping'], bundle['process']['name'],
                               decimal_places=bundle['calculation']['calculation']['rounding']['decimalPlaces'])
    require(expected == bundle, 'Prepared process or source calculation changed; prepare again before installation')
    return bundle['process']
