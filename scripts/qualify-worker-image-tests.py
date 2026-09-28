#!/usr/bin/env python3
"""Run regressions against verified /opt/agat modules inside the Linux image."""
import argparse
import hashlib
import importlib
from importlib import metadata
import json
from pathlib import Path
import platform
import sys
import unittest


def main():
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    root = parser.parse_args().source_root.resolve()
    assert platform.system() == 'Linux'
    names = ['agat_worker', 'embedding_http', 'embedding_transport', 'web_tools', 'telemetry', 'local_decisions']
    # Test-only decision fixtures live in the source tree. Runtime imports must
    # resolve to the image first, even when tests prepend their own directory.
    sys.path[:0] = ['/opt/agat', str(root)]
    verified = {}
    for name in names:
        module = importlib.import_module(name)
        path = Path(module.__file__)
        assert path == Path('/opt/agat') / (name + '.py')
        raw = path.read_bytes()
        assert raw == (root / 'workers' / (name + '.py')).read_bytes()
        verified[name] = hashlib.sha256(raw).hexdigest()
    requirements = dict(line.split('==') for line in (root / 'workers/requirements.txt').read_text().splitlines() if line)
    installed = {name: metadata.version(name) for name in requirements}
    assert installed == requirements
    print(json.dumps({'python': sys.version, 'verifiedImageModules': verified, 'requirements': installed}), flush=True)
    suite = unittest.defaultTestLoader.discover(str(root / 'workers'), pattern='test_*.py')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    for name in names:
        assert Path(sys.modules[name].__file__) == Path('/opt/agat') / (name + '.py')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
