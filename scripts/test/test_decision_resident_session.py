import copy
import unittest
from unittest.mock import patch

from scripts.lib import decision_resident_session as session


class ResidentSessionTest(unittest.TestCase):
    def setUp(self):
        self.baseline = {"hostFingerprint": "a" * 64, "bootSessionUuid": "12345678-1234-1234-1234-123456789abc",
                         "bootTimeEpoch": 1791320000, "guiSessionId": 100023, "ownerUid": 501, "capturedEpoch": 1791321000.25}
        self.current = {**self.baseline, "capturedEpoch": 1791322000.25}
        self.processes = [{"role": role, "pid": 200 + i, "startedEpoch": 1791321002}
                          for i, role in enumerate(("runtime", "prometheus", "inference", "resource_tracker"))]

    def test_same_session_reinstall_never_satisfies_boot_or_login(self):
        for expected in ("boot", "login"):
            with self.subTest(expected=expected):
                result = session.observed_event(self.baseline, self.current, expected)
                self.assertEqual(result["status"], "awaiting_event")
                self.assertIsNone(result["event"])

    def test_new_gui_login_does_not_count_as_boot(self):
        self.current["guiSessionId"] += 1
        self.assertEqual(session.observed_event(self.baseline, self.current, "login")["status"], "event_observed")
        result = session.observed_event(self.baseline, self.current, "boot")
        self.assertEqual(result["status"], "awaiting_event")
        self.assertEqual(result["event"], "login")

    def test_adjusted_calendar_boottime_in_same_os_session_is_not_an_event(self):
        for delta in (-0.111569, 0.111569):
            current = {**self.current, "bootTimeEpoch": self.baseline["bootTimeEpoch"]+delta}
            for expected in ("boot", "login"):
                with self.subTest(delta=delta, expected=expected):
                    result = session.observed_event(self.baseline, current, expected)
                    self.assertEqual(result["status"], "awaiting_event")
                    self.assertIs(result["bootChanged"], False)
                    self.assertIs(result["guiChanged"], False)

    def test_boot_accepts_reused_numeric_session_and_pids_with_new_births(self):
        self.current.update(bootSessionUuid="22345678-1234-1234-1234-123456789abc", bootTimeEpoch=1791321001)
        for expected in ("boot", "login"):
            result = session.observed_event(self.baseline, self.current, expected)
            self.assertEqual(result["status"], "event_observed")
            self.assertTrue(result["guiChanged"])
        session.validate_process_starts(self.processes, self.current, after=self.baseline["capturedEpoch"])

    def test_changed_uuid_with_old_boot_time_is_rejected(self):
        self.current["bootSessionUuid"] = "22345678-1234-1234-1234-123456789abc"
        with self.assertRaisesRegex(ValueError, "chronology"):
            session.observed_event(self.baseline, self.current, "boot")

    def test_another_mac_or_owner_cannot_satisfy_acceptance(self):
        for field, value in (("hostFingerprint", "b" * 64), ("ownerUid", 502)):
            with self.subTest(field=field):
                current = {**self.current, field: value}
                with self.assertRaisesRegex(ValueError, "another Mac or user"):
                    session.observed_event(self.baseline, current, "login")

    def test_equal_or_earlier_observation_is_rejected(self):
        for value in (self.baseline["capturedEpoch"], self.baseline["capturedEpoch"] - 1):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "not after baseline"):
                    session.observed_event(self.baseline, {**self.current, "capturedEpoch": value}, "boot")

    def test_snapshot_rejects_scalar_aliases_unknown_keys_and_invalid_uuid(self):
        invalid = [("guiSessionId", True), ("guiSessionId", 100023.0), ("guiSessionId", 0),
                   ("guiSessionId", 2**32 - 1), ("ownerUid", False), ("ownerUid", "501"),
                   ("capturedEpoch", float("nan")), ("capturedEpoch", True),
                   ("bootSessionUuid", "00000000-0000-0000-0000-000000000000"),
                   ("bootSessionUuid", self.baseline["bootSessionUuid"].upper()),
                   ("unexpected", "value"), ("hostFingerprint", "unknown")]
        for field, value in invalid:
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    session.validate_snapshot({**self.baseline, field: value})

    def test_gui_domain_parser_rejects_foreign_missing_and_ambiguous_domains(self):
        for raw in ("domain = user/501", "domain = gui/502 [100023]", "domain = gui/501 [0]",
                    "domain = gui/501 [100023]\ndomain = gui/501 [100023]"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError): session.gui_session_id(raw, 501)
        self.assertEqual(session.gui_session_id("    domain = gui/501 [100023]\n", 501), 100023)

    def test_jobs_in_different_gui_sessions_reject_before_native_api(self):
        with patch.object(session, "native_os_values") as native:
            with self.assertRaisesRegex(ValueError, "different GUI"):
                session.session_snapshot(["domain = gui/501 [100023]", "domain = gui/501 [100024]"], 501)
            native.assert_not_called()

    def test_processes_reject_old_birth_future_birth_duplicate_pid_and_role(self):
        invalid = [("startedEpoch", self.baseline["capturedEpoch"] - 10),
                   ("startedEpoch", self.current["capturedEpoch"] + 10), ("pid", True),
                   ("pid", self.processes[1]["pid"]), ("role", "foreign"), ("extra", 1)]
        for field, value in invalid:
            with self.subTest(field=field, value=value):
                rows = copy.deepcopy(self.processes); rows[0][field] = value
                with self.assertRaises(ValueError):
                    session.validate_process_starts(rows, self.current, after=self.baseline["capturedEpoch"])

    def test_utc_process_start_keeps_environment_local_to_the_native_command(self):
        with patch.object(session.subprocess, "check_output", return_value="Tue Oct  6 21:14:05 2026\n") as call:
            self.assertEqual(session.process_start_epoch(123), 1791321245)
            self.assertEqual(call.call_args.kwargs["env"]["TZ"], "UTC")
            self.assertEqual(call.call_args.args[0], ["/bin/ps", "-p", "123", "-o", "lstart="])

    def test_invalid_pid_cannot_launch_a_native_process_query(self):
        with patch.object(session.subprocess, "check_output") as call:
            for pid in (False, 0, -1, "123", 123.0, 2**31):
                with self.subTest(pid=pid):
                    with self.assertRaises(ValueError): session.process_start_epoch(pid)
            call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
