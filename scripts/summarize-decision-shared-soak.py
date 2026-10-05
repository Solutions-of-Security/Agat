#!/usr/bin/env python3
"""Reverify saved shared soak evidence and export a public timing/count allowlist."""
import argparse
import hashlib
import io
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import read_json, sealed, write_new
from scripts.lib.decision_shared_soak_summary import public_summary
from scripts.lib.decision_shared_soak_verification import committed_inputs, require, verify_run

SOURCES = ('scripts/summarize-decision-shared-soak.py', 'scripts/lib/decision_shared_soak_summary.py',
           'scripts/lib/decision_shared_soak_verification.py', 'scripts/lib/decision_baselines.py',
           'decision_runtime/__init__.py', 'decision_runtime/artifacts.py', 'decision_runtime/contracts.py',
           'decision_runtime/evaluation.py', 'decision_runtime/calibration_workflow.py',
           'decision_runtime/calibration.py', 'decision_runtime/engine.py')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    require(args.directory.resolve().is_relative_to((ROOT / 'docs/private').resolve()),
            'Use saved private evidence under docs/private')
    require(args.output.resolve().is_relative_to((ROOT / 'docs').resolve()) and not args.output.exists(),
            'Use a new summary file under docs')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, timeout=5).strip()
    raw = subprocess.check_output(['git', 'archive', commit, '--', *SOURCES], cwd=ROOT, timeout=15)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        sources = {item.name: archive.extractfile(item).read() for item in archive if item.isfile()}
    require(set(sources) == set(SOURCES) and all((ROOT / name).read_bytes() == body for name, body in sources.items()),
            'Commit analysis sources before exporting public evidence')
    plan = read_json(args.directory / 'plan.json')
    dataset, profile = committed_inputs(ROOT, plan)
    # Recompute from all sealed blocks and the exact journal, rather than trust a saved verification label.
    verification = verify_run(args.directory, plan, dataset, profile)
    result = read_json(args.directory / 'result.json')
    blocks = [read_json(args.directory / f'block-{index + 1:03d}.json') for index in range(len(result['blocks']))]
    summary = public_summary(plan, result, verification, blocks)
    summary = sealed({**{key: value for key, value in summary.items() if key != 'sha256'},
                      'analysisCommit': commit,
                      'analysisSourceSha256': {name: hashlib.sha256(body).hexdigest() for name, body in sources.items()}})
    write_new(args.output, summary)
    print(f"observed: calls={summary['counts']['measuredCalls']} measured_ms={summary['measuredMs']}")
    return 0


if __name__ == '__main__': raise SystemExit(main())
