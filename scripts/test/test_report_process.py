import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from decision_runtime.artifacts import sealed, write_new
from scripts.lib.report_process import prepare_process, verify_process
from scripts.test.test_report_source_records import TEXT, mapping
from scripts.test.test_report_request_records import source as request_source, mapping as request_mapping

ROOT = Path(__file__).resolve().parents[2]


class ReportProcessTest(unittest.TestCase):
    def test_request_mapping_uses_deduplicated_calculation_and_rejects_conflicting_input(self):
        source = request_source()
        bundle = prepare_process(source, request_mapping(source), 'Отчёт по заявкам')
        process = verify_process(bundle)
        payload = json.loads(process['graph']['nodes'][1]['config']['template'])
        self.assertEqual(payload['source'].encode(), source)
        self.assertEqual(json.loads(payload['calculationJson'])['binding']['counts'],
                         {'sourceRecords': 6, 'uniqueRequestPeriods': 4, 'duplicateRecords': 2})
        self.assertIn('одинаковых повторов исключено: 2', payload['markdown'])
        self.assertIn('| Среднее время на заявку | 2.000 ч | 1.500 ч | -25.000% |', payload['markdown'])
        changed = source + b'2026-07,001,9,1\n'
        with self.assertRaisesRegex(ValueError, 'Conflicting duplicate'):
            prepare_process(changed, request_mapping(changed), 'Отчёт по заявкам')
        forged = copy.deepcopy(bundle); forged.pop('sha256'); forged['calculation']['binding']['counts']['duplicateRecords'] = 0
        with self.assertRaises(ValueError): verify_process(sealed(forged))

    def test_literal_payload_preserves_csv_bytes_and_template_like_source_text(self):
        source = ('\ufeff' + TEXT.replace('Учебные данные', 'Исходник {{ input }} {{{ json.secret }}}').replace('\n', '\r\n')).encode()
        bundle = prepare_process(source, mapping(source), 'Проверяемый отчёт')
        process = verify_process(bundle)
        payload = process['graph']['nodes'][1]['config']['template']
        self.assertNotIn('{{', payload)
        decoded = json.loads(payload)
        self.assertEqual(decoded['source'].encode(), source)
        for key, artifact in [('source', 'source.csv'), ('markdown', 'source-report.md'), ('calculationJson', 'calculation.json')]:
            content = decoded[key].encode()
            self.assertEqual(bundle['artifacts'][artifact], {'sha256': hashlib.sha256(content).hexdigest(), 'sizeBytes': len(content)})
        self.assertEqual(json.loads(decoded['calculationJson']), bundle['calculation'])

    def test_resealed_changes_to_source_artifacts_or_approval_cannot_be_verified(self):
        source = TEXT.encode();bundle = prepare_process(source, mapping(source), 'Проверяемый отчёт')
        for kind in ['source', 'artifact', 'approval', 'partial', 'calculation']:
            changed = copy.deepcopy(bundle);changed.pop('sha256')
            if kind == 'source': changed['source']['text'] += 'changed'
            elif kind == 'artifact': changed['artifacts']['source.csv']['sha256'] = '0' * 64
            elif kind == 'approval': changed['process']['graph']['nodes'][3]['type'] = 'transform'
            elif kind == 'partial': changed['process']['graph']['allowPartialStart'] = True
            else: changed['calculation']['calculation']['metrics']['meanHoursPerRequest']['first']['display'] = '40'
            with self.subTest(kind=kind), self.assertRaises(ValueError): verify_process(sealed(changed))

    def test_preparation_rejects_oversized_literal_source_instead_of_truncating_it(self):
        source = TEXT.replace('Учебные данные', 'Ж' * 20_000).encode()
        with self.assertRaisesRegex(ValueError, 'template limit'): prepare_process(source, mapping(source), 'Отчёт')
        for name in ['', ' leading', 'x\ny', '\x00', '🧮' * 41]:
            with self.subTest(name=name), self.assertRaises(ValueError): prepare_process(TEXT.encode(), mapping(TEXT.encode()), name)

    def test_cli_emits_valid_process_only_for_matching_source_and_never_overwrites(self):
        with tempfile.TemporaryDirectory(prefix='.report-process-', dir=ROOT / 'docs') as directory:
            p = Path(directory);source = p / 'source.csv';source.write_bytes(TEXT.encode())
            config = p / 'mapping.json';write_new(config, mapping(source.read_bytes()))
            output = p / 'bundle.json';process = p / 'process.json'
            command = [sys.executable, str(ROOT / 'scripts/prepare-source-report-process.py'), '--source', str(source),
                       '--mapping', str(config), '--name', 'Расчёт', '--output', str(output), '--process-json', str(process)]
            first = subprocess.run(command, capture_output=True, timeout=5)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(verify_process(json.loads(output.read_text())), json.loads(process.read_text()))
            previous = output.read_bytes()
            self.assertNotEqual(subprocess.run(command, capture_output=True, timeout=5).returncode, 0)
            self.assertEqual(output.read_bytes(), previous)
            output.unlink();process.unlink();source.write_text(TEXT.replace('400', '401'))
            self.assertNotEqual(subprocess.run(command, capture_output=True, timeout=5).returncode, 0)
            self.assertFalse(output.exists());self.assertFalse(process.exists())


if __name__ == '__main__': unittest.main()
