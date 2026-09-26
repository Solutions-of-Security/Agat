import contextlib
import http.client
import json
import os
import signal
import socket
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path

from decision_runtime.contracts import fingerprint
from decision_runtime.engine import DecisionEngine
from decision_runtime.isolated import IsolatedBackend, _Cancelled, _receive, _send
from decision_runtime.server import CANCEL_ON_DISCONNECT_HEADER, make_server
from decision_runtime.tests.test_decisions import Backend, request
from decision_runtime.tests.test_isolated import fixture_factory
from workers.local_decisions import LocalDecisionClient


def wait_for(predicate, timeout=2):
    end=time.monotonic()+timeout
    while not predicate():
        if time.monotonic()>=end: raise AssertionError('Expected state did not arrive')
        time.sleep(0.005)


@contextlib.contextmanager
def running_server(backend):
    engine=DecisionEngine(backend)
    with make_server(engine,0) as server:
        errors=[]
        server.handle_error=lambda *_: errors.append('unhandled HTTP error')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try: yield engine,server,errors
        finally:
            server.shutdown();thread.join(timeout=2)


def raw_connection(server, *, cancellation=None):
    body=json.dumps(request(),ensure_ascii=False).encode('utf-8')
    header=(f'POST /v1/decisions HTTP/1.1\r\nHost: 127.0.0.1:{server.server_port}\r\n'
            f'Content-Type: application/json\r\nContent-Length: {len(body)}\r\n')
    if cancellation is not None: header+=f'{CANCEL_ON_DISCONNECT_HEADER}: {cancellation}\r\n'
    sock=socket.create_connection(('127.0.0.1',server.server_port),timeout=3)
    sock.sendall(header.encode('ascii')+b'\r\n'+body)
    return sock


