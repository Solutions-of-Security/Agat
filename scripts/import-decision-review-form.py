#!/usr/bin/env python3
"""Import a pinned browser answer file into the existing canonical review protocol."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json, parse_json
from scripts.lib.decision_public_sources import pinned_input, private_directory, write_json_new
from scripts.lib.decision_review_form import (IMPORT_SCHEMA, MAX_EXPORT_BYTES, MAX_REVIEW_BYTES,
                                            export_review, sha, verify_import_values)
from scripts.lib.decision_review_io import checkpoint, source_identity
from scripts.lib.decision_review_session import AUTHORITY_FLAGS
from scripts.lib.decision_shadow_pilot import utc_now

SOURCES = ("scripts/import-decision-review-form.py", "scripts/lib/decision_review_form.py",
           "scripts/lib/decision_review_session.py", "scripts/lib/decision_blind_review.py", "scripts/lib/decision_review_io.py",
           "scripts/lib/decision_public_sources.py", "scripts/lib/decision_shadow_pilot.py",
           "scripts/lib/decision_shadow_sli.py", "scripts/lib/decision_stage_inventory.py",
           "scripts/lib/decision_caller_inventory.py", "decision_runtime/__init__.py", "decision_runtime/annotations.py",
           "decision_runtime/artifacts.py", "decision_runtime/contracts.py", "decision_runtime/evaluation.py",
           "decision_runtime/engine.py", "decision_runtime/calibration.py")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("export", "manifest", "input-review"):
        parser.add_argument("--" + name, type=Path, required=True)
        parser.add_argument("--" + name + "-file-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        export_raw = pinned_input(args.export, args.export_file_sha256, MAX_EXPORT_BYTES)
        manifest_raw = pinned_input(args.manifest, args.manifest_file_sha256, MAX_REVIEW_BYTES)
        initial_raw = pinned_input(args.input_review, args.input_review_file_sha256, MAX_REVIEW_BYTES)
        export = parse_json(export_raw); manifest = parse_json(manifest_raw); initial = parse_json(initial_raw)
        review = export_review(export, manifest, initial, args.input_review_file_sha256)
        identity = source_identity(ROOT, SOURCES)
        output_raw = (canonical_json(review) + "\n").encode()
        remaining = sum(x["expectedOptionId"] is None for x in review["labels"])
        submitted = export["status"] == "submitted"
        report = sealed({"schemaVersion": IMPORT_SCHEMA, "status": "completed" if submitted else "partial",
                         "startedAt": export["startedAt"], "finishedAt": export["updatedAt"],
                         "endReason": "submitted" if submitted else "quit", "sourceCommit": identity[0], "sourceFiles": identity[1],
                         "inputReviewFileSha256": args.input_review_file_sha256, "poolSha256": review["poolSha256"],
                         "reviewerId": review["reviewerId"], "existingAnswers": 0,
                         "newAnswers": len(review["labels"]) - remaining, "revisedAnswers": 0, "clearedAnswers": 0,
                         "remainingAnswers": remaining, "submissionConfirmed": submitted,
                         "outputReviewFileSha256": sha(output_raw), "modelCalls": 0, "qualification": "not_assessed",
                         "failureType": None, **{flag: False for flag in AUTHORITY_FLAGS},
                         "exportRawUtf8": export_raw.decode("utf-8"), "exportFileSha256": args.export_file_sha256,
                         "manifest": manifest, "importedAt": utc_now()})
        verify_import_values(report, initial, review, input_sha=args.input_review_file_sha256, output_sha=sha(output_raw))
        directory = private_directory(ROOT, args.output_dir)
        checkpoint(directory, review)
        if sha((directory / "review.json").read_bytes()) != sha(output_raw):
            raise ValueError("Canonical serialization differs")
        write_json_new(directory / "session.json", report)
    except Exception as error:
        print("Не удалось импортировать ответы: " + str(error)[:300], file=sys.stderr)
        return 1
    print("Ответы импортированы. Без ответа: " + str(remaining) + ". " + str(directory))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
