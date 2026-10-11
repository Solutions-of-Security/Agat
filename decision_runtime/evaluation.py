"""Reproducible labelled evaluation; abstentions and backend errors stay visible."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import VERSION
from .artifacts import verify_seal
from .calibration import Calibration
from .contracts import Policy, Request, fingerprint, parse_json, string
from .engine import DecisionEngine, Scores


def load_dataset(path: Path) -> dict:
    return validate_dataset(parse_json(path.read_bytes()))


def validate_dataset(dataset: dict) -> dict:
    if not isinstance(dataset, dict) or dataset.get("schemaVersion") != "agat.decision.dataset.v1":
        raise ValueError("Unsupported dataset schema")
    if not isinstance(dataset.get("cases"), list) or not dataset["cases"]:
        raise ValueError("Dataset has no cases")
    if dataset.get("usage") == "development-only":
        verify_seal(dataset, "agat.decision.dataset.v1")
        if (dataset.get("routingEnabled") is not False or dataset.get("qualifiedForRouting") is not False
                or any(not isinstance(c, dict) or c.get("split") != "development"
                       or c.get("labelSource") != "assistant-reviewed" or "review" in c for c in dataset["cases"])):
            raise ValueError("Development-only assistant labels cannot become calibration, holdout or expert reviews")
    string(dataset.get("id"), 100, identifier=True)
    ids, groups, states, sources = set(), {}, {}, {}
    for case in dataset["cases"]:
        if not isinstance(case, dict):
            raise ValueError("Invalid dataset case")
        for name in ("id", "family", "groupId", "labelSource", "split", "expectedOptionId"):
            string(case.get(name), 100, identifier=True)
        if case["split"] not in {"train", "development", "calibration", "holdout"}:
            raise ValueError("Unknown split")
        request = Request.from_dict(case.get("request"))
        if request.id != case["id"] or request.id in ids:
            raise ValueError("Duplicate or mismatched case ID")
        ids.add(request.id)
        if case["expectedOptionId"] not in {o.id for o in request.options}:
            raise ValueError("Gold label is not an allowed option")
        normalized_state = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", request.state)).strip().casefold()
        keys = [(case["groupId"], groups), (fingerprint(normalized_state), states)]
        if "provenance" in case:
            source = case["provenance"]
            if not isinstance(source, dict) or source.get("kind") not in ("real", "synthetic"):
                raise ValueError("Invalid dataset provenance")
            string(source.get("sourceId"), 100, identifier=True)
            string(source.get("reference"), 1000)
            keys.append((source["sourceId"], sources))
        for key, registry in keys:
            if key in registry and registry[key] != case["split"]:
                raise ValueError("Group or identical source state leaks across splits")
            registry[key] = case["split"]
    return dataset


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(values) * q) - 1)], 3)


def metrics(rows: list[dict]) -> dict:
    attempted = len(rows)
    scored = [r for r in rows if r["result"]["status"] != "error"]
    accepted = [r for r in scored if r["result"]["status"] == "ok"]
    correct = sum(r["correct"] for r in scored)
    errors = sum(not r["correct"] for r in accepted)
    nll = brier = 0.0
    bins = [[] for _ in range(10)]
    classes = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "support": 0})
    for row in scored:
        gold, result = row["expectedOptionId"], row["result"]
        prediction = result["selectedOptionId"]
        probabilities = {x["id"]: x["probability"] for x in result["distribution"]}
        nll -= math.log(max(probabilities[gold], 1e-15))
        brier += sum((p - int(label == gold)) ** 2 for label, p in probabilities.items())
        confidence = result["selectedProbability"]
        bins[min(9, int(confidence * 10))].append((confidence, row["correct"]))
        # The same label ID in different families is not necessarily the same class.
        for label in probabilities:
            c = classes[f"{row['family']}:{label}"]
            c["support"] += int(gold == label)
            c["tp"] += int(gold == prediction == label)
            c["fp"] += int(prediction == label and gold != label)
            c["fn"] += int(gold == label and prediction != label)
    reliability, ece = [], 0.0
    for i, bucket in enumerate(bins):
        if bucket:
            confidence = sum(p for p, _ in bucket) / len(bucket)
            accuracy = sum(ok for _, ok in bucket) / len(bucket)
            ece += len(bucket) * abs(confidence - accuracy)
            reliability.append({"lower": i / 10, "upper": (i + 1) / 10, "count": len(bucket),
                                "meanProbability": confidence, "accuracy": accuracy})
    f1s = []
    for c in classes.values():
        denominator = 2 * c["tp"] + c["fp"] + c["fn"]
        c["f1"] = 2 * c["tp"] / denominator if denominator else None
        if c["f1"] is not None:
            f1s.append(c["f1"])
    duration = [r["result"]["durationMs"] for r in scored]
    return {"attempted": attempted, "scored": len(scored), "backendErrors": attempted - len(scored),
            "abstained": len(scored) - len(accepted), "accepted": len(accepted),
            "correct": correct, "accuracyAllAttempts": correct / attempted if attempted else None,
            "accuracyScored": correct / len(scored) if scored else None,
            "macroF1Scored": sum(f1s) / len(f1s) if f1s else None,
            "coverage": len(accepted) / attempted if attempted else None,
            "acceptedErrors": errors, "selectiveRisk": errors / len(accepted) if accepted else None,
            "nllScored": nll / len(scored) if scored else None,
            "brierScored": brier / len(scored) if scored else None,
            "ece10Scored": ece / len(scored) if scored else None,
            "reliability": reliability, "classes": dict(classes),
            "latencyMs": {"p50": percentile(duration, 0.5), "p95": percentile(duration, 0.95)}}


def replay_scores(case: dict, raw: dict, model: dict, policy: Policy, calibration: Calibration | None,
                  *, reverse: bool = False) -> dict:
    """Recompute typed outcomes from validated recorded logits, retaining measured inference time."""
    request = case["request"]
    if reverse:
        request = {**request, "options": list(reversed(request["options"]))}

    class RecordedBackend:
        identity = model

        def score(self, _request):
            return Scores([x["logit"] for x in raw["distribution"]], raw["inputTokens"])

    engine = DecisionEngine(RecordedBackend(), policy, calibration)
    if raw["status"] == "error":
        result = engine.error(raw.get("reason", "backend_error"), Request.from_dict(request))
    else:
        result = engine.decide(request)
    result["durationMs"] = raw["durationMs"]
    row = {k: case[k] for k in ("id", "family", "groupId", "labelSource", "expectedOptionId")}
    return {**row, "result": result, "correct": result["selectedOptionId"] == case["expectedOptionId"]}


def evaluate(engine: DecisionEngine, dataset: dict, split: str, *, reverse_options: bool = False) -> dict:
    validate_dataset(dataset)
    started_at = datetime.now(timezone.utc).isoformat()
    cases = [c for c in dataset["cases"] if c["split"] == split]
    if not cases:
        raise ValueError("Selected split is empty")
    rows = []
    for case in cases:
        result = engine.decide(case["request"])
        row = {k: case[k] for k in ("id", "family", "groupId", "labelSource", "expectedOptionId")}
        row.update(result=result, correct=result["selectedOptionId"] == case["expectedOptionId"])
        if reverse_options:
            reversed_request = {**case["request"], "options": list(reversed(case["request"]["options"]))}
            reverse = engine.decide(reversed_request)
            row["reversedResult"] = reverse
            row["orderAgreement"] = (result["selectedOptionId"] == reverse["selectedOptionId"]
                                     if result["status"] != "error" and reverse["status"] != "error" else None)
        rows.append(row)
    report = {"schemaVersion": "agat.decision.evaluation.v1", "runtimeVersion": VERSION,
              "startedAt": started_at,
              "createdAt": datetime.now(timezone.utc).isoformat(),
              "dataset": {"id": dataset["id"], "sha256": fingerprint(dataset), "split": split,
                          "groupCount": len({c["groupId"] for c in cases}),
                          "labelSources": sorted({c["labelSource"] for c in cases})},
              "model": engine.backend.identity, "policy": engine.policy.to_dict(),
              "qualityGate": "not_configured", "metrics": metrics(rows),
              "families": {family: metrics([r for r in rows if r["family"] == family])
                           for family in sorted({r["family"] for r in rows})},
              "timing": {"modelLoadMs": getattr(engine.backend, "load_ms", None),
                         "firstRequestMs": rows[0]["result"]["durationMs"],
                         "subsequentOriginalRequestsP95Ms": percentile(
                             [r["result"]["durationMs"] for r in rows[1:] if r["result"]["status"] != "error"], 0.95)},
              "cases": rows}
    if reverse_options:
        paired = [r for r in rows if r["orderAgreement"] is not None]
        report["optionOrder"] = {"attemptedPairs": len(rows), "scoredPairs": len(paired),
                                 "agreement": sum(r["orderAgreement"] for r in paired) / len(paired) if paired else None,
                                 "reversedBackendErrors": sum(r["reversedResult"]["status"] == "error" for r in rows),
                                 "reversedAccuracyAllAttempts": sum(r["reversedResult"]["selectedOptionId"] == r["expectedOptionId"] for r in rows) / len(rows)}
    if hasattr(engine.backend, "peak_memory_bytes"):
        report["peakMlxMemoryBytes"] = engine.backend.peak_memory_bytes()
    return report
