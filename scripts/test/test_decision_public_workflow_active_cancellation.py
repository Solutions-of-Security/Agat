"""Active relay tests use typed HTTP fixtures; no native/model evidence."""
from collections import Counter
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
import time
import unittest

from decision_runtime.artifacts import verify_seal
from decision_runtime.contracts import Request
from decision_runtime.metrics import OUTCOMES
from scripts.lib import decision_public_workflow_active_cancellation as active
from scripts.test import test_decision_public_workflow_timeout as fixture
from workers.local_decisions import LocalDecisionClient, PROFILE, CALLER_TIMING_VERSION


class NativeHttpFixture:
    def __init__(self, context, rows, *, early=False, epoch=1000.125, extra=False):
        self.requests = []; self.finished = Counter(); self.target_active = False; self.eof = threading.Event()
        self.target_started = threading.Event(); self.stop = threading.Event(); self.early = early
        self.lock = threading.Lock(); owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_GET(self):
                if self.path != "/metrics": self.send_error(404); return
                with owner.lock:
                    counts = owner.finished+Counter(ok=2+int(extra)); in_progress = int(owner.target_active)
                body = ("\n".join(f'agat_decision_requests_total{{outcome="{key}"}} {counts[key]}' for key in sorted(OUTCOMES))
                    +f"\nagat_decision_backend_ready 1\nagat_decision_requests_in_progress {in_progress}"
                    +f"\nagat_decision_server_start_time_seconds {epoch}\n").encode()
                self.send_response(200); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"])); request = json.loads(body)
                with owner.lock: index = len(owner.requests); owner.requests.append(body)
                if index == 2 and not owner.early:
                    with owner.lock: owner.target_active = True
                    owner.target_started.set(); self.connection.settimeout(.02); deadline = time.monotonic()+2
                    while not owner.stop.is_set() and time.monotonic()<deadline:
                        try:
                            if self.connection.recv(1) == b"": owner.eof.set(); return
                            raise AssertionError("Fixture received bytes after whole target")
                        except socket.timeout: continue
                    return
                response = json.loads(rows[index]["responseBody"]); response.update(id=request["id"],durationMs=0.0)
                raw = json.dumps(response).encode()
                with owner.lock: owner.finished["ok" if context["inputs"][index]["contextEligible"] else "context_rejected"] += 1
                # Signal request arrival before writing: the relay deliberately
                # closes an early-response socket, so flush can raise instead.
                if index == 2: owner.target_started.set()
                self.send_response(200 if context["inputs"][index]["contextEligible"] else 422)
                self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(raw)))
                self.end_headers(); self.wfile.write(raw); self.wfile.flush()
        self.server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,kwargs={"poll_interval":.01}); self.thread.start()
    def close(self):
        self.stop.set(); self.server.shutdown(); self.server.server_close(); self.thread.join(2)


