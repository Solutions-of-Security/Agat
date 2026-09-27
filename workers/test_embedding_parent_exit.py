"""Real child lifetime when its transport owner disappears without cleanup."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import embedding_http

ROOT = Path(embedding_http.__file__).resolve().parent
OWNER = '''
import sys, subprocess
sys.path.insert(0, sys.argv[1])
from agat_worker import LocalModelClient
from embedding_transport import EmbeddingSessionPool
spawn = subprocess.Popen.__init__.__code__
def observe(frame, event, result):
    if frame.f_code is spawn and event == 'return':
        print(frame.f_locals['self'].pid, flush=True)
sys.setprofile(observe)
with EmbeddingSessionPool(1) as pool:
    client = LocalModelClient(sys.argv[2], '', embedding_timeout=30,
        embedding_request=pool.request if sys.argv[3] == 'session' else None)
    client.embed('local', ['owned source'])
'''


def executing(pid):
    if sys.platform.startswith('linux'):
        try:
            # comm is parenthesized and may contain spaces; the following field
            # is state. Zombies have exited but belong to an external reaper.
            return (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
        except FileNotFoundError:
            return False
    result = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True, timeout=2)
    if result.returncode == 1:
        return False
    if result.returncode:
        raise RuntimeError('Process inventory failed')
    return bool(result.stdout.strip()) and not result.stdout.strip().startswith('Z')


@contextmanager
def held_body():
    entered, disconnected, release = threading.Event(), threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200)
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            entered.set()
            try:
                self.wfile.write(b'1\r\n{\r\n')
                self.wfile.flush()
                while not release.wait(.03):
                    self.wfile.write(b'1\r\n \r\n')
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                disconnected.set()
            finally:
                self.close_connection = True

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01))
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', entered, disconnected
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


class ParentBindingTests(unittest.TestCase):
    def test_windows_does_not_use_stale_ppid_as_a_liveness_check(self):
        with patch.object(embedding_http.os, 'name', 'nt'):
            self.assertEqual(embedding_http.parent_watch_arguments(), [])
            with self.assertRaises(SystemExit):
                embedding_http.watch_parent(['--parent-pid', '123'])

    @unittest.skipUnless(os.name == 'posix', 'Unix parent reparenting contract')
    def test_owner_identity_is_supplied_before_spawn(self):
        self.assertEqual(embedding_http.parent_watch_arguments(), ['--parent-pid', str(os.getpid())])
        for module, prefix in [('embedding_http.py', []), ('embedding_transport.py', ['--serve'])]:
            with self.subTest(module=module):
                # The worker could die before imports finish. A child must not
                # adopt getppid() at that later point as its intended owner.
                result = subprocess.run([sys.executable, str(ROOT / module), *prefix, '--parent-pid', str(os.getpid() + 1)],
                                        input=b'', stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'parent exited before startup', result.stderr)

    @unittest.skipUnless(os.name == 'posix', 'Unix parent reparenting contract')
    def test_bad_parent_binding_is_rejected_before_request_io(self):
        for arguments in [['--parent-pid'], ['--parent-pid', '0'], ['--parent-pid', '-1'],
                          ['--parent-pid', 'x'], ['--parent-pid', '99999999999'], ['--unknown', '1']]:
            for module, prefix in [('embedding_http.py', []), ('embedding_transport.py', ['--serve'])]:
                with self.subTest(arguments=arguments, module=module):
                    result = subprocess.run([sys.executable, str(ROOT / module), *prefix, *arguments],
                                            input=b'', stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, b'')
                    self.assertIn(b'Invalid embedding helper parent binding', result.stderr)

    @unittest.skipUnless(os.name == 'posix', 'Unix parent reparenting contract')
    def test_sigkill_owner_closes_body_and_exits_both_real_transports(self):
        for mode in ('isolated', 'session'):
            with self.subTest(mode=mode), held_body() as (url, entered, disconnected):
                parent = subprocess.Popen([sys.executable, '-u', '-c', OWNER, str(ROOT), url, mode],
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                          env={**os.environ, 'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1',
                                               'AGAT_OTEL_ENABLED': 'false', 'OTEL_SDK_DISABLED': 'true'})
                helper = None
                try:
                    self.assertTrue(select.select([parent.stdout], [], [], 5)[0], 'Helper PID was not observed')
                    helper = int(parent.stdout.readline(30))
                    self.assertTrue(entered.wait(5))
                    self.assertIsNone(parent.poll())
                    parent.kill()
                    self.assertEqual(parent.wait(3), -signal.SIGKILL)
                    self.assertTrue(disconnected.wait(3), 'Orphan retained model HTTP after parent SIGKILL')
                    deadline = time.monotonic() + 3
                    while executing(helper) and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertFalse(executing(helper), 'Helper remained running after owner exit')
                finally:
                    if parent.poll() is None:
                        parent.kill()
                    parent.wait(3)
                    parent.stdout.close()
                    if helper is not None and executing(helper):
                        os.kill(helper, signal.SIGKILL)


if __name__ == '__main__':
    unittest.main()
