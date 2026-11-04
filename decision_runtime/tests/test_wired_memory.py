import contextlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from decision_runtime.__main__ import main
from decision_runtime.contracts import fingerprint
from decision_runtime.engine import DecisionEngine
from decision_runtime.isolated import mlx_factory
from decision_runtime.mlx_backend import MlxBackend, configure_wired_limit, wired_limit_bytes
from decision_runtime.tests.test_calibration import FixtureBackend, dataset

GIB = 1024 ** 3


def configured_factory(config):
    backend = FixtureBackend()
    backend.identity = {**backend.identity, 'allocatorWiredLimitBytes': wired_limit_bytes(config['wired_limit_mib'])}
    return backend


class WiredMemoryTest(unittest.TestCase):
    def device(self):
        return types.SimpleNamespace(device_info=Mock(return_value={
            'memory_size': 8 * GIB, 'max_recommended_working_set_size': 6 * GIB}), set_wired_limit=Mock())

    def test_invalid_operator_values_fail_before_manifest_or_mlx(self):
        with patch('decision_runtime.mlx_backend.verify_manifest') as verify:
            for value in (-1, 65537, True, 1.5, '4096'):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'wired_limit_mib'):
                    MlxBackend(Path('unused.json'), wired_limit_mib=value)
            verify.assert_not_called()
        self.assertIsNone(wired_limit_bytes(None))
        self.assertEqual(wired_limit_bytes(65536), 65536 * 1024 * 1024)

    def test_default_never_queries_os_device_or_changes_limit(self):
        mx = self.device()
        with patch('decision_runtime.mlx_backend.platform.mac_ver') as version, \
             patch('decision_runtime.mlx_backend.platform.system') as system:
            configure_wired_limit(mx, None)
        version.assert_not_called(); system.assert_not_called()
        mx.device_info.assert_not_called(); mx.set_wired_limit.assert_not_called()

    def test_unsupported_os_rejects_before_setting_budget(self):
        for system, version in [('Linux', ''), ('Darwin', '14.9'), ('Darwin', ''), ('Darwin', 'invalid')]:
            mx = self.device()
            with self.subTest(system=system, version=version), \
                 patch('decision_runtime.mlx_backend.platform.system', return_value=system), \
                 patch('decision_runtime.mlx_backend.platform.mac_ver', return_value=(version, (), '')), \
                 self.assertRaisesRegex(RuntimeError, 'macOS 15'):
                configure_wired_limit(mx, 4 * GIB)
            mx.set_wired_limit.assert_not_called()

    def test_physical_system_and_missing_device_budgets_fail_closed(self):
        for device, requested in [
            ({'memory_size': 8 * GIB, 'max_recommended_working_set_size': 6 * GIB}, 7 * GIB),
            ({'memory_size': 8 * GIB, 'max_recommended_working_set_size': 8 * GIB}, 8 * GIB),
            ({}, 0), ({'memory_size': True, 'max_recommended_working_set_size': 1}, 0),
            ({'memory_size': 8 * GIB, 'max_recommended_working_set_size': 0}, 0)]:
            mx = self.device(); mx.device_info.return_value = device
            with self.subTest(device=device, requested=requested), \
                 patch('decision_runtime.mlx_backend.platform.system', return_value='Darwin'), \
                 patch('decision_runtime.mlx_backend.platform.mac_ver', return_value=('15.0', (), '')), \
                 self.assertRaises((ValueError, RuntimeError)):
                configure_wired_limit(mx, requested)
            mx.set_wired_limit.assert_not_called()

    def test_zero_disables_wiring_and_system_boundary_is_allowed(self):
        for value in (0, 4 * GIB, 6 * GIB):
            mx = self.device()
            with self.subTest(value=value), \
                 patch('decision_runtime.mlx_backend.platform.system', return_value='Darwin'), \
                 patch('decision_runtime.mlx_backend.platform.mac_ver', return_value=('26.6.2', (), '')):
                configure_wired_limit(mx, value)
            mx.set_wired_limit.assert_called_once_with(value)

    def backend_environment(self, root, events):
        (root / 'config.json').write_text('{"model_type":"qwen3_5"}')
        mx = self.device(); mx.metal = types.SimpleNamespace(is_available=lambda: True)
        mx.set_wired_limit.side_effect = lambda value: events.append(('wired', value))
        mx.set_cache_limit = Mock(side_effect=lambda value: events.append(('cache', value)))
        class Model: pass
        Model.__module__ = 'mlx_lm.models.qwen3_5'
        model = Model(); model.language_model = types.SimpleNamespace(
            args=types.SimpleNamespace(tie_word_embeddings=True), model=types.SimpleNamespace(embed_tokens=object()))
        load = Mock(side_effect=lambda *args, **kwargs: (events.append(('load', None)) or model, object()))
        mlx = types.ModuleType('mlx'); mlx.core = mx
        lm = types.ModuleType('mlx_lm'); lm.load = load
        modules = {'mlx': mlx, 'mlx.core': mx, 'mlx_lm': lm}
        manifest = {'repository': 'fixture', 'revision': 'a' * 40, 'artifactSha256': 'b' * 64,
                    'files': {'tokenizer.json': 'c' * 64}}
        return mx, load, modules, manifest

    def test_applied_before_model_load_and_identity_binds_explicit_zero_and_budget(self):
        identities = []
        for value in (None, 0, 4096):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); events = []
                mx, load, modules, manifest = self.backend_environment(root, events)
                with patch.dict(sys.modules, modules), \
                     patch('decision_runtime.mlx_backend.verify_manifest', return_value=(manifest, root)), \
                     patch('decision_runtime.mlx_backend.importlib.metadata.version', return_value='fixture'), \
                     patch('decision_runtime.mlx_backend.platform.system', return_value='Darwin'), \
                     patch('decision_runtime.mlx_backend.platform.mac_ver', return_value=('15.0', (), '')):
                    backend = MlxBackend(root / 'manifest.json', cache_limit_mib=128, wired_limit_mib=value)
                expected = ([] if value is None else [('wired', value * 1024 * 1024)]) + [
                    ('load', None), ('cache', 128 * 1024 * 1024)]
                self.assertEqual(events, expected)
                self.assertEqual(backend.identity.get('allocatorWiredLimitBytes'), wired_limit_bytes(value))
                self.assertEqual('allocatorWiredLimitBytes' in backend.identity, value is not None)
                identities.append(fingerprint(DecisionEngine(backend).profile()))
        self.assertEqual(len(set(identities)), 3)

    def test_api_failure_does_not_load_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); events = []
            mx, load, modules, manifest = self.backend_environment(root, events)
            mx.set_wired_limit.side_effect = RuntimeError('system limit refused')
            with patch.dict(sys.modules, modules), \
                 patch('decision_runtime.mlx_backend.verify_manifest', return_value=(manifest, root)), \
                 patch('decision_runtime.mlx_backend.platform.system', return_value='Darwin'), \
                 patch('decision_runtime.mlx_backend.platform.mac_ver', return_value=('15.0', (), '')), \
                 self.assertRaisesRegex(RuntimeError, 'system limit refused'):
                MlxBackend(root / 'manifest.json', wired_limit_mib=4096)
            load.assert_not_called(); mx.set_cache_limit.assert_not_called()

    def test_isolated_factory_passes_zero_and_budget_without_altering_old_configs(self):
        for value in (None, 0, 4096):
            with patch('decision_runtime.mlx_backend.MlxBackend') as backend:
                config = {'manifest': '/fixture/manifest.json', 'max_tokens': 2048, 'cache_limit_mib': 128}
                if value is not None: config['wired_limit_mib'] = value
                mlx_factory(config)
                expected = {'cache_limit_mib': 128}
                if value is not None: expected['wired_limit_mib'] = value
                backend.assert_called_once_with(Path('/fixture/manifest.json'), 2048, **expected)


