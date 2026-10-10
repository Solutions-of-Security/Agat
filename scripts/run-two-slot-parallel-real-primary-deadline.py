#!/usr/bin/env python3
"""Run an owned two-slot worker deadline with two pinned actual primary slots and recovery."""
import importlib.util
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.lib import decision_two_slot_parallel_real_primary_deadline as suite
spec=importlib.util.spec_from_file_location('two_slot_parallel_real_primary_deadline',ROOT/'scripts/run-two-slot-native-cancellation.py')
cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
if __name__=='__main__':raise SystemExit(cli.main(suite=suite,target_flag='deadline-at-original-index'))
