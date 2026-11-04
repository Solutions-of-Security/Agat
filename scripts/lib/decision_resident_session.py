"""Read-only macOS session identity and evidence checks for owned resident jobs."""
from __future__ import annotations

import ctypes
import hashlib
import os
import platform
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone

from decision_runtime.contracts import fields, number


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, lower, upper):
    require(type(value) is int and lower <= value <= upper, "Invalid session/process integer")
    return value


def gui_session_id(text, owner_uid):
    integer(owner_uid, 0, 2**32 - 2)
    require(isinstance(text, str) and len(text) <= 1024 * 1024, "Invalid native service state")
    rows = re.findall(r"^\s*domain = gui/(\d+)\s+\[(\d+)\]\s*$", text, re.MULTILINE)
    require(len(rows) == 1, "Missing or ambiguous GUI service domain")
    uid, session = map(int, rows[0])
    require(uid == owner_uid, "Foreign GUI service owner")
    return integer(session, 1, 2**32 - 2)


def native_os_values(gui_session):
    require(platform.system() == "Darwin", "Session acceptance requires macOS")
    integer(gui_session, 1, 2**32 - 2)
    boot_uuid = subprocess.check_output(["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"], text=True, timeout=5).strip()
    boot_uuid = str(uuid.UUID(boot_uuid))
    boot_time = subprocess.check_output(["/usr/sbin/sysctl", "-n", "kern.boottime"], text=True, timeout=5)
    match = re.search(r"\{ sec = (\d+), usec = (\d+) \}", boot_time)
    require(match is not None, "Cannot parse native boot time")
    seconds, micros = map(int, match.groups())
    require(0 <= micros < 1_000_000, "Invalid native boot microseconds")
    security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
    security.SessionGetInfo.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
    security.SessionGetInfo.restype = ctypes.c_int32
    session, attributes = ctypes.c_uint32(), ctypes.c_uint32()
    result = security.SessionGetInfo(gui_session, ctypes.byref(session), ctypes.byref(attributes))
    require(result == 0 and session.value == gui_session and attributes.value & 0x0010,
            "Owned service is not in an accessible graphical security session")

    class Wait(ctypes.Structure):
        _fields_ = [("tv_sec", ctypes.c_long), ("tv_nsec", ctypes.c_long)]

    system = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    system.gethostuuid.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(Wait)]
    system.gethostuuid.restype = ctypes.c_int
    raw, deadline = (ctypes.c_ubyte * 16)(), Wait(1, 0)
    require(system.gethostuuid(raw, ctypes.byref(deadline)) == 0 and any(raw), "Cannot bind evidence to this Mac")
    return {"hostFingerprint": hashlib.sha256(bytes(raw)).hexdigest(), "bootSessionUuid": boot_uuid,
            "bootTimeEpoch": seconds + micros / 1_000_000, "guiSessionId": gui_session}


def session_snapshot(service_states, owner_uid):
    require(isinstance(service_states, list) and len(service_states) == 2, "Inspect both owned resident services")
    sessions = [gui_session_id(state, owner_uid) for state in service_states]
    require(sessions[0] == sessions[1], "Resident jobs use different GUI sessions")
    snapshot = {**native_os_values(sessions[0]), "ownerUid": owner_uid, "capturedEpoch": time.time()}
    validate_snapshot(snapshot)
    return snapshot


def validate_snapshot(snapshot):
    fields(snapshot, {"hostFingerprint", "bootSessionUuid", "bootTimeEpoch", "guiSessionId", "ownerUid", "capturedEpoch"})
    require(isinstance(snapshot["hostFingerprint"], str) and re.fullmatch(r"[a-f0-9]{64}", snapshot["hostFingerprint"]), "Invalid host fingerprint")
    require(isinstance(snapshot["bootSessionUuid"], str)
            and str(uuid.UUID(snapshot["bootSessionUuid"])) == snapshot["bootSessionUuid"], "Invalid canonical boot UUID")
    require(uuid.UUID(snapshot["bootSessionUuid"]).int != 0, "Empty boot UUID")
    integer(snapshot["ownerUid"], 0, 2**32 - 2)
    integer(snapshot["guiSessionId"], 1, 2**32 - 2)
    boot = number(snapshot["bootTimeEpoch"], 1, 10**11)
    captured = number(snapshot["capturedEpoch"], 1, 10**11)
    require(boot <= captured, "Boot time is after observation")
    return snapshot


def observed_event(baseline, current, expected):
    validate_snapshot(baseline)
    validate_snapshot(current)
    require(expected in ("boot", "login"), "Specify boot or login acceptance")
    require(baseline["hostFingerprint"] == current["hostFingerprint"]
            and baseline["ownerUid"] == current["ownerUid"], "Session evidence belongs to another Mac or user")
    require(current["capturedEpoch"] > baseline["capturedEpoch"], "Session observation is not after baseline")
    boot_changed = baseline["bootSessionUuid"] != current["bootSessionUuid"]
    gui_changed = boot_changed or baseline["guiSessionId"] != current["guiSessionId"]
    if boot_changed:
        require(current["bootTimeEpoch"] > baseline["capturedEpoch"] - 1, "Changed boot UUID has inconsistent boot chronology")
    event = "boot_and_login" if boot_changed else "login" if gui_changed else None
    eligible = boot_changed if expected == "boot" else gui_changed
    return {"status": "event_observed" if eligible else "awaiting_event", "event": event,
            "bootChanged": boot_changed, "guiChanged": gui_changed, "expectedEvent": expected}


def process_start_epoch(pid):
    integer(pid, 1, 2**31 - 1)
    task_env = {**os.environ, "LC_ALL": "C", "TZ": "UTC"}
    raw = subprocess.check_output(["/bin/ps", "-p", str(pid), "-o", "lstart="], text=True, timeout=5, env=task_env).strip()
    require(0 < len(raw) <= 64 and "\n" not in raw, "Cannot read one native process start")
    return datetime.strptime(raw, "%a %b %d %H:%M:%S %Y").replace(tzinfo=timezone.utc).timestamp()


def validate_process_starts(processes, snapshot, *, after=None):
    validate_snapshot(snapshot)
    require(isinstance(processes, list) and len(processes) == 4, "Expected two services and their two owned children")
    roles, pids = [], []
    for row in processes:
        fields(row, {"role", "pid", "startedEpoch"})
        require(isinstance(row["role"], str), "Invalid process role")
        roles.append(row["role"])
        pids.append(integer(row["pid"], 1, 2**31 - 1))
        started = number(row["startedEpoch"], 1, 10**11)
        require(snapshot["bootTimeEpoch"] - 1 <= started <= snapshot["capturedEpoch"] + 1, "Process start is outside this boot observation")
        if after is not None:
            require(started > number(after, 1, 10**11) - 1, "Resident process predates the required event")
    require(len(set(pids)) == 4 and sorted(roles) == sorted(("runtime", "prometheus", "inference", "resource_tracker")), "Ambiguous owned process inventory")
    return processes
