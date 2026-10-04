#!/usr/bin/env python3
"""Run a committed, bounded paired endurance experiment; model services are caller-owned."""
import argparse
import hashlib
import json
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import read_json, sealed, write_new
from decision_runtime.contracts import canonical_json, fingerprint
from decision_runtime.evaluation import load_dataset
from scripts.lib.decision_baselines import LoopbackJson, OllamaBaseline
from scripts.lib.decision_performance import profile_from_health
from scripts.lib.decision_shared_soak import shared_soak, validate_plan

FILES = ('scripts/benchmark-decision-shared-soak.py', 'scripts/lib/decision_shared_soak.py',
         'scripts/benchmark-decision-shared-load.py', 'scripts/lib/decision_shared_load.py',
         'scripts/lib/decision_baselines.py', 'scripts/lib/decision_performance.py', 'workers/local_decisions.py',
         'decision_runtime/artifacts.py', 'decision_runtime/contracts.py', 'decision_runtime/evaluation.py',
         'scripts/verify-decision-shared-soak.py', 'scripts/lib/decision_shared_soak_verification.py')


def source_identity(dataset, profile):
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, timeout=5).strip()
    names = list(FILES)
    for path in (dataset, profile):
        try: name = path.resolve().relative_to(ROOT.resolve()).as_posix()
        except ValueError: raise ValueError('Commit experiment inputs inside this repository') from None
        if not name.startswith('docs/') or name.startswith('docs/private/'):
            raise ValueError('Use committed public experiment inputs under docs')
        names.append(name)
    files = {}
    for name in names:
        raw = (ROOT / name).read_bytes()
        historical = subprocess.check_output(['git', 'show', f'{commit}:{name}'], cwd=ROOT, timeout=5)
        if raw != historical: raise ValueError('Commit harness and inputs before measurement')
        files[name] = hashlib.sha256(raw).hexdigest()
    return commit, files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('decision-url', 'primary-url', 'expected-primary-digest'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--primary-model', default='qwen3:8b')
    for name in ('dataset', 'profile', 'output-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--duration-s', type=int, default=7200)
    parser.add_argument('--max-blocks', type=int, default=128)
    args = parser.parse_args(argv)
    allowed = (ROOT / 'docs/private').resolve()
    directory = args.output_dir.absolute()
    if (not directory.resolve().is_relative_to(allowed) or directory.exists() or directory.is_symlink()):
        parser.error('Use a new output directory under docs/private')
    dataset = load_dataset(args.dataset)
    validate_plan(dataset, args.duration_s, args.max_blocks)
    profile = read_json(args.profile)
    profile_from_health({'status': 'ready', 'mode': 'shadow', 'profileJson': canonical_json(profile),
                         'profileSha256': fingerprint(profile)})
    commit, sources = source_identity(args.dataset, args.profile)
    # Validate both loopback transports before allocating evidence or contacting either service.
    LoopbackJson(args.decision_url, 5);LoopbackJson(args.primary_url, 30)
    directory.mkdir(parents=True, mode=0o700)
    plan = sealed({'schemaVersion': 'agat.decision.shared-soak-plan.v1', 'implementationCommit': commit,
                   'createdAt': datetime.now(timezone.utc).isoformat(), 'sourceFiles': sources,
                   'profileSha256': fingerprint(profile), 'datasetSha256': dataset['sha256'],
                   'datasetPath': args.dataset.resolve().relative_to(ROOT.resolve()).as_posix(),
                   'profilePath': args.profile.resolve().relative_to(ROOT.resolve()).as_posix(),
                   'durationSeconds': args.duration_s, 'maxBlocks': args.max_blocks,
                   'primaryModel': args.primary_model, 'primaryDigest': args.expected_primary_digest,
                   'qualification': 'not_assessed', 'routingEnabled': False})
    write_new(directory / 'plan.json', plan);(directory / 'plan.json').chmod(0o600)
    flag = {'stop': False}
    def stop(*_): flag['stop'] = True
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    result = None;failure = None
    try:
        with (directory / 'pairs.jsonl').open('x') as journal:
            (directory / 'pairs.jsonl').chmod(0o600)
            def record(event):
                journal.write(json.dumps(event, sort_keys=True, separators=(',', ':')) + '\n');journal.flush()
            def primary():
                return OllamaBaseline(LoopbackJson(args.primary_url, 30), args.primary_model, args.expected_primary_digest)
            result = shared_soak(dataset, args.decision_url, primary, directory, profile,
                                 duration_s=args.duration_s, max_blocks=args.max_blocks,
                                 journal=record, cancel_requested=lambda: flag['stop'],
                                 progress=lambda index, block: print(f"block={index + 1} status={block['status']} reason={block['stoppedReason']}", flush=True))
        if any(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest for name, digest in sources.items()):
            failure = 'SourceChanged'
        write_new(directory / 'result.json', result);(directory / 'result.json').chmod(0o600)
    except Exception as error:
        failure = type(error).__name__
    finally:
        for sig, handler in previous.items(): signal.signal(sig, handler)
    observed = result is not None and result['status'] == 'observed' and failure is None
    launcher = sealed({'schemaVersion': 'agat.decision.shared-soak-launcher.v1',
                       'status': 'observed' if observed else 'failed', 'qualification': 'not_assessed',
                       'routingEnabled': False, 'implementationCommit': commit, 'planSha256': plan['sha256'],
                       'failureType': failure, 'resultSha256': result['sha256'] if result is not None else None,
                       'journalSha256': hashlib.sha256((directory / 'pairs.jsonl').read_bytes()).hexdigest()
                           if (directory / 'pairs.jsonl').exists() else None,
                       'serviceOwnership': 'caller', 'servicesStoppedOrRestarted': False})
    write_new(directory / 'launcher.json', launcher);(directory / 'launcher.json').chmod(0o600)
    print(f"{launcher['status']}: {directory}", flush=True)
    return int(not observed)


if __name__ == '__main__': raise SystemExit(main())
