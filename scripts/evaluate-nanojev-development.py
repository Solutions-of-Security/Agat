#!/usr/bin/env python3
"""Explicitly download, measure offline, or verify a pinned full-inventory NanoJev reference."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json, fingerprint, parse_json
from scripts.lib.decision_nanojev_baseline import development_dataset, download, frozen_sources, measure, verify_local, verify_report, weights_header
from scripts.lib.decision_public_sources import pinned_input, private_directory, write_json_new
from scripts.lib.decision_shadow_pilot import require

MAX_REPORT_BYTES = 32 * 1024**2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    fetch = commands.add_parser('download')
    fetch.add_argument('--model-dir', type=Path, required=True)
    for name in ('measure', 'verify'):
        command = commands.add_parser(name)
        for field in ('dataset', 'model-dir', 'output-dir'):
            command.add_argument('--' + field, type=Path, required=True)
        command.add_argument('--dataset-file-sha256', required=True)
        if name == 'verify':
            command.add_argument('--report', type=Path, required=True)
            command.add_argument('--report-file-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        identity = frozen_sources(ROOT)
        private = (ROOT / 'docs/private').resolve()
        if args.command == 'download':
            require(args.model_dir.resolve().is_relative_to(private) and args.model_dir.resolve() != private, 'Download into a new private local directory')
            manifest = download(args.model_dir)
            layout = weights_header(args.model_dir / 'best.safetensors')
            require(frozen_sources(ROOT) == identity, 'Native download implementation changed')
            print(f"downloaded revision={manifest['revision']} tensors={layout['tensorCount']} modelCalls=0")
            return 0
        require(args.output_dir.resolve().is_relative_to(private) and args.output_dir.resolve() != private
            and not args.output_dir.exists() and not args.output_dir.is_symlink(), 'Use a new private output directory')
        dataset = development_dataset(parse_json(pinned_input(args.dataset, args.dataset_file_sha256, 4 * 1024**2)))
        manifest = verify_local(args.model_dir)
        layout = weights_header(args.model_dir / 'best.safetensors')
        if args.command == 'measure':
            report = verify_report(dataset, measure(dataset, args.model_dir, identity))
            model_calls = report['summary']['nativeInferenceAttempts']
        else:
            report = verify_report(dataset, parse_json(pinned_input(args.report, args.report_file_sha256, MAX_REPORT_BYTES)))
            require(report['sourceIdentity']['sourceFiles'] == identity['sourceFiles'], 'Measured native sources differ from current verifier')
            for name, digest in report['sourceIdentity']['sourceFiles'].items():
                raw = subprocess.check_output(['git', 'show', report['sourceIdentity']['sourceCommit'] + ':' + name], cwd=ROOT, timeout=15)
                require(hashlib.sha256(raw).hexdigest() == digest, 'Measured native commit differs')
            model_calls = 0
        require(canonical_json(report['evaluation']['model']['weightsLayout']) == canonical_json(layout), 'Native report tensor layout differs from local checkpoint')
        require(frozen_sources(ROOT) == identity and verify_local(args.model_dir) == manifest
            and fingerprint(parse_json(pinned_input(args.dataset, args.dataset_file_sha256, 4 * 1024**2))) == fingerprint(dataset),
            'Native sources, dataset or artifacts changed')
        require(len((canonical_json(report) + '\n').encode('utf-8')) <= MAX_REPORT_BYTES, 'Complete report exceeds verifier byte bound')
        output = private_directory(ROOT, args.output_dir)
        write_json_new(output / ('report.json' if args.command == 'measure' else 'verified-report.json'), report)
        write_json_new(output / 'execution.json', sealed({'schemaVersion': 'agat.decision.nanojev-execution.v1', 'command': args.command,
            **identity, 'modelCalls': model_calls, 'datasetFileSha256': args.dataset_file_sha256,
            'modelArtifactSha256': manifest['sha256'], 'reportSha256': report['sha256'], 'routingEnabled': False, 'qualification': 'not_assessed'}))
        print(f"status=diagnostic_only cases={report['summary']['caseCount']} modelCalls={model_calls} maxProcessRssBytes={report['resourceSummary']['maxRssBytesAfterWholeScoring']}")
        return int(report['summary']['original']['backendErrors'] + report['summary']['reversed']['backendErrors'] > 0)
    except Exception as error:
        print('Cannot run NanoJev reference: ' + type(error).__name__ + ': ' + str(error)[:200], file=sys.stderr)
        return 1


if __name__ == '__main__': raise SystemExit(main())
