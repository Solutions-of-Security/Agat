import http.client
import json
import threading
import unittest

from decision_runtime.engine import DecisionEngine
from decision_runtime.contracts import fingerprint, canonical_json
from decision_runtime.server import make_server
from decision_runtime.tests.test_decisions import Backend, request


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.server = make_server(DecisionEngine(self.backend), 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def call(self, body, path="/v1/decisions", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request("POST", path, body=body, headers={"Content-Type": "application/json", **(headers or {})})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_real_http_roundtrip_returns_typed_result(self):
        status, body = self.call(json.dumps(request()))
        self.assertEqual(status, 200)
        self.assertEqual(body["value"], "yes")

    def test_profile_mismatch_does_not_run_inference_and_health_exposes_exact_pin(self):
        engine = DecisionEngine(self.backend)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request("GET", "/health")
            health = json.loads(connection.getresponse().read())
        finally:
            connection.close()
        self.assertEqual(health["profileJson"], canonical_json(engine.profile()))
        self.assertEqual(health["profileSha256"], fingerprint(engine.profile()))
        status, result = self.call(json.dumps(request()), headers={"X-Agat-Decision-Profile": "0" * 64})
        self.assertEqual((status, result["reason"]), (409, "profile_mismatch"))
        self.assertEqual(self.backend.calls, 0)
        self.assertEqual(self.call(json.dumps(request()), headers={"X-Agat-Decision-Profile": health["profileSha256"]})[0], 200)

    def test_invalid_json_and_unknown_policy_cannot_be_negative_success(self):
        for raw in ("{", "null", '{"schemaVersion": 1}', '{"id":"a","id":"b"}'):
            status, body = self.call(raw)
            self.assertEqual(status, 400)
            self.assertEqual(body["status"], "error")
        raw = {**request(), "minProbability": 0}
        self.assertEqual(self.call(json.dumps(raw))[0], 400)
        self.assertEqual(self.backend.calls, 0)

    def test_web_origins_and_untrusted_hosts_cannot_access_inference(self):
        for headers in ({"Origin": "https://other.example"}, {"Host": "other.example"}):
            status, _ = self.call(json.dumps(request()), headers=headers)
            self.assertEqual(status, 403)
        self.assertEqual(self.backend.calls, 0)

    def test_body_and_media_type_limits_are_enforced_before_model(self):
        self.assertEqual(self.call("x" * (128 * 1024 + 1))[0], 413)
        self.assertEqual(self.call("{}", headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.backend.calls, 0)

    def test_concurrent_request_is_busy_not_shared_model_state(self):
        entered, release = threading.Event(), threading.Event()
        original = self.backend.score

        def score(parsed):
            entered.set()
            release.wait(timeout=2)
            return original(parsed)

        self.backend.score = score
        responses = []
        first = threading.Thread(target=lambda: responses.append(self.call(json.dumps(request()))))
        first.start()
        try:
            self.assertTrue(entered.wait(timeout=1))
            status, body = self.call(json.dumps(request()))
            self.assertEqual((status, body["reason"]), (503, "busy"))
        finally:
            release.set()
            first.join(timeout=3)
        self.assertEqual(responses[0][0], 200)
        self.assertEqual(self.backend.calls, 1)


if __name__ == "__main__":
    unittest.main()
