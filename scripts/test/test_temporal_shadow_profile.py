"""Keep historical evidence distinct from an explicitly frozen runtime upgrade."""
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from decision_runtime import VERSION

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('temporal_profile_verifier', ROOT / 'scripts/verify-temporal-real-rag.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
launcher = verifier.launcher_module
PROFILE = f'docs/qualification/local-decisions/performance/profiles/runtime-{VERSION}.json'
WIRED_PROFILE = f'docs/qualification/local-decisions/performance/profiles/runtime-{VERSION}-wired-4096.json'


class ShadowProfileTests(unittest.TestCase):
    def setUp(self):
        self.sources = {path.relative_to(ROOT).as_posix(): path.read_bytes()
                        for path in (ROOT / 'decision_runtime').glob('*.py')}
        for name in (PROFILE, WIRED_PROFILE, launcher.SHADOW_REFERENCE):
            self.sources[name] = (ROOT / name).read_bytes()
        self.profile = json.loads(self.sources[PROFILE])
        raw = json.dumps(self.profile, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        self.decision = {'referenceFormat': 'runtime-profile-v1', 'referencePath': PROFILE,
                         'profile': {'profileJson': raw, 'profileSha256': verifier.sha(raw)}}

    def identity(self, wired=None):
        profile=deepcopy(self.profile)
        if wired is not None: profile['model']['allocatorWiredLimitBytes']=wired*1024**2
        raw=json.dumps(profile,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        decision={**self.decision,'profile':{'profileJson':raw,'profileSha256':verifier.sha(raw)}}
        if wired is not None: decision['wiredLimitMiB']=wired
        return profile,decision

    def test_explicit_wired_and_zero_exports_bind_launcher_and_archived_verifier(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'docs') as directory:
            for wired in (0,4096):
                profile,decision=self.identity(wired)
                path=ROOT/WIRED_PROFILE if wired else Path(directory)/'zero-profile-fixture.json'
                if wired==0: path.write_text(decision['profile']['profileJson'])
                name=path.relative_to(ROOT).as_posix();decision['referencePath']=name
                sources={**self.sources,name:path.read_bytes()}
                with self.subTest(wired=wired):
                    self.assertEqual(launcher.shadow_profile(path,wired_limit_mib=wired),(name,decision['profile']))
                    self.assertEqual(verifier.verify_shadow_profile(decision,sources),profile)

    def test_wired_export_requires_exact_explicit_budget_and_preserves_fixed_settings(self):
        for path,wired in ((ROOT/WIRED_PROFILE,None),(ROOT/WIRED_PROFILE,0),(ROOT/PROFILE,4096)):
            with self.subTest(path=path,wired=wired),self.assertRaises(ValueError):
                launcher.shadow_profile(path,wired_limit_mib=wired)
        for value in (True,-1,65537,2.5):
            with self.subTest(value=value),self.assertRaises(ValueError):
                launcher.shadow_profile(ROOT/WIRED_PROFILE,wired_limit_mib=value)
        with tempfile.TemporaryDirectory(dir=ROOT/'docs') as directory:
            profile,_=self.identity(4096);profile['model']['inferenceExecution']['deadlineMs']=10000
            path=Path(directory)/'changed-deadline.json';path.write_text(json.dumps(profile))
            with self.assertRaises(ValueError): launcher.shadow_profile(path,wired_limit_mib=4096)

    def test_wired_cli_rejects_missing_runtime_profile_and_invalid_budget_before_side_effects(self):
        base=['--evidence-dir',str(ROOT/'docs/private/uncreated-wired-test')]
        runtime=['--shadow-python','fixture-python','--shadow-manifest','fixture-manifest']
        cases=[['--shadow-wired-limit-mib','0'],[*runtime,'--shadow-wired-limit-mib','4096'],
               [*runtime,'--shadow-profile',str(ROOT/PROFILE),'--shadow-wired-limit-mib','4096'],
               [*runtime,'--shadow-profile',str(ROOT/WIRED_PROFILE),'--shadow-wired-limit-mib','-1'],
               [*runtime,'--shadow-profile',str(ROOT/WIRED_PROFILE),'--shadow-wired-limit-mib','65537']]
        with patch.object(launcher.subprocess,'Popen') as spawn,patch.object(launcher,'command') as command, \
             patch.object(launcher,'require_resident_shadow_model') as resident:
            for options in cases:
                with self.subTest(options=options),patch('sys.argv',['run-temporal-real-rag.py',*base,*options]),self.assertRaises(ValueError):
                    launcher.main()
            self.assertEqual(spawn.call_count,0);command.assert_not_called();resident.assert_not_called()
        self.assertFalse((ROOT/'docs/private/uncreated-wired-test').exists())

    def test_initial_start_and_recovery_use_identical_pinned_wired_argv(self):
        for wired in (None,0,4096):
            _,decision=self.identity(wired)
            args=SimpleNamespace(shadow_python=Path('fixture-python'),shadow_manifest=Path('fixture-manifest'),shadow_wired_limit_mib=wired)
            health={'status':'ready','mode':'shadow',**decision['profile']}
            warmup={'httpStatus':200,'result':{'status':'ok'}}
            processes=[Mock(),Mock()]
            for process in processes: process.poll.return_value=None
            with self.subTest(wired=wired),patch.object(launcher.subprocess,'Popen',side_effect=processes) as spawn, \
                 patch.object(launcher,'request',side_effect=[health,warmup,health,warmup]):
                states=[{},{}]
                for state in states: launcher.start_shadow_runtime(state,args,9876,decision,{},None)
                commands=[call.args[0] for call in spawn.call_args_list]
                self.assertEqual(commands[0],commands[1]);command=commands[0]
                for flag,value in (('--inference-timeout-ms','5000'),('--max-tokens','2048'),('--cache-limit-mib','128')):
                    self.assertEqual(command[command.index(flag)+1],value)
                if wired is None: self.assertNotIn('--wired-limit-mib',command)
                else: self.assertEqual(command[command.index('--wired-limit-mib')+1],str(wired))
                self.assertEqual([s['process'] for s in states],processes)

    def test_direct_start_rejects_budget_profile_and_plan_mismatch_before_spawn(self):
        _,decision=self.identity(4096)
        for budget,record in ((None,decision),(0,decision),(4096,{k:v for k,v in decision.items() if k!='wiredLimitMiB'}),
                              (4096,{**decision,'wiredLimitMiB':0})):
            args=SimpleNamespace(shadow_python=Path('fixture-python'),shadow_manifest=Path('fixture-manifest'),shadow_wired_limit_mib=budget)
            with self.subTest(budget=budget,record=record),patch.object(launcher.subprocess,'Popen') as spawn,self.assertRaises(ValueError):
                launcher.start_shadow_runtime({},args,9876,record,{},None)
            self.assertEqual(spawn.call_count,0)

    def test_rehashed_wired_record_cannot_bypass_archived_configuration_binding(self):
        _,decision=self.identity(4096);decision['referencePath']=WIRED_PROFILE
        for value in (None,True,4096.0,'4096',-1,65537,0):
            with self.subTest(value=value),self.assertRaises(AssertionError):
                verifier.verify_shadow_profile({**decision,'wiredLimitMiB':value},self.sources)
        with self.assertRaises(AssertionError):
            verifier.verify_shadow_profile({k:v for k,v in decision.items() if k!='wiredLimitMiB'},self.sources)
        for field,value in (('allocatorWiredLimitBytes',0),('maxInputTokens',4096)):
            profile=deepcopy(json.loads(decision['profile']['profileJson']));profile['model'][field]=value
            raw=json.dumps(profile,ensure_ascii=False,sort_keys=True,separators=(',',':'))
            with self.subTest(field=field),self.assertRaises(AssertionError):
                verifier.verify_shadow_profile({**decision,'profile':{'profileJson':raw,'profileSha256':verifier.sha(raw)}},
                    {**self.sources,WIRED_PROFILE:raw.encode()})

    def test_private_profile_and_public_symlink_to_private_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            project=Path(temporary);private=project/'docs/private';private.mkdir(parents=True)
            path=private/'profile.json';path.write_bytes(self.sources[PROFILE])
            link=project/'docs/profile.json';link.symlink_to(path)
            with patch.object(launcher,'ROOT',project):
                for candidate in (path,link):
                    with self.subTest(candidate=candidate),self.assertRaises(ValueError): launcher.shadow_profile(candidate)
        historical=json.loads(self.sources[launcher.SHADOW_REFERENCE])['decision']
        with self.assertRaises(AssertionError):
            verifier.verify_shadow_profile({'referencePath':launcher.SHADOW_REFERENCE,'profile':historical,'wiredLimitMiB':0},self.sources)

    def test_boolean_and_float_bytes_cannot_alias_the_canonical_wired_reference(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'docs') as directory:
            path=Path(directory)/'scalar-alias-fixture.json';name=path.relative_to(ROOT).as_posix()
            for wired,alias in ((0,False),(0,0.0),(4096,4294967296.0)):
                profile,decision=self.identity(wired);profile['model']['allocatorWiredLimitBytes']=alias
                raw=json.dumps(profile,ensure_ascii=False,sort_keys=True,separators=(',',':'));path.write_text(raw)
                decision['referencePath']=name
                with self.subTest(wired=wired,alias=alias):
                    with self.assertRaises(ValueError): launcher.shadow_profile(path,wired_limit_mib=wired)
                    # Record uses integer canonical identity; archived document uses an alias.
                    with self.assertRaises(AssertionError):
                        verifier.verify_shadow_profile(decision,{**self.sources,name:raw.encode()})

    def test_direct_start_rejects_boolean_and_float_bytes_before_model_load(self):
        for wired,alias in ((0,False),(0,0.0),(4096,4294967296.0)):
            profile,decision=self.identity(wired);profile['model']['allocatorWiredLimitBytes']=alias
            raw=json.dumps(profile,ensure_ascii=False,sort_keys=True,separators=(',',':'))
            decision['profile']={'profileJson':raw,'profileSha256':verifier.sha(raw)}
            args=SimpleNamespace(shadow_python=Path('fixture-python'),shadow_manifest=Path('fixture-manifest'),shadow_wired_limit_mib=wired)
            with self.subTest(wired=wired,alias=alias),patch.object(launcher.subprocess,'Popen') as spawn,self.assertRaises(ValueError):
                launcher.start_shadow_runtime({},args,9876,decision,{},None)
            self.assertEqual(spawn.call_count,0)

    def test_export_is_bound_to_current_launcher_and_archived_runtime(self):
        path, identity = launcher.shadow_profile(ROOT / PROFILE)
        self.assertEqual(path, PROFILE)
        self.assertEqual(identity, self.decision['profile'])
        self.assertEqual(verifier.verify_shadow_profile(self.decision, self.sources), self.profile)

    def test_launcher_rejects_old_runtime_and_changed_experiment_settings(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'docs') as directory:
            path = Path(directory) / 'profile.json'
            for key, value in [('runtimeVersion', '0.12.0'), ('model', {**self.profile['model'], 'maxInputTokens': 4096}),
                               ('policy', {**self.profile['policy'], 'minProbability': .1}),
                               ('calibration', {**self.profile['calibration'], 'temperature': 2})]:
                with self.subTest(key=key):
                    path.write_text(json.dumps({**self.profile, key: value}))
                    with self.assertRaises(ValueError):
                        launcher.shadow_profile(path)

    def test_launcher_rejects_profile_outside_docs_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as external, tempfile.TemporaryDirectory(dir=ROOT / 'docs') as internal:
            path = Path(external) / 'profile.json'
            path.write_bytes(self.sources[PROFILE])
            link = Path(internal) / 'profile.json'
            link.symlink_to(path)
            for candidate in (path, link):
                with self.assertRaises(ValueError):
                    launcher.shadow_profile(candidate)

    def test_verifier_binds_version_and_every_archived_runtime_module(self):
        for change in ('version', 'source', 'extra_module'):
            with self.subTest(change=change):
                sources = dict(self.sources)
                if change == 'version':
                    original = sources['decision_runtime/__init__.py']
                    sources['decision_runtime/__init__.py'] = original.replace(
                        f'VERSION = "{self.profile["runtimeVersion"]}"'.encode(), b'VERSION = "0.0.0"')
                    self.assertNotEqual(sources['decision_runtime/__init__.py'], original)
                elif change == 'source':
                    sources['decision_runtime/engine.py'] += b'\n# changed runtime\n'
                else:
                    sources['decision_runtime/extra.py'] = b'# new module\n'
                with self.assertRaises(AssertionError):
                    verifier.verify_shadow_profile(self.decision, sources)

    def test_rehashed_profile_cannot_change_settings_or_claim_old_identity(self):
        for key, value in [('runtimeVersion', '0.12.0'),
                           ('model', {**self.profile['model'], 'implementationSha256': '0' * 64}),
                           ('model', {**self.profile['model'], 'inferenceExecution': {
                               **self.profile['model']['inferenceExecution'], 'deadlineMs': 10000}}),
                           ('policy', {**self.profile['policy'], 'minProbability': .1})]:
            with self.subTest(key=key, value=value):
                profile = {**self.profile, key: value}
                raw = json.dumps(profile, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
                sources = {**self.sources, PROFILE: raw.encode()}
                decision = {**self.decision, 'profile': {'profileJson': raw, 'profileSha256': verifier.sha(raw)}}
                with self.assertRaises(AssertionError):
                    verifier.verify_shadow_profile(decision, sources)

    def test_historical_profile_remains_bound_to_its_original_reference(self):
        historical = json.loads(self.sources[launcher.SHADOW_REFERENCE])['decision']
        decision = {'referencePath': launcher.SHADOW_REFERENCE, 'profile': historical}
        self.assertEqual(verifier.verify_shadow_profile(decision, self.sources), json.loads(historical['profileJson']))
        decision['profile'] = self.decision['profile']
        with self.assertRaises(AssertionError):
            verifier.verify_shadow_profile(decision, self.sources)

    def test_reference_format_is_explicit_and_known(self):
        for change in ({'referenceFormat': 'unknown'}, {'referencePath': launcher.SHADOW_REFERENCE}):
            with self.assertRaises((AssertionError, KeyError)):
                verifier.verify_shadow_profile({**self.decision, **change}, self.sources)


if __name__ == '__main__':
    unittest.main()
