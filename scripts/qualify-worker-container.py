#!/usr/bin/env python3
"""Exercise the real worker image entrypoint on a private synthetic HTTP fixture."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid


def serve_fixture():
    lock = threading.Lock()
    releases = [threading.Event(), threading.Event()]
    state = {'registered': 0, 'heartbeats': 0, 'issued': [], 'modelCalls': 0,
             'completed': [], 'failed': [], 'errors': [], 'armed': False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, body=None, status=200, hold=None):
            raw = json.dumps(body).encode() if body is not None else b''
            self.send_response(status)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            if hold is not None and not hold.wait(60):
                with lock:
                    state['errors'].append('fixture_hold_timeout')
                return
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                with lock:
                    state['errors'].append('client_disconnected_before_release')

        def do_GET(self):
            with lock:
                value = json.loads(json.dumps(state))
            self.reply(value if self.path == '/state' else {'error': 'unexpected path'},
                       200 if self.path == '/state' else 404)

        def do_POST(self):
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= 32768:
                return self.reply({'error': 'too large'}, 413)
            body = json.loads(self.rfile.read(length) or '{}')
            response, status, hold = None, 204, None
            with lock:
                if self.path == '/control':
                    action = body.get('action')
                    if action == 'arm2':
                        state['armed'] = True
                    elif action in ('release1', 'release2'):
                        releases[int(action[-1]) - 1].set()
                    else:
                        state['errors'].append('unexpected_control')
                elif self.path == '/api/v1/workers/register':
                    state['registered'] += 1
                    response, status = {'id': 'synthetic-node', 'token': 'synthetic-node-token'}, 200
                elif self.path == '/v1/embeddings':
                    state['modelCalls'] += 1
                    index = state['modelCalls']
                    if index not in (1, 2) or body != {'model': 'synthetic', 'input': [f'synthetic chunk {index}']}:
                        state['errors'].append('unexpected_model_request')
                        response, status = {'error': 'unexpected model request'}, 400
                    else:
                        response, status = {'data': [{'index': 0, 'embedding': [1, 0] if index == 1 else [0, 1]}]}, 200
                        hold = releases[index - 1]
                elif self.headers.get('Authorization') != 'Bearer synthetic-node-token':
                    state['errors'].append('missing_worker_auth')
                    response, status = {'error': 'invalid fixture token'}, 401
                elif self.path == '/api/v1/workers/heartbeat':
                    state['heartbeats'] += 1
                elif self.path == '/api/v1/workers/lease':
                    pass
                elif self.path == '/api/v1/workers/knowledge/lease':
                    index = len(state['issued']) + 1
                    if index == 1 or (index == 2 and state['armed']):
                        state['issued'].append(f'lease-{index}')
                        response, status = {'leaseId': f'lease-{index}', 'collection': {'embeddingModel': 'synthetic'},
                            'document': {'name': 'synthetic'},
                            'chunks': [{'id': f'chunk-{index}', 'content': f'synthetic chunk {index}'}]}, 200
                elif self.path.startswith('/api/v1/workers/knowledge/leases/') and self.path.endswith('/complete'):
                    identity = self.path.split('/')[-2]
                    index = int(identity.split('-')[-1])
                    expected = {'embeddings': [{'chunkId': f'chunk-{index}', 'embedding': [1, 0] if index == 1 else [0, 1]}]}
                    if identity not in state['issued'] or identity in state['completed'] or body != expected:
                        state['errors'].append('invalid_or_duplicate_completion')
                    state['completed'].append(identity)
                    response, status = {'remainingChunks': 0}, 200
                elif self.path.endswith('/fail'):
                    state['failed'].append(self.path.split('/')[-2])
                    response, status = {}, 200
                elif self.path.endswith('/renew'):
                    pass
                else:
                    state['errors'].append('unexpected_worker_path')
                    response, status = {'error': 'unexpected worker path'}, 404
            self.reply(response, status, hold)

    server = ThreadingHTTPServer(('0.0.0.0', 8080), Handler)
    server.serve_forever()


CONTROL = '''import json,sys,urllib.request
origin='http://127.0.0.1:8080'
action=sys.argv[1]
if action=='state':
    with urllib.request.urlopen(origin+'/state',timeout=2) as r: print(r.read().decode())
else:
    req=urllib.request.Request(origin+'/control',data=json.dumps({'action':action}).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=2) as r: r.read()
'''
SNAPSHOT = '''from pathlib import Path
import json
root=Path('/proc')
def status(pid):
    return dict(line.split(':',1) for line in (root/str(pid)/'status').read_text().splitlines() if ':' in line)
helpers=[]
for item in root.iterdir():
    if not item.name.isdecimal(): continue
    try:
        argv=(item/'cmdline').read_bytes().split(b'\\0')
        if any(name in argv for name in (b'/opt/agat/embedding_http.py',b'/opt/agat/embedding_transport.py')):
            s=status(item.name)
            helpers.append({'pid':int(item.name),'ppid':int(s['PPid']),'state':s['State'].strip()})
    except (FileNotFoundError,ProcessLookupError): pass
s=status(1)
print(json.dumps({'pid1Argv':[v.decode() for v in (root/'1/cmdline').read_bytes().split(b'\\0') if v],
                  'pid1Uid':int(s['Uid'].split()[0]),'helpers':helpers}))
'''


def main():
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve-fixture', action='store_true')
    parser.add_argument('--image')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.serve_fixture:
        return serve_fixture()
    if not args.image or not args.output:
        parser.error('--image and --output are required')
    root = Path(__file__).resolve().parents[1]
    directory = args.output.resolve()
    if not directory.is_relative_to(root / 'docs') or directory.exists():
        parser.error('use a new evidence directory under docs')
    directory.mkdir(parents=True)

    def docker(*arguments, timeout=20, check=True):
        result = subprocess.run(['docker', *arguments], capture_output=True, text=True, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError(f'docker {arguments[0]} failed: {result.stderr[-800:]}')
        return result

    def until(function, predicate, *, seconds=10):
        end = time.monotonic() + seconds
        while True:
            value = function()
            if predicate(value):
                return value
            if time.monotonic() > end:
                raise TimeoutError('container qualification condition did not become true')
            time.sleep(.1)

    image = json.loads(docker('image', 'inspect', args.image).stdout)[0]
    assert image['Os'] == 'linux' and image['Config']['User'] == 'agat'
    assert image['Config']['Entrypoint'] == ['python', '/opt/agat/agat_worker.py']
    image_id = image['Id']
    prefix = 'agat-worker-image-' + uuid.uuid4().hex[:10]
    network = prefix + '-net'
    containers, rows, cleanup_errors = [], [], []
    created_network = False
    failure = None
    try:
        docker('network', 'create', '--internal', network)
        created_network = True
        assert json.loads(docker('network', 'inspect', network).stdout)[0]['Internal'] is True
        for index, (mode, idle) in enumerate((('isolated', '0'), ('session', '0'), ('session', '1'))):
            fixture, worker = prefix + f'-fixture-{index}', prefix + f'-worker-{index}'
            docker('run', '--detach', '--pull=never', '--network', network, '--network-alias', 'fixture', '--name', fixture,
                   '--mount', f'type=bind,src={Path(__file__).resolve()},dst=/probe.py,readonly',
                   '--entrypoint', 'python', image_id, '/probe.py', '--serve-fixture')
            containers.append(fixture)

            def control(action='state'):
                result = docker('exec', fixture, 'python', '-c', CONTROL, action)
                return json.loads(result.stdout) if action == 'state' else None

            until(lambda: docker('exec', fixture, 'python', '-c', CONTROL, 'state', check=False).returncode, lambda value: value == 0)
            environment = {'AGAT_COORDINATOR_URL': 'http://fixture:8080', 'AGAT_MODEL_BASE_URL': 'http://fixture:8080/v1',
                'AGAT_ENROLLMENT_TOKEN': 'synthetic-enrollment', 'AGAT_WORKER_MODELS': 'synthetic',
                'AGAT_EMBEDDING_MODELS': 'synthetic', 'AGAT_MODEL_DISCOVERY': 'off', 'AGAT_WEB_ENABLED': 'false',
                'AGAT_OTEL_ENABLED': 'false', 'OTEL_SDK_DISABLED': 'true', 'AGAT_WORKER_CREDENTIALS': '/state/worker.json',
                'AGAT_WORKER_CONCURRENCY': '1', 'AGAT_POLL_INTERVAL': '.2', 'AGAT_EMBEDDING_TRANSPORT': mode,
                'AGAT_EMBEDDING_TIMEOUT': '45', 'AGAT_EMBEDDING_IDLE_TIMEOUT': idle,
                'NO_PROXY': 'fixture,127.0.0.1', 'no_proxy': 'fixture,127.0.0.1'}
            command = ['run', '--detach', '--pull=never', '--network', network, '--name', worker]
            for key, value in environment.items():
                command.extend(['--env', f'{key}={value}'])
            docker(*command, image_id)
            containers.append(worker)

            def snapshot():
                return json.loads(docker('exec', worker, 'python', '-c', SNAPSHOT).stdout)

            def inspect_worker():
                return json.loads(docker('inspect', worker).stdout)[0]

            until(control, lambda state: state['modelCalls'] == 1)
            first = snapshot()
            assert first['pid1Argv'] == ['python', '/opt/agat/agat_worker.py'] and first['pid1Uid'] == 10001
            assert len(first['helpers']) == 1 and first['helpers'][0]['ppid'] == 1
            assert not first['helpers'][0]['state'].startswith('Z')
            control('release1')
            until(control, lambda state: state['completed'] == ['lease-1'])
            between = until(snapshot, lambda snap: len(snap['helpers']) == (1 if mode == 'session' and idle == '0' else 0))
            control('arm2')
            until(control, lambda state: state['modelCalls'] == 2)
            second = snapshot()
            assert len(second['helpers']) == 1 and second['helpers'][0]['ppid'] == 1
            assert not second['helpers'][0]['state'].startswith('Z')
            reused = first['helpers'][0]['pid'] == second['helpers'][0]['pid']
            assert reused is (mode == 'session' and idle == '0')
            docker('kill', '--signal', 'TERM', worker)
            time.sleep(.25)
            while_held = control()
            assert inspect_worker()['State']['Running'] is True
            assert while_held['completed'] == ['lease-1'] and while_held['failed'] == []
            control('release2')
            assert docker('wait', worker, timeout=10).stdout.strip() == '0'
            final = control()
            assert final['completed'] == ['lease-1', 'lease-2'] and final['issued'] == ['lease-1', 'lease-2']
            assert final['modelCalls'] == 2 and final['failed'] == [] and final['errors'] == []
            assert final['registered'] == 1 and final['heartbeats'] >= 1
            stopped = inspect_worker()
            assert stopped['State']['Running'] is False and stopped['State']['ExitCode'] == 0
            rows.append({'transport': mode, 'timeout': 45, 'idleTimeout': float(idle), 'imageId': stopped['Image'],
                         'firstActive': first, 'betweenLeases': between, 'secondActive': second, 'helperReused': reused,
                         'sigtermWaitedForActiveLease': True, 'state': final, 'exitCode': 0,
                         'initEnabled': bool(stopped['HostConfig'].get('Init'))})
            print(f'pass {mode} idle={idle}: real PID 1 drained two leases, helper reused={reused}', flush=True)
            docker('rm', fixture, worker, '--force')
            containers.remove(fixture)
            containers.remove(worker)
    except Exception as error:
        failure = {'type': type(error).__name__, 'message': str(error)[:1000]}
    finally:
        for name in containers:
            log = docker('logs', name, check=False)
            (directory / (name + '.log')).write_text(log.stdout + log.stderr)
            result = docker('rm', '--force', name, check=False)
            if result.returncode:
                cleanup_errors.append('container_removal_failed')
        if created_network and docker('network', 'rm', network, check=False).returncode:
            cleanup_errors.append('network_removal_failed')
    remaining = docker('ps', '--all', '--filter', f'name={prefix}', '--format', '{{.Names}}').stdout.splitlines()
    networks = docker('network', 'ls', '--filter', f'name={network}', '--format', '{{.Name}}').stdout.splitlines()
    report = {'schema': 'agat.worker-container.qualification.v1', 'status': 'pass' if not failure and not cleanup_errors and not remaining and not networks else 'fail',
              'imageId': image_id, 'imageArchitecture': image['Architecture'], 'imageEntrypoint': image['Config']['Entrypoint'],
              'probeSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'fixture': 'synthetic coordinator and model; private internal Docker network; no published ports',
              'rows': rows, 'failure': failure, 'cleanupErrors': cleanup_errors, 'remainingContainers': remaining, 'remainingNetworks': networks}
    with (directory / 'result.json').open('x') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'status': report['status'], 'cases': len(rows), 'failure': failure}), flush=True)
    return 0 if report['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
