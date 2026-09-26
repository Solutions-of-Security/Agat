import runpy
import threading
import unittest
from pathlib import Path

from decision_runtime.contracts import Request
from decision_runtime.engine import DecisionEngine
from decision_runtime.server import make_server
from decision_runtime.tests.test_decisions import Backend, request

OwnedRuntime = runpy.run_path(str(Path(__file__).resolve().parents[1]/'check-decision-service-recovery.py'))['OwnedRuntime']


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
