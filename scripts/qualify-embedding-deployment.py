"""Offline deployment qualification: no Compose services or Kubernetes mutations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

if not __debug__:
    raise RuntimeError('Assertions must be enabled')

p = argparse.ArgumentParser()
p.add_argument('--root', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
group = p.add_mutually_exclusive_group(required=True)
group.add_argument('--image')
group.add_argument('--host', action='store_true')
a = p.parse_args()
if a.output.exists() or a.output.with_suffix('.render.json').exists():
    p.error('output paths must be new; existing evidence is never overwritten')
root = a.root.resolve()
env = {k: v for k, v in os.environ.items()
       if not k.startswith(('AGAT_', 'OTEL_', 'COMPOSE_', 'NODE_OPTIONS', 'PYTHONPATH'))}
keys = ('AGAT_EMBEDDING_TRANSPORT', 'AGAT_EMBEDDING_TIMEOUT', 'AGAT_EMBEDDING_IDLE_TIMEOUT')
defaults = dict(zip(keys, ('isolated', '900', '0')))

def run(args, *, data=None, extra=None):
    result = subprocess.run(args, cwd=root, env={**env, **(extra or {})}, input=data,
                            text=True, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(f'{args[:3]} failed ({result.returncode}): {result.stderr[-2000:]}')
    return result.stdout

def selected(data):
    return {key: data.get(key) for key in keys}

files = ['.env.example', 'docker-compose.yml', 'scripts/k8s-up.sh',
         'scripts/qualify-embedding-deployment.py',
         'deploy/k8s/docker-desktop/worker-configmap.yaml',
         'deploy/k8s/docker-desktop/worker-deployment.yaml',
         'apps/coordinator/src/local-workers.ts']
modules = ['agat_worker.py', 'web_tools.py', 'telemetry.py', 'local_decisions.py',
           'embedding_http.py', 'embedding_transport.py']
files.extend('workers/' + name for name in modules)
source_hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files}
example = dict(line.split('=', 1) for line in (root / '.env.example').read_text().splitlines()
               if '=' in line and not line.startswith('#'))
assert selected(example) == defaults
script = (root / 'scripts/k8s-up.sh').read_text()
patch_assignment = script[script.index('worker_config_patch="$('):script.index('[[ -n "${coordinator_otel_patch}"')]
patch_command = script[script.index('kubectl patch configmap/agat-worker-config'):script.index('unset coordinator_otel_patch')]
assert 'kubectl' not in patch_assignment
assert patch_command.count('kubectl') == 1
# Only the patch producer and one patch invocation are executed. kubectl is a shell
# function that prints argv; the rest of k8s-up (including apply/secrets) never runs.
patch_shell = '''set -euo pipefail
namespace=agat
otel_enabled=false
otel_exporter_endpoint=
kubectl() { node --input-type=module -e 'process.stdout.write(JSON.stringify(process.argv.slice(1)))' "$@"; }
''' + patch_assignment + patch_command.replace(' >/dev/null', '')
rendered = run(['kubectl', 'kustomize', 'deploy/k8s/docker-desktop'])
objects = json.loads(run(['/usr/bin/ruby', '-ryaml', '-rjson', '-e',
                         'puts JSON.generate(YAML.load_stream(STDIN.read))'], data=rendered))
configmap = next(x for x in objects if x['kind'] == 'ConfigMap' and x['metadata']['name'] == 'agat-worker-config')
base = next(x for x in objects if x['kind'] == 'Deployment' and x['metadata']['name'] == 'agat-worker')
assert selected(configmap['data']) == defaults
managed = json.loads(run(['node', '--import', 'tsx', '--input-type=module', '-e', '''
import { buildWorkerDeployment } from './apps/coordinator/src/local-workers.ts';
const options = {namespace:'agat', workerImage:'agat-local/worker:test',
  workerConfigMap:'agat-worker-config', workerSecret:'agat-secrets',
  modelBaseUrl:'http://host.docker.internal:11434/v1', modelDiscoveryUrl:'',
  embeddingModels:'embeddinggemma', defaultWebEnabled:false, maxWorkersPerLaunch:8};
console.log(JSON.stringify(buildWorkerDeployment(options,
  {name:'Qualification',model:'qwen3:8b',workers:1,concurrency:1,webEnabled:false},
  'abcdef123456','2026-09-28T00:00:00.000Z',0)));
''']))

def resolve_worker(deployment, data):
    container = next(x for x in deployment['spec']['template']['spec']['containers'] if x['name'] == 'worker')
    assert container['envFrom'] == [{'configMapRef': {'name': 'agat-worker-config'}}]
    assert not any(x['name'] in keys for x in container['env'])
    return selected(data)

cases = [
    ('defaults', {}, True),
    ('session-idle', dict(zip(keys, ('session', '45', '30'))), True),
    ('session-rollback', dict(zip(keys, ('session', '45', '0'))), True),
    ('isolated-rollback', defaults, True),
    ('fractional', dict(zip(keys, ('session', '10.5', '0.125'))), True),
    ('empty-interpolation', dict.fromkeys(keys, ''), True),
    ('bad-transport', {keys[0]: 'pooled'}, False),
    ('timeout-zero', {keys[1]: '0'}, False),
    ('timeout-high', {keys[1]: '901'}, False),
    ('timeout-nan', {keys[1]: 'nan'}, False),
    ('timeout-inf', {keys[1]: 'inf'}, False),
    ('idle-negative', {keys[0]: 'session', keys[2]: '-1'}, False),
    ('idle-high', {keys[0]: 'session', keys[2]: '3601'}, False),
    ('idle-nan', {keys[0]: 'session', keys[2]: 'nan'}, False),
    ('idle-inf', {keys[0]: 'session', keys[2]: 'inf'}, False),
    ('idle-isolated', {keys[2]: '30'}, False),
]
rows = []
for name, deployment in [('kustomize-base', base), ('kustomize-managed', managed)]:
    rows.append({'id': name, 'env': resolve_worker(deployment, configmap['data']), 'valid': True})
with tempfile.TemporaryDirectory(prefix='agat-embedding-deployment-') as temp:
    envfile = Path(temp) / 'fixture.env'
    for name, values, valid in cases:
        envfile.write_text('AGAT_ADMIN_TOKEN=fixture-admin-token\n'
                           'AGAT_ENROLLMENT_TOKEN=fixture-enrollment-token\n'
                           'AGAT_CREDENTIALS_KEY=fixture-local-only-key\n' +
                           ''.join(f'{k}={v}\n' for k, v in values.items()))
        compose = json.loads(run(['docker', 'compose', '--env-file', str(envfile),
                                 '--profile', 'worker', 'config', '--format', 'json']))
        expected = {key: values.get(key) or defaults[key] for key in keys}
        actual = selected(compose['services']['worker']['environment'])
        assert actual == expected, (name, actual, expected)
        rows.append({'id': 'compose-' + name, 'env': actual, 'valid': valid})
        argv = json.loads(run(['bash', '-c', patch_shell], extra=values))
        assert argv[:2] == ['patch', 'configmap/agat-worker-config']
        assert '--type=merge' in argv and argv[argv.index('--namespace') + 1] == 'agat'
        patch = json.loads(argv[argv.index('--patch') + 1])['data']
        assert selected(patch) == expected, (name, patch, expected)
        assert patch['AGAT_OTEL_ENABLED'] == 'false'
        assert patch['OTEL_SERVICE_NAME'] == 'agat-worker'
        for kind, deployment in [('base', base), ('managed', managed)]:
            applied = {**configmap['data'], **patch}
            rows.append({'id': 'k8s-up-' + kind + '-' + name,
                         'env': resolve_worker(deployment, applied), 'valid': valid})

container_script = r'''
import contextlib, hashlib, io, json, os, platform, sys
from pathlib import Path
module_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/agat')
sys.path.insert(0, str(module_root))
import agat_worker
assert Path(agat_worker.__file__).resolve() == module_root / 'agat_worker.py'
request = json.load(sys.stdin)
for name, expected in request['modules'].items():
    assert hashlib.sha256((module_root / name).read_bytes()).hexdigest() == expected, name
results = []
for row in request['rows']:
    for key in list(os.environ):
        if key.startswith(('AGAT_', 'OTEL_')): del os.environ[key]
    os.environ.update(row['env'])
    os.environ.update(AGAT_MODEL_DISCOVERY='off', AGAT_OTEL_ENABLED='false')
    sys.argv = ['agat_worker.py']
    error = io.StringIO()
    try:
        with contextlib.redirect_stderr(error): config = agat_worker.parse_args()
    except SystemExit as failure:
        assert not row['valid'] and failure.code == 2, (row['id'], error.getvalue())
        results.append({**row, 'status':'rejected', 'exitCode': failure.code,
                        'error':error.getvalue().splitlines()[-1]})
    else:
        assert row['valid'], row['id']
        actual = [config.embedding_transport, config.embedding_timeout, config.embedding_idle_timeout]
        expected = [row['env']['AGAT_EMBEDDING_TRANSPORT'], float(row['env']['AGAT_EMBEDDING_TIMEOUT']),
                    float(row['env']['AGAT_EMBEDDING_IDLE_TIMEOUT'])]
        assert actual == expected, (row['id'], actual, expected)
        results.append({**row, 'status':'accepted', 'parsed':actual})
print(json.dumps({'python':platform.python_version(), 'modulePath':'/opt/agat/agat_worker.py' if module_root == Path('/opt/agat') else 'workers/agat_worker.py', 'rows':results}))
'''
a.output.with_suffix('.render.json').write_text(json.dumps({'status':'render-pass', 'cases':len(cases), 'rows':rows, 'sourceSha256':source_hashes}, indent=2) + '\n')
request_data = json.dumps({'rows': rows, 'modules': {n:source_hashes['workers/' + n] for n in modules}})
image_id = None
if a.host:
    actual = json.loads(run([sys.executable, '-c', container_script, str(root / 'workers')], data=request_data))
else:
    image_id = run(['docker', 'image', 'inspect', a.image, '--format', '{{.Id}}']).strip()
    actual = json.loads(run(['docker', 'run', '--rm', '--init', '--pull=never', '--network', 'none',
                            '-i', '--entrypoint', 'python', '-e', 'PYTHONDONTWRITEBYTECODE=1',
                            '-e', 'AGAT_OTEL_ENABLED=false', image_id, '-c', container_script], data=request_data))
result = {'status': 'pass', 'baseCommit': run(['git', 'rev-parse', 'HEAD']).strip(),
          'sourceSha256': source_hashes, 'runtime': 'host-source' if a.host else 'linux-image', 'imageId': image_id,
          'composeVersion': run(['docker','compose','version','--short']).strip(),
          'kubectlClient': json.loads(run(['kubectl','version','--client','-o','json'])),
          'cases':len(cases), 'roundTrips':len(rows), 'liveClusterAccess':False,
          'createdServices':False, **actual}
a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'status':result['status'], 'cases':result['cases'], 'roundTrips':result['roundTrips'],
                  'accepted':sum(x['status']=='accepted' for x in actual['rows']),
                  'rejected':sum(x['status']=='rejected' for x in actual['rows']), 'imageId':image_id}))
