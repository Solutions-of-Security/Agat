#!/usr/bin/env python3
"""Bind complete submitted labels to the whole frozen native option-order history."""
import argparse
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from scripts.lib.decision_public_sources import private_directory, write_json_new
from scripts.lib.decision_review_native_diagnostics import diagnose, verify_diagnostic
from scripts.lib.decision_shadow_pilot import require

SPEC = importlib.util.spec_from_file_location('review_diagnostic_sources', ROOT/'scripts/run-decision-arrival-rate.py')
launcher = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(launcher)
PATHS = ['decision_runtime', 'scripts/lib', 'scripts/diagnose-finalized-decision-development.py',
         'scripts/test/test_decision_review_native_diagnostics.py', 'scripts/run-decision-arrival-rate.py',
         'scripts/run-temporal-real-rag.py', 'scripts/profile-embedding-rag.py']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('bundle', 'evidence-dir', 'context-profile', 'permutation-context', 'output-dir'):
        parser.add_argument('--'+name, type=Path, required=True)
    for name in ('bundle', 'context-profile', 'permutation-context', 'plan', 'result'):
        parser.add_argument('--'+name+'-file-sha256', required=True)
    parser.add_argument('--diagnostic-report', type=Path)
    parser.add_argument('--diagnostic-report-file-sha256')
    args = parser.parse_args(argv)
    try:
        private = (ROOT/'docs/private').resolve()
        require(args.output_dir.resolve().is_relative_to(private) and args.output_dir.resolve() != private
                and not args.output_dir.exists() and not args.output_dir.is_symlink(), 'Use a new private output directory')
        require(bool(args.diagnostic_report) == bool(args.diagnostic_report_file_sha256), 'Supply both saved diagnostic bindings')
        imports = {str(Path(module.__file__).resolve().relative_to(ROOT)) for module in list(sys.modules.values())
                   if getattr(module, '__file__', None) and str(module.__file__).endswith('.py')
                   and Path(module.__file__).resolve().is_relative_to(ROOT)}
        paths = sorted(set(PATHS) | imports)
        identity = launcher.frozen_sources(paths)
        bindings = (args.bundle, args.bundle_file_sha256, args.evidence_dir, args.context_profile, args.permutation_context)
        pins = {'context_sha': args.context_profile_file_sha256, 'permutation_sha': args.permutation_context_file_sha256,
                'plan_sha': args.plan_file_sha256, 'result_sha': args.result_file_sha256}
        report = (verify_diagnostic(ROOT, args.diagnostic_report, args.diagnostic_report_file_sha256, *bindings, **pins)
                  if args.diagnostic_report else diagnose(ROOT, *bindings, **pins))
        require(launcher.frozen_sources(paths) == identity, 'Diagnostic implementation changed')
        receipt = sealed({'schemaVersion': 'agat.decision.finalized-development-native-execution.v1',
            'sourceCommit': identity[0], 'sourceFiles': identity[1], 'reportSha256': report['sha256'],
            'modelCalls': 0, 'routingEnabled': False, 'qualification': 'not_assessed'})
        directory = private_directory(ROOT, args.output_dir)
        write_json_new(directory/('verification.json' if args.diagnostic_report else 'diagnostic.json'), report)
        write_json_new(directory/'execution.json', receipt)
    except Exception as error:
        print('Cannot diagnose finalized development: '+type(error).__name__+': '+str(error)[:200], file=sys.stderr)
        return 1
    print('status='+report['status']+' modelCalls=0 qualification=not_assessed')
    return 0


if __name__ == '__main__': raise SystemExit(main())
