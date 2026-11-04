"""Real signals at the worker's Event lock and in-flight admission boundary."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


PROGRAM = textwrap.dedent("""
    import os, signal, sys, threading
    from unittest.mock import Mock, patch
    import agat_worker
    mode, signum = sys.argv[1], getattr(signal, sys.argv[2])
    client = Mock()
    client.lease.return_value = None
    client.knowledge_lease.return_value = None
    events = []
    class InterruptedEvent(threading.Event):
        def __init__(self):
            super().__init__(); events.append(self); self.interrupted = False
        def wait(self, timeout=None):
            if self is events[0] and threading.current_thread() is threading.main_thread() and not self.interrupted:
                self.interrupted = True
                # A real handler can run while Event.wait owns its nonreentrant Condition.
                with self._cond:
                    print('signal-with-event-lock', flush=True)
                    os.kill(os.getpid(), signum)
            return super().wait(min(timeout or .01, .01))
    def lease():
        os.kill(os.getpid(), signum)
        return None
    if mode == 'admission': client.lease.side_effect = lease
    sys.argv = ['agat_worker.py', '--models', 'fixture', '--embedding-models', 'fixture',
                '--model-discovery', 'off', '--no-web', '--poll-interval', '30']
    config = agat_worker.parse_args()
    with patch.object(agat_worker, 'CoordinatorClient', return_value=client), \
         patch.object(agat_worker, 'LocalModelClient'), \
         patch.object(agat_worker, 'register_if_needed'), \
         patch.object(agat_worker, 'discover_model_profiles', return_value=config.model_profiles), \
         patch.object(agat_worker, 'heartbeat_loop'), \
         patch.object(agat_worker.threading, 'Event', InterruptedEvent):
        assert agat_worker._worker_loop_with_telemetry(config, Mock()) == 0
    client.lease.assert_called_once()
    if mode == 'admission': client.knowledge_lease.assert_not_called()
    else:
        assert events[0].interrupted
        client.knowledge_lease.assert_called_once()
    assert not [thread for thread in threading.enumerate() if thread is not threading.main_thread()]
    print('shutdown-complete', flush=True)
""")


@unittest.skipIf(os.name == "nt", "Requires POSIX process signals")
class WorkerSignalShutdownTests(unittest.TestCase):
    def run_case(self, mode, signum):
        # run kills and waits for an owned child if the old implementation deadlocks.
        result = subprocess.run([sys.executable, "-c", PROGRAM, mode, signum],
            cwd=Path(__file__).resolve().parent, capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn("shutdown-complete", result.stdout)

    def test_sigterm_while_poll_event_lock_is_held_does_not_deadlock(self):
        self.run_case("event", "SIGTERM")

    def test_sigint_while_poll_event_lock_is_held_does_not_deadlock(self):
        self.run_case("event", "SIGINT")

    def test_signal_during_empty_primary_poll_does_not_admit_embedding_work(self):
        self.run_case("admission", "SIGTERM")


if __name__ == "__main__": unittest.main()
