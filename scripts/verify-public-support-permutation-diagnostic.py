#!/usr/bin/env python3
"""Replay frozen native option-order receipts and decompose observed outcomes offline."""
import argparse
import importlib.util
import json
from pathlib import Path
import platform
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import fingerprint,parse_json
from scripts.lib.decision_public_permutation_diagnostic import verify
from scripts.lib.decision_public_outcome_sensitivity import decompose
from scripts.lib.decision_public_sources import pinned_input,private_directory,write_json_new
from scripts.lib.decision_shadow_pilot import require

SPEC=importlib.util.spec_from_file_location('permutation_verifier_source_freezer',ROOT/'scripts/run-decision-arrival-rate.py')
launcher=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(launcher)
PATHS=['decision_runtime','scripts/lib','scripts/verify-public-support-permutation-diagnostic.py',
       'scripts/test/test_decision_public_option_diagnostic_replay.py','scripts/run-decision-arrival-rate.py',
       'scripts/run-temporal-real-rag.py','scripts/profile-embedding-rag.py']


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('evidence-dir','context-profile','permutation-context','output-dir'):parser.add_argument('--'+key,type=Path,required=True)
    for key in ('context-profile-file-sha256','permutation-context-file-sha256','plan-file-sha256','result-file-sha256'):parser.add_argument('--'+key,required=True)
    args=parser.parse_args(argv)
    try:
        private=(ROOT/'docs/private').resolve()
        require(args.output_dir.resolve().is_relative_to(private) and args.output_dir.resolve()!=private
                and not args.output_dir.exists() and not args.output_dir.is_symlink(),'Use a new private replay directory')
        imports={str(Path(module.__file__).resolve().relative_to(ROOT)) for module in list(sys.modules.values())
                 if getattr(module,'__file__',None) and str(module.__file__).endswith('.py') and Path(module.__file__).resolve().is_relative_to(ROOT)}
        paths=sorted(set(PATHS)|imports);identity=launcher.frozen_sources(paths)
        pins={'context_sha':args.context_profile_file_sha256,'permutation_sha':args.permutation_context_file_sha256,
              'plan_sha':args.plan_file_sha256,'result_sha':args.result_file_sha256}
        report=verify(ROOT,args.evidence_dir,args.context_profile,args.permutation_context,**pins)
        plan=parse_json(pinned_input(args.evidence_dir/'plan.json',pins['plan_sha'],32*1024*1024))
        result_raw=pinned_input(args.evidence_dir/'result.json',pins['result_sha'],64*1024*1024);result=parse_json(result_raw)
        sensitivity=decompose(result['phase'],plan['inputs'],plan['profile'])
        require(fingerprint(verify(ROOT,args.evidence_dir,args.context_profile,args.permutation_context,**pins))==fingerprint(report),
                'Evidence changed while outcomes were decomposed')
        require(pinned_input(args.evidence_dir/'result.json',pins['result_sha'],64*1024*1024)==result_raw
                and launcher.frozen_sources(paths)==identity,'Result or verifier sources changed')
        report=sealed({key:value for key,value in report.items() if key!='sha256'}|{'outcomeSensitivity':sensitivity,
            'verifierCommit':identity[0],'verifierSources':identity[1],
            'verifierRuntime':{'python':platform.python_version(),'system':platform.system()}})
        directory=private_directory(ROOT,args.output_dir);write_json_new(directory/'verification.json',report)
        print(json.dumps({'status':report['status'],'physicalHttpAccounting':report['physicalHttpAccounting'],'outcomeSensitivity':sensitivity['summary']}));return 0
    except Exception as error:
        print('Cannot replay option-order diagnostic: '+type(error).__name__,file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
