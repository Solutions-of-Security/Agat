import runpy
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from decision_runtime.contracts import Request
from decision_runtime.engine import DecisionEngine
from decision_runtime.server import make_server
from decision_runtime.tests.test_decisions import Backend, request

recovery = runpy.run_path(str(Path(__file__).resolve().parents[1]/'check-decision-service-recovery.py'))
OwnedRuntime, child_processes = (recovery[name] for name in ('OwnedRuntime', 'child_processes'))


class ChildProcessInspectionTest(unittest.TestCase):
    metadata = '202 101 python -c from multiprocessing.spawn import spawn_main; spawn_main()\n203 101 python -c import multiprocessing.resource_tracker\n'

    def test_delayed_metadata_uses_explicit_budget_and_default_still_times_out(self):
        run = subprocess.run
        def delayed(arguments, **kwargs):
            return run([sys.executable, '-c',
                'import time; time.sleep(2.25); print('+repr(self.metadata)+', end="")'],
                stdout=subprocess.PIPE, check=True, **kwargs).stdout
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '202 203\n')), \
             patch('subprocess.check_output', side_effect=delayed):
            with self.assertRaises(subprocess.TimeoutExpired): child_processes(101)
            rows = child_processes(101, timeout_s=10)
        self.assertEqual(rows, [{'pid':202, 'parentPid':101, 'role':'inference'},
                                {'pid':203, 'parentPid':101, 'role':'resource_tracker'}])

    def test_both_commands_share_the_selected_per_command_budget(self):
        for config, expected in (({}, 2), ({'timeout_s':10}, 10), ({'timeout_s':30}, 30)):
            with self.subTest(config=config), \
                 patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '202 203\n')) as pgrep, \
                 patch('subprocess.check_output', return_value=self.metadata) as ps:
                child_processes(101, **config)
                self.assertEqual(pgrep.call_args.kwargs['timeout'], expected)
                self.assertEqual(ps.call_args.kwargs['timeout'], expected)

    def test_invalid_budget_rejected_before_any_process_query(self):
        with patch('subprocess.run') as pgrep, patch('subprocess.check_output') as ps:
            for value in (None, True, False, 0, -1, 31, 2.5, float('nan'), '10'):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    child_processes(101, timeout_s=value)
            pgrep.assert_not_called(); ps.assert_not_called()

    def test_metadata_timeout_or_failed_query_never_becomes_empty_inventory(self):
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('pgrep',10)), \
             patch('subprocess.check_output') as ps:
            with self.assertRaises(subprocess.TimeoutExpired): child_processes(101, timeout_s=10)
            ps.assert_not_called()
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '202\n')), \
             patch('subprocess.check_output', side_effect=subprocess.TimeoutExpired('ps',10)):
            with self.assertRaises(subprocess.TimeoutExpired): child_processes(101, timeout_s=10)
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 2, '')), \
             patch('subprocess.check_output') as ps:
            with self.assertRaisesRegex(RuntimeError, 'Cannot inspect'): child_processes(101, timeout_s=10)
            ps.assert_not_called()

    def test_explicit_budget_keeps_child_ownership_and_role_guards(self):
        for metadata, message in (('202 999 python multiprocessing.spawn spawn_main', 'parent changed'),
                                  ('202 101 unrelated-service', 'Unexpected child')):
            with self.subTest(metadata=metadata), \
                 patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '202\n')), \
                 patch('subprocess.check_output', return_value=metadata):
                with self.assertRaisesRegex(RuntimeError, message): child_processes(101, timeout_s=10)


class RecoveryTransportTest(unittest.TestCase):
    def test_probe_sends_utf8_and_validates_full_bound_response(self):
        engine = DecisionEngine(Backend())
        with make_server(engine,0) as server:
            thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                probe = OwnedRuntime.__new__(OwnedRuntime)
                probe.port, probe.profile = server.server_port, engine.profile()
                raw = Request.from_dict(request())
                observation = probe.score(raw)
                self.assertEqual(observation['httpStatus'],200)
                self.assertTrue(observation['completeResponse'])
                self.assertEqual(observation['result']['inputSha256'],raw.input_sha256)
                self.assertEqual(engine.backend.calls,1)
            finally: server.shutdown();thread.join(timeout=2)


if __name__=='__main__': unittest.main()
