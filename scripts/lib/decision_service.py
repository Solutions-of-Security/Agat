"""Render a foreground macOS LaunchAgent; never install or start a service."""

from __future__ import annotations

import os
import plistlib
import re
from pathlib import Path


def launch_agent(*, root, python, manifest, policy, log_dir, port=8766, max_tokens=2048,
                 cache_limit_mib=128, inference_timeout_ms=2000, throttle_s=30,
                 label='org.agat.decision-shadow', calibration=None):
    root = Path(root).absolute()
    if not (root / 'decision_runtime' / '__main__.py').is_file():
        raise ValueError('Root must contain the decision_runtime package')

    def local_path(value):
        path = Path(value).expanduser()
        # Do not resolve a virtualenv interpreter symlink to its global Python:
        # the invoked path selects the environment and its installed MLX packages.
        return path.absolute() if path.is_absolute() else (root / path).absolute()

    python, manifest, policy, log_dir = map(local_path, (python, manifest, policy, log_dir))
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError('Python must be an existing executable')
    if not manifest.is_file() or not policy.is_file():
        raise ValueError('Manifest and policy must be existing files')
    if not log_dir.is_dir():
        raise ValueError('Create the explicit log directory before rendering')
    for value, lower, upper in ((port, 1024, 65535), (max_tokens, 64, 4096),
                                (cache_limit_mib, 0, 4096), (inference_timeout_ms, 100, 10000),
                                (throttle_s, 10, 3600)):
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError('Invalid service port, inference limit, or restart interval')
    if not isinstance(label, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{1,127}', label):
        raise ValueError('Invalid LaunchAgent label')
    arguments = [str(python), '-m', 'decision_runtime', 'serve', '--manifest', str(manifest),
                 '--policy', str(policy), '--port', str(port), '--max-tokens', str(max_tokens),
                 '--cache-limit-mib', str(cache_limit_mib), '--inference-timeout-ms', str(inference_timeout_ms),
                 '--exit-on-backend-unavailable']
    if calibration is not None:
        calibration = local_path(calibration)
        if not calibration.is_file():
            raise ValueError('Calibration must be an existing file')
        arguments += ['--calibration', str(calibration)]
    return {
        'Label': label, 'ProgramArguments': arguments, 'WorkingDirectory': str(root),
        'RunAtLoad': True, 'KeepAlive': {'SuccessfulExit': False},
        'ThrottleInterval': throttle_s, 'ExitTimeOut': 30,
        'EnvironmentVariables': {'PYTHONUNBUFFERED': '1'},
        'StandardOutPath': str(log_dir / f'{label}.out.log'),
        'StandardErrorPath': str(log_dir / f'{label}.err.log'),
    }


def write_launch_agent(path, config):
    payload = plistlib.dumps(config, fmt=plistlib.FMT_XML, sort_keys=False)
    with Path(path).open('xb') as stream:
        stream.write(payload)
