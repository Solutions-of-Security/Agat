from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agat_worker import (CoordinatorClient, DECISION_CALLER_ACCOUNTING, LocalModelClient,
                         _execute_lease_body, worker_labels)
from telemetry import ExecutionMetrics, WorkerTelemetry


class CallerAccountingWorkerTest(unittest.TestCase):
    def test_environment_cannot_advertise_disabled_or_unknown_caller_accounting(self):
        config = SimpleNamespace(model_discovery="static", model_profiles=[], embedding_models=[], web_enabled=False, decision_url="")
        with patch.dict("os.environ", {"AGAT_WORKER_LABELS": "decisionShadow=forged,decisionCallerAccounting=forged,region=test"}):
            labels = worker_labels(config)
            self.assertNotIn("decisionShadow", labels)
            self.assertNotIn("decisionCallerAccounting", labels)
            config.decision_url = "http://127.0.0.1:8766"
            self.assertEqual(worker_labels(config)["decisionCallerAccounting"], DECISION_CALLER_ACCOUNTING)
            self.assertEqual(worker_labels(config)["region"], "test")

    def test_intent_receipt_requires_exact_binding_and_boolean_permission(self):
        client = CoordinatorClient("http://unused.invalid")
        expected = {"schemaVersion": DECISION_CALLER_ACCOUNTING, "assignmentId": "assignment", "mayInvoke": True}
        for value in (expected, {**expected, "mayInvoke": False}):
            with patch.object(client, "request", return_value=value) as request:
                self.assertIs(client.begin_decision_shadow("lease", "assignment"), value["mayInvoke"])
                self.assertEqual(request.call_args.kwargs["timeout"], 5)
                self.assertEqual(request.call_args.args[1], "/api/v1/leases/lease/decision-shadow/intent")
        for value in ({**expected, "mayInvoke": 1}, {**expected, "assignmentId": "other"},
                      {**expected, "schemaVersion": "v2"}, {**expected, "extra": True}, []):
            with self.subTest(value=value), patch.object(client, "request", return_value=value):
                with self.assertRaises(ValueError): client.begin_decision_shadow("lease", "assignment")

    def test_primary_survives_intent_faults_and_duplicate_permission_without_an_extra_call(self):
        for variant in ("current", "intent_error", "duplicate", "record_error", "legacy", "unknown", "dry_run", "disabled"):
            with self.subTest(variant=variant):
                steps = []
                class Client:
                    outputs = []; failures = []
                    def event(self, *_args, **_kwargs): pass
                    def renew(self, _lease, **_kwargs): steps.append("renew")
                    def begin_decision_shadow(self, _lease, _assignment):
                        steps.append("intent")
                        if variant == "intent_error": raise RuntimeError("private")
                        return variant != "duplicate"
                    def record_decision_shadow(self, _lease, observation):
                        steps.append("record")
                        if variant == "record_error": raise RuntimeError("private")
                    def complete(self, _lease, output, **_kwargs): self.outputs.append(output)
                    def fail(self, *_args): self.failures.append(_args)
                class Decision:
                    def decide(self, *_args): steps.append("decide"); return {"status": "unavailable", "reason": "timeout"}
                client = Client(); model = LocalModelClient("http://unused.invalid", "")
                model.retrieve_knowledge = lambda *_args, **_kwargs: ""
                model.complete = lambda *_args, **_kwargs: "PRIMARY"
                model.decision_client = None if variant == "disabled" else Decision()
                shadow = {"profile": "local_decision_shadow_v1", "assignmentId": "assignment"}
                if variant != "legacy": shadow["callerAccountingVersion"] = "v2" if variant == "unknown" else DECISION_CALLER_ACCOUNTING
                lease = {"leaseId": "lease", "run": {"name": "Test", "input": "private"}, "stage": {"attempt": 1},
                         "agent": {"name": "Test", "model": "local"}, "decisionShadow": shadow}
                _execute_lease_body(client, model, lease, "local", variant == "dry_run",
                                    ExecutionMetrics.start("local", "none"), WorkerTelemetry(enabled=False))
                self.assertEqual(len(client.outputs), 1); self.assertEqual(client.failures, [])
                if variant != "dry_run": self.assertEqual(client.outputs, ["PRIMARY"])
                self.assertEqual(steps.count("decide"), int(variant in ("current", "record_error", "legacy", "unknown")))
                self.assertEqual(steps.count("intent"), int(variant not in ("legacy", "unknown", "dry_run", "disabled")))
                if variant == "current": self.assertEqual(steps, ["renew", "intent", "decide", "record"])
