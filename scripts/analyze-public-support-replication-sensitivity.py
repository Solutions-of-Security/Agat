#!/usr/bin/env python3
"""Describe sensitivity to corpus groups, paired blocks, periods and outcomes."""
import argparse
import importlib.util
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.lib import decision_public_replication_sensitivity as diagnostic
from scripts.lib.decision_public_sources import private_directory, write_json_new

SPEC=importlib.util.spec_from_file_location('replication_sensitivity_source_launcher',ROOT/'scripts/run-decision-arrival-rate.py')
launcher=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(launcher)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('context-profile','evidence-dir','output-dir'):parser.add_argument('--'+name,type=Path,required=True)
    for name in ('context-profile-file-sha256','plan-file-sha256','result-file-sha256'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args(argv)
    try:
        inputs={'context':{'path':args.context_profile.resolve().relative_to(ROOT).as_posix(),'sha256':args.context_profile_file_sha256},
            'replicated':{'evidenceDir':args.evidence_dir.resolve().relative_to(ROOT).as_posix(),
                'planFileSha256':args.plan_file_sha256,'resultFileSha256':args.result_file_sha256}}
        commit,source_files=launcher.frozen_sources(diagnostic.SOURCE_PATHS)
        value=diagnostic.create_analysis(ROOT,inputs,commit,source_files)
        if launcher.frozen_sources(diagnostic.SOURCE_PATHS) != (commit,source_files):raise ValueError('Analysis source drift')
        directory=private_directory(ROOT,args.output_dir);write_json_new(directory/'analysis.json',value)
    except Exception as error:
        print('Cannot analyze replication sensitivity: '+type(error).__name__+': '+str(error)[:200],file=sys.stderr);return 1
    print('status=observed newModelCalls=0');return 0


if __name__ == '__main__':raise SystemExit(main())
