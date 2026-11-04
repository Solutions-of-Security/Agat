#!/usr/bin/env python3
"""Verify saved soak evidence and export a public summary without host/process data."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed, verify_seal, write_new
from scripts.lib.decision_soak_summary import public_summary
from scripts.lib.decision_soak_verification import PLAN_SCHEMA, committed_sources, require, verify_run

SOURCES = ['scripts/summarize-decision-soak.py', 'scripts/lib/decision_soak_summary.py']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(args.output.resolve().is_relative_to(ROOT / 'docs') and not args.output.exists(),
            'Use a new summary file under docs')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    archive = subprocess.check_output(['git', 'archive', commit, '--', *SOURCES], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        sources = {item.name: tar.extractfile(item).read() for item in tar if item.isfile()}
    require(set(sources) == set(SOURCES) and all((ROOT / p).read_bytes() == raw for p, raw in sources.items()),
            'Commit summary sources before exporting public evidence')
    plan = verify_seal(json.loads((args.directory / 'plan.json').read_bytes()), PLAN_SCHEMA)
    require(all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == checksum
                for p, checksum in plan['sourceSha256'].items()),
            'Use the measured runtime and verification sources when exporting evidence')
    launcher = json.loads((args.directory / 'launcher.json').read_bytes())
    verification = verify_run(args.directory, plan, launcher, committed_sources(ROOT, plan))
    blocks = [json.loads((args.directory / item['path']).read_bytes()) for item in launcher['observed']['blocks']]
    result = public_summary(plan, launcher, verification, blocks)
    result = sealed({**{k: v for k, v in result.items() if k != 'sha256'}, 'analysisCommit': commit,
                     'analysisSourceSha256': {p: hashlib.sha256(raw).hexdigest() for p, raw in sources.items()}})
    write_new(args.output, result)
    print(f"observed: calls={result['summary']['attempts']} p95_ms={result['summary']['scoredWallMs']['p95']}")


if __name__ == '__main__':
    raise SystemExit(main())
