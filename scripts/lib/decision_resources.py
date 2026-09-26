"""Measure a fresh decision process, separating load, first request and warm work."""

from __future__ import annotations

import hashlib
import copy
import platform
import resource
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import Policy, Request, fingerprint
from decision_runtime.engine import DecisionEngine
from scripts.lib.decision_baselines import development_cases
from scripts.lib.decision_performance import distribution, hardware, validate_result

SCHEMA = "agat.decision.resources.v1"
ROOT = Path(__file__).resolve().parents[2]


def process_usage():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    system = platform.system()
    # ru_maxrss units are platform-specific. Never guess for another OS.
    peak = int(usage.ru_maxrss) * (1 if system == "Darwin" else 1024) if system in {"Darwin", "Linux"} else None
    return {"peakRssBytes": peak, "cpuUserSeconds": usage.ru_utime, "cpuSystemSeconds": usage.ru_stime}


class MlxMemory:
    def __init__(self, backend):
        self.mx = backend.mx

    def sample(self):
        self.mx.synchronize()
        return {"activeBytes": int(self.mx.get_active_memory()), "cacheBytes": int(self.mx.get_cache_memory()),
                "peakActiveBytes": int(self.mx.get_peak_memory()), "process": process_usage()}

    def set_cache_limit(self, limit):
        return int(self.mx.set_cache_limit(limit))

    def device(self):
        info = self.mx.device_info()
        # Allowlist hardware properties; do not include any future device identifiers.
        return {key: info[key] for key in ("device_name", "architecture", "memory_size", "total_memory",
                                           "max_recommended_working_set_size") if key in info}


def summarize_requests(rows):
    scored = [row for row in rows if row["status"] in {"ok", "abstain"}]
    return {"attempts": len(rows), "scored": len(scored), "statuses": dict(Counter(r["status"] for r in rows)),
            "failures": dict(Counter(r["reason"] for r in rows if r["status"] not in {"ok", "abstain"})),
            "scoredWallMs": distribution([r["wallMs"] for r in scored]),
            "failedWallMs": distribution([r["wallMs"] for r in rows if r["status"] not in {"ok", "abstain"}]),
            "runtimeMs": distribution([r["runtimeMs"] for r in scored]),
            "inputTokens": distribution([r["inputTokens"] for r in scored])}


