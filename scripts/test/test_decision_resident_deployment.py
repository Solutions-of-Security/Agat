import importlib.util
import json
import hashlib
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('resident_prepare',ROOT/'scripts/prepare-decision-resident-deployment.py')
prepare = importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(prepare)


class ResidentDeploymentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def wheel(self,name='mlx',version='0.32.2',filename=None):
        path = self.root/(filename or f'{name}-{version}-py3-none-any.whl')
        with zipfile.ZipFile(path,'x') as archive:
            archive.writestr(f'{name}-{version}.dist-info/METADATA',f'Name: {name}\nVersion: {version}\n')
        return path

    def test_dependency_pins_are_exact_normalized_and_unique(self):
        path = self.root/'requirements.txt';path.write_text('mlx==0.32.2\ntyping_extensions==4.16.0\n')
        self.assertEqual(prepare.requirements(path),{'mlx':'0.32.2','typing-extensions':'4.16.0'})
        for invalid in ('mlx>=0.32.2\n','mlx==0.32.2\nmlx==0.32.2\n','typing_extensions==4.16.0\ntyping-extensions==4.16.0\n',''):
            with self.subTest(invalid=invalid):
                path.write_text(invalid)
                with self.assertRaises(ValueError): prepare.requirements(path)

    def test_wheelhouse_binds_metadata_to_versions_and_hash_lock(self):
        self.wheel();wheels,lock = prepare.wheel_lock(self.root,{'mlx':'0.32.2'})
        self.assertEqual(lock,f'mlx==0.32.2 --hash=sha256:{wheels["mlx"]["sha256"]}\n')
        self.assertEqual(len(wheels['mlx']['sha256']),64)

    def test_wrong_foreign_duplicate_or_missing_wheels_are_rejected(self):
        self.wheel()
        for pins in ({'mlx':'0.32.1'},{'numpy':'2.5.3'},{'mlx':'0.32.2','numpy':'2.5.3'}):
            with self.subTest(pins=pins),self.assertRaises(ValueError): prepare.wheel_lock(self.root,pins)
        self.wheel(filename='duplicate-0-py3-none-any.whl')
        with self.assertRaises(ValueError): prepare.wheel_lock(self.root,{'mlx':'0.32.2'})

    def test_cloud_only_wheel_is_rejected_before_opening_it(self):
        path = self.wheel()
        with patch.object(prepare.stat,'SF_DATALESS',0x40000000,create=True),patch.object(Path,'stat',return_value=SimpleNamespace(st_flags=0x40000000)),patch.object(Path,'is_file',return_value=True),patch.object(prepare.zipfile,'ZipFile') as archive:
            with self.assertRaisesRegex(ValueError,'Cloud-only'): prepare.wheel_lock(self.root,{'mlx':'0.32.2'})
            archive.assert_not_called()

    def test_destination_refuses_existing_symlink_foreign_or_protected_paths(self):
        base = self.root/'Library/Application Support/Agat/decision-shadow/releases';base.mkdir(parents=True)
        new = base/'new-release';self.assertEqual(prepare.new_destination(new,self.root),new)
        new.mkdir()
        with self.assertRaises(ValueError): prepare.new_destination(new,self.root)
        linked = base/'link';linked.symlink_to(self.root/'missing')
        for path in (linked,self.root/'Documents/release',self.root/'arbitrary',base/'subdir/release'):
            with self.subTest(path=path),self.assertRaises(ValueError): prepare.new_destination(path,self.root)

    def test_cloud_only_model_is_rejected_before_manifest_read_or_hash(self):
        manifest = self.root/'manifest.json';manifest.write_text('{}')
        with patch.object(prepare.stat,'SF_DATALESS',0x40000000,create=True),patch.object(Path,'stat',return_value=SimpleNamespace(st_flags=0x40000000)),patch.object(prepare,'read_json') as reader,patch.object(prepare,'verify_manifest') as verify:
            with self.assertRaisesRegex(ValueError,'cloud-only'): prepare.resident_manifest(manifest)
            reader.assert_not_called();verify.assert_not_called()

    def test_model_traversal_is_rejected_before_model_verification(self):
        manifest = self.root/'manifest.json';manifest.write_text(json.dumps({'snapshot':str(self.root),'files':{'../outside':'a'*64}}))
        with patch.object(prepare,'verify_manifest') as verify:
            with self.assertRaisesRegex(ValueError,'filename'): prepare.resident_manifest(manifest)
            verify.assert_not_called()

    def test_scraper_config_is_foreground_loopback_and_owned_storage(self):
        config = prepare.prometheus_agent(self.root,self.root/'bin/prometheus',9095)
        self.assertEqual(config['Label'],'org.agat.decision-prometheus')
        self.assertIn('--web.listen-address=127.0.0.1:9095',config['ProgramArguments'])
        self.assertEqual(config['KeepAlive'],{'SuccessfulExit':False})
        self.assertEqual(config['ThrottleInterval'],30)
        for port in (True,80,65536):
            with self.subTest(port=port),self.assertRaises(ValueError): prepare.prometheus_agent(self.root,self.root/'bin/prometheus',port)

    def profile(self, wired=False):
        name = 'runtime-0.12.3-wired-4096.json' if wired else 'runtime-0.12.3.json'
        return json.loads((ROOT/'docs/qualification/local-decisions/performance/profiles'/name).read_text())

    def test_wired_budget_is_exact_and_default_and_explicit_zero_are_distinct(self):
        profile = self.profile()
        self.assertEqual(prepare.service_options(profile), {'wired_limit_mib': None})
        self.assertEqual(prepare.service_options(self.profile(True)), {'wired_limit_mib': 4096})
        profile['model']['allocatorWiredLimitBytes'] = 0
        self.assertEqual(prepare.service_options(profile), {'wired_limit_mib': 0})
        for value in (None, True, -1, 1, 65537 * 1024 * 1024, 4294967296.0, '4096'):
            profile['model']['allocatorWiredLimitBytes'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'whole number'):
                prepare.service_options(profile)

    def test_non_memory_recipe_changes_are_rejected(self):
        for field, value in [('maxInputTokens', 4096), ('allocatorCacheLimitBytes', 0),
                             ('inferenceExecution', {'kind': 'isolated-process', 'startMethod': 'spawn', 'deadlineMs': 10000})]:
            profile = self.profile(True); profile['model'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'recipe'):
                prepare.service_options(profile)
        profile = self.profile(); profile['calibration']['status'] = 'fitted'
        with self.assertRaisesRegex(ValueError, 'recipe'): prepare.service_options(profile)

    def test_profile_source_is_public_with_policy_and_no_symlink_escape(self):
        profiles = self.root/'docs/profiles';profiles.mkdir(parents=True)
        path = profiles/'wired.json';path.write_text(json.dumps(self.profile(True)))
        policy = self.root/prepare.POLICY;policy.parent.mkdir(parents=True);policy.write_bytes((ROOT/prepare.POLICY).read_bytes())
        with patch.object(prepare, 'ROOT', self.root):
            name, profile = prepare.profile_source(path)
            self.assertEqual(name, 'docs/profiles/wired.json');self.assertEqual(profile, self.profile(True))
            private = self.root/'docs/private/profile.json';private.parent.mkdir();private.write_bytes(path.read_bytes())
            outside = self.root/'outside.json';outside.write_bytes(path.read_bytes())
            escape = profiles/'escape.json';escape.symlink_to(outside)
            for rejected in (private, outside, escape, profiles/'missing.json'):
                with self.subTest(rejected=rejected), self.assertRaisesRegex(ValueError, 'public profile'):
                    prepare.profile_source(rejected)
            changed = self.profile(True);changed['policy']['id'] = 'other'
            path.write_text(json.dumps(changed))
            with self.assertRaises(ValueError): prepare.profile_source(path)

    def test_selected_profile_is_committed_and_bound_in_source_identity(self):
        selected = 'docs/profiles/wired.json'
        paths = ['decision_runtime/__main__.py', 'decision_runtime/models.json', 'decision_runtime/requirements-mlx.txt',
                 'scripts/prepare-decision-resident-deployment.py', 'scripts/lib/decision_service.py',
                 'scripts/lib/decision_performance.py', selected, prepare.POLICY,
                 f'{prepare.OBS}/prometheus-3.13.4.json', f'{prepare.OBS}/alerts.yml', f'{prepare.OBS}/alerts.test.yml']
        for name in paths:
            path = self.root/name;path.parent.mkdir(parents=True, exist_ok=True);path.write_text('fixture\n')
        (self.root/selected).write_text(json.dumps(self.profile(True)))
        (self.root/prepare.POLICY).write_bytes((ROOT/prepare.POLICY).read_bytes())
        for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                                                   'commit', '-qm', 'source binding fixture']):
            subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True)
        with patch.object(prepare, 'ROOT', self.root):
            commit, sources = prepare.source_identity(selected)
            self.assertEqual(sources[selected], hashlib.sha256((self.root/selected).read_bytes()).hexdigest())
            self.assertNotIn(prepare.PROFILE, sources)
            self.assertEqual(len(commit), 40)
            (self.root/selected).write_text(json.dumps(self.profile()))
            with self.assertRaisesRegex(ValueError, 'Commit deployment sources'):
                prepare.source_identity(selected)


if __name__ == '__main__': unittest.main()
