"""Experimental local, typed decisions for Agat (no workflow side effects)."""

VERSION = "0.12.0"


def implementation_sha256() -> str:
    """Bind evidence to the actual runtime sources, including uncommitted work."""
    import hashlib
    from pathlib import Path

    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()
