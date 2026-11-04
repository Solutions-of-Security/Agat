"""Bounded paired load against resident local decision and generative services."""

from __future__ import annotations

import copy
import hashlib
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import Request, canonical_json, fingerprint, number
from scripts.lib.decision_baselines import ERROR_REASONS, BaselineError, LoopbackJson, development_cases, label_result
from scripts.lib.decision_performance import distribution, hardware, profile_from_health, validate_result
from workers.local_decisions import LocalDecisionClient, PROFILE

SCHEMA = "agat.decision.shared-load.v1"
MAX_CASES = 30
MAX_ROUNDS = 2
CALLS_PER_CASE_ROUND = 8
MAX_MEASURED_CALLS = MAX_CASES * MAX_ROUNDS * CALLS_PER_CASE_ROUND
PHASES = ("decision_only_before", "primary_only_before", "sequential_pair", "overlapping_pair",
          "primary_only_after", "decision_only_after")
ROOT = Path(__file__).resolve().parents[2]


def summarize(rows):
    good = [r for r in rows if r["status"] in {"ok", "abstain"}]
    return {"attempts": len(rows), "computed": len(good),
            "failures": dict(Counter(r["reason"] for r in rows if r["status"] not in {"ok", "abstain"})),
            "computedWallMs": distribution([r["wallMs"] for r in good]),
            "failedWallMs": distribution([r["wallMs"] for r in rows if r["status"] not in {"ok", "abstain"}]),
            "inputTokens": distribution([r["inputTokens"] for r in good]),
            "outputTokens": distribution([r["outputTokens"] for r in good])}


def resident_primary(primary):
    try:
        entries = primary.transport("GET", "/api/ps")["models"]
        matches = [m for m in entries if m.get("name") == primary.model and m.get("digest") == primary.identity["digest"]]
        if len(matches) != 1:
            return {"status": "not_resident"}
        entry = matches[0]
        return {"status": "resident", "name": primary.model, "digest": primary.identity["digest"],
                **{key: int(number(entry[key], 0, 2**53 - 1)) for key in ("size", "size_vram", "context_length") if key in entry}}
    except (ValueError, KeyError, TypeError, AttributeError):
        return {"status": "unavailable"}


