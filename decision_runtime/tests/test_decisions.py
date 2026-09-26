import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from decision_runtime.contracts import DecisionError, Policy, Request, SCORE_FINGERPRINT_VERSION, parse_json
from decision_runtime.engine import DecisionEngine, Scores
from decision_runtime.evaluation import evaluate, load_dataset
from decision_runtime.mlx_backend import MlxBackend, encode_request
from decision_runtime.model_store import sha256_file, verify_manifest
from decision_runtime.contracts import fingerprint


def request():
    return {"schemaVersion": "agat.decision.v1", "id": "example", "state": "Источник: запуск перенесён.",
            "question": "Подтверждён ли запуск?", "kind": "choice", "options": [
                {"id": "yes", "description": "Да"},
                {"id": "no", "description": "Нет"},
                {"id": "unknown", "description": "Недостаточно данных", "abstain": True}]}


class Backend:
    identity = {"repository": "test-only", "revision": "fixture"}

    def __init__(self, logits=None):
        self.logits = logits if logits is not None else [8.0, 0.0, -1.0]
        self.calls = 0

    def score(self, _request):
        self.calls += 1
        return Scores(self.logits, 20)


class ContractTest(unittest.TestCase):
    def test_bad_inputs_never_reach_model(self):
        mutations = [
            lambda d: d.update(kind="free_text"),
            lambda d: d.update(schemaVersion=1),
            lambda d: d.update(state=""),
            lambda d: d.update(state="x" * 24001),
            lambda d: d.update(state="\ud800"),
            lambda d: d.update(options=[]),
            lambda d: d.update(options=d["options"] * 4),
            lambda d: d.update(policy={"minProbability": 0}),
            lambda d: d["options"][1].update(id="yes"),
            lambda d: d["options"][1].update(description="Да"),
            lambda d: d["options"][0].update(abstain="false"),
            lambda d: d["options"][0].update(value=0.9),
            lambda d: d.pop("question"),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                backend = Backend()
                raw = request()
                mutate(raw)
                result = DecisionEngine(backend).decide(raw)
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["reason"], "invalid_request")
                self.assertEqual(backend.calls, 0)

    def test_duplicate_keys_nonfinite_and_invalid_json_rejected(self):
        for raw in ('{"a": 1, "a": 2}', '{"a": NaN}', '{"a": Infinity}', '{"a": 1e999}', '{', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(DecisionError):
                parse_json(raw)

    def test_content_fingerprint_ignores_request_id_but_binds_options_and_order(self):
        raw = request()
        original = Request.from_dict(raw).input_sha256
        raw["id"] = "new-id"
        self.assertEqual(Request.from_dict(raw).input_sha256, original)
        raw["options"].reverse()
        self.assertNotEqual(Request.from_dict(raw).input_sha256, original)
        raw["options"].reverse()
        raw["options"][0]["description"] = "Иное описание"
        self.assertNotEqual(Request.from_dict(raw).input_sha256, original)

    def test_policy_rejects_nonfinite_values_and_unknown_controls(self):
        for value in (-0.1, 1.1, float("nan"), True):
            with self.assertRaises(DecisionError):
                Policy.from_dict({"id": "policy", "minProbability": value, "minMargin": 0.1})

    def test_versioned_score_fingerprint_preserves_legacy_and_normalizes_transport(self):
        raw = request()
        raw.update(kind="score", options=[
            {"id": "low", "description": "Низкий", "value": -0.0},
            {"id": "high", "description": "Высокий", "value": 1e-7}])
        legacy = Request.from_dict(raw)
        legacy_hash = fingerprint({k: v for k, v in legacy.to_dict().items() if k != "id"})
        self.assertEqual(legacy.input_sha256, legacy_hash)
        self.assertNotIn("inputFingerprintVersion", legacy.to_dict())
        raw["inputFingerprintVersion"] = SCORE_FINGERPRINT_VERSION
        portable = Request.from_dict(raw)
        self.assertNotEqual(portable.input_sha256, legacy.input_sha256)
        self.assertNotEqual(portable.schema_sha256, legacy.schema_sha256)
        self.assertEqual(math.copysign(1, portable.options[0].value), 1)
        canonical = portable._fingerprint_dict()
        self.assertEqual(canonical["options"][0]["value"], {"float64be": "0000000000000000"})
        self.assertEqual(canonical["options"][1]["value"], {"float64be": "3e7ad7f29abcaf48"})
        raw["options"][0]["value"] = 0
        raw["options"][1]["value"] = 0.0000001
        self.assertEqual(Request.from_dict(raw).input_sha256, portable.input_sha256)
        raw["options"].reverse()
        reversed_request = Request.from_dict(raw)
        self.assertNotEqual(reversed_request.input_sha256, portable.input_sha256)
        self.assertEqual(reversed_request.schema_sha256, portable.schema_sha256)

    def test_invalid_fingerprint_version_never_reaches_model(self):
        for kind, version in [("choice", SCORE_FINGERPRINT_VERSION), ("choice", None), ("score", "python-json-v1"), ("score", "unknown")]:
            raw = request(); raw.update(inputFingerprintVersion=version, kind=kind)
            backend = Backend()
            result = DecisionEngine(backend).decide(raw)
            self.assertEqual(result["reason"], "invalid_request")
            self.assertEqual(backend.calls, 0)


class DecisionTest(unittest.TestCase):
    def test_direct_scores_produce_distribution_and_code_owned_value(self):
        result = DecisionEngine(Backend([10008.0, 10000.0, 9999.0])).decide(request())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["value"], "yes")
        self.assertEqual(result["generatedTokens"], 0)
        self.assertEqual(result["mode"], "shadow")
        self.assertEqual(result["calibration"]["status"], "uncalibrated")
        self.assertAlmostEqual(sum(x["probability"] for x in result["distribution"]), 1)
        self.assertNotIn("state", result)

    def test_low_probability_and_explicit_insufficient_data_are_distinct_abstentions(self):
        low = DecisionEngine(Backend([0, 0, 0])).decide(request())
        unknown = DecisionEngine(Backend([0, 0, 10])).decide(request())
        self.assertEqual((low["status"], low["reason"], low["value"]), ("abstain", "below_threshold", None))
        self.assertEqual((unknown["status"], unknown["reason"], unknown["value"]), ("abstain", "abstain_option", None))
        self.assertEqual(unknown["selectedOptionId"], "unknown")

    def test_margin_catches_near_tie_even_when_probability_threshold_is_low(self):
        result = DecisionEngine(Backend([1, 0.99, -20]), Policy("test", 0.4, 0.1)).decide(request())
        self.assertEqual(result["status"], "abstain")

    def test_bad_scores_are_errors_never_negative_or_empty_success(self):
        for logits in ([0, 0], [0, 0, float("nan")], [0, 0, float("inf")], [0, 0, True]):
            with self.subTest(logits=logits):
                result = DecisionEngine(Backend(logits)).decide(request())
                self.assertEqual((result["status"], result["reason"]), ("error", "invalid_scores"))
                self.assertIsNone(result["value"])
                self.assertEqual(result["distribution"], [])

    def test_backend_exception_does_not_leak_input(self):
        class Broken(Backend):
            def score(self, _request):
                raise RuntimeError("SECRET_DOCUMENT_CONTENT")

        result = DecisionEngine(Broken()).decide(request())
        self.assertEqual(result["reason"], "backend_error")
        self.assertNotIn("SECRET", json.dumps(result))

    def test_boolean_false_is_a_valid_value_not_an_error(self):
        raw = request()
        raw.update(kind="boolean", options=[
            {"id": "true", "description": "Да", "value": True},
            {"id": "false", "description": "Нет", "value": False}])
        result = DecisionEngine(Backend([0, 9])).decide(raw)
        self.assertEqual(result["status"], "ok")
        self.assertIs(result["value"], False)
        raw["options"][1]["value"] = 0
        self.assertEqual(DecisionEngine(Backend()).decide(raw)["status"], "error")

    def test_score_is_expected_value_over_explicit_numeric_levels(self):
        raw = request()
        raw.update(kind="score", options=[
            {"id": "low", "description": "Низкий", "value": 0},
            {"id": "high", "description": "Высокий", "value": 10}])
        result = DecisionEngine(Backend([0, math.log(3)]), Policy("test", 0.7, 0.1)).decide(raw)
        self.assertEqual(result["status"], "ok")
        self.assertAlmostEqual(result["value"], 7.5)


