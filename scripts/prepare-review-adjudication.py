#!/usr/bin/env python3
"""Prepare blank disputed-case review and context from a pinned complete review pair."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.lib.decision_review_adjudication import prepare_handoff
from scripts.lib.decision_public_sources import private_directory, write_json_new


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for prefix in ("first", "second"):
        for name in ("session", "input-review", "output-review"):
            parser.add_argument("--" + prefix + "-" + name, type=Path, required=True)
            parser.add_argument("--" + prefix + "-" + name + "-file-sha256", required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--comparison-file-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    def binding(prefix):
        return tuple(getattr(args, prefix + "_" + name + suffix)
                     for name in ("session", "input_review", "output_review") for suffix in ("", "_file_sha256"))
    try:
        outputs = prepare_handoff(binding("first"), binding("second"), args.comparison, args.comparison_file_sha256)
        directory = private_directory(ROOT, args.output_dir)
        for name, value in outputs.items(): write_json_new(directory / name, value)
    except Exception as error:
        print("Cannot prepare review adjudication: " + type(error).__name__ + ": " + str(error)[:200], file=sys.stderr)
        return 1
    print("status=awaiting_adjudication disputedCases=" + str(outputs["packet.json"]["disputedCaseCount"])
          + " referenceLabelsCreated=0 modelCallsDuringPreparation=0 qualification=not_assessed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
