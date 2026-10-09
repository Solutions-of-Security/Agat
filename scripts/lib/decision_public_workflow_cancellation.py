"""Actual coordinator cancellation after native completion, before caller delivery."""
import hashlib
import re

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import fields, fingerprint, number, parse_json
from scripts.lib import decision_public_workflow_timeout as timeout
from scripts.lib.decision_shadow_pilot import require, timestamp
from scripts.lib.decision_shadow_sli import same_json

PLAN_SCHEMA = "agat.decision.public-workflow-plan.v5"
RESULT_SCHEMA = "agat.decision.public-workflow-result.v5"
TRANSPORT_SCHEMA = "agat.decision.public-workflow-cancellation-transport.v1"
READY_SCHEMA = "agat.decision.public-workflow-cancellation-ready.v1"
DRAIN_SCHEMA = "agat.decision.public-workflow-cancellation-drained.v1"
APPLIED_SCHEMA = "agat.decision.public-workflow-cancellation-applied.v1"
ARTIFACTS = {"caller-cancellation-transport.json", "coordinator-cancellation-ready.json", "coordinator-cancellation-drained.json",
             "coordinator-cancellation-applied.json", "coordinator-cancellation-http.json", "coordinator-cancellation-before.http.json"}


def cancellation_spec(context, target_index):
    target = timeout.timeout_spec(context, target_index)
    return {**target, "kind": "cancel_actual_run_while_owned_proxy_withholds_completed_response",
            "cancelEndpoint": "authenticated_coordinator_run_cancel", "cancelRequestDeadlineMs": 5000,
            "cancelToEofDeadlineMs": 2500, "cancelledDurableReturn": "unknown"}


def ready_receipt(row):
    return sealed({"schemaVersion": READY_SCHEMA, "targetIndex": row["index"], "caseId": row["caseId"],
        "stageId": row["stageId"], "inputSha256": row["inputSha256"], "profileSha256": row["profileSha256"],
        "requestBodySha256": row["requestBodySha256"], "responseBodySha256": row["responseBodySha256"],
        "acceptedAt": row["acceptedAt"], "upstreamCompletedAt": row["upstreamCompletedAt"],
        "upstreamCompletedNormally": True, "responseBytesWritten": 0})


def drain_receipt(row):
    original = timeout.drain_receipt(row)
    return sealed({**{k: v for k, v in original.items() if k not in {"sha256", "schemaVersion"}}, "schemaVersion": DRAIN_SCHEMA})


def receipt_bundle(raw):
    require(ARTIFACTS <= set(raw), "Missing cancellation HTTP or transport receipts")
    return {"ready": parse_json(raw["coordinator-cancellation-ready.json"]),
            "drained": parse_json(raw["coordinator-cancellation-drained.json"]),
            "applied": parse_json(raw["coordinator-cancellation-applied.json"]),
            "http": parse_json(raw["coordinator-cancellation-http.json"]),
            "before": parse_json(raw["coordinator-cancellation-before.http.json"]),
            "rawFileSha256": {name: hashlib.sha256(raw[name]).hexdigest() for name in ARTIFACTS}}


def _single_assignment(trace, stage_id):
    rows = trace["decisionCallerAccounting"]["stages"]
    require(len(rows) == 1 and rows[0]["stageId"] == stage_id and rows[0]["coverage"] == "complete"
            and len(rows[0]["assignments"]) == 1, "Caller assignment omitted or retried")
    return rows[0]["assignments"][0]


