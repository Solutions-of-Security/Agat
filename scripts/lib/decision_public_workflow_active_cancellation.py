"""Owned active-HTTP cancellation transport; no completed target model result."""
from collections import Counter
import hashlib
import http.client
import select
import socket
import time

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import Request, fields, fingerprint, number, parse_json
from decision_runtime.metrics import outcome
from scripts.lib.decision_shadow_pilot import require, timestamp
from scripts.lib import decision_public_workflow_recovery as recovery
from scripts.lib.decision_public_workflow_timeout import DeadlineProxy, timeout_spec

PLAN_SCHEMA = "agat.decision.public-workflow-plan.v6"
RESULT_SCHEMA = "agat.decision.public-workflow-result.v6"
LAUNCH_PLAN = "agat.decision.public-workflow-launch-plan.v6"
LAUNCH_RESULT = "agat.decision.public-workflow-launch-result.v6"
READY_SCHEMA = "agat.decision.public-workflow-active-cancellation-ready.v1"
DRAIN_SCHEMA = "agat.decision.public-workflow-active-cancellation-drained.v1"
TRANSPORT_SCHEMA = "agat.decision.public-workflow-active-cancellation-transport.v1"
RETIREMENT_SCHEMA = "agat.decision.public-workflow-active-cancellation-retired.v1"
RECOVERED_SCHEMA = "agat.decision.public-workflow-active-cancellation-recovered.v1"


def active_spec(context, index):
    return {**timeout_spec(context, index),
        "kind": "propagate_caller_eof_during_active_native_http_handler",
        "boundary": "active_upstream_http_handler_before_response_bytes",
        "restart": True, "warmupCount": 4, "warmupPerRuntime": 2,
        "trigger": "exactly_one_active_http_handler_with_no_response_bytes",
        "relayPollMs": 10, "activeObserveDeadlineMs": 10000, "sampleToCancelMaxMs": 250,
        "cancelToEofDeadlineMs": 2500,
        "retirementDeadlineMs": 10000, "recoveryDeadlineMs": 90000,
        "retirementExitCode": 75, "retirementReason": "inference_cancelled",
        "recoveryEndpoint": "same_loopback_port_same_frozen_profile"}


def ready_receipt(row, snapshot):
    return sealed({"schemaVersion": READY_SCHEMA,
        "targetIndex": row["index"], "caseId": row["caseId"], "stageId": row["stageId"],
        "inputSha256": row["inputSha256"], "profileSha256": row["profileSha256"],
        "requestBodySha256": row["requestBodySha256"], "acceptedAt": row["acceptedAt"],
        "upstreamRequestSentAt": row["upstreamRequestSentAt"], "activeObservedAt": snapshot["capturedAt"],
        "activeMetricsRaw": snapshot["metricsRaw"], "activeMetricsSha256": hashlib.sha256(snapshot["metricsRaw"].encode()).hexdigest(),
        "upstreamResponseBytesObserved": 0, "downstreamResponseBytesWritten": 0})


def drain_receipt(row):
    require(row["clientEofObserved"] is True and row["upstreamShutdownApplied"] is True
            and row["responseBytesWritten"] == row["upstreamResponseBytesObserved"] == 0,
            "Active caller did not propagate EOF without an upstream response")
    return sealed({"schemaVersion": DRAIN_SCHEMA, "targetIndex": row["index"], "caseId": row["caseId"],
        "stageId": row["stageId"], "inputSha256": row["inputSha256"], "profileSha256": row["profileSha256"],
        "rowSha256": fingerprint(row), "finishedAt": row["finishedAt"]})


