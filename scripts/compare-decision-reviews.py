#!/usr/bin/env python3
"""Compare two complete pinned v2 review sessions and list disagreements privately."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.lib.decision_review_pair import compare_pair
from scripts.lib.decision_public_sources import private_directory, write_json_new


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for prefix in ("first", "second"):
        for name in ("session", "input-review", "output-review"):
            parser.add_argument("--" + prefix + "-" + name, type=Path, required=True)
            parser.add_argument("--" + prefix + "-" + name + "-file-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    def binding(prefix):
        return tuple(getattr(args, prefix + "_" + name + suffix)
                     for name in ("session", "input_review", "output_review") for suffix in ("", "_file_sha256"))
    try:
        comparison = compare_pair(binding("first"), binding("second"))
        directory = private_directory(ROOT, args.output_dir)
        write_json_new(directory / "comparison.json", comparison)
    except Exception as error:
        print("Cannot compare reviews: " + type(error).__name__ + ": " + str(error)[:200], file=sys.stderr)
        return 1
    print("status=compared agreementCount=" + str(comparison["agreementCount"])
          + " disagreementCount=" + str(comparison["disagreementCount"])
          + " qualification=not_assessed modelCallsDuringComparison=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