def _verify_http(bundle, cohort, routes, spec):
    records = bundle["http"]
    require(isinstance(records, list) and len(routes)*3 <= len(records) <= 2000, "Incomplete or excessive actual HTTP journal")
    for record in records:
        fields(record, {"method", "path", "startedAt", "finishedAt", "startedMs", "finishedMs", "requestBody", "requestBodySha256", "requestBodyComplete", "httpStatus"})
        require(record["method"] == "POST" and isinstance(record["path"], str)
                and re.fullmatch(r"/api/v1/leases/[^/?#]+/(renew|decision-shadow(?:/intent)?|complete|fail)", record["path"]),
                "Unexpected or unbound lease HTTP request")
        require(isinstance(record["requestBody"], str) and type(record["requestBodyComplete"]) is bool
                and (record["requestBodyComplete"] or record["path"].endswith("/renew")), "Required HTTP body was not completely observed")
        body = record["requestBody"].encode()
        require(len(body) <= 128*1024 and hashlib.sha256(body).hexdigest() == record["requestBodySha256"], "HTTP request bytes changed")
        require(timestamp(record["startedAt"], "http.startedAt") <= timestamp(record["finishedAt"], "http.finishedAt")
                and number(record["startedMs"], 0, 600000) <= number(record["finishedMs"], 0, 600000)
                and type(record["httpStatus"]) is int, "Invalid actual HTTP boundary")
    traces = {t["run"]["id"]: t for t in cohort["traces"]}
    intents = [r for r in records if r["path"].endswith("/decision-shadow/intent")]
    returns = [r for r in records if r["path"].endswith("/decision-shadow")]
    completions = [r for r in records if r["path"].endswith("/complete")]
    require(len(intents) == len(returns) == len(completions) == len(routes), "Missing, repeated or extra intent/return/primary POST")
    for intent in intents:
        body = fields(parse_json(intent["requestBody"]), {"schemaVersion", "assignmentId"})
        require(body["schemaVersion"] == "agat.decision.caller-accounting.v1" and isinstance(body["assignmentId"], str), "Unknown caller intent contract")
    leases = set(); target_return = None; target_lease = None; target_primary = None
    for index, route in enumerate(routes):
        trace = traces[route["runId"]]
        assigned = _single_assignment(trace, route["stageId"])
        matches = [r for r in intents if parse_json(r["requestBody"]).get("assignmentId") == assigned["assignmentId"]]
        require(len(matches) == 1 and matches[0]["httpStatus"] == 200, "Actual intent does not bind durable assignment")
        lease_path = matches[0]["path"].removesuffix("/decision-shadow/intent")
        require(lease_path not in leases, "Lease reused across cases")
        leases.add(lease_path)
        submitted = [r for r in returns if r["path"] == lease_path+"/decision-shadow"]
        primary = [r for r in completions if r["path"] == lease_path+"/complete"]
        require(len(submitted) == len(primary) == 1 and parse_json(primary[0]["requestBody"])["output"] == "PRIMARY_OUTPUT",
                "Primary output changed or actual HTTP return missing")
        if index == spec["targetIndex"]:
            require(submitted[0]["httpStatus"] == primary[0]["httpStatus"] == 400, "Revoked lease accepted a late write")
            target_return = submitted[0]; target_lease = lease_path; target_primary = primary[0]
        else:
            posted = fields(parse_json(submitted[0]["requestBody"]), {"result", "callerTiming"})
            stored = trace["decisionObservations"][0]["observation"]
            require(submitted[0]["httpStatus"] == primary[0]["httpStatus"] == 200
                    and same_json(posted, {key: stored[key] for key in ("result", "callerTiming")}),
                    "Healthy HTTP return differs from durable observation")
    require(all(r["path"].rsplit("/", 1)[0] in leases for r in records if r["path"].endswith(("/renew", "/fail"))),
            "Actual HTTP journal contains a lease outside the original cohort")
    failures = [r for r in records if r["path"].endswith("/fail")]
    require(len(failures) == 1 and failures[0]["path"] == target_lease+"/fail" and failures[0]["httpStatus"] == 400,
            "Unexpected failure request, missing cleanup attempt or revived cancelled lease")
    failed = fields(parse_json(failures[0]["requestBody"]), {"error"})
    require(isinstance(failed["error"], str) and 0 < len(failed["error"]) <= 4096
            and timestamp(target_primary["finishedAt"], "completion.finishedAt") <= timestamp(failures[0]["startedAt"], "failure.startedAt"),
            "Cancelled worker failure handling did not follow rejected primary completion")
    renewals = [r for r in records if r["path"].endswith("/renew") and r["httpStatus"] != 204]
    require(len(renewals) == 1 and renewals[0]["path"] == target_lease+"/renew" and renewals[0]["httpStatus"] == 404,
            "Actual ownership revocation was not observed or another lease failed")
    require(timestamp(bundle["applied"]["requestStartedAt"], "cancel.startedAt") <= timestamp(renewals[0]["finishedAt"], "renewal.finishedAt")
            <= timestamp(target_return["startedAt"], "return.startedAt"), "Revocation and late return are reordered")
    local = fields(parse_json(target_return["requestBody"]), {"status", "reason", "callerTiming"})
    require(local["status"] == "unavailable" and local["reason"] == "cancelled", "Caller timed out or accepted undelivered model response")
    timing = fields(local["callerTiming"], {"schemaVersion", "clock", "boundary", "durationMs"})
    require(timing["schemaVersion"] == "agat.decision.caller-timing.v1" and timing["clock"] == "monotonic"
            and timing["boundary"] == "local_http_call", "Missing actual local cancellation timing")
    return number(timing["durationMs"], 0, spec["callerTimeoutMs"]), target_return


