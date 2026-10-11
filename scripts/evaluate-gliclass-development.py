#!/usr/bin/env python3
"""Download explicitly, measure offline, or verify the complete GLiClass development baseline."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import fingerprint, parse_json
from scripts.lib.decision_gliclass_baseline import (GLiClassBackend, development_dataset, download, frozen_sources,
                                                   measure, verify_local, verify_report)
from scripts.lib.decision_public_sources import pinned_input, private_directory, write_json_new
from scripts.lib.decision_shadow_pilot import require


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    fetch = commands.add_parser('download'); fetch.add_argument('--model-dir', type=Path, required=True)
    for name in ('measure', 'verify'):
        command = commands.add_parser(name)
        command.add_argument('--dataset', type=Path, required=True)
        command.add_argument('--dataset-file-sha256', required=True)
        command.add_argument('--model-dir', type=Path, required=True)
        command.add_argument('--output-dir', type=Path, required=True)
        if name == 'verify':
            command.add_argument('--report', type=Path, required=True)
            command.add_argument('--report-file-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        identity = frozen_sources(ROOT)
        if args.command == 'download':
            private = (ROOT / 'docs/private').resolve()
            require(args.model_dir.resolve().is_relative_to(private) and args.model_dir.resolve() != private,
                    'Download public weights into a new private model directory')
            manifest = download(args.model_dir)
            require(frozen_sources(ROOT) == identity, 'Download implementation changed')
            print('downloaded revision=' + manifest['revision']); return 0
        require(not args.output_dir.exists(), 'Use a new private output directory')
        private = (ROOT / 'docs/private').resolve()
        require(args.output_dir.resolve().is_relative_to(private) and args.output_dir.resolve() != private,
                'Use a new private output directory')
        dataset = development_dataset(parse_json(pinned_input(args.dataset, args.dataset_file_sha256, 4 * 1024 * 1024)))
        manifest = verify_local(args.model_dir)
        if args.command == 'measure':
            backend = GLiClassBackend(args.model_dir)
            report = verify_report(dataset, measure(dataset, backend, identity))
            model_calls = report['summary']['nativeInferenceAttempts']
        else:
            report = verify_report(dataset, parse_json(pinned_input(args.report, args.report_file_sha256, 32 * 1024 * 1024)))
            require(report['sourceIdentity']['sourceFiles'] == identity['sourceFiles'], 'Measured source files differ from current verifier snapshot')
            measured = report['sourceIdentity']['sourceCommit']
            require(len(measured) == 40, 'Invalid measured commit')
            # Require the actual measured commit to be present; no inferred or substituted source identity.
            for name, sha in report['sourceIdentity']['sourceFiles'].items():
                raw = subprocess.check_output(['git', 'show', f'{measured}:{name}'], cwd=ROOT, timeout=15)
                require(hashlib.sha256(raw).hexdigest() == sha, 'Measured committed source differs')
            model_calls = 0
        require(frozen_sources(ROOT) == identity and verify_local(args.model_dir) == manifest
                and fingerprint(parse_json(pinned_input(args.dataset, args.dataset_file_sha256, 4 * 1024 * 1024))) == fingerprint(dataset),
                'Sources, dataset or local weights changed during baseline execution')
        directory = private_directory(ROOT, args.output_dir)
        write_json_new(directory / ('report.json' if args.command == 'measure' else 'verified-report.json'), report)
        write_json_new(directory / 'execution.json', sealed({'schemaVersion': 'agat.decision.gliclass-execution.v1',
            'command': args.command, **identity, 'modelCalls': model_calls, 'reportSha256': report['sha256'],
            'datasetFileSha256': args.dataset_file_sha256, 'modelArtifactSha256': manifest['sha256'],
            'routingEnabled': False, 'qualification': 'not_assessed'}))
        print(f"status=diagnostic_only cases={report['summary']['caseCount']} modelCalls={model_calls} qualification=not_assessed")
        return int(report['summary']['original']['backendErrors'] + report['summary']['reversed']['backendErrors'] > 0)
    except Exception as error:
        print('Cannot run GLiClass baseline: ' + type(error).__name__ + ': ' + str(error)[:200], file=sys.stderr)
        return 1


if __name__ == '__main__': raise SystemExit(main())
