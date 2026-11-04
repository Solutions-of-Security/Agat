#!/usr/bin/env python3
"""Import pinned public snapshots into an immutable, label-free review pool."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import parse_json
from scripts.lib.decision_public_sources import build_pool, pinned_input, private_directory, source_identity, validate_acquisition, write_json_new
from scripts.lib.decision_shadow_pilot import require


def snapshot(directory, item):
    require(isinstance(item, dict) and isinstance(item.get("file"), str)
            and Path(item["file"]).name == item["file"] and item["file"] not in (".", ".."), "Invalid snapshot filename")
    return pinned_input(directory / item["file"], item["sha256"], 4 * 1024 * 1024)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acquisition", type=Path, required=True)
    parser.add_argument("--acquisition-file-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest_raw = pinned_input(args.acquisition, args.acquisition_file_sha256, 1024 * 1024)
        manifest = validate_acquisition(parse_json(manifest_raw))
        directory = args.acquisition.absolute().parent
        require(isinstance(manifest.get("pages"), list) and 1 <= len(manifest["pages"]) <= 30, "Invalid page inventory")
        model_raw = snapshot(directory, manifest["modelMetadata"])
        require(len(model_raw) == manifest["modelMetadata"]["bytes"], "Model metadata size differs")
        raw_pages = [snapshot(directory, item) for item in manifest["pages"]]
        require(sum(map(len, raw_pages)) <= 64 * 1024 * 1024, "Snapshot total exceeds its bound")
        commit, files = source_identity(ROOT)
        artifacts = build_pool(manifest, raw_pages, parse_json(model_raw))
        require(source_identity(ROOT) == (commit, files), "Import sources changed")
        output = private_directory(ROOT, args.output_dir)
        for name, value in artifacts.items(): write_json_new(output / name, value)
        write_json_new(output / "import.json", sealed({"schemaVersion": "agat.decision.public-support-import.v1",
            "acquisitionFileSha256": args.acquisition_file_sha256, "acquisitionSha256": manifest["sha256"],
            "sourceCommit": commit, "sourceFiles": files, "outputFileSha256": {name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in artifacts},
            "routingEnabled": False, "qualification": "not_assessed", "modelCalls": 0}))
        print(f"Public source pool prepared: {len(artifacts['pool.json']['cases'])} cases; {output}")
        return 0
    except Exception as error:
        print(f"Cannot import public support pool: {error}", file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main())
