"""Owned primary model and a bounded fixed-arrival companion for capacity diagnostics."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from decision_runtime.contracts import canonical_json, number, parse_json
from decision_runtime.model_store import sha256_file
from scripts.lib.decision_arrival_rate import drive_arrivals
from scripts.lib.decision_baselines import LoopbackJson

PRIMARY_SOURCES = ['scripts/lib/decision_arrival_primary.py', 'scripts/test/test_decision_arrival_primary.py',
                   'docs/qualification/local-decisions/performance/toolchains/ollama-0.35.1.json']
PLAN_SCHEMA = 'agat.decision.arrival-rate-plan.v2'
RESULT_SCHEMA = 'agat.decision.arrival-rate-result.v2'
PHASE_SCHEMA = 'agat.decision.arrival-rate-phase.v2'
MODEL = 'qwen3:8b'
DIGEST = '500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41'
REQUEST = {'model': MODEL, 'messages': [{'role': 'user', 'content':
    'Опиши двенадцать последовательных шагов проверки полученного документа: от регистрации до передачи результата. '
    'Каждый шаг объясни отдельным полным предложением. Используй только этот общий учебный сценарий.'}],
    'stream': False, 'think': False, 'keep_alive': '5m',
    'options': {'temperature': 0, 'seed': 0, 'num_ctx': 8192, 'num_predict': 128}}
SETTINGS = {'OLLAMA_NO_CLOUD': '1', 'OLLAMA_MAX_LOADED_MODELS': '1', 'OLLAMA_NUM_PARALLEL': '1',
            'OLLAMA_MAX_QUEUE': '1', 'OLLAMA_CONTEXT_LENGTH': '8192', 'OLLAMA_KEEP_ALIVE': '5m'}


def require(value, message):
    if not value: raise ValueError(message)


def schedules(seconds):
    return [{'caseIndex': i, 'condition': condition, 'ratePerSecond': 1, 'count': seconds,
             'clientSlots': 1, 'maxSchedulerLagMs': 100}
            for i in range(2) for condition in ('primary_idle_before', 'primary_active', 'primary_idle_after')]


def prepare(root, binaries, models):
    pin_path = root / PRIMARY_SOURCES[2]
    pin = parse_json(pin_path.read_bytes())
    require(pin['schemaVersion'] == 'agat.ollama-native-release.v1' and pin['version'] == '0.35.1', 'Primary release pin differs')
    for name, digest in pin['files'].items():
        require(not Path(name).is_absolute() and '..' not in Path(name).parts
                and sha256_file(binaries / name) == digest, 'Native primary dependency SHA differs')
    manifest_path = models / 'manifests/registry.ollama.ai/library/qwen3/8b'
    require(sha256_file(manifest_path) == DIGEST, 'Cached primary manifest differs')
    manifest = parse_json(manifest_path.read_bytes())
    artifacts = [manifest['config'], *manifest['layers']]
    for artifact in artifacts:
        digest = artifact['digest']
        require(isinstance(digest, str) and re.fullmatch('sha256:[0-9a-f]{64}', digest), 'Invalid cached blob identity')
        blob = models / 'blobs' / digest.replace(':', '-')
        require(not (getattr(blob.stat(), 'st_flags', 0) & getattr(stat, 'SF_DATALESS', 0))
                and type(artifact['size']) is int and blob.stat().st_size == artifact['size']
                and sha256_file(blob) == digest[7:], 'Cached primary blob is unavailable or differs')
    return {'model': MODEL, 'manifestSha256': DIGEST, 'blobCount': len(artifacts),
            'blobBytes': sum(a['size'] for a in artifacts), 'release': pin,
            'releaseFileSha256': sha256_file(pin_path), 'request': REQUEST, 'requestSha256': hashlib.sha256(canonical_json(REQUEST).encode()).hexdigest(),
            'settings': SETTINGS, 'ratePerSecond': .5, 'clientSlots': 1, 'timeoutSeconds': 30}


class OwnedPrimary:
    def __init__(self, runtime, root, binaries, models, log, owned, cancelled):
        self.runtime, self.root, self.binaries, self.models = runtime, root, binaries, models
        self.log, self.owned, self.cancelled = log, owned, cancelled
        self.process = None; self.port = None; self.transport = None; self.warmup = None

    def start(self):
        with socket.socket() as bound:
            bound.bind(('127.0.0.1', 0)); self.port = bound.getsockname()[1]
        self.process = subprocess.Popen([str(self.binaries / 'ollama'), 'serve'], cwd=self.root,
            stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True,
            env={**os.environ, **SETTINGS, 'OLLAMA_HOST': f'127.0.0.1:{self.port}', 'OLLAMA_MODELS': str(self.models)})
        self.owned.add(self.process.pid)
        self.transport = LoopbackJson(f'http://127.0.0.1:{self.port}', 30)
        deadline = time.monotonic() + 30
        while True:
            require(self.process.poll() is None and not self.cancelled(), 'Owned primary exited or cancelled')
            try:
                version = self.runtime.request(self.port, '/api/version', timeout=1)
                break
            except Exception:
                require(time.monotonic() < deadline, 'Owned primary startup timeout'); time.sleep(.1)
        require(version == {'version': '0.35.1'}, 'Actual primary release differs')
        tags = self.transport('GET', '/api/tags')
        entry = next((v for v in tags['models'] if v.get('name') == MODEL), {})
        require(entry.get('digest') == DIGEST and not entry.get('remote_host') and not entry.get('remote_model'), 'Primary tag differs or is remote')
        self.warmup = measure_primary(self.transport)
        require(self.warmup['status'] == 'returned', 'Primary warmup did not return a valid response')
        self.sample()

    def sample(self):
        require(self.process is not None and self.process.poll() is None, 'Owned primary process exited')
        self.owned.update(self.runtime.shared.inventory(self.process.pid)[0])
        value = self.transport('GET', '/api/ps')
        require(len(value['models']) == 1 and value['models'][0].get('digest') == DIGEST
                and value['models'][0].get('context_length') == 8192, 'Primary residence/context changed')
        return value

    def close(self, errors):
        if self.process is not None:
            try:
                if self.process.poll() is None:
                    self.runtime.request(self.port, '/api/generate', {'model': MODEL, 'stream': False, 'keep_alive': 0}, timeout=30)
                    require(self.runtime.request(self.port, '/api/ps')['models'] == [], 'Primary model did not unload')
            except Exception as error: errors.append('primaryUnload:' + type(error).__name__)
            self.runtime.stop_owned_process(self.process, self.owned, errors)


def validate_response(value):
    require(isinstance(value, dict) and value.get('model') == MODEL and value.get('done') is True
            and value.get('done_reason') in {'stop', 'length'}, 'Incomplete or foreign primary response')
    message = value.get('message', {})
    require(message.get('role') == 'assistant' and isinstance(message.get('content'), str)
            and bool(message['content']) and not message.get('thinking'), 'Invalid primary text response')
    require(type(value.get('prompt_eval_count')) is int and value['prompt_eval_count'] > 0
            and type(value.get('eval_count')) is int and 1 <= value['eval_count'] <= 128, 'Primary token budget differs')
    for key in ('total_duration', 'load_duration', 'prompt_eval_duration', 'eval_duration'):
        require(type(value.get(key)) is int, 'Invalid primary duration type')
        number(value[key], 0, 3600 * 1_000_000_000)


def measure_primary(transport):
    began = time.monotonic()
    try:
        value = transport('POST', '/api/chat', REQUEST)
        validate_response(value)
        return {'status': 'returned', 'response': value,
                'wallMs': round((time.monotonic() - began) * 1000, 3)}
    except Exception as error:
        return {'status': 'measurement_error', 'reason': type(error).__name__,
                'wallMs': round((time.monotonic() - began) * 1000, 3)}


class PrimaryArrivals:
    def __init__(self, transport, origin, seconds, cancelled):
        require(type(seconds) is int and 4 <= seconds <= 30 and callable(transport) and callable(cancelled),
                'Unsupported primary arrival window')
        number(origin, 0, 86_400_000_000)
        self.transport, self.origin, self.seconds, self.cancelled = transport, origin, seconds, cancelled
        self.rows = [None] * int(seconds / 2)
        self.failure = None
        self.thread = threading.Thread(target=self.run, name='agat-primary-arrival-scheduler')

    def run(self):
        capacity = threading.BoundedSemaphore(1); futures = []
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                def one(index, base):
                    try:
                        began = time.monotonic()
                        value = measure_primary(self.transport)
                        self.rows[index] = {**base, 'startedMs': round((began - self.origin) * 1000, 3),
                            'finishedMs': round((time.monotonic() - self.origin) * 1000, 3), **value}
                    finally: capacity.release()
                def dispatch(index, due, observed, reason):
                    base = {'index': index, 'scheduledMs': round(due * 1000, 3), 'dispatchMs': round(observed * 1000, 3)}
                    if reason or not capacity.acquire(blocking=False):
                        self.rows[index] = {**base, 'status': 'dropped', 'reason': reason or 'client_capacity'}
                    else: futures.append(pool.submit(one, index, base))
                drive_arrivals(.5, len(self.rows), 1, 100, dispatch, start=self.origin, cancelled=self.cancelled)
                for future in futures: future.result()
        except Exception as error: self.failure = type(error).__name__

    def start(self): self.thread.start()

    def finish(self):
        # Fixed schedule is at most 30 seconds; each admitted HTTP call has a 30-second watchdog.
        self.thread.join(timeout=self.seconds + 35)
        require(not self.thread.is_alive() and self.failure is None and all(r is not None for r in self.rows),
                'Primary scheduler did not finish its bounded inventory')
        return self.rows