class TokenizerTest(unittest.TestCase):
    class Tokenizer:
        def encode(self, text, **_kwargs):
            return [ord(c) for c in text]

        def decode(self, ids):
            return "".join(chr(c) for c in ids)

    def test_invalid_resource_limits_fail_before_loading_or_importing_mlx(self):
        with patch("decision_runtime.mlx_backend.verify_manifest") as verify:
            for limit in (-1, 4097, True, 0.5, "128"):
                with self.subTest(limit=limit), self.assertRaisesRegex(ValueError, "cache_limit_mib"):
                    MlxBackend(Path("unused-manifest.json"), cache_limit_mib=limit)
            verify.assert_not_called()

    def test_no_truncation_and_single_token_continuations(self):
        parsed = Request.from_dict(request())
        ids, labels = encode_request(self.Tokenizer(), parsed, 4096)
        self.assertEqual(labels, [65, 66, 67])
        self.assertTrue(self.Tokenizer().decode(ids).endswith("Answer: ("))
        with self.assertRaisesRegex(DecisionError, "no silent truncation"):
            encode_request(self.Tokenizer(), parsed, 10)

    def test_multitoken_and_colliding_labels_are_rejected(self):
        class Bad(self.Tokenizer):
            def encode(self, text, **kwargs):
                return [65, 66] if text == "A" else super().encode(text, **kwargs)

        with self.assertRaises(DecisionError):
            encode_request(Bad(), Request.from_dict(request()), 4096)


