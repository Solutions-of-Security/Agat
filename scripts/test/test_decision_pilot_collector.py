import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed, verify_seal
from scripts.lib.decision_pilot_transport import MAX_BODY_BYTES, origin
from scripts.test import test_decision_pilot_evaluation as fixtures

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("cohort_collector_cli", ROOT / "scripts/collect-decision-shadow-cohort.py")
cli = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(cli)
TOKEN = "synthetic-collector-credential"


class PilotCollectorTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PilotEvaluationTest(); self.fixture.setUp()
        self.body = json.dumps(self.fixture.cohort(), indent=2).encode() + b"\n"
        self.requests = []; self.response = {"status": 200, "headers": {}, "body": self.body, "mode": "normal"}
        parent = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *args): pass
            def do_GET(self):
                parent.requests.append({"path": self.path, "token": self.headers.get("x-agat-admin-token"), "project": self.headers.get("x-agat-project-id")})
                response = parent.response
                self.send_response(response["status"])
                self.send_header("Content-Type", response["headers"].get("Content-Type", "application/json"))
                self.send_header("Connection", "close")
                if response["mode"] == "chunked": self.send_header("Transfer-Encoding", "chunked")
                elif response["mode"] == "incomplete": self.send_header("Content-Length", str(len(response["body"])+100))
                else: self.send_header("Content-Length", str(response["headers"].get("Content-Length", len(response["body"]))))
                for key, value in response["headers"].items():
                    if key not in ("Content-Type", "Content-Length"): self.send_header(key, value)
                self.end_headers()
                try:
                    if response["mode"] == "stall": time.sleep(0.6)
                    if response["mode"] == "chunked":
                        for fragment in (response["body"][:99], response["body"][99:]):
                            self.wfile.write(f"{len(fragment):X}\r\n".encode()+fragment+b"\r\n")
                        self.wfile.write(b"0\r\n\r\n")
                    else: self.wfile.write(response["body"])
                except (BrokenPipeError, ConnectionResetError): pass
                finally: self.close_connection = True
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler); self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)

    def test_exact_bytes_scope_headers_chunked_and_ambient_proxy_is_ignored(self):
        with patch.dict(os.environ, {"http_proxy": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1", "SSLKEYLOGFILE": "/nonexistent/secret-log"}):
            raw, request = cli.capture(self.url, self.fixture.plan, TOKEN, 3)
        self.assertEqual(raw, self.body); self.assertEqual(request["requestCount"], 1)
        self.assertEqual(self.requests[0]["token"], TOKEN); self.assertEqual(self.requests[0]["project"], "fixture-project")
        self.assertIn("processVersion=1", self.requests[0]["path"]); self.assertNotIn(TOKEN, json.dumps(request))
        self.response["mode"] = "chunked"
        self.assertEqual(cli.capture(self.url, self.fixture.plan, TOKEN, 3)[0], self.body)

    def test_redirect_error_incomplete_encoding_wrong_media_and_oversize_fail_once(self):
        variants = [{"status": 302, "headers": {"Location": self.url+"/redirect"}}, {"status": 401},
            {"mode": "incomplete"}, {"headers": {"Content-Encoding": "gzip"}},
            {"headers": {"Content-Type": "text/html"}}, {"headers": {"Content-Length": MAX_BODY_BYTES+1}},
            {"headers": {"Transfer-Encoding": "chunked"}}]
        for variant in variants:
            with self.subTest(variant=variant):
                self.response = {"status": 200, "headers": {}, "body": self.body, "mode": "normal", **variant}
                before = len(self.requests)
                with self.assertRaises(ValueError): cli.capture(self.url, self.fixture.plan, TOKEN, 3)
                self.assertEqual(len(self.requests), before+1)

    def test_wrong_scope_and_missing_ledger_cannot_emit_a_valid_capture(self):
        for mutate in (lambda c: c["scope"].update(projectId="other"), lambda c: c["traces"].clear(),
                       lambda c: c["snapshot"].update(truncated=True)):
            body = self.fixture.cohort(); mutate(body); self.response["body"] = json.dumps(body).encode()
            with self.assertRaises(ValueError): cli.capture(self.url, self.fixture.plan, TOKEN, 3)

    def test_overall_network_deadline_kills_and_reaps_owned_transport(self):
        self.response["mode"] = "stall"
        original = subprocess.Popen; children = []
        def spawn(*args, **kwargs):
            child = original(*args, **kwargs); children.append(child); return child
        started = time.monotonic()
        with patch.object(cli.subprocess, "Popen", side_effect=spawn):
            with self.assertRaisesRegex(ValueError, "overall deadline"): cli.capture(self.url, self.fixture.plan, TOKEN, 0.2)
        self.assertLess(time.monotonic()-started, 1.5)
        self.assertEqual(len(children), 1); self.assertIsNotNone(children[0].returncode)

    def test_origin_and_credentials_are_rejected_before_any_network(self):
        for value in ("http://example.com", "http://localhost", "https://u:secret@example.com", "https://example.com/path", "https://example.com/?", "https://example.com/#",
                      "https://127.0.0.1:0", "http://[::1%25lo0]", "https://example.com\n", "https://example.com\\@127.0.0.1"):
            with self.subTest(value=value), self.assertRaises(ValueError): origin(value)
        self.assertEqual(origin("http://[::1]:8080").hostname, "::1")
        self.assertEqual(origin("https://coordinator.example").hostname, "coordinator.example")
        for token in (None, "", "x\r\ny", "é", "x"*4097):
            with self.assertRaises(ValueError): cli.capture(self.url, self.fixture.plan, token, 3)
        self.assertEqual(self.requests, [])

    def cli_inputs(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name); private = root / "docs/private"; private.mkdir(parents=True)
        plan = private / "plan.json"; plan.write_text(json.dumps(self.fixture.plan))
        self.output = private / "capture"; self.identity = ("a"*40, {"fixture": "b"*64})
        args = ["--plan", str(plan), "--plan-file-sha256", hashlib.sha256(plan.read_bytes()).hexdigest(),
                "--coordinator-url", self.url, "--traffic-kind", "diagnostic_fixture", "--timeout-s", "3", "--output-dir", str(self.output)]
        return root, plan, args

    def test_cli_private_immutable_receipt_no_token_and_all_unverified_flags(self):
        root, plan, args = self.cli_inputs()
        with patch.object(cli,"ROOT",root), patch.object(cli,"source_identity",return_value=self.identity), patch.dict(os.environ,{"AGAT_ADMIN_TOKEN":TOKEN}):
            # Capture is already integration-tested; exercise receipt/source checks with exact bytes.
            with patch.object(cli,"capture",return_value=(self.body,{"requestCount":1})):
                self.assertEqual(cli.main(args),0)
                report = verify_seal(json.loads((self.output/"acquisition.json").read_text()),"agat.decision.cohort-acquisition.v1")
                self.assertEqual(report["status"],"diagnostic_only"); self.assertEqual((self.output/"cohort.http.json").read_bytes(),self.body)
                self.assertEqual(self.output.stat().st_mode & 0o777,0o700)
                for path in self.output.iterdir(): self.assertEqual(path.stat().st_mode & 0o777,0o600); self.assertNotIn(TOKEN,path.read_text())
                for name in ("sloAccepted","routingEnabled","serverDeploymentSourceVerified","eligibleWorkloadVerified","populationCoverageVerified","httpAttemptInventoryVerified","agreementVerified"):
                    self.assertIs(report[name],False)
                self.assertEqual(cli.main(args),1)

    def test_bad_plan_future_window_and_source_drift_fail_without_usable_raw_output(self):
        root, plan, args = self.cli_inputs()
        with patch.object(cli,"ROOT",root), patch.object(cli,"source_identity",return_value=self.identity), patch.dict(os.environ,{"AGAT_ADMIN_TOKEN":TOKEN}), patch.object(cli,"capture") as capture:
            plan.write_text(plan.read_text()+" "); self.assertEqual(cli.main(args),1); capture.assert_not_called()
        self.assertFalse((self.output/"cohort.http.json").exists())
        args[-1] = str(self.output.parent/"future")
        future = copy.deepcopy(self.fixture.plan); future["config"]["window"] = {"startAt":"2099-01-01T00:00:00.000Z","endAt":"2099-01-02T00:00:00.000Z"}
        from decision_runtime.contracts import fingerprint
        future["configSha256"]=fingerprint(future["config"]); future=sealed({k:v for k,v in future.items() if k!="sha256"}); plan.write_text(json.dumps(future))
        args[3]=hashlib.sha256(plan.read_bytes()).hexdigest()
        with patch.object(cli,"ROOT",root), patch.object(cli,"source_identity",return_value=self.identity), patch.object(cli,"capture") as capture:
            self.assertEqual(cli.main(args),1); capture.assert_not_called()
        args[-1]=str(self.output.parent/"drift"); plan.write_text(json.dumps(self.fixture.plan)); args[3]=hashlib.sha256(plan.read_bytes()).hexdigest()
        with patch.object(cli,"ROOT",root), patch.object(cli,"source_identity",side_effect=[self.identity,("c"*40,{})]), patch.dict(os.environ,{"AGAT_ADMIN_TOKEN":TOKEN}), patch.object(cli,"capture",return_value=(self.body,{})):
            self.assertEqual(cli.main(args),1)
        self.assertFalse((Path(args[-1])/"cohort.http.json").exists())


if __name__ == "__main__": unittest.main()
