"""Prospective caller timeout after an actual, independently bound model response."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
import time

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import Request, fields, fingerprint, number, parse_json
from decision_runtime.metrics import outcome
from scripts.lib.decision_performance import validate_result
from scripts.lib.decision_shadow_pilot import require, timestamp

PLAN_SCHEMA = "agat.decision.public-workflow-plan.v4"
RESULT_SCHEMA = "agat.decision.public-workflow-result.v4"
TRANSPORT_SCHEMA = "agat.decision.public-workflow-deadline-transport.v1"
DRAIN_SCHEMA = "agat.decision.public-workflow-deadline-drained.v1"


def timeout_spec(context, target_index):
    require(type(target_index) is int and 1 <= target_index < len(context["inputs"])-1,
            "Timeout must leave a healthy prefix and suffix")
    case = context["inputs"][target_index]
    require(case["contextEligible"] is True, "Timeout target must be context eligible")
    return {"kind": "withhold_owned_proxy_response", "targetIndex": target_index,
            "targetCaseId": case["id"], "targetInputSha256": case["inputSha256"],
            "boundary": "after_upstream_completion_before_response_headers",
            "endpoint": "temporary_loopback_proxy_to_owned_runtime", "callerTimeoutMs": 10000,
            "upstreamTimeoutMs": 8000, "disconnectDeadlineMs": 15000,
            "retryCount": 0, "restart": False, "warmupCount": 2}


def drain_receipt(row):
    require(row["withheld"] is True and row["clientEofObserved"] is True and row["responseBytesWritten"] == 0,
            "Target response was not held until actual EOF")
    return sealed({"schemaVersion": DRAIN_SCHEMA, "targetIndex": row["index"], "caseId": row["caseId"],
                   "stageId": row["stageId"], "inputSha256": row["inputSha256"], "profileSha256": row["profileSha256"],
                   "rowSha256": fingerprint(row), "finishedAt": row["finishedAt"]})


def verify_transport(context, spec, transport, cohort, routes):
    """Bind raw proxy POSTs/results to every actual durable stage, including the loss."""
    require(fingerprint(spec) == fingerprint(timeout_spec(context, spec["targetIndex"])), "Unsupported caller timeout")
    verify_seal(transport, TRANSPORT_SCHEMA)
    fields(transport, {"schemaVersion", "sha256", "spec", "proxyPort", "upstreamPort", "startedAt", "closedAt", "rows",
                       "acceptedPosts", "completedUpstreamPosts", "withheldResponses", "errors", "closed", "activeHandlers"})
    require(fingerprint(transport["spec"]) == fingerprint(spec) and transport["closed"] is True
            and type(transport["activeHandlers"]) is int and transport["activeHandlers"] == 0 and transport["errors"] == [],
            "Proxy did not drain cleanly or changed the fault")
    ports = [transport[key] for key in ("proxyPort", "upstreamPort")]
    require(all(type(port) is int and 1 <= port <= 65535 and port != 8766 for port in ports)
            and ports[0] != ports[1], "Proxy targets the resident or a different transport")
    count = len(context["inputs"])
    require(all(type(transport[key]) is int and transport[key] == count for key in ("acceptedPosts", "completedUpstreamPosts"))
            and type(transport["withheldResponses"]) is int and transport["withheldResponses"] == 1
            and isinstance(transport["rows"], list) and len(transport["rows"]) == len(routes) == count,
            "Missing, repeated or extra proxy/backend POST")
    started = timestamp(transport["startedAt"], "transport.startedAt")
    closed = timestamp(transport["closedAt"], "transport.closedAt")
    require(started < closed, "Proxy lifetime is invalid")
    instances = {row["runId"]: row for row in cohort["instances"]}
    traces = {row["run"]["id"]: row for row in cohort["traces"]}
    physical = Counter(); last_end = started; stage_ids = set(); target = None
    for index, (case, route, row) in enumerate(zip(context["inputs"], routes, transport["rows"])):
        fields(row, {"index", "caseId", "stageId", "inputSha256", "requestBody", "requestBodySha256", "profileSha256",
                     "cancelOnDisconnect", "acceptedAt", "upstreamCompletedAt", "finishedAt", "upstreamMs", "elapsedMs",
                     "upstreamStatus", "upstreamContentType", "responseBody", "responseBodySha256", "withheld",
                     "downstreamWriteCompleted", "responseBytesWritten", "clientEofObserved"})
        require(type(row["index"]) is int and row["index"] == index and row["caseId"] == case["id"]
                and row["stageId"] == route["stageId"] and row["stageId"] not in stage_ids
                and row["inputSha256"] == case["inputSha256"] and row["profileSha256"] == context["profileSha256"]
                and row["cancelOnDisconnect"] is True, "Proxy input order, identity or profile differs")
        stage_ids.add(row["stageId"])
        require(isinstance(row["requestBody"], str) and isinstance(row["responseBody"], str), "Missing raw proxy bodies")
        body = row["requestBody"].encode(); response = row["responseBody"].encode()
        require(0 < len(body) <= 128*1024 and 0 < len(response) <= 64*1024
                and hashlib.sha256(body).hexdigest() == row["requestBodySha256"]
                and hashlib.sha256(response).hexdigest() == row["responseBodySha256"], "Raw proxy body pin differs")
        request = Request.from_dict(parse_json(body))
        require(fingerprint(parse_json(body)) == fingerprint({**case["request"], "id": route["stageId"]})
                and request.input_sha256 == case["inputSha256"], "Proxy forwarded transformed input")
        result = parse_json(response); validate_result(result, request, context["profile"])
        eligible = case["contextEligible"]
        require((eligible and result["status"] in {"ok", "abstain"} and result["inputTokens"] == case["inputTokens"])
                or (not eligible and result["status"] == "error" and result["reason"] == "context_too_long"), "Unexpected upstream result")
        require(type(row["upstreamStatus"]) is int and row["upstreamStatus"] == (200 if eligible else 422)
                and row["upstreamContentType"].split(";")[0] == "application/json", "Unexpected upstream HTTP response")
        accepted = timestamp(row["acceptedAt"], "proxy.acceptedAt")
        completed = timestamp(row["upstreamCompletedAt"], "proxy.upstreamCompletedAt")
        finished = timestamp(row["finishedAt"], "proxy.finishedAt")
        require(last_end <= accepted <= completed <= finished <= closed
                and timestamp(instances[route["runId"]]["createdAt"], "instance.createdAt") <= accepted,
                "Proxy responses overlap, are reordered or predate the instance")
        upstream_ms = number(row["upstreamMs"], 0, spec["upstreamTimeoutMs"])
        elapsed_ms = number(row["elapsedMs"], upstream_ms, spec["disconnectDeadlineMs"])
        require(upstream_ms >= result["durationMs"]-.1
                and abs((completed-accepted).total_seconds()*1000-upstream_ms) <= 10
                and abs((finished-accepted).total_seconds()*1000-elapsed_ms) <= 10, "Upstream timing omits the model or differs from recorded boundaries")
        observation = traces[route["runId"]]["decisionObservations"][0]["observation"]
        if index == spec["targetIndex"]:
            require(row["withheld"] is True and row["downstreamWriteCompleted"] is False and row["clientEofObserved"] is True
                    and type(row["responseBytesWritten"]) is int and row["responseBytesWritten"] == 0
                    and observation["status"] == "unavailable" and observation["reason"] == "timeout" and "result" not in observation,
                    "Undelivered response was accepted or not bound to the actual timeout")
            caller_ms = number(observation["callerTiming"]["durationMs"], 9999, spec["disconnectDeadlineMs"])
            require(9900 <= elapsed_ms and abs(elapsed_ms-caller_ms) <= 250 and upstream_ms < elapsed_ms,
                    "EOF does not follow the full caller deadline after upstream completion")
            require(finished <= timestamp(instances[routes[index+1]["runId"]]["createdAt"], "suffix.createdAt"),
                    "Suffix started before the timed-out proxy handler drained")
            target = result
        else:
            require(row["withheld"] is False and row["downstreamWriteCompleted"] is True and row["clientEofObserved"] is False
                    and type(row["responseBytesWritten"]) is int and row["responseBytesWritten"] == len(response)
                    and fingerprint(observation["result"]) == fingerprint(result), "Delivered response differs from durable result")
        physical[outcome(result)] += 1; last_end = finished
    require(target is not None, "Timeout target is missing")
    return {"physicalScheduledOutcomes": dict(physical), "physicalScheduledHttpHandlers": count,
            "completedUndeliveredResponses": 1, "undeliveredOutcome": outcome(target), "proxyPosts": count,
            "clientEofObserved": True, "runtimeRestarted": False}


class DeadlineProxy:
    """One finite inventory on a literal loopback endpoint; no POST retry or model stub."""
    def __init__(self, upstream_port, context, spec):
        require(type(upstream_port) is int and 1 <= upstream_port <= 65535 and upstream_port != 8766, "Use an owned temporary runtime")
        require(fingerprint(spec) == fingerprint(timeout_spec(context, spec["targetIndex"])), "Invalid prospective timeout")
        self.upstream_port = upstream_port; self.context = context; self.spec = spec
        self.rows = []; self.errors = []; self.accepted = 0; self.active = 0; self.closed = False
        self.lock = threading.Lock(); self.stopped = threading.Event(); self.started_at = self.now()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                if self.path != "/health": self.send_error(404); return
                try:
                    status, content_type, body = owner.forward("GET", self.path)
                    owner.send(self, status, content_type, body)
                except Exception as error:
                    owner.fail(error); self.close_connection = True
            def do_POST(self):
                with owner.lock:
                    index = owner.accepted; owner.accepted += 1; owner.active += 1
                try:
                    require(owner.active == 1 and index < len(context["inputs"]), "Unexpected concurrent or extra POST")
                    owner.handle(self, index)
                except Exception as error:
                    owner.fail(error); self.close_connection = True
                finally:
                    with owner.lock: owner.active -= 1
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = False
        self.port = self.server.server_address[1]
        try:
            require(self.port != 8766 and self.port != upstream_port, "Proxy port collides with runtime")
            self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
            self.thread.start()
        except BaseException:
            self.server.server_close(); raise

    @staticmethod
    def now(): return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def fail(self, error):
        with self.lock: self.errors.append(type(error).__name__)

    def forward(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.upstream_port, timeout=self.spec["upstreamTimeoutMs"]/1000)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse(); raw = response.read(64*1024+1)
            require(len(raw) <= 64*1024, "Upstream response is excessive")
            return response.status, response.getheader("Content-Type", ""), raw
        finally: connection.close()

    @staticmethod
    def send(handler, status, content_type, body):
        handler.send_response(status); handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body))); handler.send_header("Connection", "close")
        handler.end_headers(); handler.wfile.write(body); handler.wfile.flush(); handler.close_connection = True

    def handle(self, handler, index):
        started = time.monotonic(); accepted_at = self.now(); case = self.context["inputs"][index]
        require(handler.path == "/v1/decisions" and handler.headers.get("Transfer-Encoding") is None
                and handler.headers.get("Content-Type", "").split(";")[0] == "application/json"
                and handler.headers.get("X-Agat-Decision-Profile") == self.context["profileSha256"]
                and handler.headers.get("X-Agat-Decision-Cancel-On-Disconnect") == "1", "Invalid proxy POST")
        length = int(handler.headers.get("Content-Length", "0")); require(0 < length <= 128*1024, "Invalid POST size")
        handler.connection.settimeout(self.spec["upstreamTimeoutMs"]/1000)
        body = handler.rfile.read(length); require(len(body) == length, "Incomplete POST")
        request = Request.from_dict(parse_json(body))
        require(fingerprint(parse_json(body)) == fingerprint({**case["request"], "id": request.id}), "Reordered or changed proxy input")
        status, content_type, response = self.forward("POST", handler.path, body, {
            "Content-Type": "application/json", "Accept": "application/json", "X-Agat-Decision-Profile": self.context["profileSha256"],
            "X-Agat-Decision-Cancel-On-Disconnect": "1", "Connection": "close"})
        upstream_ms = (time.monotonic()-started)*1000; completed_at = self.now()
        require(upstream_ms < self.spec["upstreamTimeoutMs"], "Upstream exceeded prospective timeout")
        result = parse_json(response); validate_result(result, request, self.context["profile"])
        withheld = index == self.spec["targetIndex"]; eof = False
        if withheld:
            require(status == 200 and result["status"] in {"ok", "abstain"}, "Target model did not complete normally")
            deadline = started+self.spec["disconnectDeadlineMs"]/1000
            while not self.stopped.is_set() and time.monotonic() < deadline:
                handler.connection.settimeout(min(.1, max(.001, deadline-time.monotonic())))
                try:
                    require(handler.connection.recv(1) == b"", "Unexpected data after complete target POST")
                    eof = True; break
                except socket.timeout: pass
            require(eof, "Caller did not close the timed-out transport")
            handler.close_connection = True
        else: self.send(handler, status, content_type, response)
        row = {"index": index, "caseId": case["id"], "stageId": request.id, "inputSha256": request.input_sha256,
            "requestBody": body.decode(), "requestBodySha256": hashlib.sha256(body).hexdigest(), "profileSha256": self.context["profileSha256"],
            "cancelOnDisconnect": True, "acceptedAt": accepted_at, "upstreamCompletedAt": completed_at, "finishedAt": self.now(),
            "upstreamMs": round(upstream_ms, 3), "elapsedMs": round((time.monotonic()-started)*1000, 3),
            "upstreamStatus": status, "upstreamContentType": content_type, "responseBody": response.decode(),
            "responseBodySha256": hashlib.sha256(response).hexdigest(), "withheld": withheld,
            "downstreamWriteCompleted": not withheld, "responseBytesWritten": 0 if withheld else len(response), "clientEofObserved": eof}
        with self.lock: self.rows.append(row)

    def close(self):
        self.stopped.set(); self.server.shutdown(); self.server.server_close(); self.thread.join(2)
        require(not self.thread.is_alive() and self.active == 0, "Deadline proxy did not drain")
        self.closed = True; self.closed_at = self.now()

    def target_receipt(self):
        with self.lock:
            row = next((row for row in self.rows if row["index"] == self.spec["targetIndex"]), None)
            return drain_receipt(row) if row is not None else None

    def receipt(self):
        require(self.closed and self.active == 0, "Only a drained proxy can publish accounting")
        return sealed({"schemaVersion": TRANSPORT_SCHEMA, "spec": self.spec, "proxyPort": self.port, "upstreamPort": self.upstream_port,
            "startedAt": self.started_at, "closedAt": self.closed_at, "rows": list(self.rows), "acceptedPosts": self.accepted,
            "completedUpstreamPosts": len(self.rows), "withheldResponses": sum(row["withheld"] for row in self.rows),
            "errors": list(self.errors), "closed": self.closed, "activeHandlers": self.active})
