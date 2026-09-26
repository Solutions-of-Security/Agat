"""Strict, dependency-free wire contracts. Probabilities never come from text."""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "agat.decision.v1"
MAX_BODY_BYTES = 128 * 1024
MAX_STATE_CHARS = 24_000
LETTERS = "ABCDEFGHIJ"
SCORE_FINGERPRINT_VERSION = "binary64-v1"
INPUT_FINGERPRINT_VERSIONS = ("python-json-v1", SCORE_FINGERPRINT_VERSION)


class DecisionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def parse_json(raw: bytes | str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise DecisionError("invalid_request", "Duplicate JSON field")
            result[key] = value
        return result

    def constant(_value):
        raise DecisionError("invalid_request", "Non-finite JSON number")

    def decimal(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise DecisionError("invalid_request", "Non-finite JSON number")
        return parsed

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant, parse_float=decimal)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise DecisionError("invalid_request", "Expected valid UTF-8 JSON") from exc


def fields(value: Any, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional:
        raise DecisionError("invalid_request", "Missing or unknown fields")
    return value


def string(value: Any, limit: int, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DecisionError("invalid_request", "Invalid or oversized string")
    if identifier and not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        raise DecisionError("invalid_request", "Invalid identifier")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise DecisionError("invalid_request", "Invalid Unicode string") from exc
    return value


def number(value: Any, low: float, high: float) -> float:
    if type(value) not in (int, float):
        raise DecisionError("invalid_request", "Expected finite number")
    try:
        valid = math.isfinite(value) and low <= value <= high
    except OverflowError:
        valid = False
    if not valid:
        raise DecisionError("invalid_request", "Number out of range")
    return float(value)


@dataclass(frozen=True)
class Option:
    id: str
    description: str
    abstain: bool = False
    value: bool | float | None = None


@dataclass(frozen=True)
class Request:
    id: str
    state: str
    question: str
    kind: str
    options: tuple[Option, ...]
    input_fingerprint_version: str | None = None

    @classmethod
    def from_dict(cls, raw: Any) -> Request:
        d = fields(raw, {"schemaVersion", "id", "state", "question", "kind", "options"}, {"inputFingerprintVersion"})
        if d["schemaVersion"] != SCHEMA_VERSION or d["kind"] not in ("choice", "boolean", "score"):
            raise DecisionError("invalid_request", "Unsupported schema or kind")
        version = d.get("inputFingerprintVersion")
        if "inputFingerprintVersion" in d and (version != SCORE_FINGERPRINT_VERSION or d["kind"] != "score"):
            raise DecisionError("invalid_request", "Unsupported input fingerprint version")
        if not isinstance(d["options"], list) or not 2 <= len(d["options"]) <= len(LETTERS):
            raise DecisionError("invalid_request", "Expected 2 to 10 options")
        options = []
        for item in d["options"]:
            item = fields(item, {"id", "description"}, {"abstain", "value"})
            abstain = item.get("abstain", False)
            if type(abstain) is not bool:
                raise DecisionError("invalid_request", "abstain must be boolean")
            value = item.get("value")
            if d["kind"] == "boolean":
                if type(value) is not bool or abstain:
                    raise DecisionError("invalid_request", "Boolean requires true/false values")
            elif d["kind"] == "score":
                value = number(value, -1_000_000, 1_000_000)
                # JSON transports may serialize -0 as 0. Normalize the prompt as well
                # as the hash so equivalent v2 inputs produce identical inference.
                if version == SCORE_FINGERPRINT_VERSION and value == 0:
                    value = 0.0
                if abstain:
                    raise DecisionError("invalid_request", "Score levels cannot be abstain options")
            elif "value" in item:
                raise DecisionError("invalid_request", "Choice has no numeric value")
            options.append(Option(string(item["id"], 80, identifier=True),
                                  string(item["description"], 1000), abstain, value))
        if len({o.id for o in options}) != len(options) or len({o.description for o in options}) != len(options):
            raise DecisionError("invalid_request", "Duplicate option ID or description")
        if d["kind"] == "boolean" and (len(options) != 2 or {o.value for o in options} != {True, False}):
            raise DecisionError("invalid_request", "Boolean requires exactly true and false")
        if d["kind"] == "score" and len({o.value for o in options}) != len(options):
            raise DecisionError("invalid_request", "Score levels must be unique")
        return cls(string(d["id"], 100, identifier=True), string(d["state"], MAX_STATE_CHARS),
                   string(d["question"], 2000), d["kind"], tuple(options), version)

    def to_dict(self) -> dict:
        options = [{"id": o.id, "description": o.description, "abstain": o.abstain,
                    **({"value": o.value} if self.kind != "choice" else {})} for o in self.options]
        return {"schemaVersion": SCHEMA_VERSION, "id": self.id, "state": self.state,
                "question": self.question, "kind": self.kind, "options": options,
                **({"inputFingerprintVersion": self.input_fingerprint_version} if self.input_fingerprint_version else {})}

    def _fingerprint_dict(self) -> dict:
        d = self.to_dict()
        if self.input_fingerprint_version == SCORE_FINGERPRINT_VERSION:
            # Fixed keys are ASCII; only score values require numeric canonicalization.
            # This is a versioned typed contract, not a general RFC 8785 serializer.
            for option in d["options"]:
                option["value"] = {"float64be": struct.pack("!d", option["value"]).hex()}
        return d

    @property
    def input_sha256(self) -> str:
        d = self._fingerprint_dict()
        del d["id"]
        return fingerprint(d)

    @property
    def schema_sha256(self) -> str:
        d = self._fingerprint_dict()
        del d["id"], d["state"]
        # Calibrate a semantic question/candidate set; evaluate order sensitivity separately.
        d["options"] = sorted(d["options"], key=lambda option: option["id"])
        return fingerprint(d)


@dataclass(frozen=True)
class Policy:
    id: str = "agat.shadow.v1"
    min_probability: float = 0.8
    min_margin: float = 0.1

    @classmethod
    def from_dict(cls, raw: Any) -> Policy:
        d = fields(raw, {"id", "minProbability", "minMargin"})
        return cls(string(d["id"], 100, identifier=True), number(d["minProbability"], 0, 1),
                   number(d["minMargin"], 0, 1))

    def to_dict(self) -> dict:
        return {"id": self.id, "minProbability": self.min_probability, "minMargin": self.min_margin}


def probabilities(logits: list[float], count: int, temperature: float = 1.0) -> list[float]:
    if not isinstance(logits, list) or len(logits) != count:
        raise DecisionError("invalid_scores", "Backend returned an invalid score vector")
    try:
        if type(temperature) not in (int, float) or not math.isfinite(temperature) or temperature <= 0:
            raise ValueError
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in logits):
            raise ValueError
        top = max(logits)
        weights = [math.exp((v - top) / temperature) for v in logits]
        total = math.fsum(weights)
        return [v / total for v in weights]
    except (ValueError, OverflowError) as exc:
        raise DecisionError("invalid_scores", "Backend returned non-finite scores") from exc
