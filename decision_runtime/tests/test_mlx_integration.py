"""Optional real GPU check, explicitly enabled with AGAT_DECISION_TEST_MANIFEST."""

import json
import http.client
import os
import threading
import unittest
from pathlib import Path

from decision_runtime.contracts import Request


@unittest.skipUnless(os.getenv("AGAT_DECISION_TEST_MANIFEST"), "optional local MLX model is not configured")
class MlxIntegrationTest(unittest.TestCase):
    @unittest.skipUnless(os.getenv("AGAT_DECISION_TEST_CALIBRATION"), "optional fitted calibration is not configured")
    def test_calibrated_http_and_out_of_scope_error(self):
        from decision_runtime.calibration import Calibration
        from decision_runtime.engine import DecisionEngine
        from decision_runtime.mlx_backend import MlxBackend
        from decision_runtime.server import make_server

        backend = MlxBackend(Path(os.environ["AGAT_DECISION_TEST_MANIFEST"]))
        calibration = Calibration(json.loads(Path(os.environ["AGAT_DECISION_TEST_CALIBRATION"]).read_text()))
        source = Path(__file__).resolve().parents[2] / "docs/qualification/local-decisions/request.example.json"
        request = json.loads(source.read_text())
        expected = DecisionEngine(backend, calibration=calibration).decide(request)
        server = make_server(DecisionEngine(backend, calibration=calibration), 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        replies = []
        try:
            for payload, status in ((request, 200), ({**request, "question": "Different schema"}, 422)):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
                try:
                    connection.request("POST", "/v1/decisions", body=json.dumps(payload),
                                       headers={"Content-Type": "application/json"})
                    response = connection.getresponse()
                    result = json.loads(response.read())
                    self.assertEqual(response.status, status)
                    replies.append(result)
                finally:
                    connection.close()
            self.assertEqual(replies[0]["calibration"], expected["calibration"])
            self.assertEqual(replies[0]["distribution"], expected["distribution"])
            self.assertEqual(replies[1]["reason"], "calibration_out_of_scope")
            self.assertIsNone(replies[1]["value"])
            output = os.getenv("AGAT_DECISION_TEST_EVIDENCE")
            if output:
                path = Path(output)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.with_name(path.stem + "-calibrated-http.json").open("x", encoding="utf-8") as stream:
                    json.dump({"status": "pass", "responses": replies}, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_real_http_inference(self):
        from decision_runtime.engine import DecisionEngine
        from decision_runtime.mlx_backend import MlxBackend
        from decision_runtime.server import make_server

        backend = MlxBackend(Path(os.environ["AGAT_DECISION_TEST_MANIFEST"]))
        source = Path(__file__).resolve().parents[2] / "docs/qualification/local-decisions/request.example.json"
        request = json.loads(source.read_text())
        server = make_server(DecisionEngine(backend), 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
        try:
            connection.request("POST", "/v1/decisions", body=json.dumps(request),
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            result = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertIn(result["status"], ("ok", "abstain"))
            self.assertEqual(result["inputSha256"], Request.from_dict(request).input_sha256)
            self.assertEqual(result["generatedTokens"], 0)
            output = os.getenv("AGAT_DECISION_TEST_EVIDENCE")
            if output:
                path = Path(output)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.with_name(path.stem + "-http.json").open("x", encoding="utf-8") as stream:
                    json.dump(result, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_selected_head_matches_full_model_and_repeated_requests_are_isolated(self):
        from decision_runtime.mlx_backend import MlxBackend, encode_request

        backend = MlxBackend(Path(os.environ["AGAT_DECISION_TEST_MANIFEST"]))
        dataset = json.loads((Path(__file__).resolve().parents[2] /
                              "docs/qualification/local-decisions/development.v1.json").read_text())
        rows = []
        for index in (0, 12, 24):
            request = Request.from_dict(dataset["cases"][index]["request"])
            direct = backend.score(request).logits
            ids, labels = encode_request(backend.tokenizer, request, backend.max_tokens)
            full = backend.model(backend.mx.array([ids]))[0, -1, backend.mx.array(labels)]
            backend.mx.eval(full)
            reference = full.astype(backend.mx.float32).tolist()
            difference = max(abs(a - b) for a, b in zip(direct, reference))
            # Different GEMM shapes can round BF16 differently (one ULP around these logits).
            self.assertLessEqual(difference, 0.125)
            self.assertEqual(max(range(len(direct)), key=direct.__getitem__),
                             max(range(len(reference)), key=reference.__getitem__))
            rows.append({"id": request.id, "maxAbsoluteLogitDifference": difference,
                         "directLogits": direct, "fullModelLogits": reference})
        original = Request.from_dict(dataset["cases"][0]["request"])
        self.assertEqual(backend.score(original).logits, rows[0]["directLogits"])
        output = os.getenv("AGAT_DECISION_TEST_EVIDENCE")
        if output:
            path = Path(output)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as stream:
                json.dump({"status": "pass", "model": backend.identity, "checks": rows,
                           "repeatAfterOtherRequests": "identical"}, stream, ensure_ascii=False, indent=2)
                stream.write("\n")


if __name__ == "__main__":
    unittest.main()
