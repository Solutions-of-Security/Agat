import contextlib
import io
import json
import multiprocessing
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from decision_runtime.__main__ import main
from decision_runtime.tests.test_calibration import FixtureBackend, criteria, dataset


def calibration_factory(config):
    backend = FixtureBackend()
    backend.identity = {**backend.identity, "maxInputTokens": config["max_tokens"]}
    return backend


class IsolatedCliTest(unittest.TestCase):
    def test_evaluate_fit_score_and_serve_share_identity_and_reject_changed_deadline(self):
        before = {p.pid for p in multiprocessing.active_children()}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {name: root / (name + ".json") for name in ("data", "manifest", "criteria", "profile", "plan", "scores", "fit", "request", "holdout")}
            data = dataset()
            files["data"].write_text(json.dumps(data))
            model = FixtureBackend.identity
            files["manifest"].write_text(json.dumps({**model, "files": {"tokenizer.json": model["tokenizerSha256"]}}))
            files["criteria"].write_text(json.dumps(criteria()))
            files["request"].write_text(json.dumps(data["cases"][0]["request"]))

            def run(*args):
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(sys, "argv", ["decision_runtime", *map(str, args)]), \
                     patch("decision_runtime.isolated.mlx_factory", calibration_factory), \
                     contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = main()
                return code, stdout.getvalue(), stderr.getvalue()

            common = ["--manifest", files["manifest"], "--inference-timeout-ms", "1000"]
            self.assertEqual(run("profile", *common, "--output", files["profile"])[0], 0)
            self.assertEqual(run("freeze", "--dataset", files["data"], "--manifest", files["manifest"],
                                 "--criteria", files["criteria"], "--runtime-profile", files["profile"], "--output", files["plan"])[0], 0)
            self.assertEqual(json.loads(files["plan"].read_text())["schemaVersion"], "agat.decision.experiment.v2")
            self.assertEqual(run("evaluate", *common, "--dataset", files["data"], "--split", "calibration",
                                 "--plan", files["plan"], "--output", files["scores"])[0], 0)
            self.assertEqual(run("calibrate", "--dataset", files["data"], "--scores", files["scores"],
                                 "--plan", files["plan"], "--output", files["fit"])[0], 0)
            code, output, _ = run("score", *common, "--input", files["request"], "--calibration", files["fit"])
            self.assertEqual(code, 0)
            scored = json.loads(output)
            self.assertEqual(scored["calibration"]["status"], "fitted")
            self.assertEqual(scored["model"], json.loads(files["fit"].read_text())["model"])
            self.assertEqual(scored["model"]["inferenceExecution"]["deadlineMs"], 1000)
            self.assertEqual(run("evaluate", *common, "--dataset", files["data"], "--split", "holdout",
                                 "--plan", files["plan"], "--frozen-calibration", files["fit"],
                                 "--reverse-options", "--output", files["holdout"])[0], 0)
            holdout = json.loads(files["holdout"].read_text())
            self.assertTrue(all(row["result"]["calibration"]["status"] == "uncalibrated" for row in holdout["cases"]))
            engines = []
            class StopServer:
                server_port = 0
                def __enter__(self): return self
                def __exit__(self, *_args): pass
                def serve_forever(self): raise KeyboardInterrupt
            def server(engine, _port, **_options): engines.append(engine); return StopServer()
            with patch("decision_runtime.__main__.make_server", server):
                self.assertEqual(run("serve", *common, "--calibration", files["fit"])[0], 130)
            self.assertEqual(engines[0].backend.identity, scored["model"])
            self.assertFalse(engines[0].backend.is_available())
            code, _, error = run("score", "--manifest", files["manifest"], "--inference-timeout-ms", "1500",
                                 "--input", files["request"], "--calibration", files["fit"])
            self.assertEqual(code, 1)
            self.assertIn("different model", error)
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, before)


if __name__ == "__main__": unittest.main()