def retirement_event(raw, profile_sha, runtime_pid, native_pids, exit_code):
    """Bind the existing native allowlist event; no inference result is reconstructed."""
    require(isinstance(raw,bytes) and 0 < len(raw) <= 65536 and type(exit_code) is int and exit_code == 75,
            "Native runtime did not retire with a bounded log and exit 75")
    require(type(runtime_pid) is int and runtime_pid in native_pids and len(native_pids) == len(set(native_pids))
            and all(type(pid) is int and 0 < pid < 2**31 for pid in native_pids),"Retirement lacks owned native PIDs")
    events = []
    for line in raw.decode().splitlines():
        if not line.startswith("{"): continue
        value = parse_json(line)
        if value.get("schemaVersion") == "agat.decision.retirement.v1": events.append(value)
    require(len(events) == 1,"Missing or repeated native retirement event")
    event = fields(events[0],{"schemaVersion","eventName","runtimeVersion","profileSha256","exitCode","reason","childPid","childExitCode"})
    require(event["eventName"] == "decision.backend_retired" and event["runtimeVersion"] == "0.12.3"
            and event["profileSha256"] == profile_sha and type(event["exitCode"]) is int and event["exitCode"] == exit_code
            and event["reason"] == "inference_cancelled" and type(event["childPid"]) is int
            and event["childPid"] in native_pids and event["childPid"] != runtime_pid
            and type(event["childExitCode"]) is int and event["childExitCode"] in (0,-9,-15),
            "Native retirement did not bind cancellation and its owned inference child")
    return event


def retired_receipt(spec, ready, drained, *, runtime_pid, native_pids, exit_code, remaining, log_raw, observed_at):
    verify_seal(ready,READY_SCHEMA); verify_seal(drained,DRAIN_SCHEMA)
    require(ready["targetIndex"] == drained["targetIndex"] == spec["targetIndex"] and remaining == []
            and all(ready[key] == drained[key] for key in ("caseId","stageId","inputSha256","profileSha256")),
            "Retirement crossed target bindings or native processes remain")
    event = retirement_event(log_raw,ready["profileSha256"],runtime_pid,native_pids,exit_code)
    require(timestamp(ready["upstreamRequestSentAt"],"sentAt") <= timestamp(ready["activeObservedAt"],"activeAt")
            <= timestamp(drained["finishedAt"],"eofAt") <= timestamp(observed_at,"retiredAt"),
            "Native retirement or active barriers are reordered")
    _values,epoch = recovery.http_metrics(ready["activeMetricsRaw"],in_progress=1)
    return sealed({"schemaVersion":RETIREMENT_SCHEMA,"targetIndex":spec["targetIndex"],
        "caseId":ready["caseId"],"stageId":ready["stageId"],"inputSha256":ready["inputSha256"],"profileSha256":ready["profileSha256"],
        "runtimePid":runtime_pid,"runtimeExitCode":exit_code,"nativePids":sorted(native_pids),"remainingNativePids":[],
        "readySealSha256":ready["sha256"],"drainedSealSha256":drained["sha256"],"runtimeLogFileSha256":hashlib.sha256(log_raw).hexdigest(),
        "event":event,"observedExitedAt":observed_at,"retiredServerStartText":str(epoch),
        "targetTypedResult":None,"targetCompletionCounterRecorded":False})


def recovered_receipt(spec, retired, *, runtime_pid, profile_sha, server_start, warmup_file_sha, applied_at):
    verify_seal(retired,RETIREMENT_SCHEMA)
    require(type(runtime_pid) is int and 0 < runtime_pid < 2**31 and runtime_pid not in retired["nativePids"]
            and retired["targetIndex"] == spec["targetIndex"] and profile_sha == retired["profileSha256"],
            "Recovery reused a retired process or changed the frozen profile")
    number(server_start,0,86400000000)
    require(server_start > float(retired["retiredServerStartText"])
            and timestamp(retired["observedExitedAt"],"retiredAt") <= timestamp(applied_at,"recoveredAt"),
            "Recovery reused an epoch or preceded retirement")
    require(isinstance(warmup_file_sha,str) and len(warmup_file_sha) == 64 and all(c in "0123456789abcdef" for c in warmup_file_sha),
            "Recovery warmups lack a raw artifact pin")
    return sealed({"schemaVersion":RECOVERED_SCHEMA,"targetIndex":spec["targetIndex"],"retiredSealSha256":retired["sha256"],
        "runtimePid":runtime_pid,"profileSha256":profile_sha,"readyServerStartText":str(server_start),
        "warmupCount":2,"warmupFileSha256":warmup_file_sha,"appliedAt":applied_at})


