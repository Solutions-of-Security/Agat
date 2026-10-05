import plistlib
import tempfile
import unittest
from pathlib import Path

from scripts.lib.decision_service import launch_agent, write_launch_agent


class ServiceConfigTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agat service & ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'decision_runtime').mkdir()
        (self.root/'decision_runtime/__main__.py').touch()
        interpreter = self.root/'base-python';interpreter.touch();interpreter.chmod(0o700)
        (self.root/'venv').mkdir();(self.root/'venv/python').symlink_to(interpreter)
        for name in ('manifest.json','policy.json','calibration.json'):
            (self.root/name).write_text('{}')
        self.args = dict(root=self.root, python='venv/python', manifest='manifest.json',
                         policy='policy.json', log_dir=self.root)

    def test_paths_preserve_virtualenv_and_xml_round_trip_without_shell_expansion(self):
        config = launch_agent(**self.args, calibration='calibration.json')
        arguments = config['ProgramArguments']
        self.assertEqual(arguments[0],str(self.root/'venv/python'))
        self.assertNotEqual(arguments[0],str((self.root/'venv/python').resolve()))
        self.assertEqual(arguments[arguments.index('--manifest')+1],str(self.root/'manifest.json'))
        self.assertEqual(arguments[-2:],['--calibration',str(self.root/'calibration.json')])
        output = self.root/'service.plist';write_launch_agent(output,config)
        self.assertEqual(plistlib.loads(output.read_bytes()),config)
        self.assertNotIn('sh',arguments);self.assertNotIn('bash',arguments)
        with self.assertRaises(FileExistsError): write_launch_agent(output,config)

    def test_failed_process_restart_is_throttled_and_inference_is_isolated(self):
        config = launch_agent(**self.args)
        self.assertEqual(config['KeepAlive'],{'SuccessfulExit':False})
        self.assertEqual(config['ThrottleInterval'],30)
        self.assertEqual(config['ExitTimeOut'],30)
        self.assertTrue(config['RunAtLoad'])
        self.assertIn('--exit-on-backend-unavailable',config['ProgramArguments'])
        self.assertIn('--inference-timeout-ms',config['ProgramArguments'])
        self.assertEqual(config['WorkingDirectory'],str(self.root))

    def test_opt_in_wired_budget_and_zero_survive_plist_round_trip(self):
        self.assertNotIn('--wired-limit-mib', launch_agent(**self.args)['ProgramArguments'])
        for budget in (0, 4096, 65536):
            with self.subTest(budget=budget):
                config = plistlib.loads(plistlib.dumps(launch_agent(**self.args, wired_limit_mib=budget)))
                args = config['ProgramArguments']
                self.assertEqual(args[args.index('--wired-limit-mib') + 1], str(budget))

    def test_invalid_paths_and_limits_are_rejected_before_output(self):
        for overrides in ({'python':'missing'}, {'manifest':'missing'}, {'policy':'missing'},
                          {'calibration':'missing'}, {'log_dir':'missing'}, {'root':self.root/'missing'},
                          {'port':0}, {'port':True}, {'port':65536}, {'max_tokens':4097},
                          {'cache_limit_mib':-1}, {'inference_timeout_ms':99},
                          {'wired_limit_mib':-1}, {'wired_limit_mib':65537}, {'wired_limit_mib':True},
                          {'wired_limit_mib':0.5},
                          {'inference_timeout_ms':10001}, {'throttle_s':0}, {'throttle_s':9},
                          {'label':'bad/label'}, {'label':'org.agat\nshadow'}):
            with self.subTest(overrides=overrides),self.assertRaises(ValueError):
                launch_agent(**{**self.args,**overrides})


if __name__=='__main__': unittest.main()
