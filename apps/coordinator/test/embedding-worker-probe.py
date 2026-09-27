"""Observe the real worker loop on loopback; credentials arrive through stdin."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "workers"))
import agat_worker  # noqa: E402
import embedding_transport  # noqa: E402


def main() -> int:
    supplied = json.load(sys.stdin)
    url = urlsplit(supplied["coordinator"])
    if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port or url.username or url.password:
        raise ValueError("The embedding worker fixture requires a loopback coordinator")
    credentials = Path(supplied["artifacts"]) / "embedding-worker-credentials.json"
    agat_worker.save_credentials(credentials, {"id": supplied["nodeId"], "token": supplied["token"]})
    model_url = supplied.get("modelUrl", "http://127.0.0.1:1/v1")
    model = urlsplit(model_url)
    if model.scheme != "http" or model.hostname != "127.0.0.1" or not model.port or model.username or model.password:
        raise ValueError("The embedding worker fixture requires a loopback model endpoint")
    sys.argv = ["agat_worker.py", "--coordinator", supplied["coordinator"],
                "--models", supplied["model"], "--embedding-models", supplied["model"],
                "--credentials", str(credentials), "--model-url", model_url,
                "--model-discovery", "off", "--concurrency", "1", "--poll-interval", "0.2",
                "--region", supplied["region"], "--residency-domain", supplied["residencyDomain"],
                "--no-web"]
    if "embeddingTransport" in supplied:
        sys.argv.extend(["--embedding-transport", supplied["embeddingTransport"]])
    if "embeddingTimeout" in supplied:
        sys.argv.extend(["--embedding-timeout", str(supplied["embeddingTimeout"])])
    if supplied.get("dryRun", True):
        sys.argv.append("--dry-run")
    events: list[dict] = []
    requests: list[dict] = []
    transports: list[dict] = []
    session_requests: list[dict] = []
    session_retired: list[dict] = []
    signals: list[dict] = []
    helpers: list[dict] = []
    active_requests: dict[int, dict] = {}
    lock = threading.Lock()
    execute_code = agat_worker.execute_knowledge_lease.__code__
    renew_code = agat_worker.knowledge_lease_renewer.__code__
    request_code = agat_worker.CoordinatorClient.request.__code__
    error_code = agat_worker.ApiError.__init__.__code__
    transport_code = agat_worker.request_embedding_response.__code__
    session_code = embedding_transport.EmbeddingSession._exchange.__code__
    retire_code = embedding_transport.EmbeddingSession._retire.__code__
    spawn_code = subprocess.Popen.__init__.__code__
    helper_paths = {str(Path(agat_worker.__file__).parent / name) for name in ("embedding_http.py", "embedding_transport.py")}

    def observe(frame, event, result):
        code = frame.f_code
        is_stop = code.co_name == "request_stop" and code.co_filename == agat_worker.__file__
        if event not in {"call", "return"} or (not is_stop and code not in {execute_code, renew_code, request_code, error_code, transport_code, session_code, retire_code, spawn_code}):
            return
        thread_id = threading.get_native_id()
        with lock:
            if is_stop and event == "return":
                record = {"number": frame.f_locals["_signum"], "atNs": time.monotonic_ns()}
                signals.append(record)
                print("AGAT_EMBEDDING_WORKER_SIGNAL " + json.dumps(record), flush=True)
            elif code is spawn_code and event == "return":
                process = frame.f_locals["self"]
                command = frame.f_locals.get("args")
                if isinstance(command, list) and any(item in helper_paths for item in command):
                    record = {"pid": process.pid, "atNs": time.monotonic_ns()}
                    helpers.append(record)
                    print("AGAT_EMBEDDING_WORKER_HELPER " + json.dumps(record), flush=True)
            elif code in {execute_code, renew_code}:
                lease_id = frame.f_locals["lease"]["leaseId"] if code is execute_code else frame.f_locals["lease_id"]
                record = {"operation": "execute" if code is execute_code else "renew",
                          "event": event, "leaseId": lease_id, "threadId": thread_id,
                          "atNs": time.monotonic_ns()}
                events.append(record)
                print("AGAT_EMBEDDING_WORKER_EVENT " + json.dumps(record), flush=True)
            elif code is request_code:
                route = frame.f_locals["path"]
                if not route.startswith("/api/v1/workers/knowledge/"):
                    return
                if event == "call":
                    record = {"path": route, "startedNs": time.monotonic_ns(), "threadId": thread_id}
                    if route.endswith("/complete"):
                        record["chunkIds"] = [item["chunkId"] for item in frame.f_locals["body"]["embeddings"]]
                    active_requests[thread_id] = record
                    requests.append(record)
                else:
                    record = active_requests.pop(thread_id)
                    record["finishedNs"] = time.monotonic_ns()
                    response = frame.f_locals.get("response")
                    if response is not None:
                        record["status"] = response.status
                    if isinstance(result, dict) and route.endswith("/lease"):
                        record["leaseId"] = result["leaseId"]
                        record["documentId"] = result["document"]["id"]
                        record["chunkIds"] = [item["id"] for item in result["chunks"]]
                    print("AGAT_EMBEDDING_WORKER_REQUEST " + json.dumps(record), flush=True)
            elif code is transport_code and event == "return":
                process = frame.f_locals.get("process")
                if process is not None:
                    transports.append({"pid": process.pid, "returncode": process.returncode,
                                       "stdinClosed": process.stdin.closed, "stdoutClosed": process.stdout.closed})
            elif code in {session_code, retire_code} and event == "return":
                process = frame.f_locals.get("process")
                if process is not None:
                    target = session_requests if code is session_code else session_retired
                    target.append({"pid": process.pid, "returncode": process.returncode,
                                   "stdinClosed": process.stdin.closed, "stdoutClosed": process.stdout.closed})
            elif code is error_code and event == "call" and thread_id in active_requests:
                active_requests[thread_id]["status"] = frame.f_locals["status"]

    config = agat_worker.parse_args()
    try:
        sys.setprofile(observe)
        threading.setprofile(observe)
        code = agat_worker.worker_loop(config)
    finally:
        sys.setprofile(None)
        threading.setprofile(None)
        credentials.unlink(missing_ok=True)
    print("AGAT_EMBEDDING_WORKER_PROBE " + json.dumps({
        "exitCode": code, "python": sys.version.split()[0], "events": events, "requests": requests, "transports": transports,
        "sessionRequests": session_requests, "sessionRetired": session_retired, "signals": signals, "helpers": helpers,
        "activeRequests": len(active_requests),
        "liveThreads": [thread.name for thread in threading.enumerate() if thread is not threading.main_thread()],
    }), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