def verify_transport(context, spec, transport, cohort, routes, bundle):
    fields(bundle, {"ready", "drained", "applied", "http", "before", "rawFileSha256"})
    fields(bundle["rawFileSha256"], ARTIFACTS)
    diagnostics = {}
    def target_check(row, _result):
        index = spec["targetIndex"]; route = routes[index]
        ready = verify_seal(bundle["ready"], READY_SCHEMA)
        require(fingerprint(ready) == fingerprint(ready_receipt(row)), "Ready barrier does not bind completed upstream bytes")
        drained = verify_seal(bundle["drained"], DRAIN_SCHEMA)
        require(fingerprint(drained) == fingerprint(drain_receipt(row)), "Actual EOF drain barrier changed")
        applied = fields(bundle["applied"], {"schemaVersion", "targetIndex", "caseId", "runId", "instanceId", "stageId", "inputSha256", "profileSha256",
            "readyFileSha256", "beforeTraceFileSha256", "requestMethod", "requestPath", "requestBody", "requestBodySha256",
            "unauthenticatedStatus", "httpStatus", "responseBody", "responseBodySha256", "requestStartedAt", "responseCompletedAt", "cancelRequestElapsedMs"})
        require(applied["schemaVersion"] == APPLIED_SCHEMA and type(applied["targetIndex"]) is int and applied["targetIndex"] == index
                and all(applied[key] == route[key] for key in ("caseId", "runId", "instanceId", "stageId", "inputSha256"))
                and applied["profileSha256"] == context["profileSha256"], "Cancel API applied to a different instance or input")
        require(applied["readyFileSha256"] == bundle["rawFileSha256"]["coordinator-cancellation-ready.json"]
                and applied["beforeTraceFileSha256"] == bundle["rawFileSha256"]["coordinator-cancellation-before.http.json"], "Cancel API does not bind raw barriers")
        empty_sha = hashlib.sha256(b"").hexdigest()
        require(applied["requestMethod"] == "POST" and applied["requestPath"] == "/api/v1/runs/"+route["runId"]+"/cancel"
                and applied["requestBody"] == applied["responseBody"] == ""
                and applied["requestBodySha256"] == applied["responseBodySha256"] == empty_sha
                and type(applied["unauthenticatedStatus"]) is int and applied["unauthenticatedStatus"] == 401
                and type(applied["httpStatus"]) is int and applied["httpStatus"] == 204, "Cancel authentication or HTTP contract changed")
        started = timestamp(applied["requestStartedAt"], "cancel.startedAt")
        completed = timestamp(applied["responseCompletedAt"], "cancel.completedAt")
        finished = timestamp(row["finishedAt"], "proxy.finishedAt")
        require(timestamp(row["upstreamCompletedAt"], "upstream.completedAt") <= started <= completed
                and started <= finished, "Cancel did not follow completed upstream response")
        api_ms = number(applied["cancelRequestElapsedMs"], 0, spec["cancelRequestDeadlineMs"])
        require(abs((completed-started).total_seconds()*1000-api_ms) <= 10, "Cancel API timing differs from actual boundaries")
        cancel_ms = (finished-started).total_seconds()*1000
        require(0 <= cancel_ms <= spec["cancelToEofDeadlineMs"] and row["upstreamMs"] < row["elapsedMs"], "EOF did not follow prompt lease cancellation")
        before = bundle["before"]
        require(before["run"]["id"] == route["runId"] and before["run"]["status"] == "running" and before["decisionObservations"] == [],
                "Cancellation target was not an active, unobserved shadow lease")
        current = next(t for t in cohort["traces"] if t["run"]["id"] == route["runId"])
        original = _single_assignment(before, route["stageId"]); ended = _single_assignment(current, route["stageId"])
        require(original["assignmentId"] == ended["assignmentId"] and original["intent"] is True and original["negotiated"] is True
                and original["returned"] is None and original["outcome"] == "intent_pending"
                and ended["returned"] is None and ended["outcome"] == "return_missing", "Intent was missing or unknown return was fabricated")
        suffix = next(i for i in cohort["instances"] if i["runId"] == routes[index+1]["runId"])
        require(finished <= timestamp(suffix["createdAt"], "suffix.createdAt"), "Suffix started before the cancelled handler drained")
        caller_ms, late = _verify_http(bundle, cohort, routes, spec)
        require(row["upstreamMs"] <= caller_ms and abs(row["elapsedMs"]-caller_ms) <= 250
                and started <= timestamp(late["startedAt"], "late.startedAt"), "Local timing omits upstream work or predates cancellation")
        diagnostics.update(localCancelledCallerMs=caller_ms, cancelRequestToEofMs=round(cancel_ms, 3),
            lateObservationHttpStatus=400, latePrimaryCompletionHttpStatus=400, cancelledDurableReturn=None,
            attemptedCallerReturns=len(routes), cancelledAssignmentOutcome="return_missing",
            incompleteRenewalRequestBodies=sum(r["requestBodyComplete"] is False for r in bundle["http"]))
    evidence = timeout._verify_transport(context, spec, transport, cohort, routes, spec_factory=cancellation_spec,
                                        transport_schema=TRANSPORT_SCHEMA, target_check=target_check)
    return {**evidence, **diagnostics}


class CoordinatorCancellationProxy(timeout.DeadlineProxy):
    def __init__(self, upstream_port, context, spec):
        self.ready = None
        super().__init__(upstream_port, context, spec, spec_factory=cancellation_spec)

    def _target_completed(self, row):
        with self.lock:
            require(self.ready is None, "Repeated cancellation target")
            self.ready = ready_receipt(row)

    def ready_receipt(self):
        with self.lock: return self.ready

    def target_receipt(self):
        with self.lock:
            row = next((r for r in self.rows if r["index"] == self.spec["targetIndex"]), None)
            return drain_receipt(row) if row is not None else None

    def receipt(self):
        original = super().receipt()
        return sealed({**{k: v for k, v in original.items() if k not in {"sha256", "schemaVersion"}}, "schemaVersion": TRANSPORT_SCHEMA})