class ActiveRelayTest(unittest.TestCase):
    def exercise(self, *, early=False, epoch=1000.125, extra=False):
        context, _, _, routes, data = fixture.fixture(); backend = NativeHttpFixture(context,data["rows"],early=early,epoch=epoch,extra=extra)
        proxy = None; task = None; cancel = threading.Event(); received = []
        try:
            spec = active.active_spec(context,2); proxy = active.ActiveCancellationProxy(backend.server.server_port,context,spec)
            proxy.bind_warmups({"ok":2},1000.125)
            client = LocalDecisionClient(f"http://127.0.0.1:{proxy.port}")
            def invoke(index,stop):
                received.append(client.decide({"profile":PROFILE,"profileSha256":context["profileSha256"],
                    "request":{**context["inputs"][index]["request"],"id":routes[index]["stageId"]},
                    "timeoutMs":10000,"callerTimingVersion":CALLER_TIMING_VERSION},stop))
            for index in range(2): invoke(index,threading.Event())
            task = threading.Thread(target=invoke,args=(2,cancel)); task.start()
            self.assertTrue(backend.target_started.wait(1))
            budget = time.monotonic()+1
            while proxy.ready_receipt() is None and not proxy.errors and time.monotonic()<budget: time.sleep(.01)
            if early or epoch != 1000.125 or extra:
                budget = time.monotonic()+1
                while not proxy.errors and time.monotonic()<budget: time.sleep(.01)
                self.assertTrue(proxy.errors); self.assertIsNone(proxy.ready_receipt())
                self.assertIsNone(proxy.target_receipt()); return
            ready = verify_seal(proxy.ready_receipt(),active.READY_SCHEMA)
            self.assertEqual(ready["caseId"],context["inputs"][2]["id"])
            self.assertEqual(ready["upstreamResponseBytesObserved"],0); self.assertEqual(ready["downstreamResponseBytesWritten"],0)
            self.assertIsNone(proxy.target_receipt()); self.assertEqual(len(received),2)
            cancel.set(); task.join(1); self.assertFalse(task.is_alive()); self.assertEqual(received[-1]["reason"],"cancelled")
            self.assertTrue(backend.eof.wait(1))
            budget = time.monotonic()+1
            while proxy.target_receipt() is None and time.monotonic()<budget: time.sleep(.01)
            verify_seal(proxy.target_receipt(),active.DRAIN_SCHEMA)
            for index in range(3,6): invoke(index,threading.Event())
            proxy.close(); receipt = verify_seal(proxy.receipt(),active.TRANSPORT_SCHEMA)
            self.assertEqual(receipt["acceptedPosts"],6); self.assertEqual(receipt["completedUpstreamPosts"],5)
            self.assertEqual(receipt["interruptedActiveUpstreamPosts"],1); self.assertEqual(receipt["errors"],[])
            self.assertEqual([Request.from_dict(json.loads(raw)).input_sha256 for raw in backend.requests],
                             [case["inputSha256"] for case in context["inputs"]])
            row = receipt["rows"][2]
            self.assertFalse(row["upstreamCompletedNormally"]); self.assertNotIn("responseBody",row)
            self.assertEqual(row["responseBytesWritten"],0); self.assertEqual(row["upstreamResponseBytesObserved"],0)
        finally:
            cancel.set()
            if task is not None: task.join(2)
            backend.stop.set()
            if proxy is not None and not proxy.closed: proxy.close()
            backend.close()
            self.assertFalse(backend.thread.is_alive())
    def test_actual_eof_reaches_upstream_and_same_relay_serves_healthy_suffix(self): self.exercise()
    def test_ready_response_is_rejected_instead_of_claimed_as_active_cancellation(self): self.exercise(early=True)
    def test_runtime_epoch_drift_is_rejected(self): self.exercise(epoch=1001.125)
    def test_extra_native_call_is_rejected(self): self.exercise(extra=True)
    def test_missing_warmup_binding_and_rebinding_fail(self):
        context, _, _, _, data = fixture.fixture(); backend = NativeHttpFixture(context,data["rows"]); proxy = None
        try:
            proxy = active.ActiveCancellationProxy(backend.server.server_port,context,active.active_spec(context,2))
            for values in ({"ok":True,"abstain":True},{"ok":3},{"timeout":2},{"ok":-1,"abstain":3}):
                with self.assertRaises(ValueError): proxy.bind_warmups(values,1000.125)
            proxy.bind_warmups({"ok":2},1000.125)
            with self.assertRaises(ValueError): proxy.bind_warmups({"ok":2},1000.125)
        finally:
            if proxy is not None: proxy.close()
            backend.close()
    def test_active_spec_is_eligible_prospective_and_avoids_resident(self):
        context, _, _, _, _ = fixture.fixture()
        for index in (True,0,5,-1):
            with self.assertRaises(ValueError): active.active_spec(context,index)
        for port in (True,8766,-1,65536):
            with self.assertRaises(ValueError): active.ActiveCancellationProxy(port,context,active.active_spec(context,2))