class WiredCliTest(unittest.TestCase):
    def run_cli(self, args, backend_factory):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['decision_runtime', *map(str, args)]), \
             patch('decision_runtime.mlx_backend.MlxBackend', side_effect=backend_factory), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = main()
        return result, output.getvalue(), errors.getvalue()

    def test_all_inference_commands_forward_budget_and_invalid_value_does_not_load(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); data = dataset()
            for case in data['cases']: case['split'] = 'development'
            for name, value in [('request', data['cases'][0]['request']), ('dataset', data)]:
                (root / f'{name}.json').write_text(json.dumps(value))
            options = []
            def factory(*args, **kwargs):
                options.append(kwargs)
                backend = FixtureBackend(); backend.identity = {**backend.identity,
                    'allocatorWiredLimitBytes': wired_limit_bytes(kwargs['wired_limit_mib'])}
                return backend
            common = ['--manifest', root / 'unused.json', '--wired-limit-mib', 4096]
            self.assertEqual(self.run_cli(['profile', *common, '--output', root / 'profile.json'], factory)[0], 0)
            self.assertEqual(self.run_cli(['score', *common, '--input', root / 'request.json'], factory)[0], 0)
            self.assertEqual(self.run_cli(['evaluate', *common, '--dataset', root / 'dataset.json',
                                           '--output', root / 'scores.json'], factory)[0], 0)
            class StopServer:
                server_port = 0
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def serve_forever(self): raise KeyboardInterrupt
            with patch('decision_runtime.__main__.make_server', return_value=StopServer()):
                self.assertEqual(self.run_cli(['serve', *common], factory)[0], 130)
            self.assertEqual(options, [{'cache_limit_mib': None, 'wired_limit_mib': 4096}] * 4)
            result, _, errors = self.run_cli(['profile', '--manifest', root / 'missing.json',
                '--wired-limit-mib', -1, '--output', root / 'invalid.json'], factory)
            self.assertEqual(result, 1); self.assertIn('wired_limit_mib', errors)
            self.assertEqual(len(options), 4); self.assertFalse((root / 'invalid.json').exists())

    def test_cli_budget_crosses_real_spawn_and_remains_in_isolated_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for budget in (0, 4096):
                path = root / f'profile-{budget}.json'
                with patch.object(sys, 'argv', ['decision_runtime', 'profile', '--manifest', str(root / 'unused.json'),
                    '--wired-limit-mib', str(budget), '--inference-timeout-ms', '5000', '--output', str(path)]), \
                    patch('decision_runtime.isolated.mlx_factory', configured_factory), \
                    contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(), 0)
                model = json.loads(path.read_text())['model']
                self.assertEqual(model['allocatorWiredLimitBytes'], budget * 1024 * 1024)
                self.assertEqual(model['inferenceExecution']['deadlineMs'], 5000)


if __name__ == '__main__': unittest.main()