def profile_resources(dataset, backend_factory, *, policy=None, rounds=4, warmup=3, time_budget_s=120,
                      cache_limit_mib=None, memory_factory=MlxMemory, progress=None):
    cases = development_cases(dataset)
    requests = [Request.from_dict(case["request"]) for case in cases]
    if (not requests or len(requests) > 100 or any(r.kind not in {"choice", "boolean"} for r in requests)
            or type(rounds) is not int or not 1 <= rounds <= 20
            or type(warmup) is not int or not 0 <= warmup <= 20
            or type(time_budget_s) is not int or not 1 <= time_budget_s <= 300
            or (cache_limit_mib is not None and (type(cache_limit_mib) is not int or not 0 <= cache_limit_mib <= 4096))
            or len(requests) * rounds > 600):
        raise ValueError("Unsupported or excessive resource profiling plan")
    started_at = datetime.now(timezone.utc).isoformat()
    before = process_usage()
    started = time.perf_counter()
    backend = backend_factory()
    loaded = time.perf_counter()
    engine = DecisionEngine(backend, policy or Policy())
    profile = copy.deepcopy(engine.profile())
    pinned = fingerprint(profile)
    memory = memory_factory(backend)
    runtime_cache_limit = backend.identity.get("allocatorCacheLimitBytes")
    cache_limit = runtime_cache_limit if cache_limit_mib is None else cache_limit_mib * 1024 * 1024
    previous_cache_limit = memory.set_cache_limit(cache_limit) if cache_limit_mib is not None else None
    samples = []
    rows = []
    signatures = {}
    changes = set()
    stopped = None

    def sample(phase):
        value = memory.sample()
        for key in ("activeBytes", "cacheBytes", "peakActiveBytes"):
            if type(value.get(key)) is not int or value[key] < 0:
                raise ValueError("Invalid memory counter")
        samples.append({"phase": phase, "elapsedMs": round((time.perf_counter() - started) * 1000, 3), **value})

    sample("after_load")
    if progress:
        progress("loaded", round((loaded - started) * 1000, 3), 0)

    def one(request, phase, round_index):
        nonlocal stopped
        if time.perf_counter() - started >= time_budget_s:
            stopped = "time_budget"
            return False
        begin = time.perf_counter()
        result = engine.decide(request.to_dict())
        end = time.perf_counter()
        row = {"phase": phase, "round": round_index, "caseId": request.id, "inputSha256": request.input_sha256,
               "wallMs": round((end - begin) * 1000, 3), "startedMs": round((begin - started) * 1000, 3)}
        try:
            validate_result(result, request, profile)
            row.update(status=result["status"], reason=result["reason"], runtimeMs=result["durationMs"])
            if result["status"] != "error":
                row["inputTokens"] = result["inputTokens"]
                signature = fingerprint({key: result[key] for key in ("status", "reason", "value", "selectedOptionId", "distribution")})
                row["decisionSha256"] = signature
                if signatures.setdefault(request.input_sha256, signature) != signature:
                    changes.add(request.id)
        except (ValueError, TypeError, KeyError, OverflowError):
            row.update(status="error", reason="invalid_response")
        rows.append(row)
        return True

    first = one(requests[0], "first_request", 0)
    sample("after_first_request" if first else "before_first_request_budget_exhausted")
    for i in range(warmup):
        if not one(requests[i % len(requests)], "warmup", 0):
            break
    sample("after_warmup")
    for round_index in range(1, rounds + 1):
        for request in requests:
            if not one(request, "measured", round_index):
                break
        sample(f"round_{round_index}")
        if progress:
            progress("round", round_index, sum(r["phase"] == "measured" for r in rows))
        if stopped:
            break
    finished = time.perf_counter()
    stable = fingerprint(engine.profile()) == pinned
    usage = process_usage()
    measured = [row for row in rows if row["phase"] == "measured"]
    failures = sum(row["status"] == "error" for row in rows)
    hardware_info = hardware()
    hardware_info.pop("serverResourcesMeasured", None)
    source_files = ["scripts/profile-local-decisions.py", "scripts/lib/decision_resources.py",
                    "scripts/lib/decision_performance.py", "scripts/lib/decision_baselines.py"]
    return sealed({"schemaVersion": SCHEMA, "createdAt": started_at,
                   "status": "observed" if stable and not failures and not stopped and not changes else "degraded",
                   "qualifiedForRouting": False, "profile": profile, "profileSha256": pinned, "profileStable": stable,
                   "implementation": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_files},
                   "dataset": {"id": dataset["id"], "sha256": dataset["sha256"],
                               "caseIds": [r.id for r in requests], "split": "development"},
                   "hardware": {**hardware_info, "mlxDevice": memory.device()},
                   "plan": {"rounds": rounds, "warmup": warmup, "firstRequest": 1,
                            "measuredAttempts": len(requests) * rounds, "timeBudgetSeconds": time_budget_s,
                            "concurrency": 1, "cacheClearedBetweenRequests": False,
                            "allocatorCacheLimitBytes": cache_limit, "previousAllocatorCacheLimitBytes": previous_cache_limit,
                            "allocatorCacheControl": "profiling_override" if cache_limit_mib is not None
                                else "runtime_profile" if "allocatorCacheLimitBytes" in backend.identity else "upstream_default",
                            "cacheLimitAppliedAfterLoad": cache_limit is not None},
                   "loadWallMs": round((loaded - started) * 1000, 3),
                   "elapsedMs": round((finished - started) * 1000, 3), "stoppedReason": stopped,
                   "processBeforeLoad": before, "processAfterWork": usage,
                   "memory": {"samples": samples, "peakActiveBytes": max(s["peakActiveBytes"] for s in samples),
                              "maxObservedActivePlusCacheBytes": max(s["activeBytes"] + s["cacheBytes"] for s in samples)},
                   "firstRequest": next((r for r in rows if r["phase"] == "first_request"), None),
                   "warmup": [r for r in rows if r["phase"] == "warmup"],
                   "measured": measured, "summary": summarize_requests(measured),
                   "roundSummaries": [{"round": i, **summarize_requests([r for r in measured if r["round"] == i])}
                                      for i in sorted({r["round"] for r in measured})],
                   "repeatDecisionChanges": sorted(changes),
                   "limits": ["Fresh Python process only when invoked through the standalone CLI.",
                              "OS file and shader caches are not purged; this is not a cold-disk measurement.",
                              "Load includes manifest verification, dependency import, tokenizer and weight loading.",
                              "MLX active, cache and process peak RSS overlap on unified memory; do not add them.",
                              "Peak active excludes cache; active plus cache is sampled, not a continuous total peak.",
                              "An optional allocator cache limit is process-wide, excludes active buffers, and reclaims on subsequent allocation.",
                              "No HTTP, coordinator, primary model, concurrent GPU load or quality qualification.",
                              "Other applications and system GPU activity are not controlled.",
                              "Time budget is checked between requests; an active Metal forward is not interrupted."]})
