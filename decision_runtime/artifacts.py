"""Immutable-by-default local experiment files; digests are integrity, not signatures."""

from pathlib import Path

from .contracts import canonical_json, fingerprint, parse_json


def sealed(body: dict) -> dict:
    return {**body, "sha256": fingerprint(body)}


def verify_seal(value: dict, schema: str) -> dict:
    if not isinstance(value, dict) or value.get("schemaVersion") != schema:
        raise ValueError("Unsupported artifact schema")
    body = {k: v for k, v in value.items() if k != "sha256"}
    if value.get("sha256") != fingerprint(body):
        raise ValueError("Artifact checksum mismatch")
    return value


def read_json(path: Path) -> dict:
    return parse_json(path.read_bytes())


def write_new(path: Path, value: dict) -> None:
    # Serialize before creating the file: malformed data cannot leave a partial artifact.
    encoded = canonical_json(value) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(encoded)
