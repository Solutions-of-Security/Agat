"""Freeze a shadow pilot's scope before collection; never infer human agreement."""
from __future__ import annotations

from datetime import datetime, timezone
from copy import deepcopy
import hashlib
import math
import re

from decision_runtime.contracts import fingerprint
from scripts.lib.decision_shadow_sli import same_json

CONFIG_SCHEMA = "agat.decision.shadow-pilot-config.v1"
PLAN_SCHEMA = "agat.decision.shadow-pilot-plan.v1"
BOUNDARY = "process_instance_created_at_half_open"
SHA = re.compile(r"[a-f0-9]{64}")
FIELDS = {"schemaVersion", "pilotId", "projectId", "processId", "processVersionId", "owners",
          "window", "targets", "scenarioDescription", "dataUseReference", "trafficKind", "cohortBoundary"}


def require(value, message):
    if not value:
        raise ValueError(message)


def text(value, name, maximum=200):
    require(value is None or isinstance(value, str) and 0 < len(value) <= maximum
            and value == value.strip() and not any(ord(c) < 32 for c in value), f"Invalid {name}")
    return value


def timestamp(value, name):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", value),
            f"Use canonical UTC milliseconds for {name}")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z") == value, f"Invalid {name}")
    return parsed


def validate_config(config):
    require(isinstance(config, dict) and set(config) == FIELDS and config["schemaVersion"] == CONFIG_SCHEMA,
            "Unsupported pilot configuration fields/schema")
    missing = []
    for name in ("pilotId", "projectId", "processId", "processVersionId", "scenarioDescription", "dataUseReference"):
        text(config[name], name, 2000 if name in ("scenarioDescription", "dataUseReference") else 200)
        if config[name] is None:
            missing.append(name)
    require(config["trafficKind"] == "observed_workflow" and config["cohortBoundary"] == BOUNDARY,
            "Pilot scope must cover all stored process instances in the half-open creation window")
    owners = config["owners"]
    require(isinstance(owners, dict) and set(owners) == {"runtime", "business"}, "Invalid pilot owners")
    for name in ("runtime", "business"):
        value = owners[name]
        text(value, f"owners.{name}")
        if value is None:
            missing.append(f"owners.{name}")
    window = config["window"]
    require(isinstance(window, dict) and set(window) == {"startAt", "endAt"}, "Invalid pilot window")
    for name in ("startAt", "endAt"):
        value = window[name]
        if value is None:
            missing.append(f"window.{name}")
        else:
            timestamp(value, name)
    if all(value is not None for value in window.values()):
        seconds = (timestamp(window["endAt"], "endAt") - timestamp(window["startAt"], "startAt")).total_seconds()
        require(0 < seconds <= 7 * 86400, "Pilot window must be positive and at most seven days")
    targets = config["targets"]
    require(isinstance(targets, dict) and set(targets) == {
        "boundResultRatio", "timelyBoundResultRatio", "latencyThresholdMs", "callerTimeoutMs"}, "Invalid pilot targets")
    for name in ("boundResultRatio", "timelyBoundResultRatio"):
        value = targets[name]
        require(type(value) in (int, float) and math.isfinite(value) and 0 < value <= 1, f"Invalid {name}")
    timeout = targets["callerTimeoutMs"]
    threshold = targets["latencyThresholdMs"]
    require(type(timeout) is int and 100 <= timeout <= 10000
            and type(threshold) is int and 1 <= threshold <= timeout, "Latency threshold must fit the caller timeout")
    return missing


def profile_identity(profile, raw, expected_file_sha, identity):
    require(type(raw) is bytes and 0 < len(raw) <= 1024 * 1024 and isinstance(expected_file_sha, str)
            and SHA.fullmatch(expected_file_sha) and hashlib.sha256(raw).hexdigest() == expected_file_sha,
            "Profile file pin differs")
    validate_profile(profile)
    require(identity in ("coordinator_json_bytes", "runtime_fingerprint"), "Invalid profile identity mode")
    # Bind parsed semantics and exact configured bytes independently.
    from decision_runtime.contracts import parse_json
    require(same_json(profile, parse_json(raw.decode("utf-8"))), "Profile JSON bytes differ from parsed profile")
    return {"identity": identity, "sha256": expected_file_sha if identity == "coordinator_json_bytes" else fingerprint(profile),
            "fileSha256": expected_file_sha, "fingerprintSha256": fingerprint(profile), "value": profile}


