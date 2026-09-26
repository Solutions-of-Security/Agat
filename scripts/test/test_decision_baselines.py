import copy
import json
import unittest
from pathlib import Path

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import fingerprint
from scripts.lib.decision_baselines import (BaselineError, LoopbackJson, OllamaBaseline, compare_baselines,
                                           evaluate_generative, generative_rows)

ROOT = Path(__file__).resolve().parents[2]


def dataset():
    return sealed({"schemaVersion": "agat.decision.dataset.v1", "id": "baseline-test", "usage": "development-only",
                   "routingEnabled": False, "qualifiedForRouting": False, "cases": [{
                       "id": "one", "family": "clarification", "groupId": "group", "labelSource": "assistant-reviewed",
                       "split": "development", "expectedOptionId": "no", "annotationRationale": "GOLD NEVER SENT",
                       "request": {"schemaVersion": "agat.decision.v1", "id": "one", "state": "Input only",
                                   "question": "Question", "kind": "boolean", "options": [
                                       {"id": "no", "description": "No", "value": False},
                                       {"id": "yes", "description": "Yes", "value": True}]}}]})


class Transport:
    def __init__(self):
        self.calls = []
        self.digest = "1" * 64
        self.remote = False
        self.content = '{"selectedOptionId":"no"}'
        self.reason = "stop"

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/api/tags":
            return {"models": [{"name": "qwen3:8b", "digest": self.digest, **({"remote_host": "cloud"} if self.remote else {})}]}
        if path == "/api/show": return {"capabilities": ["completion"], "template": "test"}
        if path == "/api/version": return {"version": "test-only"}
        return {"model": "qwen3:8b", "done": True, "done_reason": self.reason,
                "message": {"role": "assistant", "content": self.content}, "prompt_eval_count": 30, "eval_count": 12}


class BaselineTest(unittest.TestCase):
    def test_real_adapter_protocol_contains_no_gold_and_preserves_false_and_order(self):
        transport = Transport(); backend = OllamaBaseline(transport)
        result = evaluate_generative(dataset(), backend)
        self.assertEqual(result["cases"][0]["original"]["value"], False)
        calls = [body for _, path, body in transport.calls if path == "/api/chat"]
        self.assertEqual(len(calls), 2)
        self.assertNotIn("GOLD NEVER SENT", json.dumps(calls))
        self.assertNotIn("expectedOptionId", json.dumps(calls))
        self.assertEqual(calls[0]["format"]["properties"]["selectedOptionId"]["enum"], ["no", "yes", None])
        self.assertEqual(calls[1]["format"]["properties"]["selectedOptionId"]["enum"], ["yes", "no", None])
        self.assertFalse(calls[0]["think"])
        self.assertEqual(calls[0]["options"]["temperature"], 0)
        self.assertEqual(len(generative_rows(dataset(), result)), 1)

    def test_truncation_duplicate_json_or_unknown_label_never_become_negative_boolean(self):
        for content, reason in [('garbage', 'stop'), ('{"selectedOptionId":"x"}', 'stop'),
                                ('{"selectedOptionId":"no","selectedOptionId":"yes"}', 'stop'),
                                ('{"selectedOptionId":"no"}', 'length'),
                                ('{"selectedOptionId":"no","confidence":1}', 'stop')]:
            with self.subTest(content=content, reason=reason):
                transport = Transport(); transport.content = content; transport.reason = reason
                report = evaluate_generative(dataset(), OllamaBaseline(transport))
                self.assertEqual(report["cases"][0]["original"]["status"], "error")
                summary = compare_baselines(dataset(), report, [])["models"][0]["original"]
                self.assertEqual((summary["correct"], summary["backendErrors"], summary["accepted"]), (0, 1, 0))

    def test_holdout_is_rejected_before_inference(self):
        data = dataset(); data.pop("sha256"); data["cases"][0]["split"] = "holdout"; data = sealed(data)
        transport = Transport(); backend = OllamaBaseline(transport)
        with self.assertRaises(ValueError): evaluate_generative(data, backend)
        self.assertEqual(sum(path == "/api/chat" for _, path, _ in transport.calls), 0)

    def test_cloud_models_and_changed_digest_are_rejected(self):
        transport = Transport(); transport.remote = True
        with self.assertRaises(ValueError): OllamaBaseline(transport)
        transport.remote = False
        with self.assertRaises(ValueError): OllamaBaseline(transport, expected_digest="2" * 64)
        backend = OllamaBaseline(transport); transport.digest = "2" * 64
        result = evaluate_generative(dataset(), backend)
        self.assertEqual(result["cases"][0]["original"]["reason"], "model_changed")
        self.assertEqual(sum(path == "/api/chat" for _, path, _ in transport.calls), 0)

    def test_report_binding_and_semantics_are_checked_after_resealing(self):
        data = dataset(); result = evaluate_generative(data, OllamaBaseline(Transport()))
        mutations = [lambda r: r["dataset"].update(sha256="0"*64),
                     lambda r: r["cases"][0].update(expectedOptionId="yes"),
                     lambda r: r["cases"][0]["original"].update(inputSha256="0"*64),
                     lambda r: r["cases"][0]["original"].update(value=True),
                     lambda r: r["cases"][0]["original"].update(value=0),
                     lambda r: r["cases"][0]["original"].update(outputTokens=1.5),
                     lambda r: r["cases"][0]["original"].update(confidence=0.99),
                     lambda r: r["cases"].append(copy.deepcopy(r["cases"][0]))]
        for mutation in mutations:
            changed = copy.deepcopy(result); changed.pop("sha256"); mutation(changed)
            with self.assertRaises(ValueError): generative_rows(data, sealed(changed))

    def test_transport_is_loopback_only(self):
        for url in ["https://model.example", "http://localhost:11434", "http://127.0.0.1:11434?x=1"]:
            with self.assertRaises(ValueError): LoopbackJson(url)

    def test_recorded_comparison_recomputes_labels_instead_of_trusting_cached_accuracy(self):
        base = ROOT / "docs/qualification/local-decisions"
        data = json.loads((base / "development/evidence/2026-09-26/dataset.json").read_text())
        generated = json.loads((base / "baselines/evidence/2026-09-26/qwen3-generative.json").read_text())
        logits = json.loads((base / "development/evidence/2026-09-26/decider-scores.json").read_text())
        logits["metrics"]["correct"] = 0
        for row in logits["cases"]: row["correct"] = False
        report = compare_baselines(data, generated, [logits])
        self.assertEqual(report["models"][0]["original"]["correct"], 9)
        self.assertEqual(report["models"][1]["original"]["correct"], 14)
        self.assertEqual(report["groups"], 2)
        self.assertFalse(report["qualifiedForRouting"])


if __name__ == "__main__": unittest.main()
