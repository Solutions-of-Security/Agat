"""Pinned primary companion for one whole public development arrival window."""
from collections import Counter
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from decision_runtime.contracts import fields, fingerprint, number
from scripts.lib.decision_arrival_rate import drive_arrivals
from scripts.lib import decision_arrival_primary as primary
from scripts.lib.decision_shadow_pilot import require

SCHEMAS = ("agat.decision.public-support-load-plan.v2", "agat.decision.public-support-load-result.v2", "agat.decision.public-support-load-phase.v2")
SOURCE_PATHS = [*primary.PRIMARY_SOURCES, "scripts/lib/decision_public_primary.py", "scripts/test/test_decision_public_primary.py"]


class PrimaryInventory:
    def __init__(self, transport, origin, count, cancelled, journal=None, driver=None, *, extended=False):
        require(type(extended) is bool and type(count) is int and 1 <= count <= (120 if extended else 60)
                and callable(transport) and callable(cancelled), "Unsupported public primary inventory")
        if extended:
            from scripts.lib.decision_public_schedule import drive_extended_arrivals
            driver = drive_extended_arrivals if driver is None else driver
        else:
            driver = drive_arrivals if driver is None else driver
        number(origin, 0, 86_400_000_000)
        self.transport,self.origin,self.count,self.cancelled,self.journal,self.driver = transport,origin,count,cancelled,journal,driver
        self.rows = [None]*count; self.failure = None; self.lock = threading.Lock()
        self.thread = threading.Thread(target=self.run, name="agat-public-primary-scheduler")
    def save(self, index, row):
        with self.lock:
            self.rows[index] = row
            if self.journal: self.journal(row)
    def run(self):
        capacity = threading.BoundedSemaphore(1); futures = []
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                def one(index, base):
                    try:
                        began = time.monotonic(); value = primary.measure_primary(self.transport)
                        self.save(index,{**base,"startedMs":round((began-self.origin)*1000,3),
                                         "finishedMs":round((time.monotonic()-self.origin)*1000,3),**value})
                    finally: capacity.release()
                def dispatch(index, due, observed, reason):
                    base = {"index":index,"scheduledMs":round(due*1000,3),"dispatchMs":round(observed*1000,3)}
                    if reason or not capacity.acquire(blocking=False):self.save(index,{**base,"status":"dropped","reason":reason or "client_capacity"})
                    else:futures.append(pool.submit(one,index,base))
                self.driver(.5,self.count,1,100,dispatch,start=self.origin,cancelled=self.cancelled)
                for future in futures:future.result()
        except Exception as error:self.failure = type(error).__name__
    def start(self):self.thread.start()
    def finish(self):
        self.thread.join(timeout=2*self.count+35)
        require(not self.thread.is_alive() and self.failure is None and all(row is not None for row in self.rows), "Primary inventory did not drain")
        return self.rows


def verify_plan(config, sources):
    fields(config, {"model", "manifestSha256", "blobCount", "blobBytes", "release", "releaseFileSha256", "request", "requestSha256", "settings", "ratePerSecond", "clientSlots", "timeoutSeconds"})
    expected = {"model":primary.MODEL,"manifestSha256":primary.DIGEST,"request":primary.REQUEST,"requestSha256":fingerprint(primary.REQUEST),
                "settings":primary.SETTINGS,"ratePerSecond":.5,"clientSlots":1,"timeoutSeconds":30}
    require(fingerprint({key:config[key] for key in expected}) == fingerprint(expected), "Primary protocol differs")
    import hashlib
    from decision_runtime.contracts import parse_json
    pin = sources[primary.PRIMARY_SOURCES[2]]
    require(fingerprint(config["release"]) == fingerprint(parse_json(pin)) and config["releaseFileSha256"] == hashlib.sha256(pin).hexdigest()
            and type(config["blobCount"]) is int and config["blobCount"] > 0 and type(config["blobBytes"]) is int and config["blobBytes"] > 0,
            "Primary artifact census or release pin differs")


def verify_phase(phase, count):
    extended = phase["schemaVersion"] != SCHEMAS[2]
    if extended:
        from scripts.lib.decision_public_schedule import SCHEMAS as extended_schemas
        require(phase["schemaVersion"] == extended_schemas[2], "Unsupported public primary phase")
    require(type(count) is int and 1 <= count <= (120 if extended else 60)
            and phase["condition"] == "primary_active",
            "Wrong public primary condition")
    if extended:
        require(count == len(phase["rows"])*2 and phase["ratePerSecond"] == .25, "Primary does not cover the whole quarter-rate window")
    number(phase["phaseOriginMonotonicMs"],0,86_400_000_000_000)
    rows = phase["primaryRows"]
    require(isinstance(rows,list) and len(rows) == count, "Primary scheduled denominator differs")
    intervals = []; drops = Counter(); returned = []
    for index,row in enumerate(rows):
        fields(row,{"index","scheduledMs","dispatchMs","status"},{"reason","startedMs","finishedMs","wallMs","response"})
        require(type(row["index"]) is int and row["index"] == index and abs(number(row["scheduledMs"],0,240000 if extended else 120000)-index*2000) <= .0011, "Primary fixed offsets differ")
        lag = number(row["dispatchMs"],0,phase["elapsedMs"]+.0011)-row["scheduledMs"]
        require(lag >= -.0011,"Primary dispatched early")
        if row["status"] == "dropped":
            require(set(row) == {"index","scheduledMs","dispatchMs","status","reason"} and row["reason"] in {"scheduler_lag","client_capacity"}
                    and (lag > 100-.0011 if row["reason"] == "scheduler_lag" else lag <= 100+.0011), "False primary drop or invented metadata")
            drops[row["reason"]] += 1;continue
        require(set(row) == {"index","scheduledMs","dispatchMs","status","startedMs","finishedMs","wallMs","response"}
                and row["status"] == "returned" and lag <= 100+.0011,"Incomplete primary response or catch-up")
        began = number(row["startedMs"],row["dispatchMs"]-.0011,phase["elapsedMs"]+.0011)
        finished = number(row["finishedMs"],began,phase["elapsedMs"]+.0011)
        wall = number(row["wallMs"],0,30000.1)
        require(finished-began+.02 >= wall and (not intervals or began+.0011 >= intervals[-1][1]),"Primary concurrency or wall interval differs")
        primary.validate_response(row["response"])
        require(row["response"]["total_duration"]/1_000_000 <= wall+.1,"Primary server duration exceeds caller wall")
        intervals.append((began,finished));returned.append(row)
    decisions = [(row["startedMs"],row["finishedMs"]) for row in phase["rows"] if row["status"] != "dropped"]
    overlap = sum(max(a,c)<min(b,d) for a,b in intervals for c,d in decisions)
    require(returned and overlap > 0,"No actual primary/decision HTTP overlap")
    from scripts.lib.decision_performance import distribution
    return {"scheduled":count,"returned":len(returned),"dropped":dict(drops),"returnedPerScheduled":len(returned)/count,
            "callerWallMs":distribution([row["wallMs"] for row in returned]),"httpOverlapPairs":overlap,
            "generatedTokens":sum(row["response"]["eval_count"] for row in returned)}
