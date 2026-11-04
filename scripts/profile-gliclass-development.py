#!/usr/bin/env python3
"""Profile whole-process resources around an unchanged, full-inventory GLiClass run."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import fingerprint, parse_json
from scripts.lib.decision_gliclass_baseline import development_dataset, verify_local
from scripts.lib.decision_gliclass_resources import frozen_sources, measure, verify_profile
from scripts.lib.decision_public_sources import pinned_input, private_directory, write_json_new
from scripts.lib.decision_shadow_pilot import require


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('measure', 'verify'))
    for name in ('model-dir', 'dataset', 'output-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--dataset-file-sha256', required=True)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--report-file-sha256')
    args = parser.parse_args(argv)
    try:
        require((args.command == 'verify') == bool(args.report) == bool(args.report_file_sha256),
                'Verification requires both report path/SHA; measurement accepts neither')
        private = (ROOT / 'docs/private').resolve()
        require(args.output_dir.resolve().is_relative_to(private) and args.output_dir.resolve() != private
                and not args.output_dir.exists() and not args.output_dir.is_symlink(), 'Use a new private output directory')
        identity = frozen_sources(ROOT)
        data = development_dataset(parse_json(pinned_input(args.dataset, args.dataset_file_sha256, 4 * 1024 * 1024)))
        manifest = verify_local(args.model_dir)
        if args.command == 'measure':
            report = verify_profile(data, measure(data, args.model_dir, identity))
            calls = report['baseline']['summary']['nativeInferenceAttempts']
        else:
            report = verify_profile(data, parse_json(pinned_input(args.report, args.report_file_sha256, 32 * 1024 * 1024)))
            require(report['sourceIdentity']['sourceFiles'] == identity['sourceFiles'], 'Measured resource sources differ from current snapshot')
            for name, sha in report['sourceIdentity']['sourceFiles'].items():
                raw = subprocess.check_output(['git', 'show', report['sourceIdentity']['sourceCommit'] + ':' + name], cwd=ROOT, timeout=15)
                require(hashlib.sha256(raw).hexdigest() == sha, 'Measured resource commit differs')
            calls = 0
        require(frozen_sources(ROOT) == identity and verify_local(args.model_dir) == manifest
                and fingerprint(parse_json(pinned_input(args.dataset, args.dataset_file_sha256, 4 * 1024 * 1024))) == fingerprint(data),
                'Sources, dataset or weights changed during resource profiling')
        output = private_directory(ROOT, args.output_dir)
        write_json_new(output / ('report.json' if args.command == 'measure' else 'verified-report.json'), report)
        write_json_new(output / 'execution.json', sealed({'schemaVersion': 'agat.decision.gliclass-resource-execution.v1',
            'command': args.command, **identity, 'reportSha256': report['sha256'], 'modelCalls': calls,
            'datasetFileSha256': args.dataset_file_sha256, 'modelArtifactSha256': manifest['sha256'],
            'routingEnabled': False, 'qualification': 'not_assessed'}))
        print(f"status=diagnostic_only modelCalls={calls} maxProcessRssBytes={report['resourceSummary']['maxRssBytesAfterWholeScoring']}")
        summary = report['baseline']['summary']
        return int(summary['original']['backendErrors'] + summary['reversed']['backendErrors'] > 0)
    except Exception as error:
        print('Cannot profile GLiClass resources: ' + type(error).__name__ + ': ' + str(error)[:200], file=sys.stderr)
        return 1


if __name__ == '__main__': raise SystemExit(main())
