"""Bounded development load probe using the actual worker HTTP client."""

from __future__ import annotations

import hashlib
import math
import platform
import subprocess
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import INPUT_FINGERPRINT_VERSIONS, Request, canonical_json, fields, fingerprint, number, parse_json, probabilities
from decision_runtime.evaluation import percentile
from scripts.lib.decision_baselines import LoopbackJson, development_cases
from workers.local_decisions import LocalDecisionClient, PROFILE

SCHEMA = "agat.decision.performance.v1"
ROOT = Path(__file__).resolve().parents[2]


def profile_from_health(health):
    if not isinstance(health, dict) or health.get("status") != "ready" or health.get("mode") != "shadow":
        raise ValueError("Runtime is not a ready shadow service")
    raw = health.get("profileJson")
    if not isinstance(raw, str) or hashlib.sha256(raw.encode()).hexdigest() != health.get("profileSha256"):
        raise ValueError("Invalid runtime profile fingerprint")
    profile = parse_json(raw)
    fields(profile, {"schemaVersion", "runtimeVersion", "model", "policy", "calibration"}, {"inputFingerprintVersions"})
    if ("inputFingerprintVersions" in profile
            and profile["inputFingerprintVersions"] != list(INPUT_FINGERPRINT_VERSIONS)):
        raise ValueError("Unsupported input fingerprint versions")
    if profile["schemaVersion"] != "agat.decision.v1" or canonical_json(profile) != raw:
        raise ValueError("Unsupported runtime profile")
    policy = fields(profile["policy"], {"id", "minProbability", "minMargin", "sha256"})
    number(policy["minProbability"], 0, 1); number(policy["minMargin"], 0, 1)
    if fingerprint({k: v for k, v in policy.items() if k != "sha256"}) != policy["sha256"]:
        raise ValueError("Invalid policy fingerprint")
    calibration = profile["calibration"]
    if (calibration.get("status") not in ("uncalibrated", "fitted")
            or calibration.get("semantics") != "softmax_over_allowed_options"):
        raise ValueError("Unsupported probability semantics")
    temperature = number(calibration.get("temperature"), 0.01, 100)
    if calibration["status"] == "uncalibrated" and temperature != 1:
        raise ValueError("Invalid uncalibrated temperature")
    return profile


def validate_result(result, request, profile):
    """An HTTP 200 with an invalid distribution is still a failed measurement."""
    fields(result, set(profile) | {"id", "mode", "inputSha256", "status", "reason", "selectedOptionId",
                                  "value", "distribution", "durationMs"},
           {"selectedProbability", "margin", "inputTokens", "generatedTokens"})
    if (any(result[k] != v for k, v in profile.items()) or result["mode"] != "shadow"
            or result["id"] != request.id or result["inputSha256"] != request.input_sha256):
        raise ValueError("Response binding mismatch")
    number(result["durationMs"], 0, 3_600_000)
    if result["status"] == "error":
        if (result["reason"] not in {"invalid_request", "context_too_long", "calibration_out_of_scope", "backend_error",
                                     "unsupported_tokenizer", "invalid_scores", "inference_timeout", "backend_unavailable"} or result["distribution"] != []
                or result["selectedOptionId"] is not None or result["value"] is not None):
            raise ValueError("Invalid error response")
        return
    if result["status"] not in {"ok", "abstain"} or len(result["distribution"]) != len(request.options):
        raise ValueError("Invalid decision status or distribution")
    logits = []
    for option, item in zip(request.options, result["distribution"]):
        fields(item, {"id", "probability", "logit"})
        if item["id"] != option.id:
            raise ValueError("Distribution order mismatch")
        logits.append(number(item["logit"], -1_000_000, 1_000_000))
        number(item["probability"], 0, 1)
    ps = probabilities(logits, len(logits), profile["calibration"]["temperature"])
    if any(not math.isclose(p, row["probability"], rel_tol=1e-9, abs_tol=1e-10)
           for p, row in zip(ps, result["distribution"])):
        raise ValueError("Probability mismatch")
    order = sorted(range(len(ps)), key=lambda i: ps[i], reverse=True)
    selected = request.options[order[0]]
    probability, margin = ps[order[0]], ps[order[0]] - ps[order[1]]
    reason = ("abstain_option" if selected.abstain else "below_threshold"
              if probability < profile["policy"]["minProbability"] or margin < profile["policy"]["minMargin"]
              else "accepted")
    value = (selected.id if request.kind == "choice" else selected.value) if reason == "accepted" else None
    if (result["selectedOptionId"] != selected.id or result["reason"] != reason
            or result["status"] != ("ok" if reason == "accepted" else "abstain")
            or type(result["value"]) is not type(value) or result["value"] != value
            or not math.isclose(number(result.get("selectedProbability"), 0, 1), probability, abs_tol=1e-10)
            or not math.isclose(number(result.get("margin"), 0, 1), margin, abs_tol=1e-10)
            or type(result.get("inputTokens")) is not int or result["inputTokens"] <= 0
            or type(result.get("generatedTokens")) is not int or result["generatedTokens"] != 0):
        raise ValueError("Invalid typed outcome or token counts")


