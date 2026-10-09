"""Raw-pinned direct peer receipts; primary/coordinator are explicitly unexercised."""
from collections import Counter
import hashlib
from pathlib import Path

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import Request, fields, fingerprint, number, parse_json
from decision_runtime.metrics import OUTCOMES, outcome
from scripts.lib import decision_public_workflow_active_cancellation as active
from scripts.lib.decision_performance import distribution, validate_result, profile_from_health
from scripts.lib.decision_public_load import validate_context
from scripts.lib.decision_public_load_verification import CONTEXT_PATHS, observation, same, sources_at
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_public_workflow import SOURCE_PATHS, PROFILE_PATH
from scripts.lib.decision_public_workflow_recovery import http_metrics
from scripts.lib.decision_shadow_pilot import require, timestamp
from scripts.lib.decision_shadow_sli import same_json

PATHS = SOURCE_PATHS+["scripts/run-public-support-peer-cancellation.py","scripts/verify-public-support-peer-cancellation.py",
    "scripts/test/test_decision_public_workflow_active_cancellation.py","scripts/test/test_decision_public_peer_cancellation_verification.py"]
ARTIFACTS = {"active-ready.json","active-drained.json","caller-cancellation-applied.json","native-retired.json","native-recovered.json",
             "recovery-warmup.json","relay-transport.json","runtime.log","runtime-recovered.log"}
PLAN_FIELDS = {"schemaVersion","sha256","createdAt","sourceCommit","sourceFiles","contextProfileFileSha256","context","spec","runtime",
    "profileFileSha256","manifestFileSha256","mode","primary","coordinatorExercised","durableAccountingExercised","warmupCount",
    "ownersAppointed","referenceLabels","sloAccepted","routingEnabled","qualification"}
RESULT_FIELDS = {"schemaVersion","sha256","status","planSha256","rows","warmup","samples","retired","recovered","failure","ownedPids",
    "remainingOwnedPids","cleanupErrors","runtimeExitCodes","artifactSha256","elapsedMs","primary","coordinatorExercised","durableAccountingExercised",
    "ownersAppointed","referenceLabels","classificationAccuracyMeasured","sloAccepted","routingEnabled","qualification"}