class EvaluationTest(unittest.TestCase):
    def dataset(self):
        return {"schemaVersion": "agat.decision.dataset.v1", "id": "test-v1", "cases": [
            {"id": "example", "family": "evidence", "groupId": "doc-1", "split": "development",
             "labelSource": "test-fixture", "request": request(), "expectedOptionId": "yes"}]}

    def test_group_and_identical_state_cannot_cross_splits(self):
        for change_group in (False, True):
            dataset = self.dataset()
            other = copy.deepcopy(dataset["cases"][0])
            other.update(id="second", split="holdout")
            other["request"]["id"] = "second"
            if change_group:
                other["groupId"] = "new-group"
            dataset["cases"].append(other)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "data.json"
                path.write_text(json.dumps(dataset))
                with self.assertRaisesRegex(ValueError, "leaks"):
                    load_dataset(path)

    def test_errors_are_in_coverage_denominator_and_null_is_not_perfect_risk(self):
        data = self.dataset()
        report = evaluate(DecisionEngine(Backend([0, float("nan"), 0])), data, "development")
        self.assertEqual(report["metrics"]["backendErrors"], 1)
        self.assertEqual(report["metrics"]["accuracyAllAttempts"], 0)
        self.assertEqual(report["metrics"]["coverage"], 0)
        self.assertIsNone(report["metrics"]["accuracyScored"])
        self.assertIsNone(report["metrics"]["selectiveRisk"])

    def test_order_check_does_not_double_sample_count(self):
        report = evaluate(DecisionEngine(Backend()), self.dataset(), "development", reverse_options=True)
        self.assertEqual(report["metrics"]["attempted"], 1)
        self.assertEqual(report["optionOrder"]["scoredPairs"], 1)
        self.assertEqual(report["optionOrder"]["agreement"], 0)
        self.assertEqual(report["qualityGate"], "not_configured")

    def test_wrong_accepted_decision_is_visible_in_risk_and_per_class_metrics(self):
        report = evaluate(DecisionEngine(Backend([0, 10, 0])), self.dataset(), "development")
        self.assertEqual(report["metrics"]["acceptedErrors"], 1)
        self.assertEqual(report["metrics"]["selectiveRisk"], 1)
        self.assertEqual(report["metrics"]["classes"]["evidence:yes"]["fn"], 1)
        self.assertGreater(report["metrics"]["nllScored"], 9)

    def test_empty_selected_split_is_not_success(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            evaluate(DecisionEngine(Backend()), self.dataset(), "holdout")


class ModelManifestTest(unittest.TestCase):
    def test_modified_weights_are_detected_before_loading_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "snapshot"
            snapshot.mkdir()
            files = {}
            for name in ("config.json", "tokenizer.json", "tokenizer_config.json", "model.safetensors"):
                p = snapshot / name
                p.write_text("fixture")
                files[name] = sha256_file(p)
            manifest = {"schemaVersion": 1, "snapshot": str(snapshot), "files": files,
                        "artifactSha256": fingerprint(files)}
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps(manifest))
            verify_manifest(path)
            (snapshot / "model.safetensors").write_text("different")
            with self.assertRaisesRegex(ValueError, "checksum"):
                verify_manifest(path)


if __name__ == "__main__":
    unittest.main()
