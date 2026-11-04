#!/usr/bin/env python3
"""Measure or replay every unlabelled public development input on a pinned MLX checkpoint."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json, fingerprint, parse_json
from scripts.lib.decision_public_mlx_baseline import NAMES, frozen_sources, local_manifest, measure, verify_report
from scripts.lib.decision_public_load import validate_context, historical_context_sources
from scripts.lib.decision_public_sources import pinned_input, private_directory, write_json_new
from scripts.lib.decision_shadow_pilot import require

MAX_REPORT_BYTES = 32 * 1024**2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('measure', 'verify'):
        command = commands.add_parser(name)
        command.add_argument('--candidate', choices=NAMES, required=True)
        for field in ('context-profile', 'manifest', 'output-dir'): command.add_argument('--' + field, type=Path, required=True)
        command.add_argument('--context-file-sha256', required=True)
        command.add_argument('--manifest-file-sha256', required=True)
        if name == 'verify':
            command.add_argument('--report', type=Path, required=True); command.add_argument('--report-file-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        identity = frozen_sources(ROOT); private = (ROOT / 'docs/private').resolve()
        require(args.output_dir.resolve().is_relative_to(private) and args.output_dir.resolve() != private
            and not args.output_dir.exists() and not args.output_dir.is_symlink(), 'Use a new private output directory')
        raw_context = pinned_input(args.context_profile, args.context_file_sha256, 32 * 1024**2)
        context = validate_context(parse_json(raw_context)); historical_context_sources(ROOT, context)
        raw_manifest = pinned_input(args.manifest, args.manifest_file_sha256, 1024**2)
        local = local_manifest(args.candidate, args.manifest)
        if args.command == 'measure':
            report = verify_report(context, measure(context, args.candidate, args.manifest, identity))
            model_calls = report['summary']['nativeInferenceAttempts']
        else:
            report = verify_report(context, parse_json(pinned_input(args.report, args.report_file_sha256, MAX_REPORT_BYTES)))
            require(report['candidate'] == args.candidate and report['sourceIdentity']['sourceFiles'] == identity['sourceFiles'], 'Measured diagnostic sources differ')
            for name, digest in report['sourceIdentity']['sourceFiles'].items():
                raw = subprocess.check_output(['git', 'show', report['sourceIdentity']['sourceCommit'] + ':' + name], cwd=ROOT, timeout=15)
                require(hashlib.sha256(raw).hexdigest() == digest, 'Measured source commit differs')
            model_calls = 0
        require(frozen_sources(ROOT) == identity and local_manifest(args.candidate, args.manifest) == local
            and pinned_input(args.context_profile, args.context_file_sha256, 32 * 1024**2) == raw_context
            and pinned_input(args.manifest, args.manifest_file_sha256, 1024**2) == raw_manifest, 'Source context, implementation or pinned model changed')
        require(len((canonical_json(report) + '\n').encode()) <= MAX_REPORT_BYTES, 'Whole diagnostic exceeds verifier byte bound')
        output = private_directory(ROOT, args.output_dir)
        write_json_new(output / ('report.json' if args.command == 'measure' else 'verified-report.json'), report)
        write_json_new(output / 'execution.json', sealed({'schemaVersion': 'agat.decision.public-support-mlx-execution.v1',
            'command': args.command, 'candidate': args.candidate, **identity, 'modelCalls': model_calls,
            'contextFileSha256': args.context_file_sha256, 'manifestFileSha256': args.manifest_file_sha256,
            'modelArtifactSha256': local[0]['artifactSha256'], 'reportSha256': report['sha256'], 'routingEnabled': False, 'qualification': 'not_assessed'}))
        print(f"status=unlabelled_development_diagnostic cases={report['summary']['caseCount']} modelCalls={model_calls}")
        return int(any(row[key]['reason'] == 'backend_error' for row in report['cases'] for key in ('result', 'reversedResult')))
    except Exception as error:
        print('Cannot run unlabelled MLX diagnostic: ' + type(error).__name__, file=sys.stderr); return 1


if __name__ == '__main__': raise SystemExit(main())
