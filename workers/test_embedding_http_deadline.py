"""Real transport deadlines, cancellation, cleanup and urllib compatibility."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import base64
import io
import json
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import embedding_http
from agat_worker import CoordinatorClient, LocalModelClient, execute_knowledge_lease, parse_args

@contextmanager
def slow_endpoint(mode):
    entered, release = (threading.Event(), threading.Event())

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            entered.set()
            try:
                if mode == 'headers':
                    release.wait(4)
                self.send_response(500 if mode == 'error' else 200)
                self.send_header('Transfer-Encoding', 'chunked')
                self.end_headers()
                prefix = b'e' if mode == 'error' else json.dumps({'data': [{'index': 0, 'embedding': [1, 0]}]}).encode()
                self.wfile.write(f'{len(prefix):x}\r\n'.encode() + prefix + b'\r\n')
                self.wfile.flush()
                while not release.wait(0.03):
                    self.wfile.write(b'1\r\n \r\n')
                    self.wfile.flush()
                self.wfile.write(b'0\r\n\r\n')
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                self.close_connection = True
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        with patch.dict(os.environ, {'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'}):
            yield (f'http://127.0.0.1:{server.server_port}/v1', entered, release)
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)

class EmbeddingDeadlineTests(unittest.TestCase):

    def test_total_deadline_includes_headers_success_and_error_bodies(self):
        for mode in ['headers', 'body', 'error']:
            with self.subTest(mode=mode), slow_endpoint(mode) as (url, entered, release):
                client = LocalModelClient(url, '', embedding_timeout=0.75)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(client.embed, 'local', ['input'])
                    try:
                        self.assertTrue(entered.wait(2))
                        with self.assertRaisesRegex(RuntimeError, 'deadline'):
                            future.result(timeout=2)
                    finally:
                        release.set()

    def test_lost_lease_releases_worker_before_model_body_finishes(self):
        with slow_endpoint('body') as (url, entered, release):
            coordinator = Mock(spec=CoordinatorClient)

            def renew(_client, _lease, _stop, cancelled):
                if entered.wait(2):
                    cancelled.set()
            lease = {'leaseId': 'lease', 'collection': {'embeddingModel': 'local'}, 'document': {'name': 'synthetic'}, 'chunks': [{'id': 'chunk', 'content': 'input'}]}
            with patch('agat_worker.knowledge_lease_renewer', side_effect=renew), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(execute_knowledge_lease, coordinator, LocalModelClient(url, ''), lease, False)
                try:
                    self.assertTrue(entered.wait(2))
                    future.result(timeout=2)
                    self.assertFalse(release.is_set())
                finally:
                    release.set()
            coordinator.knowledge_complete.assert_not_called()
            coordinator.knowledge_fail.assert_not_called()

@contextmanager
def endpoint(handler, tls_context=None):
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    if tls_context:
        server.socket = tls_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
        if thread.is_alive():
            raise AssertionError('HTTP fixture thread survived cleanup')

@contextmanager
def observed_processes():
    children = []
    create = subprocess.Popen

    def start(*args, **kwargs):
        child = create(*args, **kwargs)
        children.append(child)
        return child
    try:
        with patch('embedding_http.subprocess.Popen', side_effect=start):
            yield children
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate()
                raise AssertionError('Embedding helper survived its request')
            if not child.stdin.closed or not child.stdout.closed:
                raise AssertionError('Embedding helper pipes remained open')

class CompatibilityTests(unittest.TestCase):

    def test_precancelled_request_does_not_spawn(self):
        cancelled = threading.Event()
        cancelled.set()
        with observed_processes() as children:
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                LocalModelClient('http://unused.invalid/v1', 'secret').embed('local', ['input'], cancelled=cancelled)
        self.assertEqual(children, [])

    def test_stalled_helper_is_killed_reaped_and_leaves_no_credentials_on_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / 'blocked-helper.py'
            helper.write_text('import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(30)\n')
            with patch.object(embedding_http, '__file__', str(helper)), observed_processes() as children:
                started = time.monotonic()
                with self.assertRaisesRegex(RuntimeError, 'deadline'):
                    LocalModelClient('http://unused.invalid/v1', 'private-test-key', embedding_timeout=0.25).embed('local', ['private-test-input'])
                self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(len(children), 1)
            self.assertNotEqual(children[0].returncode, 0)
            self.assertNotIn('private-test', ' '.join(children[0].args))

    def test_cancelled_slow_response_reaps_helper_before_return(self):
        for mode in ('headers', 'body', 'error'):
            with self.subTest(mode=mode), slow_endpoint(mode) as (url, entered, release), observed_processes() as children:
                cancelled = threading.Event()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(LocalModelClient(url, '').embed, 'local', ['input'], cancelled=cancelled)
                    try:
                        self.assertTrue(entered.wait(2))
                        cancelled.set()
                        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                            future.result(timeout=2)
                        self.assertFalse(release.is_set())
                        self.assertEqual(len(children), 1)
                        self.assertIsNotNone(children[0].poll())
                    finally:
                        release.set()

    def test_timeout_configuration_is_finite_and_propagates_from_cli_and_environment(self):
        for value in ('0', '-1', 'nan', 'inf', '901', 'invalid'):
            with self.subTest(value=value), patch.dict(os.environ, {'AGAT_EMBEDDING_TIMEOUT': value}), patch.object(sys, 'argv', ['worker']), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parse_args()
        with patch.dict(os.environ, {'AGAT_EMBEDDING_TIMEOUT': '2.5'}), patch.object(sys, 'argv', ['worker']):
            self.assertEqual(parse_args().embedding_timeout, 2.5)
        with patch.dict(os.environ, {'AGAT_EMBEDDING_TIMEOUT': '2.5'}), patch.object(sys, 'argv', ['worker', '--embedding-timeout', '4']):
            self.assertEqual(parse_args().embedding_timeout, 4)
        for value in (0, -1, float('nan'), float('inf'), 901, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                LocalModelClient('http://unused.invalid/v1', '', embedding_timeout=value)

    def test_http_proxy_auth_and_model_headers_are_preserved(self):
        received = []

        class Proxy(BaseHTTPRequestHandler):

            def log_message(self, *_args):
                pass

            def do_POST(self):
                received.append((self.path, dict(self.headers), json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                body = json.dumps({'data': [{'index': 0, 'embedding': [1, 0]}]}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        with endpoint(Proxy) as port, patch.dict(os.environ, {'http_proxy': f'http://proxy-user:proxy-password@127.0.0.1:{port}', 'HTTP_PROXY': '', 'no_proxy': '', 'NO_PROXY': ''}), observed_processes() as children:
            self.assertEqual(LocalModelClient('http://model.agat.invalid/v1', 'model-secret').embed('local', ['Пример']), [[1, 0]])
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0].returncode, 0)
        self.assertEqual(received[0][0], 'http://model.agat.invalid/v1/embeddings')
        headers = received[0][1]
        self.assertEqual(headers['Authorization'], 'Bearer model-secret')
        self.assertEqual(headers['Proxy-Authorization'], 'Basic ' + base64.b64encode(b'proxy-user:proxy-password').decode())
        self.assertEqual(received[0][2], {'model': 'local', 'input': ['Пример']})

    def test_urllib_redirect_behavior_is_preserved(self):
        received = []

        class Redirect(BaseHTTPRequestHandler):

            def log_message(self, *_args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                received.append(('POST', self.path))
                self.send_response(303)
                self.send_header('Location', '/result')
                self.send_header('Content-Length', '0')
                self.end_headers()

            def do_GET(self):
                received.append(('GET', self.path))
                body = b'{"data":[{"index":0,"embedding":[1,0]}]}'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        with endpoint(Redirect) as port, patch.dict(os.environ, {'no_proxy': '127.0.0.1', 'NO_PROXY': '127.0.0.1'}), observed_processes():
            self.assertEqual(LocalModelClient(f'http://127.0.0.1:{port}/v1', '').embed('local', ['input']), [[1, 0]])
        self.assertEqual(received, [('POST', '/v1/embeddings'), ('GET', '/result')])

    def test_https_verifies_certificate_and_uses_configured_ca(self):
        with tempfile.TemporaryDirectory() as directory:
            cert = Path(directory) / 'ca.pem'
            key = Path(directory) / 'key.pem'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1', '-keyout', str(key), '-out', str(cert)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.load_cert_chain(cert, key)
            received = []

            class Secure(BaseHTTPRequestHandler):

                def log_message(self, *_args):
                    pass

                def do_POST(self):
                    received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                    body = b'{"data":[{"index":0,"embedding":[1,0]}]}'
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
            with endpoint(Secure, tls) as port, patch.dict(os.environ, {'no_proxy': 'localhost,127.0.0.1', 'NO_PROXY': 'localhost,127.0.0.1'}):
                client = LocalModelClient(f'https://localhost:{port}/v1', '', embedding_timeout=3)
                with observed_processes(), self.assertRaisesRegex(RuntimeError, 'CERTIFICATE_VERIFY_FAILED'):
                    client.embed('local', ['input'])
                with patch.dict(os.environ, {'SSL_CERT_FILE': str(cert)}), observed_processes():
                    self.assertEqual(client.embed('local', ['input']), [[1, 0]])
            self.assertEqual(received, [{'model': 'local', 'input': ['input']}])

    def test_redirects_share_one_total_deadline(self):
        received = []

        class RedirectChain(BaseHTTPRequestHandler):

            def log_message(self, *_args):
                pass

            def respond(self):
                received.append(self.path)
                time.sleep(0.15)
                self.send_response(303)
                self.send_header('Location', f'/hop/{len(received)}')
                self.send_header('Content-Length', '0')
                self.end_headers()

            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.respond()

            def do_GET(self):
                self.respond()
        with endpoint(RedirectChain) as port, patch.dict(os.environ, {'no_proxy': '127.0.0.1', 'NO_PROXY': '127.0.0.1'}), observed_processes() as children:
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, 'deadline'):
                LocalModelClient(f'http://127.0.0.1:{port}/v1', '', embedding_timeout=0.75).embed('local', ['input'])
            self.assertLess(time.monotonic() - started, 2)
        self.assertGreaterEqual(len(received), 2)
        self.assertEqual(len(children), 1)
        self.assertNotEqual(children[0].returncode, 0)

    def test_concurrent_cancellation_does_not_close_another_requests_transport(self):
        with slow_endpoint('body') as (cancel_url, cancel_entered, cancel_release), slow_endpoint('body') as (keep_url, keep_entered, keep_release), observed_processes() as children:
            cancelled = threading.Event()
            with ThreadPoolExecutor(max_workers=2) as pool:
                refused = pool.submit(LocalModelClient(cancel_url, '').embed, 'local', ['cancel'], cancelled=cancelled)
                retained = pool.submit(LocalModelClient(keep_url, '').embed, 'local', ['keep'])
                try:
                    self.assertTrue(cancel_entered.wait(2))
                    self.assertTrue(keep_entered.wait(2))
                    cancelled.set()
                    with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                        refused.result(timeout=2)
                    self.assertFalse(retained.done())
                    keep_release.set()
                    self.assertEqual(retained.result(timeout=2), [[1, 0]])
                finally:
                    cancel_release.set()
                    keep_release.set()
        self.assertEqual(len(children), 2)
        self.assertEqual(sum((child.returncode == 0 for child in children)), 1)
if __name__ == '__main__':
    unittest.main(verbosity=2)
