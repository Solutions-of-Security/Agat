"""Census of stored shadow stages, independent of actual HTTP attempt counts."""
from __future__ import annotations

import re
from dataclasses import dataclass

SCHEMA = "agat.decision.shadow-stage-inventory.v1"
TERMINAL = {"completed", "failed", "cancelled"}
STATES = TERMINAL | {"pending", "queued", "running", "waiting_approval", "waiting_external"}


def require(condition, message):
    if not condition: raise ValueError(message)


def optional_sha(value):
    return value is None or isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value)


@dataclass
class Census:
    counts: dict
    assigned_recorded: dict
    gaps: list


def census(traces, profile_sha):
    counts = {"storedShadowStages": 0, "assignedStoredStages": 0, "recordedAssignedStages": 0,
              "missingTerminalResults": 0, "pendingAssignedStages": 0, "unassignedStoredStages": 0,
              "replayedStoredStages": 0, "tracesWithInventory": 0, "tracesMissingInventory": 0,
              "inventoryProfileMismatches": 0, "missingInventoryBindings": 0}
    assigned_recorded = {}; seen = set()
    for trace in traces:
        require(isinstance(trace, dict) and isinstance(trace.get("run"), dict), "Invalid inventory trace")
        run = trace["run"].get("id")
        require(isinstance(run, str) and 0 < len(run) <= 200, "Invalid inventory run identity")
        observations = trace.get("decisionObservations")
        require(isinstance(observations, list) and len(observations) <= 10_000, "Invalid inventory observations")
        recorded = {}
        for row in observations:
            require(isinstance(row, dict) and isinstance(row.get("stageId"), str), "Invalid inventory observation identity")
            require(row["stageId"] not in recorded, "Duplicate recorded stage in inventory")
            recorded[row["stageId"]] = row
        if "decisionStageInventory" not in trace:
            counts["tracesMissingInventory"] += 1
            for stage in recorded:
                require((run, stage) not in seen, "Duplicate run/stage observation across provided traces")
                seen.add((run, stage))
            continue
        counts["tracesWithInventory"] += 1
        inventory = trace["decisionStageInventory"]
        require(isinstance(inventory, dict) and set(inventory) == {"schemaVersion", "scope", "stages"}
                and inventory["schemaVersion"] == SCHEMA and inventory["scope"] == "stored_shadow_stages", "Invalid stage inventory identity")
        stages = inventory["stages"]
        require(isinstance(stages, list) and len(stages) <= 10_000, "Invalid stage inventory size")
        local = set()
        for row in stages:
            require(isinstance(row, dict) and set(row) == {"stageId", "stageStatus", "assigned", "observationRecorded", "profileSha256", "inputSha256", "callerTimeoutMs"}, "Invalid stage inventory fields")
            stage = row["stageId"]
            require(isinstance(stage, str) and 0 < len(stage) <= 200 and stage not in local, "Duplicate or invalid inventory stage")
            require((run, stage) not in seen, "Duplicate run/stage observation across provided traces")
            local.add(stage); seen.add((run, stage))
            require(isinstance(row["stageStatus"], str) and row["stageStatus"] in STATES, "Unknown inventory stage status")
            require(type(row["assigned"]) is bool and type(row["observationRecorded"]) is bool, "Invalid inventory assignment/observation marker")
            require(row["observationRecorded"] == (stage in recorded), "Inventory and recorded observation marker differ")
            require(optional_sha(row["profileSha256"]) and optional_sha(row["inputSha256"]), "Invalid inventory binding SHA")
            timeout = row["callerTimeoutMs"]
            require(timeout is None or type(timeout) is int and 100 <= timeout <= 10_000, "Invalid inventory caller timeout")
            counts["storedShadowStages"] += 1
            observation = recorded[stage].get("observation") if stage in recorded else None
            if stage in recorded:
                require(isinstance(observation, dict), "Invalid recorded inventory observation")
                for key in ("profileSha256", "inputSha256", "callerTimeoutMs"):
                    actual = recorded[stage].get(key)
                    require(type(actual) is type(row[key]) and actual == row[key], "Inventory and recorded lease bindings differ")
                if "reusedFromStageId" in observation or observation.get("reason") == "safe_replay_unavailable":
                    counts["replayedStoredStages"] += 1
                    continue
            if not row["assigned"]:
                require(row["profileSha256"] is None and row["inputSha256"] is None and timeout is None, "Unassigned inventory has lease bindings")
                counts["unassignedStoredStages"] += 1
                continue
            counts["assignedStoredStages"] += 1
            counts["missingInventoryBindings"] += int(any(row[key] is None for key in ("profileSha256", "inputSha256", "callerTimeoutMs")))
            counts["inventoryProfileMismatches"] += int(row["profileSha256"] is not None and row["profileSha256"] != profile_sha)
            if observation is not None:
                counts["recordedAssignedStages"] += 1; assigned_recorded[(run, stage)] = row
            elif row["stageStatus"] in TERMINAL:
                counts["missingTerminalResults"] += 1
            else:
                counts["pendingAssignedStages"] += 1
        require(set(recorded) <= local, "Stored observation is omitted from stage inventory")
    gaps = [name for condition, name in (
        (counts["tracesMissingInventory"], "missing_stage_inventory"),
        (counts["pendingAssignedStages"], "pending_assigned_stages"),
        (counts["missingInventoryBindings"], "missing_inventory_bindings"),
        (counts["inventoryProfileMismatches"], "inventory_profile_binding_mismatch")) if condition]
    return Census(counts, assigned_recorded, gaps)
