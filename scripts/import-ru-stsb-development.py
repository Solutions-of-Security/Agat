#!/usr/bin/env python3
"""Explicitly acquire, import or replay the complete pinned Russian Russian STS development corpus."""
import argparse
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json, parse_json
from scripts.lib.decision_public_sources import pinned_input, private_directory, write_json_new, write_raw_new
from scripts.lib.decision_rustsb_corpus import (SOURCE_PATHS, acquire, build_dataset, descriptor,
    pinned_sources, verify_dataset)
from scripts.lib.decision_shadow_pilot import require


def frozen_sources(root):
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True, timeout=10).strip()
    raw = subprocess.check_output(['git', 'archive', commit, '--', *SOURCE_PATHS], cwd=root, timeout=30)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        sources = {item.name: archive.extractfile(item).read() for item in archive if item.isfile()}
    require(all(path in sources or any(name.startswith(path + '/') for name in sources) for path in SOURCE_PATHS)
        and all((root / name).read_bytes() == raw for name, raw in sources.items()), 'Commit complete corpus sources before importing')
    require(not subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '--', *SOURCE_PATHS], cwd=root, timeout=10),
        'Untracked corpus implementation')
    return {'sourceCommit': commit, 'sourceFiles': {name: hashlib.sha256(raw).hexdigest() for name, raw in sources.items()}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    fetch = commands.add_parser('acquire'); fetch.add_argument('--output-dir', type=Path, required=True)
    for name in ('import', 'verify'):
        command = commands.add_parser(name)
        command.add_argument('--development', type=Path, required=True)
        command.add_argument('--declaration', type=Path, required=True)
        command.add_argument('--development-file-sha256', required=True)
        command.add_argument('--declaration-file-sha256', required=True)
        command.add_argument('--license', type=Path, required=True)
        command.add_argument('--license-file-sha256', required=True)
        command.add_argument('--output-dir', type=Path, required=True)
        if name == 'verify':
            command.add_argument('--dataset', type=Path, required=True)
            command.add_argument('--dataset-file-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        private = (ROOT / 'docs/private').resolve()
        require(private.is_relative_to(ROOT.resolve()) and args.output_dir.resolve().is_relative_to(private)
            and args.output_dir.resolve() != private and not args.output_dir.exists() and not args.output_dir.is_symlink(),
            'Use a new private output directory')
        identity = frozen_sources(ROOT); source = descriptor(ROOT)
        if args.command == 'acquire':
            payload = acquire(ROOT)
        else:
            development_raw, declaration_raw, license_raw = pinned_sources(args.development, args.development_file_sha256,
                args.declaration, args.declaration_file_sha256, args.license, args.license_file_sha256, ROOT)
            if args.command == 'import':
                dataset = verify_dataset(build_dataset(development_raw, declaration_raw, license_raw, ROOT), development_raw, declaration_raw, license_raw, ROOT)
            else:
                dataset = verify_dataset(parse_json(pinned_input(args.dataset, args.dataset_file_sha256, 4 * 1024**2)),
                    development_raw, declaration_raw, license_raw, ROOT)
            encoded = (canonical_json(dataset) + '\n').encode()
            require(len(encoded) <= 4 * 1024**2, 'Complete dataset exceeds publication byte bound')
            payload = {'dataset.json' if args.command == 'import' else 'verified-dataset.json': encoded}
            require(pinned_sources(args.development, args.development_file_sha256, args.declaration, args.declaration_file_sha256,
                args.license, args.license_file_sha256, ROOT) == (development_raw, declaration_raw, license_raw), 'Original source bytes changed')
            if args.command == 'verify':
                require(parse_json(pinned_input(args.dataset, args.dataset_file_sha256, 4 * 1024**2)) == dataset,
                    'Input dataset changed')
        require(frozen_sources(ROOT) == identity and descriptor(ROOT) == source, 'Committed corpus implementation changed')
        execution = sealed(dict(schemaVersion='agat.decision.ru-stsb-import-execution.v1', command=args.command,
            **identity, sourceDescriptorSha256=hashlib.sha256(canonical_json(source).encode()).hexdigest(),
            outputFiles={name: {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in payload.items()},
            modelCalls=0, trainOrTestRead=False, localIndependentHumanReviews=0,
            routingEnabled=False, qualification='not_assessed'))
        directory = private_directory(ROOT, args.output_dir)
        for name, raw in payload.items():
            write_raw_new(directory / name, raw)
        write_json_new(directory / 'execution.json', execution)
        print('status=external_development_only modelCalls=0 qualification=not_assessed')
        return 0
    except Exception as error:
        print('Cannot import Russian STS: ' + type(error).__name__ + ': ' + str(error)[:200], file=sys.stderr)
        return 1


if __name__ == '__main__': raise SystemExit(main())
