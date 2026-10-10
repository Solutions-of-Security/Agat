"""Replay pinned adjudication artifacts without running the terminal or models."""
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import parse_json
from scripts.lib.decision_adjudication_session import validate_handoff, verify_adjudication_values
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_review_session import MAX_REVIEW_BYTES, MAX_SESSION_BYTES


def verify_session(session_path, session_sha, initial_path, initial_sha, saved_path, saved_sha,
                   packet_path, packet_sha, blank_path, blank_sha, notes_path, notes_sha):
    session_raw = pinned_input(session_path, session_sha, MAX_SESSION_BYTES)
    initial_raw = pinned_input(initial_path, initial_sha, MAX_REVIEW_BYTES)
    saved_raw = pinned_input(saved_path, saved_sha, MAX_REVIEW_BYTES)
    packet_raw = pinned_input(packet_path, packet_sha, MAX_SESSION_BYTES)
    blank_raw = pinned_input(blank_path, blank_sha, MAX_REVIEW_BYTES)
    notes_raw = pinned_input(notes_path, notes_sha, MAX_REVIEW_BYTES)
    packet, blank, _notes = validate_handoff(
        parse_json(packet_raw), parse_json(blank_raw), parse_json(notes_raw),
        blank_raw=blank_raw, notes_raw=notes_raw)
    report = verify_adjudication_values(
        parse_json(session_raw), parse_json(initial_raw), parse_json(saved_raw), packet, blank,
        input_sha=initial_sha, output_sha=saved_sha,
        packet_sha=packet_sha, blank_sha=blank_sha, notes_sha=notes_sha)
    return sealed({key: value for key, value in report.items() if key != 'sha256'}
                  | {'sessionFileSha256': session_sha})
