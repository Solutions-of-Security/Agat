"""Scalar temperature scaling; positive temperature preserves the winning label."""

from __future__ import annotations

import copy
import math
import re

from .artifacts import verify_seal
from .contracts import DecisionError, Request, fields, fingerprint, number, probabilities

CALIBRATION_SCHEMA = "agat.decision.calibration.v1"


def fit_temperature(examples: list[tuple[list[float], int]]) -> dict:
    if not examples:
        raise ValueError("Cannot fit an empty calibration set")
    centered = []
    for logits, gold in examples:
        probabilities(logits, len(logits))
        if len(logits) < 2 or type(gold) is not int or not 0 <= gold < len(logits):
            raise ValueError("Invalid calibration target")
        if any(abs(z) > 1_000_000 for z in logits):
            raise ValueError("Logits are outside the supported numerical range")
        centered.append(([z - max(logits) for z in logits], gold))

    def objective(beta):
        losses, gradients = [], []
        for zs, gold in centered:
            weights = [math.exp(z * beta) for z in zs]
            total = math.fsum(weights)
            losses.append(math.log(total) - beta * zs[gold])
            gradients.append(math.fsum(w * z for w, z in zip(weights, zs)) / total - zs[gold])
        return math.fsum(losses) / len(centered), math.fsum(gradients) / len(centered)

    # NLL is convex in inverse temperature beta; the derivative is monotone.
    lo, hi = 0.05, 20.0
    before, at_one = objective(1.0)
    if abs(at_one) < 1e-12:
        beta = 1.0
    elif objective(lo)[1] >= 0:
        beta = lo
    elif objective(hi)[1] <= 0:
        beta = hi
    else:
        for _ in range(80):
            mid = (lo + hi) / 2
            if objective(mid)[1] < 0:
                lo = mid
            else:
                hi = mid
        beta = (lo + hi) / 2
    after = objective(beta)[0]
    if after > before + 1e-10:
        raise ValueError("Calibration optimizer increased NLL")
    return {"temperature": 1 / beta, "nllBefore": before, "nllAfter": after,
            "temperatureBounds": [0.05, 20.0], "atBound": beta in (0.05, 20.0),
            "examples": len(examples)}


class Calibration:
    def __init__(self, artifact: dict):
        verify_seal(artifact, CALIBRATION_SCHEMA)
        fields(artifact, {"schemaVersion", "method", "createdAt", "temperature", "fitSplit", "datasetSha256",
                          "planSha256", "scoreReportSha256", "model", "schemaSha256s", "fit", "fitCaseIds",
                          "fitGroupIds", "labelSources", "sha256"})
        for key in ("datasetSha256", "planSha256", "scoreReportSha256"):
            if not isinstance(artifact[key], str) or not re.fullmatch(r"[0-9a-f]{64}", artifact[key]):
                raise ValueError("Invalid calibration provenance digest")
        self.artifact = copy.deepcopy(artifact)
        self.temperature = number(artifact.get("temperature"), 0.05, 20.0)
        if artifact.get("method") != "temperature-scaling.v1" or artifact.get("fitSplit") != "calibration":
            raise ValueError("Unsupported calibration method or split")
        if not isinstance(artifact.get("model"), dict) or not artifact["model"]:
            raise ValueError("Missing calibration model binding")
        scopes = artifact.get("schemaSha256s")
        if not isinstance(scopes, list) or not scopes or any(not isinstance(x, str) or not re.fullmatch(r"[0-9a-f]{64}", x) for x in scopes):
            raise ValueError("Missing calibration schema scope")
        self.scopes = frozenset(scopes)

    def validate_model(self, identity: dict) -> None:
        if fingerprint(identity) != fingerprint(self.artifact["model"]):
            raise ValueError("Calibration belongs to different model/runtime/tokenizer/prompt settings")

    def validate_request(self, request: Request) -> None:
        if request.schema_sha256 not in self.scopes:
            raise DecisionError("calibration_out_of_scope", "Question or candidate schema was not calibrated")

    def describe(self) -> dict:
        return {"status": "fitted", "temperature": self.temperature,
                "semantics": "softmax_over_allowed_options", "artifactSha256": self.artifact["sha256"],
                "datasetSha256": self.artifact["datasetSha256"], "fitSplit": "calibration",
                "qualifiedForRouting": False}
