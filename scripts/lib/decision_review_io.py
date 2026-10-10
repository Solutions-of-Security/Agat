"""Committed source binding and atomic checkpoints for owned review directories."""
import hashlib
import os
from pathlib import Path
import subprocess
import uuid

from decision_runtime.contracts import canonical_json
from scripts.lib.decision_shadow_pilot import require

MAX_REVIEW_BYTES = 32 * 1024 * 1024


def source_identity(root, sources):
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True, timeout=5).strip()
    files = {}
    for name in sources:
        path = Path(root) / name
        require(not path.is_symlink(), 'Review source must not be a symlink')
        raw = path.read_bytes()
        expected = subprocess.check_output(['git', 'show', f'{commit}:{name}'], cwd=root, timeout=5)
        require(raw == expected, 'Commit review sources before starting the session')
        files[name] = hashlib.sha256(raw).hexdigest()
    return commit, files


def checkpoint(directory, review):
    raw = (canonical_json(review) + '\n').encode('utf-8')
    require(len(raw) <= MAX_REVIEW_BYTES, 'Review checkpoint exceeds its byte bound')
    temporary = directory / f'.review-{uuid.uuid4().hex}.tmp'
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, directory / 'review.json')
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)
