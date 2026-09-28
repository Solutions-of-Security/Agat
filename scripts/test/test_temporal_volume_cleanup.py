"""Disposable RAG cleanup removes anonymous data, while named data is retained."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from uuid import uuid4

from test_temporal_cleanup import launcher


PROXY = '''import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
docker = os.environ['AGAT_FIXTURE_DOCKER']
if args[0] == 'run':
    name = args[args.index('--name') + 1]
    container = subprocess.check_output([docker, 'run', '--pull', 'never', '--network', 'none',
        '--read-only', '--detach', '--name', name, '--cidfile', os.environ['AGAT_FIXTURE_CID'],
        '--mount', 'type=volume,target=/fixture-anonymous',
        '--mount', 'type=volume,src=' + os.environ['AGAT_FIXTURE_NAMED'] + ',target=/fixture-retained',
        os.environ['AGAT_FIXTURE_IMAGE'], 'sleep', '120'], text=True, timeout=10).strip()
    detail = json.loads(subprocess.check_output([docker, 'inspect', container], text=True, timeout=10))[0]
    anonymous = next(row['Name'] for row in detail['Mounts'] if row['Destination'] == '/fixture-anonymous')
    Path(os.environ['AGAT_FIXTURE_RECORD']).write_text(json.dumps({'id': container, 'name': name, 'anonymous': anonymous,
        'imageId': os.environ['AGAT_FIXTURE_IMAGE']}))
    subprocess.run([docker, 'exec', container, 'sh', '-c', 'printf retained > /fixture-retained/marker'], check=True, timeout=10)
    print(container)
elif args[0] == 'port':
    # A real shell startup failure must run its EXIT trap before returning.
    sys.exit(73)
else:
    os.execv(docker, [docker, *args])
'''


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, timeout=10).strip()


@contextmanager
def volume_resources():
    image = os.environ.get('AGAT_TEST_DOCKER_CLEANUP_IMAGE', 'busybox:1.36')
    image_id = json.loads(docker('image', 'inspect', image))[0]['Id']
    named = 'agat-temporal-retained-' + uuid4().hex
    with tempfile.TemporaryDirectory(prefix='agat-volume-cleanup-') as temporary:
        directory = Path(temporary)
        record, cidfile = directory / 'record.json', directory / 'container.id'
        docker('volume', 'create', named)
        try:
            proxy = directory / 'docker'
            proxy.write_text('#!' + sys.executable + '\n' + PROXY)
            proxy.chmod(0o700)
            npm = directory / 'npm'
            npm.write_text('#!/bin/sh\nexit 0\n'); npm.chmod(0o700)
            environment = {**os.environ, 'PATH': str(directory) + os.pathsep + os.environ['PATH'],
                'AGAT_FIXTURE_DOCKER': shutil.which('docker'), 'AGAT_FIXTURE_CID': str(cidfile),
                'AGAT_FIXTURE_NAMED': named, 'AGAT_FIXTURE_IMAGE': image_id, 'AGAT_FIXTURE_RECORD': str(record)}
            yield proxy, record, named, environment
        finally:
            detail = json.loads(record.read_text()) if record.exists() else {}
            container_id = detail.get('id') or (cidfile.read_text().strip() if cidfile.exists() else None)
            volumes = {named}
            if detail.get('anonymous'):
                volumes.add(detail['anonymous'])
            errors = []
            if container_id:
                try:
                    inspected = subprocess.run(['docker', 'inspect', container_id], text=True,
                        capture_output=True, timeout=10, check=False)
                    if inspected.returncode == 0:
                        volumes.update(row['Name'] for row in json.loads(inspected.stdout)[0]['Mounts'] if row['Type'] == 'volume')
                    subprocess.run(['docker', 'rm', '--force', '--volumes', container_id],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
                except (OSError, subprocess.SubprocessError) as error:
                    errors.append(type(error).__name__)
            for volume in volumes:
                try:
                    subprocess.run(['docker', 'volume', 'rm', volume], stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=10, check=False)
                except (OSError, subprocess.SubprocessError) as error:
                    errors.append(type(error).__name__)
            remaining = set(docker('volume', 'ls', '--quiet').splitlines()) & volumes
            alive = set(docker('ps', '--all', '--no-trunc', '--format', '{{.ID}}').splitlines())
            if errors or remaining or container_id in alive:
                raise RuntimeError('Owned volume fixture cleanup failed')


@unittest.skipUnless(os.environ.get('AGAT_TEST_DOCKER_CLEANUP') == '1', 'Requires explicit owned Docker fixtures')
class TemporalVolumeCleanupTests(unittest.TestCase):
    def assert_disposable_data_removed(self, record, named):
        detail = json.loads(record.read_text())
        self.assertFalse(detail['id'] in docker('ps', '--all', '--no-trunc', '--format', '{{.ID}}').splitlines(),
                         'Cleanup left the owned container present')
        volumes = set(docker('volume', 'ls', '--quiet').splitlines())
        self.assertTrue(named in volumes, 'Cleanup removed explicitly named data')
        self.assertFalse(detail['anonymous'] in volumes, 'Cleanup leaked its disposable anonymous volume')
        self.assertEqual(docker('run', '--rm', '--pull', 'never', '--network', 'none', '--read-only',
            '--mount', 'type=volume,src=' + named + ',target=/data,readonly', detail['imageId'],
            'cat', '/data/marker'), 'retained')

    def test_python_fallback_removes_anonymous_data_and_preserves_named_data(self):
        with volume_resources() as (proxy, record, named, environment):
            name = f'agat-temporal-postgres-rag-{os.getpid()}-{time.time_ns()}'
            subprocess.run([str(proxy), 'run', '--name', name], env=environment,
                stdout=subprocess.DEVNULL, check=True, timeout=20)
            errors = []
            launcher.cleanup_owned_containers({os.getpid()}, {name}, errors)
            self.assertEqual(errors, [])
            self.assert_disposable_data_removed(record, named)

    def run_shell_failure(self, script):
        with volume_resources() as (_proxy, record, named, environment):
            result = subprocess.run(['bash', str(launcher.ROOT / 'scripts' / script)], cwd=launcher.ROOT,
                env=environment, capture_output=True, text=True, timeout=30, check=False)
            self.assertEqual(result.returncode, 73, result.stderr)
            self.assert_disposable_data_removed(record, named)

    def test_postgres_launcher_exit_trap_removes_its_disposable_data(self):
        self.run_shell_failure('test-temporal-postgres-rag.sh')

    def test_temporal_launcher_exit_trap_removes_its_disposable_data(self):
        self.run_shell_failure('test-temporal-rag.sh')


if __name__ == '__main__':
    unittest.main()
