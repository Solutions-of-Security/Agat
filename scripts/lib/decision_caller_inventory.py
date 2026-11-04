"""Census of negotiated caller intents; never a physical HTTP request inventory."""
from __future__ import annotations

import re

SCHEMA = "agat.decision.caller-inventory.v1"
HISTORY = "agat.decision.shadow-assignment-inventory.v1"
UUID = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}")
SHA = re.compile(r"[a-f0-9]{64}")


def require(value, message):
    if not value: raise ValueError(message)


def census(traces, profile, profile_sha, threshold, timing, same_json):
    counts = {key: 0 for key in ("tracesWithAccounting", "tracesMissingAccounting", "storedStages", "legacyGapStages", "replayStages",
        "assignments", "unnegotiatedAssignments", "noIntentRecorded", "intents", "returned", "pendingIntents", "missingReturns",
        "dataGapAssignments", "unknownReturns", "boundResultsWithinCallerDeadline", "timelyBoundResults", "profileBindingMismatches")}
    seen = set(); seen_ids = set(); gaps = []; latencies = []
    for trace in traces:
        run = trace["run"]["id"]
        if "decisionCallerAccounting" not in trace:
            counts["tracesMissingAccounting"] += 1; continue
        counts["tracesWithAccounting"] += 1
        inventory = trace["decisionCallerAccounting"]; history = trace.get("decisionAssignmentHistory")
        require(isinstance(inventory, dict) and set(inventory) == {"schemaVersion", "scope", "stages"}
                and inventory["schemaVersion"] == SCHEMA and inventory["scope"] == "caller_operation_intents", "Invalid caller inventory identity")
        require(isinstance(history, dict) and set(history) == {"schemaVersion", "scope", "stages"}
                and history["schemaVersion"] == HISTORY and history["scope"] == "coordinator_shadow_assignments", "Caller inventory lacks assignment history")
        require(isinstance(inventory["stages"], list) and len(inventory["stages"]) <= 10_000
                and isinstance(history["stages"], list) and len(history["stages"]) <= 10_000, "Invalid caller stage count")
        stages = {}
        for stage in history["stages"]:
            require(isinstance(stage, dict) and set(stage) == {"stageId", "coverage", "assignments"}
                    and isinstance(stage["stageId"], str) and 0 < len(stage["stageId"]) <= 200 and stage["stageId"] not in stages
                    and isinstance(stage["coverage"], str) and stage["coverage"] in ("complete", "legacy_gap", "replay")
                    and isinstance(stage["assignments"], list) and len(stage["assignments"]) <= 1000, "Invalid assignment history stage")
            stages[stage["stageId"]] = stage
        local = set()
        for stage in inventory["stages"]:
            require(isinstance(stage, dict) and set(stage) == {"stageId", "coverage", "assignments"}
                    and isinstance(stage["stageId"], str) and stage["stageId"] in stages and stage["stageId"] not in local
                    and (run, stage["stageId"]) not in seen and isinstance(stage["coverage"], str)
                    and stage["coverage"] in ("complete", "legacy_gap", "replay")
                    and isinstance(stage["assignments"], list) and len(stage["assignments"]) <= 1000, "Invalid caller inventory stage")
            local.add(stage["stageId"]); seen.add((run, stage["stageId"])); counts["storedStages"] += 1
            counts["legacyGapStages"] += int(stage["coverage"] == "legacy_gap")
            counts["replayStages"] += int(stage["coverage"] == "replay")
            assigned = {}; last_attempt = 0
            for row in stages[stage["stageId"]]["assignments"]:
                require(isinstance(row, dict) and set(row) == {"assignmentId", "stageAttempt", "profileSha256", "inputSha256", "callerTimeoutMs", "observation", "outcome"}
                        and isinstance(row["assignmentId"], str) and UUID.fullmatch(row["assignmentId"]) and row["assignmentId"] not in assigned
                        and type(row["stageAttempt"]) is int and last_attempt < row["stageAttempt"] <= 2**53-1
                        and all(isinstance(row[k], str) and SHA.fullmatch(row[k]) for k in ("profileSha256", "inputSha256"))
                        and type(row["callerTimeoutMs"]) is int and 100 <= row["callerTimeoutMs"] <= 10_000
                        and isinstance(row["outcome"], str) and row["outcome"] in ("recorded", "pending", "ended_without_observation")
                        and (row["observation"] is None or isinstance(row["observation"], dict)), "Invalid assignment history row")
                require((row["outcome"] == "recorded") == (row["observation"] is not None), "Assignment observation marker differs")
                assigned[row["assignmentId"]] = row; last_attempt = row["stageAttempt"]
            if stage["coverage"] == "complete":
                require(stages[stage["stageId"]]["coverage"] == "complete" and len(stage["assignments"]) == len(assigned), "Caller completeness contradicts assignment history")
            if stage["coverage"] == "replay":
                require(stages[stage["stageId"]]["coverage"] == "replay" and not stage["assignments"] and not assigned, "Replay created caller intents")
            last_attempt = 0; local_ids = set()
            for row in stage["assignments"]:
                require(isinstance(row, dict) and set(row) == {"assignmentId", "stageAttempt", "negotiated", "intent", "returned", "outcome"}
                        and isinstance(row["assignmentId"], str) and UUID.fullmatch(row["assignmentId"]) and row["assignmentId"] not in seen_ids
                        and type(row["stageAttempt"]) is int and last_attempt < row["stageAttempt"] <= 2**53-1
                        and type(row["negotiated"]) is bool and type(row["intent"]) is bool and (not row["intent"] or row["negotiated"])
                        and isinstance(row["outcome"], str) and row["outcome"] in ("returned", "unnegotiated", "no_intent_recorded", "intent_pending", "return_missing", "data_gap"), "Invalid caller inventory row")
                seen_ids.add(row["assignmentId"]); local_ids.add(row["assignmentId"]); last_attempt = row["stageAttempt"]
                assignment = assigned.get(row["assignmentId"])
                require(assignment is not None or row["outcome"] == "data_gap" and stage["coverage"] == "legacy_gap", "Caller assignment is missing")
                if assignment: require(assignment["stageAttempt"] == row["stageAttempt"], "Caller attempt binding differs")
                counts["assignments"] += 1; counts["intents"] += int(row["intent"])
                counts["unnegotiatedAssignments"] += int(not row["negotiated"])
                counts["noIntentRecorded"] += int(row["outcome"] == "no_intent_recorded")
                counts["dataGapAssignments"] += int(row["outcome"] == "data_gap")
                returned = row["returned"]; observation = assignment["observation"] if assignment else None
                synthetic = isinstance(observation, dict) and observation.get("status") == "unavailable" and observation.get("reason") in ("disabled", "dry_run", "missing_result")
                if row["outcome"] == "data_gap": require(stage["coverage"] == "legacy_gap", "Caller data gap claims completeness")
                elif returned is None:
                    require(not row["negotiated"] or observation is None or synthetic, "Caller observation is unaccounted")
                    expected = "unnegotiated" if not row["negotiated"] else "no_intent_recorded" if not row["intent"] else "intent_pending" if assignment["outcome"] == "pending" else "return_missing"
                    require(row["outcome"] == expected, "Caller outcome contradicts assignment state")
                if returned is None:
                    if row["intent"]:
                        counts["unknownReturns"] += 1
                        counts["pendingIntents"] += int(row["outcome"] == "intent_pending")
                        counts["missingReturns"] += int(row["outcome"] == "return_missing")
                    continue
                require(isinstance(returned, dict) and set(returned) == {"callerTiming", "status", "reason"} and row["intent"]
                        and isinstance(returned["status"], str) and returned["status"] in ("ok", "abstain", "error", "unavailable")
                        and isinstance(returned["reason"], str) and len(returned["reason"]) <= 200, "Invalid caller return")
                caller_ms = timing(returned["callerTiming"])
                if row["outcome"] == "data_gap": counts["unknownReturns"] += 1; continue
                require(row["outcome"] == "returned" and isinstance(observation, dict) and observation.get("mode") == "shadow"
                        and observation.get("fallback") == "primary" and observation.get("status") == returned["status"]
                        and observation.get("reason") == returned["reason"] and same_json(observation.get("callerTiming"), returned["callerTiming"]), "Caller return and stored observation differ")
                counts["returned"] += 1; latencies.append(caller_ms)
                matching = assignment["profileSha256"] == profile_sha
                if returned["status"] in ("ok", "abstain", "error"):
                    result = observation.get("result")
                    require(isinstance(result, dict) and result.get("mode") == "shadow" and result.get("status") == returned["status"]
                            and result.get("reason") == returned["reason"], "Caller result semantics differ")
                    matching = matching and result.get("id") == stage["stageId"] and result.get("inputSha256") == assignment["inputSha256"]
                    matching = matching and all(key in result and same_json(value, result[key]) for key, value in profile.items())
                counts["profileBindingMismatches"] += int(not matching)
                good = matching and returned["status"] in ("ok", "abstain") and caller_ms <= assignment["callerTimeoutMs"]
                counts["boundResultsWithinCallerDeadline"] += int(good)
                counts["timelyBoundResults"] += int(good and caller_ms <= threshold)
            if stage["coverage"] == "complete": require(local_ids == set(assigned), "Caller inventory omitted an assignment")
        require(local == set(stages), "Caller inventory omitted a stored stage")
    for key, name in (("tracesMissingAccounting", "missing_caller_accounting"), ("legacyGapStages", "caller_accounting_legacy_gap"),
                      ("unnegotiatedAssignments", "unnegotiated_caller_accounting"), ("unknownReturns", "unknown_caller_returns"),
                      ("profileBindingMismatches", "caller_accounting_profile_mismatch")):
        if counts[key]: gaps.append(name)
    total = counts["intents"]
    complete = not any(counts[key] for key in ("tracesMissingAccounting", "legacyGapStages", "unnegotiatedAssignments")) and not any(t["truncated"] for t in traces)
    ratio = lambda good: {"lower": good/total if total else None, "upper": (good+counts["unknownReturns"])/total if total else None}
    return {"scope": "provided_negotiated_caller_intents", "counts": counts, "dataGaps": gaps,
            "callerIntentInventoryVerified": complete, "callerReturnCoverageVerified": complete and counts["unknownReturns"] == 0,
            "httpAttemptInventoryVerified": False, "populationCoverageVerified": False,
            "withinCallerDeadlineRatio": ratio(counts["boundResultsWithinCallerDeadline"]),
            "timelyBoundResultRatio": ratio(counts["timelyBoundResults"]), "latencySamplesMs": latencies}
