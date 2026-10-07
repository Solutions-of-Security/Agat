"""One credential-bearing HTTP GET in a disposable, parent-deadlined process."""
from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import re
import ssl
import sys
from urllib.parse import urlsplit

MAX_BODY_BYTES = 16 * 1024 * 1024


class RejectedStatus(ValueError):
    def __init__(self, status):
        super().__init__("Cohort HTTP status was rejected")
        self.http_status = status


def origin(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or any(ord(c) <= 32 or ord(c) >= 127 for c in value):
        raise ValueError("Use an ASCII coordinator origin without whitespace")
    url = urlsplit(value)
    if (url.scheme not in ("http", "https") or not url.hostname or url.username is not None or url.password is not None
            or url.path not in ("", "/") or "?" in value or "#" in value or "%" in url.netloc or "\\" in value):
        raise ValueError("Use a coordinator origin without credentials, path, query or fragment")
    host = url.hostname
    if url.scheme == "http":
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError("Plain HTTP is allowed only for a literal loopback address")
    if not re.fullmatch(r"[A-Za-z0-9.:-]{1,253}", host) or url.port is not None and not 1 <= url.port <= 65535:
        raise ValueError("Invalid coordinator host or port")
    return url


def fetch(value, path, token, project, timeout_s):
    url = origin(value)
    if not isinstance(path, str) or not path.startswith("/api/v1/processes/") or any(ord(c) < 33 or ord(c) > 126 for c in path):
        raise ValueError("Invalid cohort request path")
    if not isinstance(token, str) or not 1 <= len(token) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError("Use a nonempty ASCII admin token without whitespace")
    if not isinstance(project, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", project):
        raise ValueError("Invalid project header")
    if type(timeout_s) not in (int, float) or not 0.1 <= timeout_s <= 30:
        raise ValueError("Invalid transport timeout")
    if url.scheme == "https":
        context = ssl.create_default_context(); context.set_alpn_protocols(["http/1.1"])
        connection = http.client.HTTPSConnection(url.hostname, url.port, timeout=timeout_s, context=context)
    else:
        connection = http.client.HTTPConnection(url.hostname, url.port, timeout=timeout_s)
    try:
        connection.request("GET", path, headers={"Accept": "application/json", "Accept-Encoding": "identity",
            "x-agat-admin-token": token, "x-agat-project-id": project, "Connection": "close"})
        with connection.getresponse() as response:
            if response.status != 200:
                raise RejectedStatus(response.status)
            if response.headers.get_content_type() != "application/json":
                raise ValueError("Cohort response is not application/json")
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("Encoded cohort responses are not supported")
            lengths = response.headers.get_all("Content-Length", [])
            transfers = response.headers.get_all("Transfer-Encoding", [])
            if len(lengths) > 1 or lengths and (not re.fullmatch(r"[0-9]+", lengths[0]) or transfers):
                raise ValueError("Ambiguous cohort response framing")
            expected = int(lengths[0]) if lengths else None
            if expected is not None and not 0 < expected <= MAX_BODY_BYTES:
                raise ValueError("Cohort response length is outside the byte limit")
            if transfers and (len(transfers) != 1 or transfers[0].lower().strip() != "chunked"):
                raise ValueError("Unsupported cohort transfer encoding")
            raw = response.read(MAX_BODY_BYTES + 1)
            if not 0 < len(raw) <= MAX_BODY_BYTES or expected is not None and len(raw) != expected:
                raise ValueError("Cohort body is incomplete or exceeds the byte limit")
            return raw, {"status": response.status, "contentType": "application/json", "bodyBytes": len(raw),
                         "declaredContentLength": expected, "transferEncoding": "chunked" if transfers else None}
    finally:
        connection.close()


def main():
    try:
        raw = sys.stdin.buffer.read(16 * 1024 + 1)
        if len(raw) > 16 * 1024: raise ValueError("Transport input exceeds its bound")
        data = json.loads(raw)
        if set(data) != {"origin", "path", "token", "project", "timeoutS"}: raise ValueError("Invalid transport fields")
        body, response = fetch(data["origin"], data["path"], data["token"], data["project"], data["timeoutS"])
        print(json.dumps({"status": "captured", "response": response, "bodyBase64": base64.b64encode(body).decode("ascii")}))
        return 0
    except Exception as error:
        # Do not echo URLs, headers, response bodies or exception messages from networking.
        print(json.dumps({"status": "failed", "errorType": type(error).__name__, "httpStatus": getattr(error, "http_status", None)}))
        return 1


if __name__ == "__main__": raise SystemExit(main())
