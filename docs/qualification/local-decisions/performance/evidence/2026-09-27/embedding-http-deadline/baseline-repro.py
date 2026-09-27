from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import sys
import threading
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, os.path.join(os.getcwd(), 'workers'))
from agat_worker import CoordinatorClient, LocalModelClient, execute_knowledge_lease

@contextmanager
def slow_endpoint(mode):
    entered, release = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *_args): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            entered.set()
            try:
                if mode == 'headers':
                    release.wait(4)
                self.send_response(500 if mode == 'error' else 200)
                self.send_header('Transfer-Encoding', 'chunked')
                self.end_headers()
                prefix = b'e' if mode == 'error' else json.dumps({'data':[{'index':0,'embedding':[1,0]}]}).encode()
                self.wfile.write(f'{len(prefix):x}\r\n'.encode()+prefix+b'\r\n'); self.wfile.flush()
                while not release.wait(0.03):
                    self.wfile.write(b'1\r\n \r\n'); self.wfile.flush()
                self.wfile.write(b'0\r\n\r\n'); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError): pass
            finally: self.close_connection=True
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01),daemon=True); thread.start()
    try:
        with patch.dict(os.environ, {'NO_PROXY':'127.0.0.1','no_proxy':'127.0.0.1'}):
            yield f'http://127.0.0.1:{server.server_port}/v1',entered,release
    finally:
        release.set(); server.shutdown(); server.server_close(); thread.join(2)

class DeadlineBaseline(unittest.TestCase):
    def test_total_deadline_includes_headers_success_and_error_bodies(self):
        for mode in ['headers','body','error']:
            with self.subTest(mode=mode),slow_endpoint(mode) as (url,entered,release):
                client=LocalModelClient(url,''); client.embedding_timeout=0.3
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future=pool.submit(client.embed,'local',['input'])
                    try:
                        self.assertTrue(entered.wait(2))
                        with self.assertRaisesRegex(RuntimeError,'deadline'):
                            future.result(timeout=1.2)
                    finally: release.set()
    def test_lost_lease_releases_worker_before_model_body_finishes(self):
        with slow_endpoint('body') as (url,entered,release):
            coordinator=Mock(spec=CoordinatorClient)
            def renew(_client,_lease,_stop,cancelled):
                if entered.wait(2): cancelled.set()
            lease={'leaseId':'lease','collection':{'embeddingModel':'local'},'document':{'name':'synthetic'},'chunks':[{'id':'chunk','content':'input'}]}
            with patch('agat_worker.knowledge_lease_renewer',side_effect=renew),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()),ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(execute_knowledge_lease,coordinator,LocalModelClient(url,''),lease,False)
                try:
                    self.assertTrue(entered.wait(2)); future.result(timeout=1.2)
                    self.assertFalse(release.is_set())
                finally: release.set()
            coordinator.knowledge_complete.assert_not_called(); coordinator.knowledge_fail.assert_not_called()

if __name__=='__main__': unittest.main(verbosity=2)