def distribution(values):
    return {"count": len(values), "p50": percentile(values, 0.5), "p95": percentile(values, 0.95),
            "max": round(max(values), 3) if values else None}


def summarize(rows, elapsed_ms):
    scored = [r for r in rows if r["status"] in ("ok", "abstain")]
    failed = [r for r in rows if r["status"] not in ("ok", "abstain")]
    wall = [r["wallMs"] for r in scored]
    return {"attempts": len(rows), "scored": len(scored), "statuses": dict(Counter(r["status"] for r in rows)),
            "failures": dict(Counter(r["reason"] for r in failed)), "elapsedMs": round(elapsed_ms, 3),
            "completedPerSecond": round(len(scored) / (elapsed_ms / 1000), 3) if elapsed_ms else 0,
            "scoredWallMs": distribution(wall), "failedWallMs": distribution([r["wallMs"] for r in failed]),
            "runtimeMs": distribution([r["runtimeMs"] for r in scored]),
            "inputTokens": distribution([r["inputTokens"] for r in scored]),
            "scoredAboveBudgetMs": {str(b): sum(v > b for v in wall) for b in (100, 250, 500, 1000, 2000, 5000, 10000)}}


def hardware():
    result = {"system": platform.system(), "release": platform.release(), "architecture": platform.machine(),
              "pythonVersion": platform.python_version(), "serverResourcesMeasured": False}
    if platform.system() == "Darwin":
        for name, key in (("cpu", "machdep.cpu.brand_string"), ("memoryBytes", "hw.memsize")):
            try:
                value = subprocess.check_output(["/usr/sbin/sysctl", "-n", key], timeout=2, text=True,
                                                stderr=subprocess.DEVNULL).strip()
                result[name] = int(value) if name == "memoryBytes" else value
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
    return result


