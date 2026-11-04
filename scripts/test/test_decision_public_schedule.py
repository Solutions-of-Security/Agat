import copy
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts.lib import decision_arrival_rate as legacy
from scripts.lib import decision_public_schedule as schedule
from scripts.lib import decision_public_load as load
from scripts.lib import decision_public_primary as primary
from scripts.lib import decision_public_load_verification as verifier
from scripts.test import test_decision_public_primary as companion_tests
from scripts.test import test_decision_public_load_verification as fixtures
from scripts.test.test_decision_public_load import context_fixture

ROOT = Path(__file__).resolve().parents[2]


class Clock:
    def __init__(self): self.now = 100.
    def clock(self): return self.now
    def sleep(self, seconds): self.now += seconds


class PublicScheduleTest(unittest.TestCase):
    def test_two_segments_keep_all_global_offsets_and_shared_origin(self):
        for rate, count, interval in ((.25, 49, 4), (.5, 98, 2), (.25, 60, 4), (.5, 120, 2)):
            clock = Clock(); rows = []
            origin = schedule.drive_extended_arrivals(rate, count, 1, 100, lambda *row: rows.append(row),
                clock=clock.clock, sleep=clock.sleep, start=100.)
            self.assertEqual(origin, 100.)
            self.assertEqual([row[0] for row in rows], list(range(count)))
            for index, due, observed, reason in rows:
                self.assertAlmostEqual(due, index*interval)
                self.assertAlmostEqual(observed, due)
                self.assertIsNone(reason)
        with self.assertRaises(ValueError): legacy.validate_schedule(.25, 49, 1, 100)
        with self.assertRaises(ValueError): legacy.validate_schedule(.5, 98, 1, 100)

    def test_extended_limits_are_checked_before_dispatch(self):
        for values in ((True, 1, 1, 100), (.25, 61, 1, 100), (.5, 121, 1, 100),
                       (.25, False, 1, 100), (.5, 1, True, 100), (.5, 1, 1, True), (.1, 1, 1, 100)):
            dispatch = unittest.mock.Mock()
            with self.subTest(values=values), self.assertRaises(ValueError):
                schedule.drive_extended_arrivals(*values, dispatch)
            dispatch.assert_not_called()

    def test_boundary_lag_is_dropped_without_rebasing_next_arrival(self):
        clock = Clock(); rows = []; delayed = [False]
        def sleep(seconds):
            clock.sleep(seconds)
            if clock.now >= 220. and not delayed[0]: clock.sleep(.2); delayed[0] = True
        schedule.drive_extended_arrivals(.25, 49, 1, 100, lambda *row: rows.append(row),
            clock=clock.clock, sleep=sleep, start=100.)
        self.assertEqual(rows[30][0:2], (30, 120.))
        self.assertEqual(rows[30][3], "scheduler_lag")
        self.assertAlmostEqual(rows[31][1], 124.)
        self.assertIsNone(rows[31][3])
        self.assertEqual(len(rows), 49)

    def test_cancelled_extended_primary_keeps_both_segments_without_model_calls(self):
        with patch.object(primary.primary, "measure_primary") as model:
            value = primary.PrimaryInventory(lambda *_: None, time.monotonic(), 98, lambda: True, extended=True)
            value.start(); rows = value.finish()
            model.assert_not_called()
        self.assertEqual(len(rows), 98)
        self.assertTrue(all(row["reason"] == "cancelled" for row in rows))
        self.assertEqual(rows[-1]["scheduledMs"], 194000.)
        with self.assertRaises(ValueError): primary.PrimaryInventory(lambda *_: None, time.monotonic(), 98, lambda: True)

    def test_primary_slot_stays_occupied_across_the_segment_boundary(self):
        entered = threading.Event(); release = threading.Event(); clock = Clock(); calls = []
        def transport(*args):
            calls.append(args); entered.set(); self.assertTrue(release.wait(2)); return companion_tests.fixtures.response()
        def driver(rate, count, slots, lag, dispatch, **kwargs):
            def forward(index, due, observed, reason):
                dispatch(index, due, observed, "cancelled" if index < 59 else reason)
                if index == 59: self.assertTrue(entered.wait(2))
                if index == 60: release.set()
            schedule.drive_extended_arrivals(rate, count, slots, lag, forward,
                clock=clock.clock, sleep=clock.sleep, start=100.)
        value = primary.PrimaryInventory(transport, time.monotonic(), 61, lambda: False, driver=driver, extended=True)
        value.start(); rows = value.finish()
        self.assertEqual(len(calls), 1)
        self.assertEqual(rows[59]["status"], "returned")
        self.assertEqual(rows[60]["reason"], "client_capacity")
        self.assertEqual(rows[60]["scheduledMs"], 120000.)

    def test_decision_runner_preserves_cancelled_inventory_at_quarter_rate(self):
        context = context_fixture(); prospective = schedule.lower_schedule(context["proposedDiagnosticSchedule"], len(context["inputs"]))
        observed = load.run_inventory(None, context["inputs"], context["profile"], schedule=prospective, cancelled=lambda: True)
        self.assertEqual(observed["schemaVersion"], schedule.SCHEMAS[2])
        self.assertEqual(observed["ratePerSecond"], .25)
        self.assertEqual([row["scheduledMs"] for row in observed["rows"]], [i*4000 for i in range(len(context["inputs"]))])
        self.assertEqual(observed["summary"]["dropped"], {"cancelled": len(context["inputs"])})

    def test_schedule_adjustment_cannot_change_budget_inventory_or_primary_duration(self):
        context = context_fixture(); count = len(context["inputs"])
        plan = {"schemaVersion": schedule.SCHEMAS[0], "schedule": schedule.lower_schedule(context["proposedDiagnosticSchedule"], count),
                "scheduleAdjustment": schedule.adjustment(context["proposedDiagnosticSchedule"], count)}
        self.assertEqual(schedule.verify_plan(plan, context), count*2)
        for mutate in (lambda p: p["schedule"].update(retries=1), lambda p: p["schedule"].update(callerTimeoutMs=20000),
                       lambda p: p["scheduleAdjustment"].update(primaryScheduledCases=count),
                       lambda p: p["scheduleAdjustment"].update(originalScheduleSha256="0"*64),
                       lambda p: p["schedule"].update(ratePerSecond=.5), lambda p: p["schedule"].update(clientSlots=True)):
            changed = copy.deepcopy(plan); mutate(changed)
            with self.assertRaises(ValueError): schedule.verify_plan(changed, context)

    def test_reduced_flag_without_primary_fails_before_sources_or_processes(self):
        cli = companion_tests.cli
        args = ["--context-profile", "missing", "--context-profile-file-sha256", "0"*64, "--runtime-python", "missing",
                "--manifest", "missing", "--evidence-dir", "docs/private/never-created-quarter-fixture", "--decision-rate", "0.25"]
        with patch.object(cli.launcher, "frozen_sources") as sources, patch.object(cli.subprocess, "Popen") as process:
            self.assertEqual(cli.main(args), 1)
            sources.assert_not_called(); process.assert_not_called()

    def test_full_v3_git_replay_checks_primary_denominator_and_rehashed_corruption(self):
        context, plan, result = fixtures.fixture()
        names = set(plan["sourceFiles"]) | set(primary.SOURCE_PATHS) | set(schedule.SOURCE_PATHS)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in names:
                target = root/name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes((ROOT/name).read_bytes())
            subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "-c", "user.name=Synthetic fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "Synthetic quarter-rate sources"], cwd=root, check=True)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            context.update(sourceCommit=commit, sourceFiles={name: fixtures.digest((root/name).read_bytes()) for name in context["sourceFiles"]})
            count = len(plan["inputs"])
            pin = primary.primary.PRIMARY_SOURCES[2]
            config = {"model": primary.primary.MODEL, "manifestSha256": primary.primary.DIGEST, "blobCount": 3, "blobBytes": 123,
                      "release": json.loads((ROOT/pin).read_bytes()), "releaseFileSha256": fixtures.digest((ROOT/pin).read_bytes()),
                      "request": primary.primary.REQUEST, "requestSha256": fixtures.fingerprint(primary.primary.REQUEST),
                      "settings": primary.primary.SETTINGS, "ratePerSecond": .5, "clientSlots": 1, "timeoutSeconds": 30}
            plan.update(schemaVersion=schedule.SCHEMAS[0], sourceCommit=commit,
                        sourceFiles={name: fixtures.digest((root/name).read_bytes()) for name in names}, primaryCompanionStarted=True,
                        condition="primary_active", primary=config, schedule=schedule.lower_schedule(context["proposedDiagnosticSchedule"], count),
                        scheduleAdjustment=schedule.adjustment(context["proposedDiagnosticSchedule"], count))
            for i, row in enumerate(result["phase"]["rows"]):
                for key in ("scheduledMs", "dispatchMs", "startedMs", "finishedMs"): row[key] += i*2000
            primary_rows = [{"index": i, "scheduledMs": i*2000., "dispatchMs": i*2000.+1., "startedMs": i*2000.+2.,
                             "finishedMs": i*2000.+502., "wallMs": 500., "status": "returned", "response": companion_tests.fixtures.response()}
                            for i in range(count*2)]
            result["phase"].update(schemaVersion=schedule.SCHEMAS[2], ratePerSecond=.25, condition="primary_active",
                                   phaseOriginMonotonicMs=100000., primaryRows=primary_rows, elapsedMs=primary_rows[-1]["finishedMs"]+1)
            result.update(schemaVersion=schedule.SCHEMAS[1], primaryRows=primary_rows,
                          primaryWarmup={"status": "returned", "wallMs": 500., "response": companion_tests.fixtures.response()},
                          elapsedMs=result["phase"]["elapsedMs"]+100)
            for sample in result["samples"]: sample["primaryResidence"] = {"models": [{"digest": primary.primary.DIGEST, "context_length": 8192}]}
            result["samples"][-1]["elapsedMs"] = result["phase"]["elapsedMs"]+80
            directory = root/"docs/private/fixture"; directory.mkdir(parents=True)
            def write(p, r):
                pins = fixtures.write_fixture(directory, context, p, r)
                stored = json.loads((directory/"result.json").read_bytes())
                logs = {"primary.log": b"Synthetic primary log.\n", "primary-requests.jsonl": b"".join(json.dumps(row).encode()+b"\n" for row in r["primaryRows"])}
                for name, raw in logs.items(): (directory/name).write_bytes(raw); stored["logSha256"][name] = fixtures.digest(raw)
                raw = fixtures.encoded(fixtures.reseal(stored)); (directory/"result.json").write_bytes(raw); pins["result_sha"] = fixtures.digest(raw)
                return pins
            pins = write(plan, result)
            observed = verifier.verify(root, directory, directory/"context.json", **pins)
            self.assertEqual(observed["primarySummary"]["scheduled"], count*2)
            self.assertEqual(observed["primarySummary"]["httpOverlapPairs"], count)
            for mutate in (lambda p, r: r["primaryRows"].pop(), lambda p, r: r["phase"]["rows"][1].update(scheduledMs=2000),
                           lambda p, r: p["scheduleAdjustment"].update(primaryScheduledCases=count),
                           lambda p, r: p.update(schemaVersion=primary.SCHEMAS[0]),
                           lambda p, r: r["primaryRows"][-1].update(scheduledMs=120000)):
                p, r = copy.deepcopy(plan), copy.deepcopy(result); mutate(p, r)
                pins = write(p, r)
                with self.assertRaises(ValueError): verifier.verify(root, directory, directory/"context.json", **pins)


if __name__ == "__main__": unittest.main()
