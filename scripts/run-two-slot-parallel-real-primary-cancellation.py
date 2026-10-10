#!/usr/bin/env python3
"""Run a two-worker cancellation with pinned two-slot primary and owned recovery."""
import importlib.util
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.lib import decision_two_slot_parallel_real_primary_cancellation as suite
spec=importlib.util.spec_from_file_location('two_slot_parallel_primary',ROOT/'scripts/run-two-slot-native-cancellation.py')
cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
if __name__=='__main__':raise SystemExit(cli.main(suite=suite))