def inventory(context, plan, result, artifacts):
    context = validate_context(context); fields(plan,PLAN_FIELDS); fields(result,RESULT_FIELDS)
    verify_seal(plan,"agat.decision.public-peer-cancellation-plan.v1")
    verify_seal(result,"agat.decision.public-peer-cancellation-result.v1")
    same(plan["context"],context,"Peer embedded context changed")
    require(result["planSha256"] == plan["sha256"] and set(artifacts) == ARTIFACTS,"Peer plan or artifact inventory changed")
    fields(result["artifactSha256"],ARTIFACTS)
    for name,raw in artifacts.items():
        require(isinstance(raw,bytes) and hashlib.sha256(raw).hexdigest() == result["artifactSha256"][name],"Peer raw artifact differs")
    require(plan["schemaVersion"] == "agat.decision.public-peer-cancellation-plan.v1" and result["schemaVersion"] == "agat.decision.public-peer-cancellation-result.v1",
            "Unknown direct native peer protocol")
    for value in (plan,result):
        require(value["primary"] == "not_exercised" and all(value[key] is False for key in
            ("coordinatorExercised","durableAccountingExercised","ownersAppointed","sloAccepted","routingEnabled"))
            and type(value["referenceLabels"]) is int and value["referenceLabels"] == 0 and value["qualification"] == "not_assessed",
            "Direct peer evidence invents workflow, labels or qualification")
    require(plan["mode"] == "serial_direct_native_peer_cancellation" and type(plan["warmupCount"]) is int and plan["warmupCount"] == 4
            and result["status"] == "observed" and result["failure"] is None and result["remainingOwnedPids"] == result["cleanupErrors"] == []
            and result["classificationAccuracyMeasured"] is False and result["runtimeExitCodes"] == [75,130],"Peer run or recorded cleanup failed")
    require(all(type(code) is int for code in result["runtimeExitCodes"]) and isinstance(result["ownedPids"],list)
            and result["ownedPids"] == sorted(set(result["ownedPids"]))
            and all(type(pid) is int and 0 < pid < 2**31 for pid in result["ownedPids"]),"Peer cleanup identities are invalid")
    number(result["elapsedMs"],0,440000)
    spec = plan["spec"]; same(spec,active.active_spec(context,spec["targetIndex"]),"Prospective active peer boundary differs")
    rows = result["rows"]; count = len(context["inputs"]); target = spec["targetIndex"]
    require(isinstance(rows,list) and len(rows) == count,"Peer input omitted or repeated")
    transport = verify_seal(parse_json(artifacts["relay-transport.json"]),active.TRANSPORT_SCHEMA)
    fields(transport,{"schemaVersion","sha256","spec","proxyPort","upstreamPort","startedAt","closedAt","rows","acceptedPosts",
        "completedUpstreamPosts","interruptedActiveUpstreamPosts","errors","closed","activeHandlers"})
    same(transport["spec"],spec,"Relay changed its prospective boundary")
    require(all(type(transport[key]) is int for key in ("activeHandlers","acceptedPosts","completedUpstreamPosts","interruptedActiveUpstreamPosts"))
            and transport["closed"] is True and transport["activeHandlers"] == 0 and transport["errors"] == []
            and transport["acceptedPosts"] == count and transport["completedUpstreamPosts"] == count-1
            and transport["interruptedActiveUpstreamPosts"] == 1 and len(transport["rows"]) == count,"Relay inventory did not close exactly")
    ports = [transport[key] for key in ("proxyPort","upstreamPort")]
    require(all(type(port) is int and 1 <= port <= 65535 and port != 8766 for port in ports) and ports[0] != ports[1],"Peer port targets the protected resident")
    delivered = Counter(); statuses = Counter(); caller = []; last = timestamp(transport["startedAt"],"relay.startedAt")
    for index,(case,row,wire) in enumerate(zip(context["inputs"],rows,transport["rows"])):
        fields(row,{"index","caseId","inputSha256","status","reason","observation","callerMs","wallMs"})
        require(type(row["index"]) is int and type(wire["index"]) is int and row["index"] == wire["index"] == index and row["caseId"] == wire["caseId"] == case["id"]
                and row["inputSha256"] == wire["inputSha256"] == case["inputSha256"] and wire["profileSha256"] == context["profileSha256"]
                and wire["cancelOnDisconnect"] is True,"Original peer input order or profile changed")
        raw = wire["requestBody"].encode(); request = Request.from_dict(parse_json(raw))
        require(0 < len(raw) <= 128*1024 and hashlib.sha256(raw).hexdigest() == wire["requestBodySha256"]
                and request == Request.from_dict(case["request"]) and wire["stageId"] == request.id,"Peer forwarded transformed request bytes")
        require(last <= timestamp(wire["acceptedAt"],"acceptedAt") <= timestamp(wire["finishedAt"],"finishedAt")
                <= timestamp(transport["closedAt"],"closedAt"),"Peer requests overlap or are reordered")
        last = timestamp(wire["finishedAt"],"finishedAt")
        number(wire["elapsedMs"],0,spec["callerTimeoutMs"]+250)
        require(abs(row["callerMs"]-wire["elapsedMs"]) <= 250,"Peer caller timing omits relay work")
        observed_outcome = observation(row,case,context["profile"]); statuses[row["status"]] += 1
        if index == target:
            fields(wire,{"index","caseId","stageId","inputSha256","requestBody","requestBodySha256","profileSha256","cancelOnDisconnect","acceptedAt",
                "upstreamRequestSentAt","clientEofObservedAt","upstreamShutdownAt","finishedAt","elapsedMs","clientEofObserved","upstreamShutdownApplied",
                "upstreamResponseBytesObserved","responseBytesWritten","downstreamWriteCompleted","upstreamCompletedNormally"})
            require(row["status"] == "unavailable" and row["reason"] == "cancelled" and "result" not in row["observation"]
                    and wire["upstreamCompletedNormally"] is False and wire["downstreamWriteCompleted"] is False
                    and wire["clientEofObserved"] is True and wire["upstreamShutdownApplied"] is True
                    and type(wire["upstreamResponseBytesObserved"]) is int and type(wire["responseBytesWritten"]) is int
                    and wire["upstreamResponseBytesObserved"] == wire["responseBytesWritten"] == 0,"Peer target invented a completed result")
            require(abs(row["callerMs"]-wire["elapsedMs"]) <= 250,"Target caller timing omits active work")
            continue
        fields(wire,{"index","caseId","stageId","inputSha256","requestBody","requestBodySha256","profileSha256","cancelOnDisconnect","acceptedAt",
            "upstreamCompletedAt","finishedAt","upstreamMs","elapsedMs","upstreamStatus","upstreamContentType","responseBody","responseBodySha256",
            "withheld","downstreamWriteCompleted","responseBytesWritten","clientEofObserved"})
        response = wire["responseBody"].encode(); typed = parse_json(response); validate_result(typed,request,context["profile"])
        require(hashlib.sha256(response).hexdigest() == wire["responseBodySha256"] and 0 < len(response) <= 64*1024
                and same_json(typed,row["observation"]["result"]) and type(wire["upstreamStatus"]) is int
                and wire["upstreamStatus"] == (200 if case["contextEligible"] else 422)
                and wire["upstreamContentType"].split(";")[0] == "application/json" and wire["withheld"] is False
                and wire["downstreamWriteCompleted"] is True and wire["clientEofObserved"] is False
                and type(wire["responseBytesWritten"]) is int and wire["responseBytesWritten"] == len(response),
                "Delivered peer response or physical HTTP boundary differs")
        require(timestamp(wire["acceptedAt"],"acceptedAt") <= timestamp(wire["upstreamCompletedAt"],"completedAt") <= timestamp(wire["finishedAt"],"finishedAt")
                and number(wire["upstreamMs"],0,spec["upstreamTimeoutMs"]) >= typed["durationMs"]-.1,"Peer native timing is incomplete")
        delivered[observed_outcome] += 1
        if row["status"] in {"ok","abstain"}: caller.append(row["callerMs"])
    ready = verify_seal(parse_json(artifacts["active-ready.json"]),active.READY_SCHEMA)
    drained = verify_seal(parse_json(artifacts["active-drained.json"]),active.DRAIN_SCHEMA)
    wire = transport["rows"][target]
    same(ready,active.ready_receipt(wire,{"capturedAt":ready["activeObservedAt"],"metricsRaw":ready["activeMetricsRaw"]}),"Active barrier does not bind exact request/snapshot")
    same(drained,active.drain_receipt(wire),"EOF drain does not bind target row")
    applied = fields(parse_json(artifacts["caller-cancellation-applied.json"]),{"schemaVersion","targetIndex","caseId","inputSha256","requestedAt","readyFileSha256","method","coordinatorExercised"})
    require(applied["schemaVersion"] == "agat.decision.public-peer-cancellation-applied.v1" and applied["targetIndex"] == target
            and applied["caseId"] == spec["targetCaseId"] and applied["inputSha256"] == spec["targetInputSha256"]
            and applied["method"] == "local_caller_event" and applied["coordinatorExercised"] is False
            and applied["readyFileSha256"] == hashlib.sha256(artifacts["active-ready.json"]).hexdigest(),"Peer Event falsely attributed or retargeted")
    active_at = timestamp(ready["activeObservedAt"],"activeAt"); cancel_at = timestamp(applied["requestedAt"],"requestedAt")
    eof_at = timestamp(wire["clientEofObservedAt"],"eofAt")
    require(0 <= (cancel_at-active_at).total_seconds()*1000 <= spec["sampleToCancelMaxMs"]
            and 0 <= (eof_at-cancel_at).total_seconds()*1000 <= spec["cancelToEofDeadlineMs"]
            and eof_at <= timestamp(wire["upstreamShutdownAt"],"shutdownAt") <= timestamp(wire["finishedAt"],"finishedAt"),"Active cancellation missed its prospective boundary")
    retired = verify_seal(parse_json(artifacts["native-retired.json"]),active.RETIREMENT_SCHEMA)
    same(retired,active.retired_receipt(spec,ready,drained,runtime_pid=retired["runtimePid"],native_pids=retired["nativePids"],exit_code=75,remaining=[],
        log_raw=artifacts["runtime.log"],observed_at=retired["observedExitedAt"]),"Native retirement proof differs")
    require(set(retired["nativePids"]) <= set(result["ownedPids"]) and result["retired"] == retired,"Native PIDs not owned by this peer experiment")
    require(0 <= (timestamp(retired["observedExitedAt"],"retiredAt")-eof_at).total_seconds()*1000 <= spec["retirementDeadlineMs"],
            "Native retirement exceeded its prospective deadline")
    recovered = verify_seal(parse_json(artifacts["native-recovered.json"]),active.RECOVERED_SCHEMA)
    same(recovered,active.recovered_receipt(spec,retired,runtime_pid=recovered["runtimePid"],profile_sha=context["profileSha256"],
        server_start=float(recovered["readyServerStartText"]),warmup_file_sha=hashlib.sha256(artifacts["recovery-warmup.json"]).hexdigest(),applied_at=recovered["appliedAt"]),"Recovery proof differs")
    require(recovered["runtimePid"] in result["ownedPids"] and result["recovered"] == recovered
            and timestamp(recovered["appliedAt"],"recoveredAt") <= timestamp(transport["rows"][target+1]["acceptedAt"],"suffixAt"),"Suffix crossed retirement/recovery barrier")
    require((timestamp(recovered["appliedAt"],"recoveredAt")-timestamp(retired["observedExitedAt"],"retiredAt")).total_seconds()*1000
            <= spec["recoveryDeadlineMs"],"Native recovery exceeded its prospective deadline")
    physical = verify_physical(context,result,transport,ready,retired,recovered,artifacts)
    return sealed({"schemaVersion":"agat.decision.public-peer-cancellation-inventory.v1","status":"integration_pass","scheduled":count,
        "callerReturns":count,"computed":len(caller),"unavailableCancelledReturns":1,"deliveredNativeResults":count-1,
        "statuses":dict(statuses),"deliveredPhysicalOutcomes":dict(delivered),"callerMsComputed":distribution(caller),
        "targetCallerMs":rows[target]["callerMs"],"cancelEventToEofMs":round((eof_at-cancel_at).total_seconds()*1000,3),
        "targetTypedResult":None,"targetCompletionCounterRecorded":False,"healthySuffixCases":count-target-1,**physical,
        "primary":"not_exercised","coordinatorExercised":False,"durableAccountingExercised":False,
        "referenceLabels":0,"ownersAppointed":False,"sloAccepted":False,"routingEnabled":False,"qualification":"not_assessed"})


