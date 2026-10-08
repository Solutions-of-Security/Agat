#!/usr/bin/env python3
"""Review a pinned blank/partial package in a terminal without model predictions."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json, parse_json
from scripts.lib.decision_blind_review import interact, terminal_text, validate_progress
from scripts.lib.decision_public_sources import pinned_input, private_directory, write_json_new
from scripts.lib.decision_shadow_pilot import require, utc_now

SOURCES = ("scripts/review-decision-pool.py", "scripts/lib/decision_blind_review.py",
           "scripts/lib/decision_public_sources.py", "scripts/lib/decision_shadow_pilot.py",
           "decision_runtime/annotations.py", "decision_runtime/artifacts.py", "decision_runtime/contracts.py")
MAX_REVIEW_BYTES = 32 * 1024 * 1024


def source_identity(root):
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True, timeout=5).strip()
    files = {}
    for name in SOURCES:
        raw = (root / name).read_bytes()
        expected = subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=root, timeout=5)
        require(raw == expected, "Commit review sources before starting the session")
        files[name] = hashlib.sha256(raw).hexdigest()
    return commit, files


def checkpoint(directory, review):
    raw = (canonical_json(review) + "\n").encode("utf-8")
    require(len(raw) <= MAX_REVIEW_BYTES, "Review checkpoint exceeds its byte bound")
    temporary = directory / f".review-{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, directory / "review.json")
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None, *, input_stream=None, output_stream=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--review-file-sha256", required=True)
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    stream = input_stream if input_stream is not None else sys.stdin
    output = output_stream if output_stream is not None else sys.stdout
    directory = None; report = None
    try:
        require(stream.isatty() and output.isatty(), "Use interactive input and output terminals")
        raw = pinned_input(args.review, args.review_file_sha256, MAX_REVIEW_BYTES)
        review = validate_progress(parse_json(raw), args.reviewer_id)
        identity = source_identity(ROOT)
        started = utc_now()
        directory = private_directory(ROOT, args.output_dir)
        review["reviewerId"] = args.reviewer_id
        checkpoint(directory, review)
        report = {"schemaVersion": "agat.decision.blind-review-session.v1", "status": "failed",
                  "startedAt": started, "finishedAt": None, "endReason": None,
                  "sourceCommit": identity[0], "sourceFiles": identity[1],
                  "inputReviewFileSha256": args.review_file_sha256, "poolSha256": review["poolSha256"],
                  "reviewerId": args.reviewer_id, "existingAnswers": sum(x["expectedOptionId"] is not None for x in review["labels"]),
                  "newAnswers": 0, "remainingAnswers": None, "outputReviewFileSha256": None,
                  "reviewerIdentityVerified": False, "humanExecutionVerified": False,
                  "independentReviewVerified": False, "expertQualificationsVerified": False,
                  "modelCalls": 0, "routingEnabled": False, "qualification": "not_assessed", "failureType": None}

        def save(candidate):
            require(source_identity(ROOT) == identity, "Review sources changed during the session")
            checkpoint(directory, candidate)

        review, reason = interact(review, stream, output, save)
        require(source_identity(ROOT) == identity, "Review sources changed during the session")
        require(pinned_input(args.review, args.review_file_sha256, MAX_REVIEW_BYTES) == raw,
                "Review input changed during the session")
        remaining = sum(x["expectedOptionId"] is None for x in review["labels"])
        # Stamp submission only when every explicit option+rationale was confirmed.
        finished = utc_now()
        if remaining == 0:
            review["reviewedAt"] = finished
        checkpoint(directory, review)
        report.update(status="completed" if remaining == 0 else "partial", finishedAt=finished, endReason=reason,
                      newAnswers=len(review["labels"]) - remaining - report["existingAnswers"], remainingAnswers=remaining,
                      outputReviewFileSha256=hashlib.sha256((directory / "review.json").read_bytes()).hexdigest())
        write_json_new(directory / "session.json", sealed(report))
        output.write(f"Review {report['status']}: {remaining} remaining. Output: {terminal_text(str(directory))}\n")
        output.flush()
        return 0 if remaining == 0 else 2
    except Exception as error:
        if directory is not None and report is not None:
            report.update(finishedAt=utc_now(), failureType=type(error).__name__)
            if (directory / "review.json").is_file():
                report["outputReviewFileSha256"] = hashlib.sha256((directory / "review.json").read_bytes()).hexdigest()
            if not (directory / "session.json").exists():
                write_json_new(directory / "session.json", sealed(report))
        print(f"Cannot complete blind review: {terminal_text(str(error))[:1000]}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
