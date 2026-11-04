#!/usr/bin/env python3
"""Run a published worker caller deadline with two actual concurrent worker slots."""
import importlib.util
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from scripts.lib import decision_two_slot_deadline as suite
SPEC = importlib.util.spec_from_file_location('two_slot_native_driver', ROOT/'scripts/run-two-slot-native-cancellation.py')
harness = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(harness)
def main(argv=None): return harness.main(argv, suite=suite, target_flag='deadline-at-original-index')
if __name__ == '__main__': raise SystemExit(main())
