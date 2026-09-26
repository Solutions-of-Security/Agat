"""Explicit, pinned downloads; serving only reads verified local files."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .contracts import canonical_json, fingerprint, parse_json

ROOT = Path(__file__).resolve().parent
DEFAULT_STORE = ROOT.parent / ".local-models" / "decisions"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(name: str, store: Path = DEFAULT_STORE) -> Path:
    catalog = parse_json((ROOT / "models.json").read_bytes())
    if name not in catalog:
        raise ValueError("Unknown model; see decision_runtime/models.json")
    entry = catalog[name]
    store = store.resolve()
    store.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(store / "hf-home"))
    from huggingface_hub import snapshot_download

    snapshot = Path(snapshot_download(
        repo_id=entry["repository"], revision=entry["revision"], cache_dir=store / "hub",
        allow_patterns=["config.json", "model*.safetensors", "model*.index.json", "tokenizer.json",
                        "tokenizer_config.json", "vocab.json", "merges.txt", "chat_template.jinja",
                        "decider_config.json", "README.md", "LICENSE"],
    ))
    # No remote Python is downloaded or trusted. Record every inference input, not just the repo name.
    files = {p.name: sha256_file(p) for p in sorted(snapshot.iterdir())
             if p.is_file() and p.name not in {"README.md", "LICENSE"}}
    manifest = {"schemaVersion": 1, "name": name, **entry, "snapshot": str(snapshot),
                "files": files, "artifactSha256": fingerprint(files)}
    target = store / f"{name}.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


def verify_manifest(path: Path) -> tuple[dict, Path]:
    manifest = parse_json(path.read_bytes())
    if manifest.get("schemaVersion") != 1 or not isinstance(manifest.get("files"), dict):
        raise ValueError("Invalid model manifest; run the download command")
    files = manifest["files"]
    if not {"config.json", "tokenizer.json", "tokenizer_config.json"} <= files.keys():
        raise ValueError("Incomplete model manifest")
    if not any(name.endswith(".safetensors") for name in files):
        raise ValueError("Model weights are missing from the manifest")
    snapshot = Path(manifest["snapshot"])
    if not snapshot.is_dir() or fingerprint(files) != manifest.get("artifactSha256"):
        raise ValueError("Invalid local model artifact")
    actual = {p.name for p in snapshot.iterdir() if p.is_file() and p.name not in {"README.md", "LICENSE"}}
    if actual != set(files):
        raise ValueError("Model directory differs from the pinned manifest")
    for name, checksum in files.items():
        if Path(name).name != name or sha256_file(snapshot / name) != checksum:
            raise ValueError("Model file checksum mismatch")
    return manifest, snapshot
