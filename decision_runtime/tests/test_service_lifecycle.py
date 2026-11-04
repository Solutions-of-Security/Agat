import contextlib
import http.client
import io
import json
import sys
import threading
import unittest
from unittest.mock import patch

from decision_runtime.__main__ import main
from decision_runtime.contracts import DecisionError, Request, fingerprint
from decision_runtime.engine import DecisionEngine
from decision_runtime.isolated import IsolatedBackend
from decision_runtime.server import make_server
from decision_runtime.tests.test_decisions import Backend, request
from decision_runtime.tests.test_isolated import fixture_factory


class ServiceLifecycleTest(unittest.TestCase):
    def call(self, server, method='POST', raw=None):
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        try:
            connection.request(method,'/health' if method=='GET' else '/v1/decisions',
                               body=None if method=='GET' else json.dumps(raw or request()),
                               headers={'Content-Type':'application/json'})
            response=connection.getresponse()
            return response.status,json.loads(response.read())
        finally: connection.close()

    def test_deadline_delivers_complete_error_then_stops_server_without_retry(self):
        with IsolatedBackend(fixture_factory,{'mode':'hang'},timeout_ms=100) as backend:
            engine=DecisionEngine(backend)
            with make_server(engine,0,exit_on_backend_unavailable=True) as server:
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                try:
                    status,result=self.call(server)
                    self.assertEqual((status,result['reason']),(504,'inference_timeout'))
                    self.assertEqual(result['inputSha256'],Request.from_dict(request()).input_sha256)
                    thread.join(timeout=2)
                    self.assertFalse(thread.is_alive())
                    self.assertTrue(server.backend_failed.is_set())
                    self.assertFalse(backend.is_available())
                finally: server.shutdown();thread.join(timeout=2)

    def test_default_unavailable_health_stays_online_and_returns_profile(self):
        backend=Backend();backend.is_available=lambda:False
        engine=DecisionEngine(backend)
        with make_server(engine,0) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                status,result=self.call(server,'GET')
                self.assertEqual((status,result['status']),(503,'unavailable'))
                self.assertEqual(result['profileSha256'],fingerprint(engine.profile()))
                self.assertFalse(server.backend_failed.is_set());self.assertTrue(thread.is_alive())
            finally: server.shutdown();thread.join(timeout=2)

    def test_idle_child_death_stops_server_without_an_http_request(self):
        with IsolatedBackend(fixture_factory,{'mode':'normal'},timeout_ms=1000) as backend:
            with make_server(DecisionEngine(backend),0,exit_on_backend_unavailable=True) as server:
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                try:
                    backend._process.kill();backend._process.join(timeout=2)
                    thread.join(timeout=2)
                    self.assertFalse(thread.is_alive());self.assertTrue(server.backend_failed.is_set())
                finally: server.shutdown();thread.join(timeout=2)

    def test_close_drains_accepted_response_after_idle_check_notices_failure(self):
        failed=threading.Event();release=threading.Event();closed=threading.Event()
        backend=Backend();backend.is_available=lambda:not failed.is_set()
        def score(_request):
            backend.calls+=1;failed.set()
            if not release.wait(timeout=5): raise AssertionError('test did not release response')
            raise DecisionError('backend_error','fixture')
        backend.score=score
        response=[]
        with make_server(DecisionEngine(backend),0,exit_on_backend_unavailable=True) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            client=threading.Thread(target=lambda:response.append(self.call(server)),daemon=True);client.start()
            closer=None
            try:
                self.assertTrue(failed.wait(timeout=2))
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive());self.assertTrue(server.backend_failed.is_set())
                def close(): server.server_close();closed.set()
                closer=threading.Thread(target=close,daemon=True);closer.start()
                self.assertFalse(closed.wait(timeout=0.1),'response handler was abandoned')
                release.set();client.join(timeout=2);closer.join(timeout=2)
                self.assertFalse(client.is_alive());self.assertTrue(closed.is_set())
                self.assertEqual((response[0][0],response[0][1]['reason']),(500,'backend_error'))
                self.assertEqual(response[0][1]['inputSha256'],Request.from_dict(request()).input_sha256)
                self.assertEqual(backend.calls,1)
            finally:
                release.set();server.shutdown();thread.join(timeout=2);client.join(timeout=2)
                if closer: closer.join(timeout=2)

    def test_bad_input_and_recoverable_backend_error_do_not_restart(self):
        backend=Backend();backend.is_available=lambda:True
        with make_server(DecisionEngine(backend),0,exit_on_backend_unavailable=True) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                self.assertEqual(self.call(server,raw={'invalid':True})[0],400)
                backend.score=lambda _:(_ for _ in ()).throw(DecisionError('context_too_long','fixture'))
                self.assertEqual(self.call(server)[0],422)
                self.assertFalse(server.backend_failed.is_set());self.assertTrue(thread.is_alive())
            finally: server.shutdown();thread.join(timeout=2)

    def test_cli_requires_isolation_and_returns_retryable_exit_after_failure(self):
        with patch.object(sys,'argv',['decision_runtime','serve','--manifest','missing','--exit-on-backend-unavailable']), \
             patch('decision_runtime.mlx_backend.MlxBackend') as backend, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(),1)
        backend.assert_not_called()
        class Stopped:
            server_port=0
            backend_failed=threading.Event()
            def __enter__(self): return self
            def __exit__(self,*_): pass
            def serve_forever(self): self.backend_failed.set()
        backend=Backend();backend.close=lambda:None
        with patch.object(sys,'argv',['decision_runtime','serve','--manifest','fixture','--inference-timeout-ms','100',
                                      '--exit-on-backend-unavailable']), \
             patch('decision_runtime.isolated.IsolatedBackend',return_value=backend), \
             patch('decision_runtime.__main__.make_server',return_value=Stopped()), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(),75)


if __name__=='__main__': unittest.main()