def bounded_metrics(port):
    """Native metadata GET with a total monotonic budget, including trickling bytes."""
    require(type(port) is int and 1 <= port <= 65535 and port != 8766,"Metrics must address an owned temporary port")
    deadline = time.monotonic()+.25
    connection = socket.create_connection(("127.0.0.1",port),timeout=.05)
    try:
        connection.setblocking(False)
        pending = memoryview(f"GET /metrics HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode("ascii"))
        raw = bytearray(); header_end = None; length = None
        while time.monotonic() < deadline:
            readable,writable,_ = select.select([] if pending else [connection], [connection] if pending else [], [],
                                                min(.01,max(.001,deadline-time.monotonic())))
            if writable:
                try: sent = connection.send(pending)
                except BlockingIOError: continue
                require(sent > 0,"Metrics peer closed while sending"); pending = pending[sent:]
            if readable:
                try: chunk = connection.recv(65536)
                except BlockingIOError: continue
                require(chunk,"Metrics response ended before a complete bounded body")
                raw.extend(chunk); require(len(raw) <= 1048576+4096,"Metrics response is oversized")
                if header_end is None:
                    marker = raw.find(b"\r\n\r\n")
                    require(marker >= 0 or len(raw) <= 4096,"Metrics headers are oversized")
                    if marker >= 0:
                        require(marker <= 4096,"Metrics headers are oversized"); header_end = marker+4
                        lines = bytes(raw[:marker]).decode("ascii").split("\r\n")
                        status = lines[0].split(" ",2)
                        require(len(status) == 3 and status[:2] in (["HTTP/1.0","200"],["HTTP/1.1","200"]),"Metrics status is not 200")
                        headers = {}
                        for line in lines[1:]:
                            key,separator,value = line.partition(":")
                            require(separator and key.lower() not in headers,"Malformed or duplicate metrics header")
                            headers[key.lower()] = value.strip()
                        require("transfer-encoding" not in headers and headers.get("content-length","").isdigit(),"Metrics length is missing or ambiguous")
                        length = int(headers["content-length"]); require(0 < length <= 1048576,"Invalid metrics body size")
                if header_end is not None and len(raw) >= header_end+length:
                    require(len(raw) == header_end+length,"Unexpected bytes after metrics body")
                    return bytes(raw[header_end:]).decode("utf8")
        raise TimeoutError("Metrics total deadline exceeded")
    finally: connection.close()


class ActiveCancellationProxy(DeadlineProxy):
    """Select both sockets; upstream readiness fails instead of hiding completed work."""
    def __init__(self, upstream_port, context, spec, *, spec_factory=active_spec):
        self.ready = None; self.warmup_outcomes = None; self.warmup_epoch = None
        super().__init__(upstream_port, context, spec, spec_factory=spec_factory)

    def _snapshot(self):
        # An observation GET does not enter the decision POST inventory.
        body = bounded_metrics(self.upstream_port)
        try: counters, epoch = recovery.http_metrics(body, in_progress=1); active = True
        except ValueError: counters, epoch = recovery.http_metrics(body, in_progress=0); active = False
        # Both idle and active snapshots must bind the original runtime epoch and prefix.
        expected = Counter()
        with self.lock:
            for row in self.rows: expected[outcome(parse_json(row["responseBody"]))] += 1
        require(self.warmup_outcomes is not None and epoch == self.warmup_epoch,"Native active observation changed its bound runtime epoch")
        expected.update(self.warmup_outcomes)
        require(counters == {key: expected[key] for key in counters},"Active snapshot contains an extra or completed target call")
        return {"capturedAt": self.now(), "metricsRaw": body} if active else None

    def bind_warmups(self, outcomes, server_start):
        require(isinstance(outcomes, dict) and sum(outcomes.values()) == 2
                and all(key in {"ok", "abstain"} and type(value) is int and value >= 0 for key,value in outcomes.items()),
                "Exactly two computed warmups must precede the active target")
        number(server_start,0,86400000000)
        with self.lock:
            require(self.accepted == 0 and self.warmup_outcomes is None, "Warmups rebound after scoring")
            self.warmup_outcomes = dict(outcomes); self.warmup_epoch = server_start

    def handle(self, handler, index):
        if index != self.spec["targetIndex"]: return super().handle(handler, index)
        started = time.monotonic(); accepted_at = self.now(); case = self.context["inputs"][index]
        require(handler.path == "/v1/decisions" and handler.headers.get("Transfer-Encoding") is None
                and len(handler.headers.get_all("Content-Length",[])) == 1
                and handler.headers.get("Content-Type", "").split(";")[0] == "application/json"
                and handler.headers.get("X-Agat-Decision-Profile") == self.context["profileSha256"]
                and handler.headers.get_all("X-Agat-Decision-Cancel-On-Disconnect",[]) == ["1"], "Invalid active target POST")
        length = int(handler.headers.get("Content-Length", "0")); require(0 < length <= 128*1024, "Invalid active POST size")
        handler.connection.settimeout(self.spec["upstreamTimeoutMs"]/1000)
        body = handler.rfile.read(length); require(len(body) == length, "Incomplete whole active target")
        request = Request.from_dict(parse_json(body))
        require(request == Request.from_dict({**case["request"], "id": request.id}) and request.input_sha256 == case["inputSha256"],
                "Active target input was transformed or reordered")
        row = {"index": index, "caseId": case["id"], "stageId": request.id, "inputSha256": request.input_sha256,
            "requestBody": body.decode(), "requestBodySha256": hashlib.sha256(body).hexdigest(),
            "profileSha256": self.context["profileSha256"], "cancelOnDisconnect": True, "acceptedAt": accepted_at}
        upstream = http.client.HTTPConnection("127.0.0.1", self.upstream_port, timeout=self.spec["upstreamTimeoutMs"]/1000)
        try:
            upstream.request("POST", handler.path, body=body, headers={"Content-Type": "application/json", "Accept": "application/json",
                "X-Agat-Decision-Profile": self.context["profileSha256"], "X-Agat-Decision-Cancel-On-Disconnect": "1", "Connection": "close"})
            row["upstreamRequestSentAt"] = self.now(); observed = started+self.spec["activeObserveDeadlineMs"]/1000
            deadline = started+self.spec["disconnectDeadlineMs"]/1000
            while not self.stopped.is_set() and time.monotonic() < deadline:
                readable, _, _ = select.select([handler.connection, upstream.sock], [], [], min(.01,max(.001,deadline-time.monotonic())))
                require(upstream.sock not in readable, "Native response became readable before active cancellation")
                if handler.connection in readable:
                    require(self.ready is not None and handler.connection.recv(1) == b"", "Caller EOF lacked an active barrier or whole POST")
                    row["clientEofObservedAt"] = self.now()
                    upstream.sock.shutdown(socket.SHUT_RDWR); row["upstreamShutdownAt"] = self.now()
                    row.update(finishedAt=self.now(), elapsedMs=round((time.monotonic()-started)*1000,3),
                        clientEofObserved=True, upstreamShutdownApplied=True, upstreamResponseBytesObserved=0,
                        responseBytesWritten=0, downstreamWriteCompleted=False, upstreamCompletedNormally=False)
                    with self.lock: self.rows.append(row)
                    handler.close_connection = True; return
                if self.ready is None:
                    require(time.monotonic() < observed, "Native handler was not observed active")
                    try: snapshot = self._snapshot()
                    except (OSError, http.client.HTTPException): continue
                    if snapshot is None: continue
                    require(upstream.sock not in select.select([upstream.sock], [], [], 0)[0], "Native response arrived while active metrics were read")
                    with self.lock:
                        self.ready = ready_receipt(row, snapshot)
            raise TimeoutError("Active caller EOF exceeded its prospective budget")
        finally: upstream.close()

    def ready_receipt(self):
        with self.lock: return self.ready

    def target_receipt(self):
        with self.lock:
            row = next((row for row in self.rows if row["index"] == self.spec["targetIndex"]), None)
            return drain_receipt(row) if row is not None else None

    def receipt(self):
        require(self.closed and self.active == 0, "Only a drained active relay can publish accounting")
        return sealed({"schemaVersion": TRANSPORT_SCHEMA, "spec": self.spec, "proxyPort": self.port, "upstreamPort": self.upstream_port,
            "startedAt": self.started_at, "closedAt": self.closed_at, "rows": list(self.rows), "acceptedPosts": self.accepted,
            "completedUpstreamPosts": sum(row.get("upstreamCompletedNormally") is not False for row in self.rows),
            "interruptedActiveUpstreamPosts": sum(row.get("upstreamShutdownApplied") is True for row in self.rows),
            "errors": list(self.errors), "closed": self.closed, "activeHandlers": self.active})
