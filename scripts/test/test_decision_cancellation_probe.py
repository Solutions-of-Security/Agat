import importlib.util
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location('cancellation_workflow_probe',Path(__file__).resolve().parents[1]/'check-decision-cancellation-workflow.py')
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)


class RetirementProbeTest(unittest.TestCase):
    def test_unavailable_is_not_yet_reaped_and_wait_is_bounded(self):
        class Retiring:
            calls=0
            def diagnostics(self):
                self.calls+=1
                return {'available':False,'stopReason':'inference_cancelled','childExitCode':None if self.calls<3 else -15}
        result=probe.wait_for_retirement(Retiring(),timeout_s=.2)
        self.assertIsNone(result['initial']['childExitCode'])
        self.assertEqual(result['retired']['childExitCode'],-15)
        self.assertGreaterEqual(result['waitMs'],10)
        self.assertLess(result['waitMs'],200)
        class Stuck:
            def diagnostics(self):return {'available':False,'stopReason':'inference_cancelled','childExitCode':None}
        with self.assertRaisesRegex(RuntimeError,'not reaped'):probe.wait_for_retirement(Stuck(),timeout_s=.02)

    def test_other_failure_cannot_pass_as_cancellation(self):
        class Deadline:
            def diagnostics(self):return {'available':False,'stopReason':'inference_timeout','childExitCode':-15}
        with self.assertRaisesRegex(RuntimeError,'not cancelled'):probe.wait_for_retirement(Deadline())


if __name__=='__main__':unittest.main()
