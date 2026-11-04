"""Exercise launcher cleanup/reporting when late filesystem allocation fails."""
from contextlib import ExitStack
import errno
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from test_temporal_cleanup import launcher


class ProcessFixture:
    def __init__(self, pid, returncode=None):
        self.pid, self.returncode = pid, returncode

    def poll(self):
        return self.returncode


class PrivateLogRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='agat-log-retention-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.directory = self.root / 'docs/private/run'
        self.logs = self.root / 'docs/private/temporal-real-rag/private/run'
        self.started = False
        self.processes = []
        fixture = b'{"input":"explicit fixture, no model inference"}'
        (self.root / 'docs').mkdir()
        (self.root / 'docs/fixture.json').write_bytes(fixture)
        model_root = self.root / 'models'
        models = {name: launcher.sha(b'{}') for name in ['qwen3:8b', 'embeddinggemma:latest']}
        for name in models:
            path = model_root / 'manifests/registry.ollama.ai/library' / name.replace(':', '/')
            path.parent.mkdir(parents=True); path.write_bytes(b'{}')
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w') as tar:
            item = tarfile.TarInfo('docs/fixture.json'); item.size = len(fixture)
            tar.addfile(item, io.BytesIO(fixture))
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for target, name, value in [(launcher, 'ROOT', self.root), (launcher, 'SOURCES', ['docs/fixture.json']),
            (launcher.shared, 'FIXTURE', 'docs/fixture.json'), (launcher.shared, 'MODELS', models)]:
            self.stack.enter_context(patch.object(target, name, value))
        self.stack.enter_context(patch.dict(os.environ, {'OLLAMA_MODELS': str(model_root)}))
        self.stack.enter_context(patch.object(launcher.sys, 'argv', ['launcher', '--evidence-dir', str(self.directory)]))
        self.stack.enter_context(patch.object(launcher.platform, 'system', return_value='Darwin'))
        self.stack.enter_context(patch.object(launcher.subprocess, 'check_output', return_value=archive.getvalue()))
        self.stack.enter_context(patch.object(launcher, 'command', side_effect=self.command))
        self.stack.enter_context(patch.object(launcher, 'request', side_effect=self.request))
        self.popen = self.stack.enter_context(patch.object(launcher.subprocess, 'Popen', side_effect=self.start))
        self.stack.enter_context(patch.object(launcher.shared, 'inventory', side_effect=lambda pid: ({pid}, set())))
        self.stack.enter_context(patch.object(launcher.shared, 'stop', side_effect=self.stop))
        self.stack.enter_context(patch('builtins.print'))

    def command(self, args):
        if args[:2] == ['git', 'rev-parse']: return 'a' * 40
        if args[:2] == ['git', 'ls-files']: return ''
        if args[:2] == ['docker', 'ps']: return ''
        if args[:2] == ['docker', 'version']: return 'fixture'
        if args == ['node', '--version']: return 'v24.0.0'
        if args[-1] == 'hw.memsize': return str(32 * 1024**3)
        if args[-1] == 'machdep.cpu.brand_string': return 'fixture'
        raise AssertionError('Unexpected fixture command')

    def request(self, _port, path, body=None, timeout=5):
        if path == '/api/version': return {'version': 'fixture'}
        if path == '/api/ps': return {'models': []}
        if path in ['/api/embed', '/api/chat']:
            return {'model': body['model'], 'total_duration': 100, 'load_duration': 50}
        if path == '/api/generate': return {}
        raise AssertionError('Unexpected fixture request')

    def start(self, args, **kwargs):
        if args == ['ollama', 'serve']:
            self.started = True
            kwargs['stdout'].write(b'fixture model diagnostic\n'); kwargs['stdout'].flush()
            process = ProcessFixture(12345)
        else:
            self.assertEqual(args[:2], ['bash', 'scripts/test-temporal-postgres-rag.sh'])
            kwargs['stdout'].write(b'fixture failed workload\n'); kwargs['stdout'].flush()
            process = ProcessFixture(12346, 73)
        self.processes.append(process)
        return process

    @staticmethod
    def stop(process):
        if process is not None and process.poll() is None:
            process.returncode = 0

    def assert_retained_failure(self):
        self.assertEqual(launcher.main(), 1)
        report = json.loads((self.directory / 'launcher.json').read_text())
        self.assertEqual(report['status'], 'fail')
        self.assertEqual(report['failure']['reason'], 'Live integration tests failed; inspect tests.log')
        self.assertEqual(report['cleanupErrors'], [])
        self.assertEqual(report['remainingOwnedPids'], [])
        log = self.logs / 'ollama.log'
        self.assertEqual(log.read_bytes(), b'fixture model diagnostic\n')
        self.assertEqual(report['logSha256']['ollama.log'], launcher.sha(log.read_bytes()))
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
        self.assertTrue(all(process.poll() is not None for process in self.processes))

    def test_late_directory_allocation_failure_does_not_discard_process_logs_or_report(self):
        original = Path.mkdir
        def mkdir(path, *args, **kwargs):
            if self.started and path == self.logs:
                raise OSError(errno.ENOSPC, 'Injected late allocation failure')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'mkdir', mkdir):
            self.assert_retained_failure()

    def test_late_log_file_allocation_failure_does_not_mask_workload_failure(self):
        original = os.open
        def open_file(path, flags, mode=0o777, **kwargs):
            if self.started and Path(path).parent == self.logs:
                raise OSError(errno.ENOSPC, 'Injected late log allocation failure')
            return original(path, flags, mode, **kwargs)
        with patch.object(os, 'open', side_effect=open_file):
            self.assert_retained_failure()

    def test_private_directory_failure_prevents_starting_any_owned_process(self):
        original = Path.mkdir
        def mkdir(path, *args, **kwargs):
            if path == self.logs:
                raise OSError(errno.ENOSPC, 'Injected admission allocation failure')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'mkdir', mkdir), self.assertRaises(OSError):
            launcher.main()
        self.assertEqual(self.popen.call_count, 0)

    def test_existing_log_is_not_overwritten_and_prevents_process_admission(self):
        self.logs.mkdir(parents=True)
        path = self.logs / 'ollama.log'; path.write_bytes(b'previous private evidence')
        with self.assertRaises(FileExistsError):
            launcher.main()
        self.assertEqual(path.read_bytes(), b'previous private evidence')
        self.assertEqual(self.popen.call_count, 0)

    def test_second_log_allocation_failure_closes_first_handle_before_admission(self):
        original, descriptors = os.open, []
        def open_file(path, flags, mode=0o777, **kwargs):
            if Path(path) == self.logs / 'decision.log':
                raise OSError(errno.ENOSPC, 'Injected second log allocation failure')
            descriptor = original(path, flags, mode, **kwargs)
            if Path(path) == self.logs / 'ollama.log':
                descriptors.append(descriptor)
            return descriptor
        with patch.object(os, 'open', side_effect=open_file), self.assertRaises(OSError):
            launcher.main()
        self.assertEqual(self.popen.call_count, 0)
        self.assertEqual(len(descriptors), 1)
        with self.assertRaises(OSError) as raised:
            os.fstat(descriptors[0])
        self.assertEqual(raised.exception.errno, errno.EBADF)

    def test_report_write_failure_keeps_existing_private_logs_and_stops_processes(self):
        original = launcher.write
        def write(path, value):
            if path.name == 'launcher.json':
                raise OSError(errno.ENOSPC, 'Injected final report allocation failure')
            return original(path, value)
        with patch.object(launcher, 'write', side_effect=write), self.assertRaises(OSError):
            launcher.main()
        self.assertEqual((self.logs / 'ollama.log').read_bytes(), b'fixture model diagnostic\n')
        self.assertFalse((self.directory / 'launcher.json').exists())
        self.assertTrue(all(process.poll() is not None for process in self.processes))


if __name__ == '__main__':
    unittest.main()
