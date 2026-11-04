import importlib.util
import hashlib
import json
import signal
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from decision_runtime.contracts import canonical_json, fingerprint


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('launchd_probe', ROOT/'scripts/check-decision-launchd.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class LaunchdProbeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agat-launchd-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root/'docs/private/probe'
        self.output = self.directory/'launchd-recovery.json'
        self.profile = json.loads((ROOT/'docs/qualification/local-decisions/performance/profiles/runtime-0.12.2.json').read_text())
        self.expected = self.root/'expected.json'
        self.expected.write_text(canonical_json(self.profile))
        self.args = ['--python', str(self.root/'python'), '--manifest', str(self.root/'manifest.json'),
                     '--policy', str(self.root/'policy.json'), '--request', str(ROOT/'docs/qualification/local-decisions/request.example.json'),
                     '--expected-profile', str(self.expected), '--inference-timeout-ms', '5000',
                     '--evidence-dir', str(self.directory), '--output', str(self.output)]
        self.children = [{'pid':112, 'parentPid':111, 'role':'inference'},
                         {'pid':113, 'parentPid':111, 'role':'resource_tracker'}]
        self.state = 'absent'
        self.scores = []
        self.signals = []
        self.health_error = False
        self.health_profile = self.profile
        self.second_profile = None
        self.cleanup_ok = True
        self.config = None

        owner = self
        class FakeRuntime:
            def call(self, method, path):
                if owner.health_error:
                    raise ConnectionRefusedError('fixture: startup has no HTTP listener')
                profile = owner.second_profile if owner.state == 'second' and owner.second_profile is not None else owner.health_profile
                return 200, {'status':'ready', 'mode':'shadow', 'profileJson':canonical_json(profile),
                             'profileSha256':fingerprint(profile)}

            def score(self, request):
                owner.scores.append(request.input_sha256)
                return {'httpStatus':200, 'result':{'status':'abstain', 'reason':'below_threshold',
                        'selectedOptionId':'fixture', 'value':None, 'distribution':[]}}

        def launchctl(*arguments):
            if arguments[0] == 'bootstrap':
                self.state = 'first'
                Path(self.config['StandardErrorPath']).write_text('fixture: retained startup diagnostics\n')
            elif arguments[0] == 'bootout':
                self.state = 'absent'
            return SimpleNamespace(returncode=0, stdout='', stderr='')

        def service_info(target, diagnostic_path=None):
            if self.state == 'absent': return None
            if self.state == 'failed':
                self.state = 'second'
                return {'runs':1, 'last exit code':75}
            return {'pid':111 if self.state == 'first' else 211,
                    'runs':1 if self.state == 'first' else 2}

        def children(pid):
            return self.children if pid == 111 else [
                {'pid':212, 'parentPid':211, 'role':'inference'},
                {'pid':213, 'parentPid':211, 'role':'resource_tracker'}]

        def kill(pid, sig):
            self.signals.append((pid, sig))
            self.state = 'failed'

        def config(**kwargs):
            self.config_kwargs = kwargs
            self.config = {'Label':kwargs['label'], 'ProgramArguments':['python', '--port', '8766'],
                           'StandardOutPath':str(kwargs['log_dir']/'runtime.out.log'),
                           'StandardErrorPath':str(kwargs['log_dir']/'runtime.err.log')}
            self.assertEqual(kwargs['inference_timeout_ms'], 5000)
            return self.config

        patches = [patch.object(probe, 'ROOT', self.root), patch.object(probe.platform, 'system', return_value='Darwin'),
                   patch.object(probe, 'launch_agent', side_effect=config),
                   patch.object(probe, 'launchctl', side_effect=launchctl),
                   patch.object(probe, 'service_info', side_effect=service_info),
                   patch.object(probe, 'child_processes', side_effect=children),
                   patch.object(probe, 'OwnedRuntime', FakeRuntime),
                   patch.object(probe.os, 'kill', side_effect=kill),
                   patch.object(probe, 'gone', side_effect=lambda pids, timeout: self.cleanup_ok),
                   patch.object(probe.subprocess, 'run', return_value=SimpleNamespace(returncode=0)),
                   patch.object(probe.time, 'sleep'),
                   patch.object(probe, 'harness_identity', return_value=('a'*40, {'fixture':'a'*64})),
                   patch.object(probe, 'harness_fingerprints', return_value={'fixture':'a'*64})]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        sockets = patch('socket.socket')
        reservation = sockets.start().return_value
        self.addCleanup(sockets.stop)
        reservation.__enter__.return_value = reservation
        reservation.getsockname.return_value = ('127.0.0.1', 38666)

    def test_exact_profile_restart_keeps_private_logs_and_cleans_owned_job(self):
        self.assertEqual(probe.main(self.args), 0)
        report = json.loads(self.output.read_text())
        self.assertEqual(report['status'], 'observed')
        self.assertTrue(all(report['checks'].values()))
        self.assertEqual(report['expectedProfileSha256'], fingerprint(self.profile))
        self.assertEqual(report['implementationCommit'], 'a'*40)
        self.assertEqual(report['ownedPids'], [111,112,113,211,212,213])
        self.assertEqual(len(self.scores), 2)
        self.assertEqual(self.signals, [(112, signal.SIGKILL)])
        self.assertEqual(self.state, 'absent')
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        for path in (self.output, Path(self.config['StandardErrorPath']), Path(self.config['StandardOutPath'])):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertIn('retained startup diagnostics', Path(self.config['StandardErrorPath']).read_text())
        for name, checksum in report['retainedFilesSha256'].items():
            self.assertEqual(hashlib.sha256((self.directory/name).read_bytes()).hexdigest(), checksum)

    def test_wrong_profile_stops_before_scoring_or_fault_injection(self):
        self.health_profile = {**self.profile, 'runtimeVersion':'0.12.1'}
        self.assertEqual(probe.main(self.args), 1)
        report = json.loads(self.output.read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertIn('expected profile', report['failure']['message'])
        self.assertFalse(report['checks']['expectedProfileMatched'])
        self.assertTrue(report['checks']['temporaryServiceRemoved'])
        self.assertTrue(report['checks']['allOwnedProcessesStopped'])
        self.assertEqual(self.scores, [])
        self.assertEqual(self.signals, [])
        self.assertEqual(report['ownedPids'], [111,112,113])

    def test_wired_profile_requires_matching_opt_in_and_reaches_service_recipe(self):
        self.profile['model']['allocatorWiredLimitBytes'] = 4096 * 1024 * 1024
        self.expected.write_text(canonical_json(self.profile))
        self.args += ['--wired-limit-mib', '4096']
        self.assertEqual(probe.main(self.args), 0)
        self.assertEqual(self.config_kwargs['wired_limit_mib'], 4096)
        report = json.loads(self.output.read_text())
        self.assertEqual(report['expectedProfileSha256'], fingerprint(self.profile))
        self.assertTrue(report['checks']['expectedProfileMatched'])

    def test_wired_profile_mismatch_rejects_before_launch_or_scoring(self):
        self.profile['model']['allocatorWiredLimitBytes'] = 4096 * 1024 * 1024
        self.expected.write_text(canonical_json(self.profile))
        self.assertEqual(probe.main(self.args), 1)
        self.assertEqual(self.scores, [])
        self.assertEqual(self.state, 'absent')
        self.assertIsNone(self.config)
        self.assertFalse(self.directory.exists())

    def test_startup_failure_retains_children_inventory_and_logs(self):
        self.health_error = True
        # Advance past the startup budget, then leave time for cleanup checks.
        tick = iter([0,50,100,150])
        with patch.object(probe.time, 'monotonic', side_effect=lambda: next(tick,200)):
            self.assertEqual(probe.main(self.args), 1)
        report = json.loads(self.output.read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['ownedPids'], [111,112,113])
        self.assertTrue(report['checks']['allOwnedProcessesStopped'])
        self.assertTrue(report['checks']['temporaryServiceRemoved'])
        self.assertEqual(self.state, 'absent')
        self.assertEqual(self.signals, [])
        self.assertTrue(Path(self.config['StandardErrorPath']).is_file())

    def test_failed_cleanup_cannot_be_reported_as_observed(self):
        self.health_profile = {**self.profile, 'runtimeVersion':'0.12.1'}
        self.cleanup_ok = False
        self.assertEqual(probe.main(self.args), 1)
        report = json.loads(self.output.read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['checks']['allOwnedProcessesStopped'])
        self.assertIsNotNone(report['cleanupFailure'])

    def test_profile_change_on_restart_stops_before_second_scoring(self):
        self.second_profile = {**self.profile, 'runtimeVersion':'0.12.1'}
        self.assertEqual(probe.main(self.args), 1)
        report = json.loads(self.output.read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['checks']['expectedProfileMatched'])
        self.assertEqual(len(self.scores), 1)
        self.assertEqual(self.signals, [(112, signal.SIGKILL)])
        self.assertEqual(report['ownedPids'], [111,112,113,211,212,213])
        self.assertTrue(report['checks']['allOwnedProcessesStopped'])

    def test_incomplete_child_inventory_cannot_claim_full_cleanup(self):
        with patch.object(probe, 'child_processes', side_effect=RuntimeError('Unknown owned child')):
            self.assertEqual(probe.main(self.args), 1)
        report = json.loads(self.output.read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['checks']['allOwnedProcessesStopped'])
        self.assertTrue(report['checks']['temporaryServiceRemoved'])
        self.assertIsNotNone(report['cleanupFailure'])
        self.assertEqual(self.signals, [])

    def test_harness_changed_during_measurement_cannot_pass(self):
        with patch.object(probe, 'harness_fingerprints', return_value={'fixture':'b'*64}):
            self.assertEqual(probe.main(self.args), 1)
        report = json.loads(self.output.read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['checks']['harnessSourcesStable'])
        self.assertTrue(report['checks']['temporaryServiceRemoved'])
        self.assertTrue(report['checks']['allOwnedProcessesStopped'])

    def test_sigterm_keeps_diagnostics_and_boots_out_the_owned_job(self):
        previous = signal.getsignal(signal.SIGTERM)
        def cancel(_runtime, _request):
            signal.raise_signal(signal.SIGTERM)
        with patch.object(probe.OwnedRuntime, 'score', cancel):
            self.assertEqual(probe.main(self.args), 1)
        report = json.loads(self.output.read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['failure']['type'], 'KeyboardInterrupt')
        self.assertTrue(report['checks']['temporaryServiceRemoved'])
        self.assertTrue(report['checks']['allOwnedProcessesStopped'])
        self.assertTrue(report['logsRetained'])
        self.assertEqual(self.state, 'absent')
        self.assertEqual(self.signals, [])
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_config_mismatch_is_rejected_before_job_registration(self):
        args = list(self.args)
        args[args.index('--inference-timeout-ms')+1] = '2000'
        self.assertEqual(probe.main(args), 1)
        self.assertIsNone(self.config)
        self.assertEqual(self.state, 'absent')
        self.assertFalse(self.output.exists())

    def test_resident_service_root_is_forwarded_and_profile_validation_still_precedes_scoring(self):
        self.health_profile = {**self.profile,'runtimeVersion':'0.12.1'}
        root = self.root/'resident/runtime'
        self.assertEqual(probe.main(self.args+['--service-root',str(root)]),1)
        self.assertEqual(probe.launch_agent.call_args.kwargs['root'],root)
        self.assertEqual(self.scores,[])
        self.assertEqual(self.signals,[])
        self.assertTrue(json.loads(self.output.read_text())['checks']['temporaryServiceRemoved'])

    def test_monitoring_requires_both_binaries_private_evidence_and_expected_profile(self):
        for flags in (['--prometheus','fixture'], ['--promtool','fixture']):
            with self.subTest(flags=flags):
                self.assertEqual(probe.main(self.args+flags),1)
                self.assertIsNone(self.config)
                self.assertFalse(self.output.exists())
        args = list(self.args)
        index = args.index('--expected-profile');del args[index:index+2]
        self.assertEqual(probe.main(args+['--prometheus','fixture','--promtool','fixture']),1)
        self.assertIsNone(self.config)

    def test_existing_evidence_and_public_raw_destination_are_rejected(self):
        self.directory.mkdir(parents=True)
        self.assertEqual(probe.main(self.args), 1)
        self.assertIsNone(self.config)
        self.directory.rmdir()
        args = list(self.args)
        args[args.index('--evidence-dir')+1] = str(self.root/'docs/public-probe')
        self.assertEqual(probe.main(args), 1)
        self.assertIsNone(self.config)


class ServiceInfoTest(unittest.TestCase):
    def test_only_confirmed_missing_job_counts_as_absent(self):
        target = 'gui/501/org.agat.fixture'
        absent = SimpleNamespace(returncode=113, stdout='', stderr='Could not find service "org.agat.fixture" in domain for user gui: 501')
        with patch.object(probe, 'launchctl', return_value=absent):
            self.assertIsNone(probe.service_info(target))
        for result in (SimpleNamespace(returncode=5, stdout='', stderr='Permission denied'),
                       SimpleNamespace(returncode=113, stdout='', stderr='Could not find domain')):
            with self.subTest(code=result.returncode), patch.object(probe, 'launchctl', return_value=result), self.assertRaises(RuntimeError):
                probe.service_info(target)

    def test_native_state_fields_are_read_without_interpreting_other_output(self):
        state = SimpleNamespace(returncode=0, stdout='\tpid = 211\n\truns = 2\n\tlast exit code = 75\n\tother = 9\n', stderr='')
        with patch.object(probe, 'launchctl', return_value=state):
            self.assertEqual(probe.service_info('gui/501/org.agat.fixture'), {'pid':211, 'runs':2, 'last exit code':75})

    def test_native_sysexits_suffix_is_parsed_and_raw_output_is_retained(self):
        state = SimpleNamespace(returncode=0, stdout='\truns = 1\n\tlast exit code = 75: EX_TEMPFAIL\n', stderr='')
        with tempfile.TemporaryDirectory() as temporary:
            diagnostic = Path(temporary)/'state.txt'
            with patch.object(probe, 'launchctl', return_value=state):
                self.assertEqual(probe.service_info('gui/501/org.agat.fixture', diagnostic), {'runs':1, 'last exit code':75})
            self.assertEqual(diagnostic.read_text(), state.stdout)
            self.assertEqual(stat.S_IMODE(diagnostic.stat().st_mode), 0o600)
        for invalid in ('75 extra', '75: EX_TEMPFAIL junk', '75: 5'):
            state.stdout = f'\tlast exit code = {invalid}\n'
            with self.subTest(invalid=invalid), patch.object(probe, 'launchctl', return_value=state):
                self.assertNotIn('last exit code', probe.service_info('gui/501/org.agat.fixture'))

    def test_exit_observation_waits_for_launchd_and_rejects_wrong_status(self):
        snapshots = []
        with patch.object(probe, 'service_info', side_effect=[{'runs':1}, {'runs':1, 'last exit code':75}]), patch.object(probe.time, 'sleep'):
            self.assertEqual(probe.wait_for_exit('fixture', 75, snapshots, None)['last exit code'], 75)
        self.assertEqual(len(snapshots), 2)
        with patch.object(probe, 'service_info', return_value={'runs':1, 'last exit code':78}):
            with self.assertRaisesRegex(RuntimeError, 'unexpected exit code'): probe.wait_for_exit('fixture', 75, [], None)
        with patch.object(probe, 'service_info', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'disappeared'): probe.wait_for_exit('fixture', 75, [], None)

    def test_exit_observation_has_a_bounded_deadline(self):
        with patch.object(probe.time, 'monotonic', side_effect=[0,0,6]), patch.object(probe.time, 'sleep'), patch.object(probe, 'service_info', return_value={'runs':1}):
            with self.assertRaisesRegex(RuntimeError, 'within 5 seconds'): probe.wait_for_exit('fixture', 75, [], None)

    def test_cleanup_waits_for_native_registration_removal_without_rebooting_it(self):
        with patch.object(probe, 'service_info', side_effect=[{'runs':2}, None]), patch.object(probe.time, 'sleep'), patch.object(probe, 'launchctl') as control:
            self.assertTrue(probe.wait_for_removal('fixture', None))
            control.assert_not_called()
        with patch.object(probe.time, 'monotonic', side_effect=[0,0,9]), patch.object(probe.time, 'sleep'), patch.object(probe, 'service_info', return_value={'runs':2}):
            self.assertFalse(probe.wait_for_removal('fixture', None))


class HarnessIdentityTest(unittest.TestCase):
    def test_only_committed_harness_bytes_are_admitted(self):
        raw = b'committed native probe fixture'
        sources = {'fixture.py':hashlib.sha256(raw).hexdigest()}
        with patch.object(probe, 'harness_fingerprints', return_value=sources):
            with patch.object(probe.subprocess, 'check_output', side_effect=['a'*40,raw]):
                self.assertEqual(probe.harness_identity(), ('a'*40,sources))
            with patch.object(probe.subprocess, 'check_output', side_effect=['a'*40,b'modified source']):
                with self.assertRaises(RuntimeError): probe.harness_identity()


if __name__ == '__main__': unittest.main()
