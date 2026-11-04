#!/usr/bin/env python3
"""Seal a prospective whole-inventory ABBA/BAAB design without model calls."""
import argparse
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from scripts.lib import decision_public_counterbalance as design
from scripts.lib.decision_public_sources import private_directory, write_json_new

SPEC = importlib.util.spec_from_file_location('counterbalanced_source_freeze', ROOT/'scripts/run-decision-arrival-rate.py')
launcher = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(launcher)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('context-profile', 'output-dir'): parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--context-profile-file-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        commit, sources = launcher.frozen_sources(design.SOURCE_PATHS)
        plan = design.create_plan(ROOT, args.context_profile.resolve(), args.context_profile_file_sha256, commit, sources)
        directory = private_directory(ROOT, args.output_dir); write_json_new(directory/'plan.json', plan)
    except Exception as error:
        print('Cannot prepare counterbalanced design: '+type(error).__name__+': '+str(error)[:200], file=sys.stderr); return 1
    print('status=planned modelCalls=0 plannedWorkflows='+str(plan['evidence']['plannedWorkflows'])); return 0


if __name__ == '__main__': raise SystemExit(main())
