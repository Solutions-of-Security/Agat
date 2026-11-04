#!/usr/bin/env python3
"""Measure or replay a pinned native candidate on every original Russian DaNetQA development reference."""
import argparse
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json, parse_json
from scripts.lib.decision_public_sources import pinned_input, private_directory, write_json_new
from scripts.lib import decision_gliclass_baseline as gl, decision_nanojev_baseline as nano, decision_public_mlx_baseline as mlx
from scripts.lib.decision_danetqa_corpus import pinned_sources, verify_dataset
from scripts.lib.decision_danetqa_baseline import NAMES, SOURCE_PATHS, measure, verify_report
from scripts.lib.decision_shadow_pilot import require

MAX_REPORT_BYTES = 32 * 1024**2


def frozen_sources(root):
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True, timeout=10).strip()
    raw = subprocess.check_output(['git', 'archive', commit, '--', *SOURCE_PATHS], cwd=root, timeout=30)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        sources = {item.name: archive.extractfile(item).read() for item in archive if item.isfile()}
    require(all(path in sources or any(name.startswith(path + '/') for name in sources) for path in SOURCE_PATHS)
        and all((root / name).read_bytes() == raw for name, raw in sources.items()), 'Commit complete external baseline sources before measuring')
    require(not subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '--', *SOURCE_PATHS], cwd=root, timeout=10),
        'Untracked external baseline implementation')
    return {'sourceCommit': commit, 'sourceFiles': {name: hashlib.sha256(raw).hexdigest() for name, raw in sources.items()}}


def model_input(args):
    if args.candidate in NAMES[:2]:
        require(args.model_dir is not None and args.manifest is None and args.manifest_file_sha256 is None, 'Use a CPU model directory')
        return args.model_dir, (gl if args.candidate == 'gliclass' else nano).verify_local(args.model_dir), None
    require(args.manifest is not None and args.model_dir is None and args.manifest_file_sha256 is not None, 'Use an explicitly byte-pinned MLX manifest')
    raw = pinned_input(args.manifest, args.manifest_file_sha256, 1024**2)
    return args.manifest, mlx.local_manifest(args.candidate, args.manifest), raw


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('measure', 'verify'):
        command = commands.add_parser(name); command.add_argument('--candidate', choices=NAMES, required=True)
        for field in ('dataset', 'archive', 'license', 'output-dir'): command.add_argument('--' + field, type=Path, required=True)
        for field in ('dataset', 'archive', 'license'): command.add_argument('--' + field + '-file-sha256', required=True)
        models = command.add_mutually_exclusive_group(required=True)
        models.add_argument('--model-dir', type=Path); models.add_argument('--manifest', type=Path)
        command.add_argument('--manifest-file-sha256')
        if name == 'verify':
            command.add_argument('--report', type=Path, required=True); command.add_argument('--report-file-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        private = (ROOT / 'docs/private').resolve()
        require(private.is_relative_to(ROOT.resolve()) and args.output_dir.resolve().is_relative_to(private)
            and args.output_dir.resolve() != private and not args.output_dir.exists() and not args.output_dir.is_symlink(),
            'Use a new private output directory')
        identity = frozen_sources(ROOT)
        archive_raw, license_raw = pinned_sources(args.archive, args.archive_file_sha256, args.license, args.license_file_sha256, ROOT)
        dataset_raw = pinned_input(args.dataset, args.dataset_file_sha256, 4 * 1024**2)
        dataset = verify_dataset(parse_json(dataset_raw), archive_raw, license_raw, ROOT)
        model_path, local, manifest_raw = model_input(args)
        if args.command == 'measure':
            report = measure(dataset, archive_raw, license_raw, args.candidate, model_path, identity)
            model_calls = report['summary']['nativeInferenceAttempts']
        else:
            report = verify_report(dataset, archive_raw, license_raw,
                parse_json(pinned_input(args.report, args.report_file_sha256, MAX_REPORT_BYTES)))
            require(report['candidate'] == args.candidate and report['sourceIdentity']['sourceFiles'] == identity['sourceFiles'], 'Measured candidate or sources differ')
            for name, digest in report['sourceIdentity']['sourceFiles'].items():
                raw = subprocess.check_output(['git', 'show', report['sourceIdentity']['sourceCommit'] + ':' + name], cwd=ROOT, timeout=15)
                require(hashlib.sha256(raw).hexdigest() == digest, 'Measured source commit differs')
            model_calls = 0
        require(frozen_sources(ROOT) == identity and model_input(args) == (model_path, local, manifest_raw)
            and pinned_sources(args.archive, args.archive_file_sha256, args.license, args.license_file_sha256, ROOT) == (archive_raw, license_raw)
            and pinned_input(args.dataset, args.dataset_file_sha256, 4 * 1024**2) == dataset_raw, 'Sources, original references or pinned model changed')
        require(len((canonical_json(report) + '\n').encode()) <= MAX_REPORT_BYTES, 'Complete external report exceeds publication byte bound')
        output = private_directory(ROOT, args.output_dir)
        write_json_new(output / ('report.json' if args.command == 'measure' else 'verified-report.json'), report)
        write_json_new(output / 'execution.json', sealed(dict(schemaVersion='agat.decision.danetqa-native-execution.v1',
            command=args.command, candidate=args.candidate, **identity, modelCalls=model_calls,
            datasetFileSha256=args.dataset_file_sha256, archiveFileSha256=args.archive_file_sha256, licenseFileSha256=args.license_file_sha256,
            modelArtifactSha256=report['model']['artifactSha256'], reportSha256=report['sha256'],
            classificationAccuracyMeasured=True, localIndependentHumanReviews=0, routingEnabled=False, qualification='not_assessed')))
        print(f"status=external_development_diagnostic cases={report['summary']['caseCount']} modelCalls={model_calls} qualification=not_assessed")
        return int(any(row[key]['reason'] == 'backend_error' for row in report['evaluation']['cases'] for key in ('result', 'reversedResult')))
    except Exception as error:
        print('Cannot evaluate external DaNetQA: ' + type(error).__name__ + ': ' + str(error)[:200], file=sys.stderr); return 1


if __name__ == '__main__': raise SystemExit(main())