def verify_physical(context,result,transport,ready,retired,recovered,artifacts):
    samples = result["samples"]; warmup = result["warmup"]; target = transport["spec"]["targetIndex"]
    require(len(samples) == 6 and len(warmup) == 4,"Missing two native origins and separate warmups")
    labels = ("ready_before_scoring","after_warmup","before_target","ready_before_scoring","after_warmup","after_inventory")
    values = []; epochs = []; previous = -1
    for index,(sample,label) in enumerate(zip(samples,labels)):
        fields(sample,{"label","epoch","capturedAt","runtimePid","metricsRaw","counters","serverStart","health","ownedPids","elapsedMs"})
        require(sample["label"] == label and type(sample["epoch"]) is int and sample["epoch"] == index//3
                and type(sample["runtimePid"]) is int and sample["runtimePid"] == (retired if index<3 else recovered)["runtimePid"]
                and number(sample["elapsedMs"],0,result["elapsedMs"]) > previous and profile_from_health(sample["health"]) == context["profile"],"Native snapshots reordered or profile changed")
        counts,epoch = http_metrics(sample["metricsRaw"],in_progress=0)
        same(counts,sample["counters"],"Physical counters changed"); same(epoch,sample["serverStart"],"Physical epoch changed")
        require(isinstance(sample["ownedPids"],list) and sample["ownedPids"] == sorted(set(sample["ownedPids"]))
                and all(type(pid) is int for pid in sample["ownedPids"]) and sample["runtimePid"] in sample["ownedPids"]
                and set(sample["ownedPids"]) <= set(result["ownedPids"]),"Snapshot contains a foreign PID")
        values.append(counts); epochs.append(epoch); previous=sample["elapsedMs"]
    require(len(set(epochs[:3])) == len(set(epochs[3:])) == 1 and epochs[3] > epochs[0]
            and str(epochs[0]) == retired["retiredServerStartText"] and str(epochs[3]) == recovered["readyServerStartText"],"Runtime origins changed or were reused")
    require(all(value == 0 for row in (values[0],values[3]) for value in row.values()),"Native origins not empty")
    case = next(case for case in context["inputs"] if case["contextEligible"]); warm_outcomes = [Counter(),Counter()]
    for index,row in enumerate(warmup):
        fields(row,{"epoch","iteration","caseId","status","reason","observation","callerMs","wallMs"})
        require(type(row["epoch"]) is int and type(row["iteration"]) is int and row["epoch"] == index//2 and row["iteration"] == index%2
                and row["caseId"] == case["id"] and row["status"] in {"ok","abstain"},"Warmup identity changed")
        warm_outcomes[index//2][observation(row,case,context["profile"])] += 1
    same(parse_json(artifacts["recovery-warmup.json"]),warmup[2:],"Recovery warmup raw bytes differ")
    prefix = Counter(outcome(parse_json(row["responseBody"])) for row in transport["rows"][:target])
    suffix = Counter(outcome(parse_json(row["responseBody"])) for row in transport["rows"][target+1:])
    for before,after,expected in ((0,1,warm_outcomes[0]),(1,2,prefix),(3,4,warm_outcomes[1]),(4,5,suffix)):
        require({key:values[after][key]-values[before][key] for key in OUTCOMES} == {key:expected[key] for key in OUTCOMES},"Missing or extra physical call")
    active_counts,active_epoch = http_metrics(ready["activeMetricsRaw"],in_progress=1)
    same(active_counts,values[2],"Active snapshot contains a completed target"); same(active_epoch,epochs[0],"Active target changed its original epoch")
    require(timestamp(samples[2]["capturedAt"],"prefixAt") <= timestamp(ready["activeObservedAt"],"activeAt")
            and timestamp(retired["observedExitedAt"],"retiredAt") <= timestamp(samples[3]["capturedAt"],"newOriginAt")
            <= timestamp(samples[4]["capturedAt"],"warmupAt") <= timestamp(recovered["appliedAt"],"recoveredAt")
            <= timestamp(samples[5]["capturedAt"],"finalAt"),"Physical snapshots crossed lifecycle barriers")
    return {"runtimeEpochs":2,"warmupCalls":4,"knownCompletedPhysicalCalls":sum(values[2].values())+sum(values[5].values()),
            "physicalScheduledPostStarts":len(context["inputs"]),"interruptedActiveNativePosts":1,"targetTerminalCounterUnknown":True}


def verify(root,directory,context_path,*,context_sha,plan_sha,result_sha):
    raw = {"context":pinned_input(context_path,context_sha,32*1024*1024),"plan":pinned_input(directory/"plan.json",plan_sha,32*1024*1024),
        "result":pinned_input(directory/"result.json",result_sha,64*1024*1024)}
    context = validate_context(parse_json(raw["context"])); plan = verify_seal(parse_json(raw["plan"]),"agat.decision.public-peer-cancellation-plan.v1")
    result = verify_seal(parse_json(raw["result"]),"agat.decision.public-peer-cancellation-result.v1")
    same(plan["context"],context,"Embedded raw-pinned context differs")
    require(plan["contextProfileFileSha256"] == context_sha and result["planSha256"] == plan["sha256"]
            and plan["profileFileSha256"] == context["profileFileSha256"] and plan["manifestFileSha256"] == context["manifestFileSha256"],"Peer raw plan/context/model binding differs")
    sources_at(root,context["sourceCommit"],context["sourceFiles"],CONTEXT_PATHS)
    sources = sources_at(root,plan["sourceCommit"],plan["sourceFiles"],PATHS)
    require(hashlib.sha256(sources[PROFILE_PATH]).hexdigest() == context["profileFileSha256"],"Historical profile bytes differ")
    same(parse_json(sources[PROFILE_PATH]),context["profile"],"Historical profile semantics differ")
    implementation = hashlib.sha256()
    for name in sorted(name for name in sources if Path(name).parent == Path("decision_runtime") and name.endswith(".py")):
        implementation.update(Path(name).name.encode()+b"\0"+sources[name]+b"\0")
    require(implementation.hexdigest() == context["profile"]["model"]["implementationSha256"],"Peer native implementation differs")
    requirements = dict(line.split("==") for line in sources["decision_runtime/requirements-mlx.txt"].decode().splitlines() if line and not line.startswith("#"))
    same(plan["runtime"],{"python":"3.13.12","machine":"arm64","packages":requirements},"Historical native dependencies differ")
    same(plan["runtime"],context["tokenizerEnvironment"],"Native environment differs from context")
    fields(result["artifactSha256"],ARTIFACTS)
    artifacts = {name:pinned_input(directory/name,result["artifactSha256"][name],16*1024*1024) for name in ARTIFACTS}
    evidence = inventory(context,plan,result,artifacts)
    for name,expected in raw.items():
        path = context_path if name == "context" else directory/(name+".json")
        require(path.read_bytes() == expected,"Consumed peer receipt changed")
    for name,expected in artifacts.items(): require(pinned_input(directory/name,result["artifactSha256"][name],16*1024*1024) == expected,"Peer artifact changed during replay")
    return sealed({"schemaVersion":"agat.decision.public-peer-cancellation-verification.v1","status":"pass","sourceCommit":plan["sourceCommit"],
        "sourceFilesCount":len(sources),"contextProfileFileSha256":context_sha,"planFileSha256":plan_sha,"resultFileSha256":result_sha,
        "inventory":evidence,"verificationModelCalls":0,"reportedCleanupComplete":True,"liveCleanupVerified":False,
        "gpuKernelPreemptionEstablished":False,"primary":"not_exercised","coordinatorExercised":False,"durableAccountingExercised":False,
        "referenceLabels":0,"ownersAppointed":False,"sloAccepted":False,"routingEnabled":False,"qualification":"not_assessed"})