class BoundedMetricsTest(unittest.TestCase):
    def exercise(self, response, *, trickle=False, body_only=False):
        listener = socket.socket(); listener.bind(("127.0.0.1",0)); listener.listen(1); stop = threading.Event()
        def serve():
            connection,_ = listener.accept()
            with connection:
                connection.recv(4096)
                try:
                    if trickle:
                        if body_only:
                            header,body = response.split(b"\r\n\r\n",1)
                            connection.sendall(header+b"\r\n\r\n"); slow = body
                        else: slow = response
                        for value in slow:
                            connection.send(bytes([value]))
                            if stop.wait(.01): break
                    else: connection.sendall(response)
                except (BrokenPipeError,ConnectionResetError): pass
        thread = threading.Thread(target=serve); thread.start(); started = time.monotonic()
        try:
            if trickle:
                with self.assertRaises(TimeoutError): active.bounded_metrics(listener.getsockname()[1])
                self.assertLess(time.monotonic()-started,.75)
            else:
                with self.assertRaises((ValueError,UnicodeError)): active.bounded_metrics(listener.getsockname()[1])
        finally: stop.set(); listener.close(); thread.join(1); self.assertFalse(thread.is_alive())
    def test_trickling_headers_cannot_extend_total_deadline(self):
        self.exercise(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nabcdefghij",trickle=True)
    def test_trickling_body_cannot_extend_total_deadline(self):
        self.exercise(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n"+b"a"*100,trickle=True,body_only=True)
    def test_ambiguous_length_invalid_status_and_oversized_headers_fail(self):
        for raw in (b"HTTP/1.1 503 Unavailable\r\nContent-Length: 1\r\n\r\nx",
            b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\nx",
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Length: 1\r\n\r\nx",
            b"HTTP/1.1 200 OK\r\nContent-Length: 1048577\r\n\r\nx",b"HTTP/1.1 200 OK\r\nX: "+b"a"*5000):
            with self.subTest(response=raw[:40]): self.exercise(raw)


class RetirementBindingTest(unittest.TestCase):
    def fixture(self):
        context,_,_,_,data = fixture.fixture(); spec = active.active_spec(context,2)
        row = {**data["rows"][2],"upstreamRequestSentAt":"2026-10-09T02:00:00.001Z",
            "acceptedAt":"2026-10-09T02:00:00.000Z","finishedAt":"2026-10-09T02:00:00.100Z",
            "clientEofObserved":True,"upstreamShutdownApplied":True,"upstreamResponseBytesObserved":0,"responseBytesWritten":0}
        metrics = "\n".join(f'agat_decision_requests_total{{outcome="{key}"}} 0' for key in sorted(OUTCOMES))
        metrics += "\nagat_decision_backend_ready 1\nagat_decision_requests_in_progress 1\nagat_decision_server_start_time_seconds 1000.125\n"
        ready = active.ready_receipt(row,{"capturedAt":"2026-10-09T02:00:00.002Z","metricsRaw":metrics})
        drained = active.drain_receipt(row)
        event = {"schemaVersion":"agat.decision.retirement.v1","eventName":"decision.backend_retired","runtimeVersion":"0.12.3",
            "profileSha256":context["profileSha256"],"exitCode":75,"reason":"inference_cancelled","childPid":102,"childExitCode":-15}
        log = b"Agat decision runtime: owned fixture\n"+json.dumps(event).encode()+b"\n"
        return spec,ready,drained,event,log
    def make_retired(self, *, observed="2026-10-09T02:00:00.101Z", **kwargs):
        spec,ready,drained,event,log = self.fixture()
        args = {"runtime_pid":101,"native_pids":[101,102,103],"exit_code":75,"remaining":[],"log_raw":log,"observed_at":observed}
        args.update(kwargs)
        return active.retired_receipt(spec,ready,drained,**args)
    def test_cancelled_retirement_preserves_unknown_result_and_counter(self):
        retired = verify_seal(self.make_retired(),active.RETIREMENT_SCHEMA)
        self.assertIsNone(retired["targetTypedResult"]); self.assertFalse(retired["targetCompletionCounterRecorded"])
        self.assertEqual(retired["event"]["childPid"],102); self.assertEqual(retired["runtimeExitCode"],75)
        self.assertEqual(retired["runtimeLogFileSha256"],hashlib.sha256(self.fixture()[-1]).hexdigest())
    def test_retirement_reason_profile_pid_exit_and_duplicate_event_cannot_be_substituted(self):
        spec,ready,drained,event,log = self.fixture()
        for changes in ({"reason":"inference_timeout"},{"childPid":999},{"childPid":101},{"childExitCode":None},
                        {"profileSha256":"0"*64},{"exitCode":0},{"childPid":True},{"extra":"not_allowlisted"}):
            altered={**event,**changes}
            with self.subTest(changes=changes),self.assertRaises(ValueError): self.make_retired(log_raw=json.dumps(altered).encode())
        for bad in (log+json.dumps(event).encode()+b"\n",b"no retirement event",b"x"*65537):
            with self.assertRaises(ValueError): self.make_retired(log_raw=bad)
        with self.assertRaises(ValueError): self.make_retired(exit_code=0)
    def test_retirement_requires_native_cleanup_and_ordered_barriers(self):
        for kwargs in ({"remaining":[102]},{"native_pids":[101,103]},{"native_pids":[101,102,102]},
                       {"observed":"2026-10-09T01:59:59.000Z"}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError): self.make_retired(**kwargs)
    def test_recovery_has_new_pid_epoch_and_separate_warmup_pin(self):
        retired=self.make_retired(); spec=self.fixture()[0]
        recovered=active.recovered_receipt(spec,retired,runtime_pid=201,profile_sha=retired["profileSha256"],
            server_start=1001.125,warmup_file_sha="a"*64,applied_at="2026-10-09T02:00:01.000Z")
        verify_seal(recovered,active.RECOVERED_SCHEMA)
        self.assertEqual(recovered["warmupCount"],2); self.assertEqual(recovered["retiredSealSha256"],retired["sha256"])
    def test_recovery_rejects_old_process_epoch_changed_profile_missing_warmups_and_reordered_time(self):
        retired=self.make_retired(); spec=self.fixture()[0]
        good={"runtime_pid":201,"profile_sha":retired["profileSha256"],"server_start":1001.125,
              "warmup_file_sha":"a"*64,"applied_at":"2026-10-09T02:00:01.000Z"}
        for changes in ({"runtime_pid":102},{"server_start":1000.125},{"profile_sha":"0"*64},
                        {"warmup_file_sha":""},{"applied_at":"2026-10-09T01:59:59.000Z"}):
            with self.subTest(changes=changes),self.assertRaises(ValueError): active.recovered_receipt(spec,retired,**{**good,**changes})


if __name__ == "__main__": unittest.main()