def benchmark_shared(dataset, decision_url, primary_factory, *, rounds=1, warmup=2, time_budget_s=300,
                     timeout_ms=10000, client=None, health_transport=None, progress=None,
                     pair_observer=None, stop_on_failure=False, cancel_requested=None, expected_profile=None):
    cases = development_cases(dataset)
    requests = [Request.from_dict(case["request"]) for case in cases]
    if (not requests or len(requests) > MAX_CASES or any(r.kind not in {"choice", "boolean"} for r in requests)
            or any(len(canonical_json(r.to_dict()).encode()) > 6144 for r in requests)
            or type(rounds) is not int or not 1 <= rounds <= MAX_ROUNDS
            or len(requests) * rounds * CALLS_PER_CASE_ROUND > MAX_MEASURED_CALLS
            or type(warmup) is not int or not 1 <= warmup <= 4
            or type(time_budget_s) is not int or not 1 <= time_budget_s <= 600
            or type(timeout_ms) is not int or not 100 <= timeout_ms <= 10000):
        raise ValueError("Unsupported or excessive shared-load plan")
    if (type(stop_on_failure) is not bool or any(callback is not None and not callable(callback)
            for callback in (pair_observer, cancel_requested))):
        raise ValueError("Invalid shared-load observation controls")
    if expected_profile is not None:
        profile_from_health({"status": "ready", "mode": "shadow", "profileJson": canonical_json(expected_profile),
                             "profileSha256": fingerprint(expected_profile)})
        expected_profile = copy.deepcopy(expected_profile)
    transport = health_transport or LoopbackJson(decision_url, 5)
    client = client or LocalDecisionClient(decision_url)
    health = transport("GET", "/health")
    profile = profile_from_health(health)
    if expected_profile is not None and profile != expected_profile:
        raise ValueError("Shared-load profile differs from the frozen experiment")
    profile_sha = health["profileSha256"]
    primary = primary_factory()
    primary_identity = copy.deepcopy(primary.identity)
    source_files = ["scripts/benchmark-decision-shared-load.py", "scripts/lib/decision_shared_load.py",
                    "scripts/lib/decision_performance.py", "scripts/lib/decision_baselines.py", "workers/local_decisions.py"]
    sources = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_files}
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    stopped = None
    signatures = {"decision": {}, "primary": {}}
    changes = {"decision": set(), "primary": set()}

    def one(kind, request, gate=None):
        if gate is not None:
            gate.wait(timeout=5)
        begin = time.perf_counter()
        row = {"kind": kind, "caseId": request.id, "inputSha256": request.input_sha256,
               "startedMs": (begin - started) * 1000}
        try:
            if kind == "decision":
                envelope = client.decide({"profile": PROFILE, "profileSha256": profile_sha,
                                          "timeoutMs": timeout_ms, "request": request.to_dict()})
                if set(envelope) == {"status", "reason"} and envelope["status"] == "unavailable":
                    if envelope["reason"] not in {"timeout", "busy", "unreachable", "profile_mismatch", "invalid_response", "cancelled"}:
                        raise ValueError("Unexpected unavailable reason")
                    row.update(envelope)
                else:
                    if set(envelope) != {"result"}:
                        raise ValueError("Invalid decision envelope")
                    result = envelope["result"]
                    validate_result(result, request, profile)
                    row.update(status=result["status"], reason=result["reason"])
                    if result["status"] != "error":
                        row.update(inputTokens=result["inputTokens"], outputTokens=0,
                                   decisionSha256=fingerprint({k: result[k] for k in ("status", "reason", "value", "selectedOptionId", "distribution")}))
            else:
                predicted = primary.predict(request)
                value = label_result(request, predicted["selectedOptionId"])
                row.update(status=value["status"], reason=value["reason"], inputTokens=predicted["inputTokens"],
                           outputTokens=predicted["outputTokens"], decisionSha256=fingerprint(value))
        except BaselineError as error:
            row.update(status="error", reason=error.reason if error.reason in ERROR_REASONS else "backend_error")
        except Exception:
            # Do not store backend exception text or any source/generation content.
            row.update(status="error", reason="invalid_response")
        finished = time.perf_counter()
        row.update(finishedMs=(finished - started) * 1000, wallMs=round((finished - begin) * 1000, 3))
        if "decisionSha256" in row:
            if signatures[kind].setdefault(request.input_sha256, row["decisionSha256"]) != row["decisionSha256"]:
                changes[kind].add(request.id)
        return row

    def observed_pair(phase, index, pair):
        nonlocal stopped
        if pair_observer is not None:
            try:
                pair_observer(phase, index, copy.deepcopy(pair))
            except Exception:
                stopped = "observation_failed"
        if stopped is None and stop_on_failure:
            if any(row["status"] not in {"ok", "abstain"} for row in pair):
                stopped = "request_failed"
            elif changes["decision"]:
                stopped = "decision_changed"

    def budget_available():
        nonlocal stopped
        if stopped is not None:
            return False
        if cancel_requested is not None:
            try:
                cancelled = cancel_requested()
            except Exception:
                stopped = "observation_failed"
                return False
            if cancelled:
                stopped = "cancelled"
                return False
        if time.perf_counter() - started >= time_budget_s:
            stopped = "time_budget"
            return False
        return True

    def sequential(request):
        pair = [one("primary", request)]
        if not (stop_on_failure and pair[0]["status"] not in {"ok", "abstain"}):
            pair.append(one("decision", request))
        return pair

    warmups = []
    for i in range(warmup):
        if not budget_available(): break
        request = requests[i % len(requests)]
        pair = sequential(request)
        warmups.extend(pair)
        observed_pair("warmup", i + 1, pair)
    resident = [{"phase": "after_warmup", **resident_primary(primary)}]
    phases = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for phase in PHASES:
            if stopped is not None: break
            rows, pairs = [], []
            for index, request in enumerate(requests * rounds):
                if not budget_available(): break
                if phase.startswith("decision_only"):
                    pair = [one("decision", request)]
                elif phase.startswith("primary_only"):
                    pair = [one("primary", request)]
                elif phase == "sequential_pair":
                    pair = sequential(request)
                else:
                    gate = threading.Barrier(2)
                    futures = [pool.submit(one, kind, request, gate) for kind in ("primary", "decision")]
                    pair = [future.result() for future in futures]
                rows.extend(pair)
                if len(pair) == 2:
                    begin, end = min(r["startedMs"] for r in pair), max(r["finishedMs"] for r in pair)
                    overlap = max(0, min(r["finishedMs"] for r in pair) - max(r["startedMs"] for r in pair))
                    pairs.append({"caseId": request.id, "elapsedMs": round(end - begin, 3), "requestOverlapMs": round(overlap, 3)})
                observed_pair(phase, index + 1, pair)
                if progress and (index + 1) % 5 == 0:
                    progress(phase, index + 1)
            phases.append({"name": phase, "rows": rows, "pairs": pairs,
                           "summary": {kind: summarize([r for r in rows if r["kind"] == kind]) for kind in ("primary", "decision")}})
            resident.append({"phase": phase, **resident_primary(primary)})
            if stopped: break
    try:
        final_health = transport("GET", "/health")
        stable = profile_from_health(final_health) == profile and final_health["profileSha256"] == profile_sha
    except Exception:
        stable = False
    try:
        primary_stable = primary.tag().get("digest") == primary_identity["digest"] and primary.identity == primary_identity
    except Exception:
        primary_stable = False
    all_rows = warmups + [r for p in phases for r in p["rows"]]
    failed = any(r["status"] not in {"ok", "abstain"} for r in all_rows)
    return sealed({"schemaVersion": SCHEMA, "createdAt": started_at,
                   "status": "observed" if stable and primary_stable and not stopped and not failed and not changes["decision"] else "degraded",
                   "qualifiedForRouting": False, "dataset": {"id": dataset["id"], "sha256": dataset["sha256"], "caseIds": [r.id for r in requests]},
                   "decisionProfile": profile, "decisionProfileSha256": profile_sha, "decisionProfileStable": stable,
                   "primary": primary_identity, "primaryStable": primary_stable, "host": hardware(), "harnessFiles": sources,
                   "plan": {"phaseOrder": list(PHASES), "rounds": rounds, "warmupPairs": warmup,
                            "timeBudgetSeconds": time_budget_s, "decisionTimeoutMs": timeout_ms,
                            "expectedMeasuredRequests": len(requests) * rounds * CALLS_PER_CASE_ROUND, "maxConcurrentCallsPerModel": 1,
                            "retry": False, "modelLoadExcluded": True,
                            "stopOnFailure": stop_on_failure, "pairObservation": pair_observer is not None,
                            "cooperativeCancellation": cancel_requested is not None,
                            "expectedProfilePinned": expected_profile is not None},
                   "elapsedMs": round((time.perf_counter() - started) * 1000, 3), "stoppedReason": stopped,
                   "warmup": warmups, "phases": phases, "primaryResidence": resident,
                   "repeatDecisionChanges": {kind: sorted(ids) for kind, ids in changes.items()},
                   "limits": ["Overlapping HTTP calls do not prove simultaneous GPU kernels or measure GPU utilization.",
                              "Sequential pair resembles primary then shadow; overlapping pair represents independent concurrent work.",
                              "Both model services remain loaded during single-service control phases.",
                              "The primary is a real local structured label generator, not a full production agent workflow.",
                              "Request/template caches, thermal state and other applications are not controlled.",
                              "Ollama residence is reported by /api/ps snapshots; no combined peak memory claim.",
                              "Only repeated development inputs; no independent quality or production SLO claim.",
                              "Time budget is checked between pairs; HTTP timeout does not guarantee cancellation of GPU work."]})
