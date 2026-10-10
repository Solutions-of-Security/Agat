"""Verify recorded Python spans without executing or authenticating a model."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

SCHEMA = "agat.decision.native-python-observation.v1"
MAX_JOURNAL_BYTES = 8 * 1024 * 1024
KINDS = ("backend_score", "text_backbone", "mx_eval")
COMMON = {"schemaVersion", "sequence", "previousSha256", "sha256", "event"}
BINDING = {"scoreId", "inputSha256", "threadId", "pid"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def is_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def unique_pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "Duplicate JSON member")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("Nonfinite JSON number")


def counts(events):
    values = Counter(event["kind"] for event in events)
    return {kind: values[kind] for kind in KINDS}


def verify_journal(path, expected_file_sha256, *, expected_observer_sha256,
                   expected_identity_sha256=None, expected_input_sha256s=None):
    """Check integrity/order and report incomplete spans without inferring work.

    Pins bind bytes and declared metadata. They do not revalidate observer/runtime
    sources or prove the model actually ran. A valid prefix is diagnostic evidence;
    only a nonempty trace with all spans ended and hooks confirmed is complete.
    """
    require(is_sha(expected_file_sha256) and is_sha(expected_observer_sha256), "Invalid external SHA pin")
    require(expected_identity_sha256 is None or is_sha(expected_identity_sha256), "Invalid identity SHA pin")
    if expected_input_sha256s is not None:
        require(type(expected_input_sha256s) in (list, tuple) and len(expected_input_sha256s) <= 10000
                and all(is_sha(value) for value in expected_input_sha256s), "Invalid input sequence pins")
    path = Path(path)
    require(not path.is_symlink() and path.is_file(), "Journal must be a regular nonsymlink file")
    with path.open("rb") as stream:
        raw = stream.read(MAX_JOURNAL_BYTES + 1)
    require(0 < len(raw) <= MAX_JOURNAL_BYTES, "Journal size outside bounds")
    require(digest(raw) == expected_file_sha256, "Journal bytes differ from external SHA pin")
    require(raw.endswith(b"\n"), "Unfinished JSONL record")
    records = [json.loads(line, object_pairs_hook=unique_pairs, parse_constant=reject_constant)
               for line in raw.splitlines()]
    require(bool(records), "Journal header is required")
    header = records[0]
    require(isinstance(header, dict) and set(header) == COMMON | {
        "pid", "identity", "clocks", "capturesRequestText", "modelLoadObserved"}, "Invalid header fields")
    require(header["event"] == "header" and integer(header["pid"], 1), "Invalid header identity")
    require(header["clocks"] == {"host": "perf_counter_ns", "threadCpu": "thread_time_ns"}, "Unsupported observation clocks")
    require(header["capturesRequestText"] is False and header["modelLoadObserved"] is False, "Unsupported observation scope")
    identity = header["identity"]
    require(isinstance(identity, dict) and isinstance(identity.get("pythonCallObservation"), dict), "Missing observer identity")
    marker = identity["pythonCallObservation"]
    require(set(marker) == {"schemaVersion", "observerSourceSha256", "kind", "gpuKernelsMeasured",
                            "gpuTimeMeasured", "instrumentationOverheadExcluded"}, "Invalid observer marker fields")
    require(marker["schemaVersion"] == SCHEMA and marker["kind"] == "scoped-python-hooks", "Unsupported observer schema")
    require(marker["observerSourceSha256"] == expected_observer_sha256, "Declared observer source differs from pin")
    require(all(marker[key] is False for key in ("gpuKernelsMeasured", "gpuTimeMeasured", "instrumentationOverheadExcluded")),
            "Unsupported measurement claim")
    identity_sha = digest(canonical(identity))
    if expected_identity_sha256 is not None:
        require(identity_sha == expected_identity_sha256, "Header identity differs from pin")
    previous = None
    stack = []
    starts = {}
    ends = {}
    hooks = {}
    scores = []
    owner_thread = None
    previous_host = None
    previous_cpu = None
    for sequence, event in enumerate(records):
        require(isinstance(event, dict), "Journal record must be an object")
        require(integer(event.get("sequence")) and event["sequence"] == sequence, "Invalid record sequence")
        require(event.get("schemaVersion") == SCHEMA, "Unsupported record schema")
        require(event.get("previousSha256") == previous and is_sha(event.get("sha256")), "Broken record chain")
        require(digest(canonical({k: v for k, v in event.items() if k != "sha256"})) == event["sha256"], "Invalid record seal")
        previous = event["sha256"]
        if sequence == 0:
            continue
        require(event.get("event") in ("call_start", "call_end", "hooks_restored"), "Unsupported record type")
        require(BINDING <= event.keys() and integer(event["scoreId"], 1) and is_sha(event["inputSha256"]), "Invalid score binding")
        require(event["pid"] == header["pid"] and integer(event["pid"], 1) and integer(event["threadId"], 1), "Invalid process/thread binding")
        if owner_thread is None:
            owner_thread = event["threadId"]
        require(event["threadId"] == owner_thread, "Observation owner thread changed")
        if event["event"] == "hooks_restored":
            require(set(event) == COMMON | BINDING | {"modelObjectPreserved", "modelCallRestored", "evalRestored"}, "Invalid hook fields")
            require(len(stack) == 1 and starts[stack[0]]["kind"] == "backend_score", "Hook restoration outside backend scope")
            require(event["scoreId"] not in hooks, "Duplicate hook restoration")
            backend = starts[stack[0]]
            require(all(event[key] == backend[key] for key in BINDING), "Hook binding changed")
            require(all(type(event[key]) is bool for key in ("modelObjectPreserved", "modelCallRestored", "evalRestored")), "Hook flags must be booleans")
            hooks[event["scoreId"]] = event
            continue
        span_fields = BINDING | {"spanId", "kind", "hostNs", "threadCpuNs"}
        end_fields = {"status", "exceptionType", "hostElapsedNs", "threadCpuElapsedNs"}
        require(set(event) == COMMON | span_fields | (end_fields if event["event"] == "call_end" else set()), "Invalid call fields")
        require(integer(event["spanId"], 1) and event["kind"] in KINDS, "Invalid Python span identity")
        require(integer(event["hostNs"]) and integer(event["threadCpuNs"]), "Invalid span clocks")
        require(previous_host is None or event["hostNs"] >= previous_host, "Host clock went backwards")
        require(previous_cpu is None or event["threadCpuNs"] >= previous_cpu, "Thread CPU clock went backwards")
        previous_host, previous_cpu = event["hostNs"], event["threadCpuNs"]
        if event["event"] == "call_start":
            require(event["spanId"] == len(starts) + 1, "Invalid span sequence")
            if event["kind"] == "backend_score":
                require(not stack and event["scoreId"] == len(scores) + 1, "Concurrent or unordered backend score")
                scores.append(event)
            else:
                require(bool(stack) and starts[stack[0]]["kind"] == "backend_score", "Nested call outside backend score")
                backend = starts[stack[0]]
                require(all(event[key] == backend[key] for key in BINDING), "Nested call binding changed")
                require(event["scoreId"] not in hooks, "Call after hook restoration")
            starts[event["spanId"]] = event
            stack.append(event["spanId"])
        else:
            require(bool(stack) and stack[-1] == event["spanId"], "Unpaired or out-of-order call end")
            start = starts[event["spanId"]]
            require(all(event[key] == start[key] for key in BINDING | {"spanId", "kind"}), "Call-end binding changed")
            for clock, elapsed in (("hostNs", "hostElapsedNs"), ("threadCpuNs", "threadCpuElapsedNs")):
                require(integer(event[elapsed]) and event[elapsed] == event[clock] - start[clock], "Invalid elapsed span")
            require(event["status"] in ("returned", "raised"), "Unsupported call-end status")
            exception = event["exceptionType"]
            require((event["status"] == "returned" and exception is None) or
                    (event["status"] == "raised" and isinstance(exception, str) and 0 < len(exception) <= 128
                     and "\n" not in exception and "\r" not in exception), "Invalid exception status")
            stack.pop()
            ends[event["spanId"]] = event
    input_shas = [event["inputSha256"] for event in scores]
    expected_inputs = None if expected_input_sha256s is None else list(expected_input_sha256s)
    if expected_inputs is not None:
        require(len(input_shas) <= len(expected_inputs) and input_shas == expected_inputs[:len(input_shas)],
                "Recorded input sequence differs from pins")
    unfinished = [event for span, event in starts.items() if span not in ends]
    all_ended = not unfinished
    hooks_confirmed = bool(scores) and all(
        event["scoreId"] in hooks and all(hooks[event["scoreId"]][key] is True
            for key in ("modelObjectPreserved", "modelCallRestored", "evalRestored")) for event in scores)
    recorded_complete = bool(scores) and all_ended and hooks_confirmed
    expected_fully_recorded = None if expected_inputs is None else input_shas == expected_inputs
    complete = recorded_complete and expected_fully_recorded is not False
    per_score = []
    for score in scores:
        number = score["scoreId"]
        ended = [event for event in ends.values() if event["scoreId"] == number]
        per_score.append({"scoreId": number, "inputSha256": score["inputSha256"],
                          "backendStatus": ends.get(score["spanId"], {}).get("status", "unfinished"),
                          "entries": counts(e for e in starts.values() if e["scoreId"] == number),
                          "returns": counts(e for e in ended if e["status"] == "returned"),
                          "raises": counts(e for e in ended if e["status"] == "raised"),
                          "unfinished": counts(e for e in unfinished if e["scoreId"] == number),
                          "hooksConfirmedRestored": number in hooks and all(hooks[number][key] is True
                              for key in ("modelObjectPreserved", "modelCallRestored", "evalRestored"))})
    result = {"schemaVersion": "agat.decision.native-python-journal-verification.v1",
              "status": "verified" if complete else "incomplete", "journalFileSha256": digest(raw),
              "recordCount": len(records), "headerIdentitySha256": identity_sha,
              "observerDeclaredSourceSha256": expected_observer_sha256,
              "journalExternalBytePinMatched": True, "observerDeclaredSourcePinMatched": True,
              "identityBindingMatched": expected_identity_sha256 is not None,
              "inputSequenceBindingMatched": expected_input_sha256s is not None,
              "expectedInputSequenceSha256": None if expected_inputs is None else digest(canonical(expected_inputs)),
              "expectedInputCount": None if expected_inputs is None else len(expected_inputs),
              "recordedInputCount": len(input_shas), "expectedInputSequenceFullyRecorded": expected_fully_recorded,
              "unstartedExpectedInputs": None if expected_inputs is None else len(expected_inputs) - len(input_shas),
              "allRecordedSpansEnded": all_ended, "recordedHooksConfirmedRestored": hooks_confirmed,
              "recordedScoringTraceComplete": recorded_complete,
              "completeScoringTrace": complete, "entries": counts(starts.values()),
              "returns": counts(e for e in ends.values() if e["status"] == "returned"),
              "raises": counts(e for e in ends.values() if e["status"] == "raised"),
              "unfinished": counts(unfinished), "scores": per_score, "requestTextCaptureDeclared": False,
              "observerCodeRevalidated": False, "runtimeSourcesRevalidated": False,
              "modelExecutionAuthenticated": False, "modelCallsDuringVerification": 0,
              "modelLoadObserved": False, "gpuKernelsMeasured": False, "gpuTimeMeasured": False,
              "instrumentationOverheadExcluded": False, "classificationAccuracyMeasured": False,
              "routingEnabled": False, "qualification": "not_assessed"}
    result["sha256"] = digest(canonical(result))
    return result
