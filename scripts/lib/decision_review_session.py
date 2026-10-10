"""Verify bound v2 terminal receipts; file integrity does not establish human authority."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import PurePosixPath
import re

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import fields, fingerprint, parse_json, string
from scripts.lib.decision_blind_review import validate_progress
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_shadow_pilot import require

SESSION_SCHEMA = "agat.decision.blind-review-session.v2"
VERIFICATION_SCHEMA = "agat.decision.blind-review-session-verification.v1"
MAX_REVIEW_BYTES = 32 * 1024 * 1024
MAX_SESSION_BYTES = 256 * 1024
AUTHORITY_FLAGS = ("reviewerIdentityVerified", "humanExecutionVerified", "independentReviewVerified",
                   "expertQualificationsVerified", "routingEnabled")
SESSION_FIELDS = {"schemaVersion", "sha256", "status", "startedAt", "finishedAt", "endReason",
                  "sourceCommit", "sourceFiles", "inputReviewFileSha256", "poolSha256", "reviewerId",
                  "existingAnswers", "newAnswers", "revisedAnswers", "clearedAnswers", "remainingAnswers",
                  "submissionConfirmed", "outputReviewFileSha256", "modelCalls", "qualification",
                  "failureType", *AUTHORITY_FLAGS}


def utc_time(value):
    string(value, 100)
    require(value.endswith("Z") and "T" in value, "Use a UTC session timestamp")
    result = datetime.fromisoformat(value[:-1] + "+00:00")
    require(result.tzinfo == timezone.utc, "Use a UTC session timestamp")
    return result


def verify_values(session, initial, saved, *, input_sha, output_sha):
    fields(session, SESSION_FIELDS)
    require(session["schemaVersion"] == SESSION_SCHEMA
            and session["sha256"] == fingerprint({key: value for key, value in session.items() if key != "sha256"}),
            "Use a sealed v2 session receipt")
    require(session["inputReviewFileSha256"] == input_sha and session["outputReviewFileSha256"] == output_sha,
            "Session input or output binding differs")
    for flag in AUTHORITY_FLAGS:
        require(session[flag] is False, "Session grants unsupported authority")
    require(type(session["modelCalls"]) is int and session["modelCalls"] == 0
            and session["qualification"] == "not_assessed", "Session grants unsupported model qualification")
    started = utc_time(session["startedAt"]); finished = utc_time(session["finishedAt"])
    require(started <= finished, "Session timestamps are reversed")
    require(isinstance(session["sourceCommit"], str) and re.fullmatch(r"[a-f0-9]{40}", session["sourceCommit"]),
            "Invalid declared source commit")
    sources = session["sourceFiles"]
    require(isinstance(sources, dict) and 1 <= len(sources) <= 100
            and "scripts/review-decision-pool.py" in sources, "Invalid declared source inventory")
    for name, digest in sources.items():
        require(isinstance(name, str) and len(name) <= 300 and "\\" not in name
                and not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts
                and PurePosixPath(name).as_posix() == name
                and name.endswith(".py") and isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest),
                "Invalid declared source path or SHA")
    reviewer = session["reviewerId"]
    initial = validate_progress(initial, reviewer)
    require(isinstance(saved, dict) and "reviewedAt" in saved, "Saved review is missing its submission stamp")
    stamp = saved["reviewedAt"]
    saved = validate_progress({**saved, "reviewedAt": None}, reviewer)
    require(saved["reviewerId"] == reviewer, "Saved review ownership differs")
    for key in ("schemaVersion", "pool", "poolSha256", "splitSeed"):
        require(initial[key] == saved[key], "Review task pool or split binding changed")
    require(session["poolSha256"] == initial["poolSha256"], "Session pool binding differs")
    changes = list(zip(initial["labels"], saved["labels"]))
    counts = {
        "existingAnswers": sum(before["expectedOptionId"] is not None for before, _after in changes),
        "newAnswers": sum(before["expectedOptionId"] is None and after["expectedOptionId"] is not None for before, after in changes),
        "revisedAnswers": sum(before["expectedOptionId"] is not None and after["expectedOptionId"] is not None and before != after for before, after in changes),
        "clearedAnswers": sum(before["expectedOptionId"] is not None and after["expectedOptionId"] is None for before, after in changes),
        "remainingAnswers": sum(after["expectedOptionId"] is None for _before, after in changes),
    }
    total = len(changes)
    for key in ("existingAnswers", "newAnswers", "revisedAnswers", "clearedAnswers"):
        require(type(session[key]) is int and 0 <= session[key] <= total, "Invalid receipt answer count")
    require(session["existingAnswers"] == counts["existingAnswers"], "Initial answer count differs")
    status = session["status"]; reason = session["endReason"]; submitted = session["submissionConfirmed"]
    require(type(submitted) is bool and status in ("completed", "partial", "failed"), "Invalid session state")
    require(not submitted or (counts["remainingAnswers"] == 0 and stamp is not None),
            "Submitted receipt requires a complete stamped checkpoint")
    placeholder_counts = status == "failed" and session["remainingAnswers"] is None
    if placeholder_counts:
        require(reason is None and not submitted and all(session[key] == 0 for key in ("newAnswers", "revisedAnswers", "clearedAnswers")),
                "Failed receipt placeholders differ")
    else:
        require(type(session["remainingAnswers"]) is int, "Invalid remaining answer count")
        for key, count in counts.items():
            require(session[key] == count, "Receipt net answer counts differ")
    if status == "completed":
        require(reason == "submitted" and submitted and counts["remainingAnswers"] == 0
                and stamp == session["finishedAt"] and session["failureType"] is None,
                "Completed review lacks explicit submission evidence")
    elif status == "partial":
        require(reason in ("quit", "eof", "interrupted") and not submitted and stamp is None
                and session["failureType"] is None, "Partial draft grants completion")
    else:
        string(session["failureType"], 100)
        require(reason in (None, "quit", "eof", "interrupted", "submitted") and submitted == (reason == "submitted"),
                "Failed session submission state differs")
        require(placeholder_counts or reason is not None, "Finalized failed counts require an end reason")
        if stamp is not None:
            require(submitted and counts["remainingAnswers"] == 0 and started <= utc_time(stamp) <= finished,
                    "Failed checkpoint grants unsupported submission stamp")
    if stamp is not None:
        require(started <= utc_time(stamp) <= finished, "Submission timestamp is outside the session")
    return {"sessionStatus": status, "endReason": reason, "counts": counts,
            "reportedCountsReconciled": not placeholder_counts,
            "allAnswersPresent": counts["remainingAnswers"] == 0,
            "completeReviewArtifact": status == "completed", "submissionConfirmed": submitted,
            "reviewSourceCommit": session["sourceCommit"], "declaredReviewSourceFiles": sources}


def verify_session(session_path, session_sha, initial_path, initial_sha, saved_path, saved_sha):
    session_raw = pinned_input(session_path, session_sha, MAX_SESSION_BYTES)
    initial_raw = pinned_input(initial_path, initial_sha, MAX_REVIEW_BYTES)
    saved_raw = pinned_input(saved_path, saved_sha, MAX_REVIEW_BYTES)
    details = verify_values(parse_json(session_raw), parse_json(initial_raw), parse_json(saved_raw),
                            input_sha=initial_sha, output_sha=saved_sha)
    return sealed({"schemaVersion": VERIFICATION_SCHEMA, "status": "pass", **details,
                   "sessionFileSha256": session_sha, "inputReviewFileSha256": initial_sha,
                   "outputReviewFileSha256": saved_sha, "reviewSourcesRevalidated": False,
                   **{flag: False for flag in AUTHORITY_FLAGS}, "modelCallsDuringVerification": 0,
                   "qualification": "not_assessed"})