def benchmark(dataset, url, *, rounds=4, concurrency=(1, 2, 4), warmup=3, timeout_ms=10000,
              health_transport=None, client=None, progress=None):
    cases = development_cases(dataset)
    requests = [Request.from_dict(case["request"]) for case in cases]
    if (len(requests) > 100 or any(r.kind not in ("choice", "boolean") for r in requests)
            or type(rounds) is not int or not 1 <= rounds <= 10
            or type(warmup) is not int or not 0 <= warmup <= 20
            or not isinstance(concurrency, (tuple, list)) or not concurrency
            or len(set(concurrency)) != len(concurrency)
            or any(type(c) is not int or not 1 <= c <= 8 for c in concurrency)
            or type(timeout_ms) is not int or not 100 <= timeout_ms <= 10000
            or len(requests) * rounds * len(concurrency) > 1200):
        raise ValueError("Unsupported or excessive benchmark plan")
    transport = health_transport or LoopbackJson(url, timeout=5)
    client = client or LocalDecisionClient(url)
    health = transport("GET", "/health")
    profile = profile_from_health(health)
    profile_sha = health["profileSha256"]
    start = time.perf_counter()

    def one(request, index, batch, gate=None):
        shadow = {"profile": PROFILE, "profileSha256": profile_sha, "timeoutMs": timeout_ms,
                  "request": request.to_dict()}
        if gate:
            gate.wait(timeout=10)
        started = time.perf_counter()
        observation = client.decide(shadow)
        finished = time.perf_counter()
        row = {"index": index, "batch": batch, "caseId": request.id, "inputSha256": request.input_sha256,
               "payloadBytes": len(canonical_json(request.to_dict()).encode()),
               "startedMs": round((started - start) * 1000, 3), "wallMs": round((finished - started) * 1000, 3)}
        try:
            if set(observation) == {"result"}:
                result = observation["result"]
                validate_result(result, request, profile)
                row.update(status=result["status"], reason=result["reason"], runtimeMs=result["durationMs"],
                           resultSha256=fingerprint(result))
                if result["status"] != "error":
                    row["inputTokens"] = result["inputTokens"]
            elif (set(observation) == {"status", "reason"} and observation["status"] == "unavailable"
                  and observation["reason"] in {"busy", "timeout", "cancelled", "unreachable", "invalid_response", "profile_mismatch"}):
                row.update(observation)
            else:
                raise ValueError("Invalid observation")
        except (ValueError, TypeError, KeyError, OverflowError):
            row.update(status="unavailable", reason="invalid_response")
        return row

    warmup_rows = [one(requests[i % len(requests)], i, i) for i in range(warmup)]
    if progress:
        progress("warmup", len(warmup_rows), None)
    phases = []
    for count in concurrency:
        rows = []
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=count) as pool:
            schedule = requests * rounds
            for offset in range(0, len(schedule), count):
                batch = schedule[offset:offset + count]
                gate = threading.Barrier(len(batch))
                futures = [pool.submit(one, request, offset + i, offset // count, gate) for i, request in enumerate(batch)]
                rows.extend(f.result() for f in futures)
                if progress and (offset + len(batch)) % 15 == 0:
                    progress("measured", len(rows), count)
        elapsed = (time.perf_counter() - started) * 1000
        phases.append({"concurrency": count, "summary": summarize(rows, elapsed), "rows": rows})
    final_health = transport("GET", "/health")
    profile_stable = (profile_from_health(final_health) == profile and final_health["profileSha256"] == profile_sha)
    all_rows = warmup_rows + [r for phase in phases for r in phase["rows"]]
    failures = [r for r in all_rows if r["status"] not in ("ok", "abstain") and r["reason"] != "busy"]
    sources = ["scripts/lib/decision_performance.py", "scripts/benchmark-local-decisions.py", "workers/local_decisions.py"]
    return sealed({"schemaVersion": SCHEMA, "createdAt": datetime.now(timezone.utc).isoformat(),
                   "status": "observed" if profile_stable and not failures else "degraded", "qualifiedForRouting": False,
                   "dataset": {"id": dataset["id"], "sha256": dataset["sha256"], "usage": "development-only", "cases": len(cases)},
                   "profile": profile, "profileSha256": profile_sha, "profileStable": profile_stable,
                   "harnessFiles": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in sources},
                   "host": hardware(), "plan": {"rounds": rounds, "concurrency": list(concurrency), "warmup": warmup,
                   "timeoutMs": timeout_ms, "arrivalPattern": "synchronized bursts; wait for each burst before the next",
                   "preexistingRuntimeWarmState": "unknown", "includesModelLoad": False, "retry": False},
                   "warmup": warmup_rows, "phases": phases,
                   "limits": ["Local development traffic; no production arrival-rate or SLO claim.",
                              "Repeated cases are latency samples, not independent quality evidence.",
                              "Timeout closes the client; an in-flight GPU forward may continue.",
                              "Only the decision HTTP call is timed, not primary inference or coordinator persistence."]})
