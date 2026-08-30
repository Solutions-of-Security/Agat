import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any

from agat_worker import CoordinatorClient


class _BrokerHandler(BaseHTTPRequestHandler):
    received: dict[str, Any] = {}
    redirect = False

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract.
        if self.redirect:
            self.send_response(302)
            self.send_header("Location", "/redirected")
            self.end_headers()
            return
        length = int(self.headers.get("content-length", "0"))
        type(self).received = {
            "authorization": self.headers.get("authorization"),
            "payload": json.loads(self.rfile.read(length).decode("utf-8")),
        }
        body = json.dumps(
            {
                "statement": {"schemaVersion": 1},
                "keyId": "runtime-key",
                "signature": "c2lnbmF0dXJl",
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


class WorkerRuntimeAttestationBrokerTest(unittest.TestCase):
    def setUp(self) -> None:
        _BrokerHandler.redirect = False
        _BrokerHandler.received = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _BrokerHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.config = SimpleNamespace(
            runtime_attestation_broker_url=(
                f"http://127.0.0.1:{self.server.server_port}/attest"
            ),
            runtime_attestation_broker_token="scoped-token",
            runtime_attestation_timeout=2.0,
        )

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_posts_exact_challenge_binding_with_scoped_token(self) -> None:
        challenge = {
            "challenge": "nonce",
            "challengeSha256": "a" * 64,
            "expiresAt": "2030-01-01T00:00:00.000Z",
            "binding": {"releaseId": "worker-1.8.0"},
        }
        evidence = CoordinatorClient("http://coordinator.invalid")._runtime_attestation(
            self.config, challenge
        )
        self.assertEqual(evidence["keyId"], "runtime-key")
        self.assertEqual(_BrokerHandler.received["authorization"], "Bearer scoped-token")
        self.assertEqual(
            _BrokerHandler.received["payload"],
            {"schemaVersion": 1, **challenge},
        )

    def test_refuses_attestation_broker_redirects(self) -> None:
        _BrokerHandler.redirect = True
        with self.assertRaisesRegex(RuntimeError, "HTTP 302"):
            CoordinatorClient("http://coordinator.invalid")._runtime_attestation(
                self.config,
                {
                    "challenge": "nonce",
                    "challengeSha256": "a" * 64,
                    "expiresAt": "2030-01-01T00:00:00.000Z",
                    "binding": {},
                },
            )


if __name__ == "__main__":
    unittest.main()
