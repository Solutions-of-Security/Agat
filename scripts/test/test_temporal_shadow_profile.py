"""Keep historical evidence distinct from an explicitly frozen runtime upgrade."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('temporal_profile_verifier', ROOT / 'scripts/verify-temporal-real-rag.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
launcher = verifier.launcher_module
PROFILE = 'docs/qualification/local-decisions/performance/profiles/runtime-0.12.1.json'


class ShadowProfileTests(unittest.TestCase):
    def setUp(self):
        self.sources = {path.relative_to(ROOT).as_posix(): path.read_bytes()
                        for path in (ROOT / 'decision_runtime').glob('*.py')}
        for name in (PROFILE, launcher.SHADOW_REFERENCE):
            self.sources[name] = (ROOT / name).read_bytes()
        self.profile = json.loads(self.sources[PROFILE])
        raw = json.dumps(self.profile, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        self.decision = {'referenceFormat': 'runtime-profile-v1', 'referencePath': PROFILE,
                         'profile': {'profileJson': raw, 'profileSha256': verifier.sha(raw)}}

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
                    sources['decision_runtime/__init__.py'] = sources['decision_runtime/__init__.py'].replace(b'0.12.1', b'0.12.2')
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