def validate_profile(profile):
    require(isinstance(profile, dict) and set(profile) in (
        {"schemaVersion", "runtimeVersion", "model", "policy", "calibration"},
        {"schemaVersion", "runtimeVersion", "model", "policy", "calibration", "inputFingerprintVersions"})
        and profile["schemaVersion"] == "agat.decision.v1"
        and isinstance(profile["runtimeVersion"], str) and profile["runtimeVersion"]
        and all(isinstance(profile[k], dict) and profile[k] for k in ("model", "policy", "calibration")),
        "Invalid runtime profile fields")


def prepare(config, profile, raw, expected_file_sha, identity, *, prepared_at, source_commit, source_files):
    missing = validate_config(config)
    prepared = timestamp(prepared_at, "preparedAt")
    if config["window"]["startAt"] is not None:
        require(timestamp(config["window"]["startAt"], "startAt") > prepared, "Freeze the pilot plan before the observation window starts")
    require(isinstance(source_commit, str) and re.fullmatch(r"[a-f0-9]{40}", source_commit)
            and isinstance(source_files, dict) and source_files
            and all(isinstance(k, str) and k and isinstance(v, str) and SHA.fullmatch(v) for k, v in source_files.items()),
            "Invalid committed source identity")
    profile_pin = profile_identity(profile, raw, expected_file_sha, identity)
    return {"schemaVersion": PLAN_SCHEMA, "status": "draft_incomplete" if missing else "ready_for_review",
            "missingFields": missing, "preparedAt": prepared_at, "config": deepcopy(config),
            "configSha256": fingerprint(config), "profile": profile_pin,
            "sourceCommit": source_commit, "sourceFiles": source_files,
            "measurementBoundary": "caller_operation_intents_in_stored_process_cohort",
            "scope": "planned_stored_process_cohort", "sloAccepted": False, "routingEnabled": False,
            "qualification": "not_assessed", "populationCoverageVerified": False,
            "agreementVerified": False}


def verify_plan(plan):
    from decision_runtime.artifacts import verify_seal
    verify_seal(plan, PLAN_SCHEMA)
    require(set(plan) == {"schemaVersion", "status", "missingFields", "preparedAt", "config", "configSha256",
        "profile", "sourceCommit", "sourceFiles", "measurementBoundary", "scope", "sloAccepted", "routingEnabled",
        "qualification", "populationCoverageVerified", "agreementVerified", "sha256"}, "Invalid pilot plan fields")
    missing = validate_config(plan["config"])
    require(plan["configSha256"] == fingerprint(plan["config"]) and plan["missingFields"] == missing
            and plan["status"] == ("draft_incomplete" if missing else "ready_for_review"), "Pilot readiness/config binding differs")
    prepared = timestamp(plan["preparedAt"], "preparedAt")
    if plan["config"]["window"]["startAt"] is not None:
        require(timestamp(plan["config"]["window"]["startAt"], "startAt") > prepared, "Pilot plan was prepared after its observation window started")
    pin = plan["profile"]
    require(isinstance(pin, dict) and set(pin) == {"identity", "sha256", "fileSha256", "fingerprintSha256", "value"}
            and pin["identity"] in ("coordinator_json_bytes", "runtime_fingerprint")
            and all(isinstance(pin[k], str) and SHA.fullmatch(pin[k]) for k in ("sha256", "fileSha256", "fingerprintSha256"))
            and pin["fingerprintSha256"] == fingerprint(pin["value"])
            and pin["sha256"] == pin["fileSha256" if pin["identity"] == "coordinator_json_bytes" else "fingerprintSha256"],
            "Invalid pilot profile identity")
    validate_profile(pin["value"])
    require(plan["measurementBoundary"] == "caller_operation_intents_in_stored_process_cohort"
            and plan["scope"] == "planned_stored_process_cohort"
            and all(plan[k] is False for k in ("sloAccepted", "routingEnabled", "populationCoverageVerified", "agreementVerified"))
            and plan["qualification"] == "not_assessed", "Pilot plan cannot grant SLO, routing or qualification")
    require(isinstance(plan["sourceCommit"], str) and re.fullmatch(r"[a-f0-9]{40}", plan["sourceCommit"])
            and isinstance(plan["sourceFiles"], dict) and plan["sourceFiles"]
            and all(isinstance(k, str) and k and isinstance(v, str) and SHA.fullmatch(v) for k, v in plan["sourceFiles"].items()),
            "Invalid pilot source binding")
    return plan


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
