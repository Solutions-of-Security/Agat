#!/usr/bin/env python3
"""Run the original public inventory in real-primary/control pairs on two worker slots."""
import importlib.util
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from scripts.lib import decision_public_paired_real_primary as suite
SPEC = importlib.util.spec_from_file_location('paired_real_primary_harness', ROOT/'scripts/run-public-support-real-primary.py')
harness = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(harness)
def main(argv=None): return harness.main(argv, suite=suite)
if __name__ == '__main__': raise SystemExit(main())
