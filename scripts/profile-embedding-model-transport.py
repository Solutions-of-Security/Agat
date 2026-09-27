#!/usr/bin/env python3
"""Paired transport measurements against one already-installed loopback model."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('transport_profile_support', ROOT / 'scripts/profile-embedding-transport.py')
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
MODEL = 'embeddinggemma:latest'
DIGEST = '85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1'
BASE = 'http://127.0.0.1:11434'
SOURCES = ['scripts/profile-embedding-model-transport.py', 'scripts/verify-embedding-model-transport.py',
           'scripts/profile-embedding-transport.py', 'workers/agat_worker.py', 'workers/embedding_http.py',
           'workers/telemetry.py', 'workers/web_tools.py', 'workers/local_decisions.py']


def metadata():
    def get(route):
        with urllib.request.urlopen(BASE + route, timeout=5) as response:
            return json.loads(response.read(1024 * 1024))
    models = [model for model in get('/api/tags')['models'] if model['name'] == MODEL]
    assert len(models) == 1 and models[0]['digest'] == DIGEST
    return {'version': get('/api/version')['version'], 'model': {key: models[0][key] for key in ('name', 'digest', 'size')},
            'loaded': [{key: model.get(key) for key in ('name', 'digest', 'size', 'size_vram')} for model in get('/api/ps')['models']]}


def inputs(batch, case):
    return [f'Учебный документ {case}-{index}. За неделю поступило {20 + case + index} обращений. '
            f'Сотрудник проверил источник и зарегистрировал результат. '
            f'Статус заявки: выполнено. Группа: поддержка. Период: сентябрь 2026.' for index in range(batch)]


def phases():
    warm = [{'id': f'warm-b{batch}-{mode}', 'batch': batch, 'concurrency': 1, 'mode': mode, 'calls': 1, 'measured': False}
            for batch in (1, 32) for mode in ('direct', 'isolated')]
    measured = [{'id': f'b{batch}-c{concurrency}-{block}', 'batch': batch, 'concurrency': concurrency, 'mode': mode,
                 'calls': 4, 'measured': True} for batch in (1, 32) for concurrency in (1, 4)
                for block, mode in enumerate(('direct', 'isolated', 'isolated', 'direct'))]
    return warm + measured


def run_phase(item, direct):
    context = threading.local()
    children = []
    lock = threading.Lock()
    create = subprocess.Popen
    helper = str((ROOT / 'workers/embedding_http.py').resolve())

    def spawn(*args, **kwargs):
        child = create(*args, **kwargs)
        if isinstance(args[0], list) and args[0][-1] == helper:
            with lock:
                children.append((context.identity, child))
        return child

    def call(index):
        context.identity = index
        contents = inputs(item['batch'], index)
        payload = {'model': MODEL, 'input': contents}
        options = {'embedding_timeout': 30} if item['mode'] == 'isolated' else {}
        client_type = direct if item['mode'] == 'direct' else support.agat_worker.LocalModelClient
        client = client_type(BASE + '/v1', '', telemetry=support.WorkerTelemetry(enabled=False), **options)
        row = {'id': f"{item['id']}-{index}", 'case': index, 'inputSha256': support.sha(json.dumps(payload, ensure_ascii=False).encode()),
               'startedNs': time.monotonic_ns()}
        vectors = client.embed(MODEL, contents)
        row['finishedNs'] = time.monotonic_ns()
        assert len(vectors) == item['batch'] and all(len(vector) == 768 for vector in vectors)
        with lock:
            owned = [child for owner, child in children if owner == index]
            row['children'] = [{'pid': child.pid, 'returncode': child.returncode,
                                'stdinClosed': child.stdin.closed, 'stdoutClosed': child.stdout.closed} for child in owned]
        assert len(owned) == (item['mode'] == 'isolated')
        assert all(child.returncode == 0 and child.stdin.closed and child.stdout.closed for child in owned)
        return row, vectors

    with patch('subprocess.Popen', side_effect=spawn):
        with ThreadPoolExecutor(max_workers=item['concurrency']) as executor:
            observed = list(executor.map(call, range(item['calls'])))
    return observed


def main():
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / 'docs'):
        parser.error('Evidence must be under /docs')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'vectors').mkdir()
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    plan = {'schema': 'agat.embedding.model-transport.v1', 'createdAt': datetime.now(timezone.utc).isoformat(),
            'implementationCommit': commit, 'baselineCommit': support.BASELINE, 'sourceSha256': {},
            'model': MODEL, 'modelDigest': DIGEST, 'endpoint': BASE + '/v1', 'dimensions': 768,
            'platform': platform.system(), 'machine': platform.machine(), 'python': platform.python_version(),
            'maxComponentDifference': 1e-6, 'maxCosineDistance': 1e-10, 'phases': phases(),
            'cases': {f'b{b}-{case}': inputs(b, case) for b in (1, 32) for case in range(4)}}
    for name in SOURCES:
        raw = (ROOT / name).read_bytes()
        assert raw == subprocess.check_output(['git', 'show', f'{commit}:{name}'], cwd=ROOT), name
        plan['sourceSha256'][name] = support.sha(raw)
    result = {'status': 'incomplete', 'failure': None, 'phases': [], 'vectorFiles': {}}
    started = time.monotonic()
    try:
        with patch.dict(os.environ, {'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'}), support.legacy_client() as (direct, legacy_sha):
            plan['baselineWorkerSha256'] = legacy_sha
            plan['metadataBefore'] = metadata()
            support.write(output / 'plan.json', plan)
            result['planSha256'] = support.sha((output / 'plan.json').read_bytes())
            for item in plan['phases']:
                if time.monotonic() - started > 180:
                    raise RuntimeError('Model profile exceeded 180 seconds')
                observed = run_phase(item, direct)
                rows = []
                # Persist after each phase so compression/I/O is outside all timed calls.
                for row, vectors in observed:
                    raw = support.encoded(vectors)
                    sha = support.sha(raw)
                    row['vectorSha256'] = sha
                    if sha not in result['vectorFiles']:
                        data = gzip.compress(raw, mtime=0)
                        path = output / 'vectors' / f'{sha}.json.gz'
                        path.write_bytes(data)
                        result['vectorFiles'][sha] = {'sha256': support.sha(data), 'bytes': len(data), 'decodedBytes': len(raw)}
                    rows.append(row)
                result['phases'].append({'id': item['id'], 'rows': rows})
                print(f"{item['id']}: {len(rows)} successful calls", flush=True)
            result['metadataAfter'] = metadata()
            assert result['metadataAfter']['model'] == plan['metadataBefore']['model']
            assert result['metadataAfter']['version'] == plan['metadataBefore']['version']
            result['status'] = 'observed'
    except Exception as error:
        result['failure'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        result['elapsedSeconds'] = time.monotonic() - started
        support.write(output / 'result.json', result)


if __name__ == '__main__':
    main()
