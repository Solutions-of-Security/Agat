"""Policy is evaluated in code; the model has no tools or routing authority."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from . import VERSION
from .calibration import Calibration
from .contracts import DecisionError, INPUT_FINGERPRINT_VERSIONS, Policy, Request, SCHEMA_VERSION, fingerprint, probabilities


@dataclass(frozen=True)
class Scores:
    logits: list[float]
    input_tokens: int


class Backend(Protocol):
    identity: dict

    def score(self, request: Request) -> Scores: ...


class DecisionEngine:
    def __init__(self, backend: Backend, policy: Policy | None = None, calibration: Calibration | None = None):
        self.backend = backend
        self.policy = policy or Policy()
        self.calibration = calibration
        if calibration is not None:
            calibration.validate_model(backend.identity)

    def error(self, code: str, request: Request | None = None) -> dict:
        return {**self._base(request), "status": "error", "reason": code,
                "selectedOptionId": None, "value": None, "distribution": []}

    def profile(self) -> dict:
        """Immutable execution identity, excluding request-specific fields."""
        base = self._base(None)
        return {key: base[key] for key in ("schemaVersion", "runtimeVersion", "model", "policy", "calibration", "inputFingerprintVersions")}

    def _base(self, request: Request | None) -> dict:
        policy = self.policy.to_dict()
        return {"schemaVersion": SCHEMA_VERSION, "runtimeVersion": VERSION, "id": request.id if request else None,
                "mode": "shadow", "inputSha256": request.input_sha256 if request else None,
                "inputFingerprintVersions": list(INPUT_FINGERPRINT_VERSIONS),
                **({"inputFingerprintVersion": request.input_fingerprint_version}
                   if request and request.input_fingerprint_version else {}),
                "model": self.backend.identity, "policy": {**policy, "sha256": fingerprint(policy)},
                "calibration": self.calibration.describe() if self.calibration else {
                    "status": "uncalibrated", "temperature": 1.0, "semantics": "softmax_over_allowed_options"}}

    def decide(self, raw: dict, *, cancelled=None) -> dict:
        start = time.perf_counter()
        request = None
        try:
            request = Request.from_dict(raw)
            if self.calibration:
                self.calibration.validate_request(request)
            if cancelled is not None and cancelled.is_set():
                raise DecisionError("inference_cancelled", "Request cancelled before inference")
            cancellable = getattr(self.backend, "score_with_cancellation", None)
            scores = (cancellable(request, cancelled) if cancelled is not None and callable(cancellable)
                      else self.backend.score(request))
            if cancelled is not None and cancelled.is_set():
                raise DecisionError("inference_cancelled", "Discard late result")
            temperature = self.calibration.temperature if self.calibration else 1.0
            ps = probabilities(scores.logits, len(request.options), temperature)
            ranked = sorted(range(len(ps)), key=lambda i: ps[i], reverse=True)
            selected = request.options[ranked[0]]
            probability = ps[ranked[0]]
            margin = probability - ps[ranked[1]]
            if selected.abstain:
                reason = "abstain_option"
            elif probability < self.policy.min_probability or margin < self.policy.min_margin:
                reason = "below_threshold"
            else:
                reason = "accepted"
            accepted = reason == "accepted"
            value = None
            if accepted:
                if request.kind == "choice":
                    value = selected.id
                elif request.kind == "boolean":
                    value = selected.value
                else:
                    value = sum(p * o.value for p, o in zip(ps, request.options))
            result = {**self._base(request), "status": "ok" if accepted else "abstain",
                      "reason": reason, "selectedOptionId": selected.id, "value": value,
                      "selectedProbability": probability, "margin": margin,
                      "distribution": [{"id": o.id, "probability": p, "logit": float(z)}
                                       for o, p, z in zip(request.options, ps, scores.logits)],
                      "inputTokens": scores.input_tokens, "generatedTokens": 0}
        except DecisionError as exc:
            result = self.error(exc.code, request)
        except Exception:
            # Exceptions can include source text, paths or tokens. Never send them to a caller/log.
            result = self.error("backend_error", request)
        result["durationMs"] = round((time.perf_counter() - start) * 1000, 3)
        return result
