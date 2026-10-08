"""Prospective loss of an owned runtime between completed workflow cases."""
from decision_runtime.contracts import fields, fingerprint
from scripts.lib.decision_shadow_pilot import require, timestamp

PLAN_SCHEMA = "agat.decision.public-workflow-plan.v2"
RESULT_SCHEMA = "agat.decision.public-workflow-result.v2"
REQUEST_SCHEMA = "agat.decision.public-workflow-loss-request.v1"
APPLIED_SCHEMA = "agat.decision.public-workflow-loss-applied.v1"


def loss_spec(context, before_index):
    require(type(before_index) is int and 1 <= before_index < len(context["inputs"]), "Loss must leave both healthy and unavailable cases")
    return {"kind": "stop_owned_runtime", "beforeIndex": before_index,
            "boundary": "after_completed_previous_instance_before_next_creation", "restart": False,
            "endpoint": "reserve_original_loopback_port_with_tcp_reset_guard"}


def validate_request(context, spec, request, routes):
    require(fingerprint(spec) == fingerprint(loss_spec(context, spec["beforeIndex"])), "Unsupported runtime loss specification")
    fields(request, {"schemaVersion", "beforeIndex", "afterCaseId", "afterRunId", "createdAt"})
    before = spec["beforeIndex"]
    require(request["schemaVersion"] == REQUEST_SCHEMA and type(request["beforeIndex"]) is int and request["beforeIndex"] == before
            and len(routes) == before and request["afterCaseId"] == context["inputs"][before-1]["id"]
            and request["afterRunId"] == routes[-1]["runId"], "Loss request does not follow the prospective complete prefix")
    timestamp(request["createdAt"], "loss.createdAt")
    for index, (case, route) in enumerate(zip(context["inputs"], routes)):
        require(type(route["index"]) is int and route["index"] == index and route["caseId"] == case["id"]
                and route["inputSha256"] == case["inputSha256"] and route["runStatus"] == "completed"
                and type(route["primaryCalls"]) is int and route["primaryCalls"] == 1
                and route["primaryBranch"] is True and route["wrongBranch"] is False, "Incomplete or changed prefix before loss")


def verify_boundary(context, spec, request, applied, cohort, routes):
    from decision_runtime.artifacts import verify_seal
    validate_request(context, spec, request, routes[:spec["beforeIndex"]])
    verify_seal(applied, APPLIED_SCHEMA)
    fields(applied, {"schemaVersion", "sha256", "beforeIndex", "requestFileSha256", "runtimePid", "runtimeExitCode", "runtimeExited", "endpointGuardedWithTcpReset", "appliedAt"})
    require(type(applied["beforeIndex"]) is int and applied["beforeIndex"] == spec["beforeIndex"]
            and type(applied["runtimePid"]) is int and applied["runtimePid"] > 0
            and type(applied["runtimeExitCode"]) is int and applied["runtimeExited"] is True
            and applied["endpointGuardedWithTcpReset"] is True, "Runtime loss was not acknowledged with an owned exit and reserved endpoint")
    require(isinstance(applied["requestFileSha256"], str) and len(applied["requestFileSha256"]) == 64
            and all(c in "0123456789abcdef" for c in applied["requestFileSha256"]), "Invalid loss request pin")
    requested_at = timestamp(request["createdAt"], "loss.createdAt"); applied_at = timestamp(applied["appliedAt"], "loss.appliedAt")
    require(requested_at <= applied_at, "Runtime loss predates its request")
    instances = {row["runId"]: row for row in cohort["instances"]}
    for index, route in enumerate(routes):
        created = timestamp(instances[route["runId"]]["createdAt"], "instance.createdAt")
        require(created <= requested_at if index < spec["beforeIndex"] else created >= applied_at, "Instance crossed the declared loss barrier")


class ResetGuard:
    """Own the vacated TCP endpoint; accept then reset without reading payloads."""
    def __init__(self, port):
        import socket, threading
        self.socket = socket.socket(); self.accepted = 0; self.reset = 0; self.errors = []
        self.stopped = threading.Event()
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind(("127.0.0.1", port)); self.socket.listen(8); self.socket.settimeout(.1)
            self.thread = threading.Thread(target=self._run, daemon=True); self.thread.start()
        except BaseException:
            self.socket.close(); raise

    def _run(self):
        import socket, struct
        while not self.stopped.is_set():
            try: connection, _ = self.socket.accept()
            except socket.timeout: continue
            except OSError as error:
                if not self.stopped.is_set(): self.errors.append(type(error).__name__)
                break
            self.accepted += 1
            try:
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                connection.close(); self.reset += 1
            except OSError as error:
                self.errors.append(type(error).__name__); connection.close()

    def close(self):
        self.stopped.set(); self.thread.join(2); self.socket.close()
        require(not self.thread.is_alive(), "Transport reset guard did not stop")

    def receipt(self):
        return {"schemaVersion": "agat.decision.public-workflow-reset-guard.v1", "acceptedConnections": self.accepted,
                "resetConnections": self.reset, "errors": list(self.errors), "payloadsRead": False}


def stop_and_reserve(process, port, runtime, owned, errors):
    """Stop a live owned Popen session, then guard only its original endpoint."""
    import os
    require(process.poll() is None and process.pid != os.getpid() and os.getpgid(process.pid) == process.pid
            and type(port) is int and 1 <= port <= 65535 and port != 8766, "Loss requires a live owned process session and experimental port")
    runtime_owned = set(); runtime.stop_owned_process(process, runtime_owned, errors); owned.update(runtime_owned)
    require(process.poll() is not None and not errors and runtime.remaining_owned_processes(runtime_owned, errors) == [] and not errors,
            "Owned runtime did not stop cleanly")
    return ResetGuard(port)


def publish_applied(directory, applied):
    """The reader only sees the completed private receipt, without overwrite."""
    import os
    from scripts.lib.decision_public_sources import write_json_new
    pending = directory/"runtime-loss-applied.pending.json"; final = directory/"runtime-loss-applied.json"
    write_json_new(pending, applied)
    try: os.link(pending, final)
    finally: pending.unlink()