class CancellationTest(unittest.TestCase):
    def test_precancelled_request_does_not_retire_or_dispatch_to_healthy_process(self):
        with tempfile.TemporaryDirectory() as directory:
            entered=Path(directory)/'entered'
            with IsolatedBackend(fixture_factory,{'entered':str(entered)},timeout_ms=1000) as backend:
                cancelled=threading.Event();cancelled.set()
                engine=DecisionEngine(backend)
                result=engine.decide(request(),cancelled=cancelled)
                self.assertEqual(result['reason'],'inference_cancelled')
                self.assertFalse(entered.exists());self.assertTrue(backend.is_available())
                self.assertEqual(engine.decide(request())['status'],'ok')

    def test_inflight_cancellation_kills_noncooperative_process_and_discards_late_result(self):
        with tempfile.TemporaryDirectory() as directory:
            entered=Path(directory)/'entered'; completed=Path(directory)/'completed'
            config={'mode':'hang','ignore_term':True,'entered':str(entered),'completed':str(completed)}
            with IsolatedBackend(fixture_factory,config,timeout_ms=10000) as backend:
                cancelled=threading.Event();results=[];engine=DecisionEngine(backend)
                thread=threading.Thread(target=lambda:results.append(engine.decide(request(),cancelled=cancelled)))
                thread.start()
                try:
                    wait_for(entered.exists);start=time.monotonic();cancelled.set();thread.join(timeout=2)
                    self.assertFalse(thread.is_alive());self.assertLess(time.monotonic()-start,1.5)
                    result=results[0]
                    self.assertEqual((result['status'],result['reason']),('error','inference_cancelled'))
                    self.assertEqual(result['distribution'],[]);self.assertIsNone(result['value'])
                    self.assertFalse(completed.exists());self.assertFalse(backend.is_available())
                    self.assertEqual(backend.diagnostics()['childExitCode'],-signal.SIGKILL)
                    self.assertEqual(backend.diagnostics()['stopReason'],'inference_cancelled')
                    self.assertEqual(engine.decide(request())['reason'],'backend_unavailable')
                    with self.assertRaises(ProcessLookupError):os.kill(backend.diagnostics()['childPid'],0)
                finally:cancelled.set();thread.join(timeout=2)

    def test_cancellation_interrupts_partial_receive_and_backpressured_send(self):
        for mode in ('receive','send'):
            with self.subTest(mode=mode):
                left,right=socket.socketpair();cancelled=threading.Event();failures=[]
                if mode=='receive':right.sendall(b'\x00\x00')
                else:left.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,1024)
                def run():
                    try:
                        if mode=='receive':_receive(left,time.monotonic()+5,cancelled)
                        else:_send(left,{'data':'x'*120000},time.monotonic()+5,cancelled)
                    except BaseException as exc:failures.append(type(exc))
                thread=threading.Thread(target=run);thread.start()
                try:
                    time.sleep(.04);cancelled.set();thread.join(timeout=.5)
                    self.assertFalse(thread.is_alive());self.assertEqual(failures,[_Cancelled])
                finally:cancelled.set();left.close();right.close();thread.join(timeout=1)

    def test_half_close_keeps_ordinary_http_response_but_opt_in_cancels(self):
        for opt_in in (False,True):
            with self.subTest(opt_in=opt_in),tempfile.TemporaryDirectory() as directory:
                entered=Path(directory)/'entered'
                with IsolatedBackend(fixture_factory,{'mode':'slow','entered':str(entered)},timeout_ms=2000) as backend:
                    with running_server(backend) as (_engine,server,errors):
                        sock=raw_connection(server,cancellation='1' if opt_in else None)
                        try:
                            wait_for(entered.exists);sock.shutdown(socket.SHUT_WR)
                            response=http.client.HTTPResponse(sock);response.begin();result=json.loads(response.read())
                            self.assertEqual((response.status,result['reason']),
                                             (500,'inference_cancelled') if opt_in else (200,'accepted'))
                            self.assertEqual(backend.is_available(),not opt_in)
                            self.assertEqual(errors,[])
                        finally:sock.close()

    def test_full_close_or_reset_only_retires_opted_in_active_process(self):
        for reset in (False,True):
            with self.subTest(reset=reset),tempfile.TemporaryDirectory() as directory:
                entered=Path(directory)/'entered'
                with IsolatedBackend(fixture_factory,{'mode':'hang','entered':str(entered)},timeout_ms=10000) as backend:
                    with running_server(backend) as (_engine,server,errors):
                        sock=raw_connection(server,cancellation='1')
                        try:
                            wait_for(entered.exists)
                            if reset:sock.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0))
                            start=time.monotonic();sock.close()
                            wait_for(lambda:backend.diagnostics()['childExitCode'] is not None)
                            self.assertLess(time.monotonic()-start,1)
                            self.assertEqual(backend.diagnostics()['stopReason'],'inference_cancelled')
                            wait_for(lambda:not any(t.name=='decision-peer-cancellation' for t in threading.enumerate()))
                            self.assertEqual(errors,[])
                        finally:sock.close()

    def test_worker_timeout_and_user_cancellation_stop_the_remote_inference(self):
        for mode in ('timeout','cancel'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                entered=Path(directory)/'entered'
                with IsolatedBackend(fixture_factory,{'mode':'hang','entered':str(entered)},timeout_ms=10000) as backend:
                    with running_server(backend) as (engine,server,errors):
                        client=LocalDecisionClient(f'http://127.0.0.1:{server.server_port}')
                        shadow={'profile':'local_decision_shadow_v2','timeoutMs':200 if mode=='timeout' else 5000,
                                'profileSha256':fingerprint(engine.profile()),'request':request()}
                        cancelled=threading.Event();results=[]
                        thread=threading.Thread(target=lambda:results.append(client.decide(shadow,cancelled)));thread.start()
                        try:
                            wait_for(entered.exists)
                            if mode=='cancel':cancelled.set()
                            thread.join(timeout=1);self.assertFalse(thread.is_alive())
                            self.assertEqual(results,[{'status':'unavailable','reason':'timeout' if mode=='timeout' else 'cancelled'}])
                            wait_for(lambda:backend.diagnostics()['childExitCode'] is not None)
                            self.assertEqual(backend.diagnostics()['stopReason'],'inference_cancelled')
                            self.assertEqual(errors,[])
                        finally:cancelled.set();thread.join(timeout=2)

    def test_completed_connection_cannot_cancel_a_subsequent_request(self):
        with IsolatedBackend(fixture_factory,{'mode':'slow'},timeout_ms=2000) as backend:
            with running_server(backend) as (engine,server,errors):
                client=LocalDecisionClient(f'http://127.0.0.1:{server.server_port}')
                shadow={'profile':'local_decision_shadow_v2','timeoutMs':1000,
                        'profileSha256':fingerprint(engine.profile()),'request':request()}
                first=client.decide(shadow);second=client.decide(shadow)
                self.assertEqual(first['result']['distribution'],second['result']['distribution'])
                self.assertEqual(second['result']['status'],'ok');self.assertTrue(backend.is_available())
                self.assertIsNone(backend.diagnostics()['stopReason']);self.assertEqual(errors,[])

    def test_invalid_cancellation_header_fails_before_inference(self):
        backend=Backend()
        with running_server(backend) as (_engine,server,_errors):
            for value in ('0','yes','1\r\n'+CANCEL_ON_DISCONNECT_HEADER+': 1'):
                sock=raw_connection(server,cancellation=value)
                try:
                    response=http.client.HTTPResponse(sock);response.begin()
                    self.assertEqual(response.status,400);self.assertEqual(json.loads(response.read())['reason'],'invalid_request')
                finally:sock.close()
            self.assertEqual(backend.calls,0)


if __name__=='__main__':unittest.main()
