"""Synthetic context-length/resource probe; never consumes qualification datasets."""

from __future__ import annotations

import copy
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import Policy, Request, fingerprint
from decision_runtime.engine import DecisionEngine
from decision_runtime.mlx_backend import prompt_parts
from scripts.lib.decision_performance import hardware, validate_result
from scripts.lib.decision_resources import MlxMemory, summarize_requests

SCHEMA = "agat.decision.context-resources.v1"
GENERATOR = "agat.synthetic-context.v1"
POSITIONS = ("front", "middle", "end")
FACT = "Контрольный код документа: АГАТ-42."
FILLER = " x"
ROOT = Path(__file__).resolve().parents[2]


def token_count(tokenizer, request):
    # Match runtime's separate state/question encoding, including all wrapper tokens.
    return sum(len(tokenizer.encode(part, add_special_tokens=False)) for part in prompt_parts(request))


def make_probe(tokenizer, target_tokens, position):
    if type(target_tokens) is not int or not 256 <= target_tokens <= 4097 or position not in POSITIONS:
        raise ValueError("Invalid synthetic context target")

    def candidate(padding):
        before = 0 if position == "front" else padding if position == "end" else padding // 2
        state = ("Синтетический ресурсный пример. Ниже служебный фон и одна запись кода.\n"
                 + FILLER * before + "\n" + FACT + "\n" + FILLER * (padding - before))
        return Request.from_dict({"schemaVersion": "agat.decision.v1", "id": f"context-{target_tokens}-{position}",
                                  "state": state, "question": "Какой контрольный код явно указан в документе?",
                                  "kind": "choice", "options": [
                                      {"id": "code_42", "description": "АГАТ-42"},
                                      {"id": "code_24", "description": "АГАТ-24"},
                                      {"id": "missing", "description": "Контрольный код не указан", "abstain": True}]})

    # Search only for a padding length. Never slice input tokens or the source text.
    # A tokenizer with unreachable targets fails explicitly instead of approximating.
    low, high = 0, 8192
    while low <= high:
        middle = (low + high) // 2
        request = candidate(middle)
        count = token_count(tokenizer, request)
        if count == target_tokens:
            return request
        if count < target_tokens:
            low = middle + 1
        else:
            high = middle - 1
    raise ValueError("Tokenizer cannot construct this exact synthetic token length")


