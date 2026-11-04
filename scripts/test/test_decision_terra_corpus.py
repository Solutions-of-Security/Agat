"""Synthetic parser/CLI contracts; these fixtures are not published human references."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json, fingerprint
from scripts.lib import decision_terra_corpus as corpus

SPEC = importlib.util.spec_from_file_location('terra_cli', ROOT / 'scripts/import-terra-development.py')
cli = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(cli)
sha = lambda raw: hashlib.sha256(raw).hexdigest()


def fixture(rows=None, extra=None, member='TERRa/val.jsonl'):
    rows = rows if rows is not None else [
        {'premise': 'Текст о документе.', 'hypothesis': 'Есть документ.', 'label': 'entailment', 'idx': 0},
        {'premise': 'Текст о согласовании.', 'hypothesis': 'Документ подписан.', 'label': 'not_entailment', 'idx': 1}]
    raw = ''.join(json.dumps(v, ensure_ascii=False) + '\n' for v in rows).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, raw)
        # Bad JSON proves semantic train/test members are not read.
        archive.writestr('TERRa/test.jsonl', b'not JSON and must stay unread')
        archive.writestr('TERRa/train.jsonl', b'not JSON and must stay unread')
        if extra: archive.writestr(extra, b'unsafe')
    packed = stream.getvalue(); license_raw = b'synthetic license fixture; no real corpus claim'
    source = dict(schemaVersion='synthetic-source', archive=dict(url='https://example.invalid/fixture', bytes=len(packed), sha256=sha(packed)),
        development=dict(member=member, bytes=len(raw), sha256=sha(raw), cases=len(rows),
            groups=len({corpus.normalized_premise(v['premise']) for v in rows}),
            labelCounts={label: sum(v['label'] == label for v in rows) for label in corpus.LABELS}),
        license=dict(bytes=len(license_raw), sha256=sha(license_raw)))
    return packed, license_raw, source


class TerraCorpusTests(unittest.TestCase):
    def test_publisher_descriptor_is_closed_and_immutable(self):
        original = corpus.descriptor(ROOT)
        for mutate in (lambda v: v.update(routingEnabled=True), lambda v: v.update(revision='a' * 40),
                lambda v: v['archive'].update(sha256='a' * 64), lambda v: v['development'].update(member='TERRa/test.jsonl'),
                lambda v: v['license'].update(name='Apache-2.0'), lambda v: v.update(references=[])):
            bad = copy.deepcopy(original); mutate(bad)
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp); path = root / corpus.DESCRIPTOR; path.parent.mkdir(parents=True); path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError): corpus.descriptor(root)

    def test_complete_source_labels_never_enter_model_request(self):
        packed, license_raw, source = fixture()
        with patch.object(corpus, 'descriptor', return_value=source):
            value = corpus.build_dataset(packed, license_raw)
            self.assertEqual(corpus.verify_dataset(value, packed, license_raw), value)
            result = corpus.evaluation_dataset(value, packed, license_raw)
        self.assertEqual(value['summary']['publishedReferenceLabels'], 2)
        self.assertEqual(result['cases'][0]['expectedOptionId'], 'entailment')
        self.assertEqual(result['cases'][1]['expectedOptionId'], 'not_entailment')
        for case in result['cases']:
            self.assertEqual(set(case['request']), {'schemaVersion', 'id', 'state', 'question', 'kind', 'options'})
            self.assertEqual(case['labelSource'], 'published-corpus')
        self.assertFalse(value['humanLabelsVerified']); self.assertEqual(value['localIndependentHumanReviews'], 0)
        self.assertFalse(value['summary']['trainOrTestMembersRead']); self.assertEqual(value['summary']['modelCalls'], 0)

    def test_whitespace_case_unicode_grouping_preserves_original_text(self):
        rows = [{'premise': 'ТЕКСТ\tＡ.', 'hypothesis': 'Первое утверждение.', 'label': 'entailment', 'idx': 0},
                {'premise': 'текст A.', 'hypothesis': 'Второе утверждение.', 'label': 'not_entailment', 'idx': 1}]
        packed, license_raw, source = fixture(rows)
        with patch.object(corpus, 'descriptor', return_value=source): value = corpus.build_dataset(packed, license_raw)
        self.assertEqual(value['cases'][0]['groupId'], value['cases'][1]['groupId'])
        self.assertEqual([v['request']['state'] for v in value['cases']], [v['premise'] for v in rows])
        self.assertEqual(value['summary']['normalizedPremiseGroups'], 1)

    def test_source_byte_pins_refuse_modified_archive_license_and_development(self):
        packed, license_raw, source = fixture()
        with patch.object(corpus, 'descriptor', return_value=source):
            for archive, license in ((packed + b'x', license_raw), (packed, license_raw + b'x')):
                with self.assertRaises(ValueError): corpus.build_dataset(archive, license)
        bad = copy.deepcopy(source); bad['development']['sha256'] = 'a' * 64
        with self.assertRaises(ValueError): corpus.development_bytes(packed, bad)

    def test_unsafe_zip_members_and_duplicate_development_are_refused(self):
        for extra in ('../outside', '/absolute', 'TERRa\\outside'):
            packed, _, source = fixture(extra=extra)
            with self.assertRaises(ValueError): corpus.development_bytes(packed, source)
        packed, _, source = fixture(); stream = io.BytesIO(packed)
        with zipfile.ZipFile(stream, 'a') as archive: archive.writestr('TERRa/val.jsonl', b'{}')
        packed = stream.getvalue(); source['archive'] = dict(bytes=len(packed), sha256=sha(packed))
        with self.assertRaises(ValueError): corpus.development_bytes(packed, source)

    def test_wrong_member_size_missing_member_and_symlink_are_refused(self):
        packed, _, source = fixture(); wrong = copy.deepcopy(source); wrong['development']['bytes'] += 1
        with self.assertRaises(ValueError): corpus.development_bytes(packed, wrong)
        wrong = copy.deepcopy(source); wrong['development']['member'] = 'missing.jsonl'
        with self.assertRaises(ValueError): corpus.development_bytes(packed, wrong)
        stream = io.BytesIO(); info = zipfile.ZipInfo('TERRa/val.jsonl'); info.create_system = 3; info.external_attr = 0o120777 << 16
        with zipfile.ZipFile(stream, 'w') as archive: archive.writestr(info, b'{}')
        packed = stream.getvalue(); source['archive'] = dict(bytes=len(packed), sha256=sha(packed)); source['development']['bytes'] = 2
        with self.assertRaises(ValueError): corpus.development_bytes(packed, source)

    def test_duplicate_json_unknown_labels_boolean_duplicate_and_missing_indices(self):
        _, _, source = fixture()
        valid = [{'premise': 'А.', 'hypothesis': 'Б.', 'label': 'entailment', 'idx': 0},
                 {'premise': 'В.', 'hypothesis': 'Г.', 'label': 'not_entailment', 'idx': 1}]
        for key, val in (('label', 'neutral'), ('idx', False), ('idx', 1), ('idx', 2), ('premise', ''), ('hypothesis', 'x' * 1801)):
            bad = copy.deepcopy(valid); bad[0][key] = val
            with self.assertRaises(ValueError): corpus.cases_from_rows('\n'.join(json.dumps(v) for v in bad).encode(), source)
        duplicate = b'{"premise":"A","hypothesis":"B","label":"entailment","idx":0,"idx":0}\n{}'
        with self.assertRaises(ValueError): corpus.cases_from_rows(duplicate, source)
        with self.assertRaises(ValueError): corpus.cases_from_rows(json.dumps(valid[0]).encode(), source)

    def test_conflicting_normalized_duplicate_labels_are_refused(self):
        rows = [{'premise': 'Текст.', 'hypothesis': 'Вывод.', 'label': 'entailment', 'idx': 0},
                {'premise': 'текст.', 'hypothesis': 'вывод.', 'label': 'not_entailment', 'idx': 1}]
        packed, license_raw, source = fixture(rows)
        with patch.object(corpus, 'descriptor', return_value=source), self.assertRaises(ValueError): corpus.build_dataset(packed, license_raw)

    def test_resealed_gold_request_order_group_scope_authority_and_count_mutations_refused(self):
        packed, license_raw, source = fixture()
        with patch.object(corpus, 'descriptor', return_value=source):
            value = corpus.build_dataset(packed, license_raw)
            for mutate in (lambda v: v['cases'][0].update(expectedOptionId='not_entailment'),
                    lambda v: v['cases'][0]['request'].update(state='Изменённый текст.'),
                    lambda v: v['cases'][0].update(groupId='fake-group'), lambda v: v['cases'].reverse(),
                    lambda v: v['cases'][0].update(split='holdout'), lambda v: v['cases'][0].update(labelSource='assistant-reviewed'),
                    lambda v: v.update(humanLabelsVerified=True), lambda v: v.update(localIndependentHumanReviews=False),
                    lambda v: v.update(routingEnabled=True), lambda v: v['summary'].update(publishedReferenceLabels=1),
                    lambda v: v.update(extraAuthority=False)):
                bad = copy.deepcopy(value); mutate(bad); bad = sealed({k: v for k, v in bad.items() if k != 'sha256'})
                with self.assertRaises(ValueError): corpus.evaluation_dataset(bad, packed, license_raw)

    def test_actual_isolated_cli_help_imports_no_model_packages(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in corpus.SOURCE_PATHS:
                incoming = ROOT / name; target = root / name; target.parent.mkdir(parents=True, exist_ok=True)
                if incoming.is_dir(): shutil.copytree(incoming, target, ignore=shutil.ignore_patterns('__pycache__'))
                else: shutil.copy2(incoming, target)
            program = "import runpy,sys;sys.argv=[sys.argv[1],'--help'];\ntry:runpy.run_path(sys.argv[0],run_name='__main__')\nexcept SystemExit as e:assert e.code==0\nassert not {'mlx','mlx_lm','torch','transformers','numpy','gliclass'} & sys.modules.keys()"
            value = subprocess.run([sys.executable, '-I', '-B', '-c', program, str(root / 'scripts/import-terra-development.py')],
                cwd=root, capture_output=True, text=True, timeout=20)
            self.assertEqual(value.returncode, 0, value.stderr)

    def test_cli_positive_replay_and_wrong_byte_pin_leave_no_partial_output(self):
        packed, license_raw, source = fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); private = root / 'docs/private'; private.mkdir(parents=True)
            archive = private / 'TERRa.zip'; archive.write_bytes(packed); license = private / 'LICENSE'; license.write_bytes(license_raw)
            args = ['--archive', str(archive), '--archive-file-sha256', sha(packed), '--license', str(license), '--license-file-sha256', sha(license_raw)]
            with patch.object(cli, 'ROOT', root), patch.object(cli, 'frozen_sources', return_value={'sourceCommit': 'a' * 40, 'sourceFiles': {'synthetic-fixture': 'b' * 64}}), \
                    patch.object(cli, 'descriptor', return_value=source), patch.object(corpus, 'descriptor', return_value=source):
                out = private / 'imported'; self.assertEqual(cli.main(['import', *args, '--output-dir', str(out)]), 0)
                raw = (out / 'dataset.json').read_bytes(); replay = private / 'verified'
                self.assertEqual(cli.main(['verify', *args, '--dataset', str(out / 'dataset.json'), '--dataset-file-sha256', sha(raw), '--output-dir', str(replay)]), 0)
                self.assertEqual((replay / 'verified-dataset.json').read_bytes(), raw)
                self.assertEqual(json.loads((replay / 'execution.json').read_bytes())['modelCalls'], 0)
                self.assertEqual(out.stat().st_mode & 0o777, 0o700); self.assertEqual((out / 'dataset.json').stat().st_mode & 0o777, 0o600)
                bad_args = list(args); bad_args[3] = '0' * 64; denied = private / 'wrong-pin'
                self.assertEqual(cli.main(['import', *bad_args, '--output-dir', str(denied)]), 1); self.assertFalse(denied.exists())

    def test_acquisition_is_explicit_pinned_bounded_and_checks_both_files(self):
        packed, license_raw, source = fixture(); objects = [io.BytesIO(packed), io.BytesIO(license_raw)]
        source['archive']['url'] = 'https://example.invalid/source'; source['license']['url'] = 'https://example.invalid/license'
        with patch.object(corpus, 'descriptor', return_value=source), patch.object(corpus.urllib.request, 'urlopen', side_effect=objects) as fetch:
            result = corpus.acquire(); self.assertEqual(result, {'TERRa.zip': packed, 'LICENSE': license_raw}); self.assertEqual(fetch.call_count, 2)
        with patch.object(corpus, 'descriptor', return_value=source), patch.object(corpus.urllib.request, 'urlopen', return_value=io.BytesIO(packed + b'x')):
            with self.assertRaises(ValueError): corpus.acquire()

    def test_cli_byte_bound_includes_canonical_final_newline(self):
        packed, license_raw, source = fixture()
        limit = 4 * 1024**2
        value = {'fill': 'a' * (limit - len(canonical_json({'fill': ''}).encode()))}
        self.assertEqual(len(canonical_json(value).encode()), limit)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); output = root / 'docs/private/too-large'
            with patch.object(cli, 'ROOT', root), patch.object(cli, 'frozen_sources', return_value={}), \
                    patch.object(cli, 'descriptor', return_value=source), patch.object(cli, 'pinned_sources', return_value=(packed, license_raw)), \
                    patch.object(cli, 'build_dataset', return_value=value), patch.object(cli, 'verify_dataset', return_value=value):
                args = ['import', '--archive', 'unused.zip', '--archive-file-sha256', sha(packed), '--license', 'unused-license',
                    '--license-file-sha256', sha(license_raw), '--output-dir', str(output)]
                self.assertEqual(cli.main(args), 1); self.assertFalse(output.exists())


if __name__ == '__main__': unittest.main()
