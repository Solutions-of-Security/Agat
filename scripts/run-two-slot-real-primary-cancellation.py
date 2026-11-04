#!/usr/bin/env python3
"""Run active native cancellation while an actual primary peer remains in flight."""
import importlib.util
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.lib import decision_two_slot_real_primary_cancellation as suite
spec=importlib.util.spec_from_file_location('two_slot_real_primary_launcher',ROOT/'scripts/run-two-slot-native-cancellation.py')
launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
if __name__=='__main__':raise SystemExit(launcher.main(suite=suite))