def profile_context(backend_factory, *, max_tokens=2048, targets=(256, 512, 1024, 2048), rounds=3,
                    time_budget_s=120, memory_factory=MlxMemory, progress=None):
    if (type(max_tokens) is not int or max_tokens not in {512, 1024, 2048, 4096}
            or not isinstance(targets, (list, tuple)) or not targets or len(targets) > 5
            or any(type(t) is not int or not 256 <= t <= max_tokens for t in targets)
            or list(targets) != sorted(set(targets)) or targets[-1] != max_tokens
            or type(rounds) is not int or not 1 <= rounds <= 5 or len(targets) * 3 * rounds > 75
            or type(time_budget_s) is not int or not 1 <= time_budget_s <= 300):
        raise ValueError("Unsupported or excessive context profiling plan")
    sources = ["scripts/profile-decision-context.py", "scripts/lib/decision_context.py",
               "scripts/lib/decision_resources.py", "scripts/lib/decision_performance.py"]
    source_hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources}
    created = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    backend = backend_factory()
    loaded = time.perf_counter()
    if backend.identity.get("maxInputTokens") != max_tokens:
        raise ValueError("Backend context limit does not match the probe plan")
    engine = DecisionEngine(backend, Policy())
    profile = copy.deepcopy(engine.profile())
    memory = memory_factory(backend)
    requests = [(target, position, make_probe(backend.tokenizer, target, position))
                for target in targets for position in POSITIONS]
    over_limit = make_probe(backend.tokenizer, max_tokens + 1, "end")
    rows, samples, signatures, changed = [], [], {}, set()
    stopped = None

    def sample(phase):
        value = memory.sample()
        if any(type(value.get(k)) is not int or value[k] < 0 for k in ("activeBytes", "cacheBytes", "peakActiveBytes")):
            raise ValueError("Invalid memory counter")
        samples.append({"phase": phase, **value})

    sample("after_load_and_tokenization")

    def one(target, position, request, phase, iteration=0):
        nonlocal stopped
        if time.perf_counter() - started >= time_budget_s:
            stopped = "time_budget"
            return False
        begin = time.perf_counter()
        result = engine.decide(request.to_dict())
        end = time.perf_counter()
        row = {"phase": phase, "iteration": iteration, "targetTokens": target, "position": position,
               "caseId": request.id, "inputSha256": request.input_sha256,
               "wallMs": round((end - begin) * 1000, 3)}
        try:
            validate_result(result, request, profile)
            row.update(status=result["status"], reason=result["reason"], runtimeMs=result["durationMs"])
            if result["status"] != "error":
                if result["inputTokens"] != target:
                    raise ValueError("Input was truncated or counted incorrectly")
                row.update(inputTokens=result["inputTokens"], selectedOptionId=result["selectedOptionId"],
                           selectedProbability=result["selectedProbability"],
                           decisionSha256=fingerprint({k: result[k] for k in ("status", "reason", "value", "selectedOptionId", "distribution")}))
                if signatures.setdefault(request.input_sha256, row["decisionSha256"]) != row["decisionSha256"]:
                    changed.add(request.id)
            row["expectationMet"] = ((result["status"] == "error" and result["reason"] == "context_too_long")
                                     if target > max_tokens else result["status"] in {"ok", "abstain"})
        except (ValueError, TypeError, KeyError, OverflowError):
            row.update(status="error", reason="invalid_response", expectationMet=False)
        rows.append(row)
        return True

    one(max_tokens + 1, "end", over_limit, "over_limit_before")
    one(*requests[0], "warmup")
    sample("after_warmup")
    for target, position, request in requests:
        for iteration in range(1, rounds + 1):
            if not one(target, position, request, "measured", iteration):
                break
        sample(f"{target}-{position}")
        if progress:
            progress(target, position, sum(r["phase"] == "measured" for r in rows))
        if stopped:
            break
    one(max_tokens + 1, "end", over_limit, "over_limit_after")
    sample("after_boundary_check")
    stable = fingerprint(profile) == fingerprint(engine.profile())
    measured = [r for r in rows if r["phase"] == "measured"]
    host = hardware(); host.pop("serverResourcesMeasured", None)
    return sealed({"schemaVersion": SCHEMA, "createdAt": created,
                   "status": "observed" if stable and not stopped and not changed and all(r["expectationMet"] for r in rows) else "degraded",
                   "qualifiedForRouting": False, "workload": {"generator": GENERATOR,
                       "kind": "synthetic_resource_probe", "datasetUsed": False, "fact": FACT, "paddingUnit": FILLER,
                       "meaningfulFactOptionId": "code_42", "requests": [
                           {"targetTokens": t, "position": p, "request": r.to_dict(), "inputSha256": r.input_sha256}
                           for t, p, r in requests + [(max_tokens + 1, "end", over_limit)]]},
                   "profile": profile, "profileSha256": fingerprint(profile), "profileStable": stable,
                   "harnessFiles": source_hashes, "host": {**host, "mlxDevice": memory.device()},
                   "plan": {"maxInputTokens": max_tokens, "targets": list(targets), "positions": list(POSITIONS),
                            "rounds": rounds, "warmupRequests": 1, "expectedBoundaryRejections": 2,
                            "measuredRequests": len(requests) * rounds, "timeBudgetSeconds": time_budget_s},
                   "loadWallMs": round((loaded - started) * 1000, 3),
                   "elapsedMs": round((time.perf_counter() - started) * 1000, 3), "stoppedReason": stopped,
                   "rows": rows, "summary": summarize_requests(measured),
                   "byTarget": [{"targetTokens": t, **summarize_requests([r for r in measured if r["targetTokens"] == t])}
                                for t in targets], "repeatDecisionChanges": sorted(changed),
                   "memory": {"samples": samples, "peakActiveBytes": max(s["peakActiveBytes"] for s in samples),
                              "maxObservedActivePlusCacheBytes": max(s["activeBytes"] + s["cacheBytes"] for s in samples)},
                   "limits": ["Synthetic repeated padding and one fixed fact are not production document complexity or independent quality data.",
                              "Token count includes wrapper, state, question and option descriptions; no truncation.",
                              "Warmup covers only the smallest front-position input; first execution of each longer shape is included.",
                              "Choice labels and probabilities are diagnostic; they do not qualify routing or calibration.",
                              "No HTTP, queue, coordinator, primary workload, GPU utilization or production SLO is measured.",
                              "Active plus cache is sampled; peak active and process RSS overlap and must not be summed.",
                              "Time budget is checked between calls; it does not interrupt GPU work."]})
