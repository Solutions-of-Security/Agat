"""Production idle lifecycle plus worker configuration and ownership checks."""
from contextlib import redirect_stderr
from dataclasses import replace
import importlib.util
import io
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

import agat_worker
from embedding_transport import IdleEmbeddingSessionPool

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('shared_idle_lifecycle', ROOT / 'scripts/test/test_embedding_idle_pool.py')
scenarios = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scenarios)


class ProductionIdleLifecycleTests(scenarios.IdlePoolTests):
    pool_type = IdleEmbeddingSessionPool


class WorkerIdleConfigurationTests(unittest.TestCase):
    def config(self, *args, environment=None):
        with patch.dict(os.environ, {'AGAT_EMBEDDING_TRANSPORT': 'isolated', 'AGAT_EMBEDDING_IDLE_TIMEOUT': '0',
                                     **(environment or {})}), patch.object(sys, 'argv',
                ['agat_worker.py', '--models', 'local', '--model-discovery', 'off', '--no-web', *args]):
            return agat_worker.parse_args()

    def test_default_env_cli_and_explicit_disable(self):
        self.assertEqual(self.config().embedding_idle_timeout, 0)
        self.assertEqual(self.config('--embedding-transport', 'session').embedding_idle_timeout, 0)
        env = {'AGAT_EMBEDDING_TRANSPORT': 'session', 'AGAT_EMBEDDING_IDLE_TIMEOUT': '12.5'}
        self.assertEqual(self.config(environment=env).embedding_idle_timeout, 12.5)
        self.assertEqual(self.config('--embedding-idle-timeout', '3600', environment=env).embedding_idle_timeout, 3600)
        self.assertEqual(self.config('--embedding-idle-timeout', '0', environment=env).embedding_idle_timeout, 0)

    def test_invalid_cli_and_env_values_fail_before_work(self):
        for value in ('-1', 'nan', 'inf', '-inf', '3600.1', '', 'yes'):
            for cli in (False, True):
                with self.subTest(value=value, cli=cli), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    if cli:
                        self.config('--embedding-transport', 'session', f'--embedding-idle-timeout={value}')
                    else:
                        self.config(environment={'AGAT_EMBEDDING_TRANSPORT': 'session', 'AGAT_EMBEDDING_IDLE_TIMEOUT': value})
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.config('--embedding-idle-timeout', '1')

    def test_programmatic_invalid_config_does_not_enter_loop(self):
        cfg = self.config('--embedding-transport', 'session')
        for value in (True, False, float('nan'), float('inf'), -1, 3601, '1', None):
            with self.subTest(value=value), patch.object(agat_worker, 'WorkerTelemetry'), \
                    patch.object(agat_worker, '_worker_loop_with_telemetry') as work, self.assertRaises(ValueError):
                agat_worker.worker_loop(replace(cfg, embedding_idle_timeout=value))
            work.assert_not_called()
        with patch.object(agat_worker, 'WorkerTelemetry'), self.assertRaisesRegex(ValueError, 'requires session'):
            agat_worker.worker_loop(replace(cfg, embedding_transport='isolated', embedding_idle_timeout=1))

    def test_disabled_session_has_no_maintenance_thread(self):
        cfg = self.config('--embedding-transport', 'session')
        before = {thread.ident for thread in threading.enumerate()}
        with patch.object(agat_worker, 'WorkerTelemetry'), patch.object(agat_worker, '_worker_loop_with_telemetry', return_value=0) as work:
            self.assertEqual(agat_worker.worker_loop(cfg), 0)
            self.assertNotIsInstance(work.call_args.args[2].__self__, IdleEmbeddingSessionPool)
            self.assertEqual(work.call_args.kwargs, {})
        self.assertEqual({thread.ident for thread in threading.enumerate()}, before)

    def test_worker_owns_idle_pool_and_closes_after_success_or_exception(self):
        for failure in (False, True):
            cfg = self.config('--embedding-transport', 'session', '--embedding-idle-timeout', '.05')
            with self.subTest(failure=failure), scenarios.endpoint(scenarios.Echo) as port, scenarios.observed_processes() as children:
                seen = []
                def work(actual, telemetry, request, *, check_embedding_transport):
                    self.assertIs(actual, cfg)
                    pool = request.__self__; seen.append(pool)
                    self.assertIsInstance(pool, IdleEmbeddingSessionPool)
                    check_embedding_transport()
                    scenarios.request(pool, f'http://127.0.0.1:{port}/echo')
                    if failure:
                        raise RuntimeError('Injected worker failure')
                    return 0
                telemetry = Mock()
                with patch.object(agat_worker, 'WorkerTelemetry', return_value=telemetry), \
                        patch.object(agat_worker, '_worker_loop_with_telemetry', side_effect=work):
                    if failure:
                        with self.assertRaisesRegex(RuntimeError, 'Injected worker failure'):
                            agat_worker.worker_loop(cfg)
                    else:
                        self.assertEqual(agat_worker.worker_loop(cfg), 0)
                self.assertFalse(seen[0]._maintenance.is_alive())
                self.assertEqual(len(children), 1)
                self.assertTrue(all(p.returncode == 0 and p.stdin.closed and p.stdout.closed for p in children))
                telemetry.shutdown.assert_called_once()

    def test_maintenance_failure_stops_worker_admission_and_heartbeat(self):
        cfg = self.config('--embedding-transport', 'session', '--embedding-idle-timeout', '.03')
        coordinator = Mock(); coordinator.lease.return_value = None
        started, ended = threading.Event(), threading.Event()
        def heartbeat(_client, _cfg, stop):
            started.set()
            stop.wait(3)
            ended.set()
        with IdleEmbeddingSessionPool(1, idle_timeout=.03) as pool:
            with patch.object(pool._sessions[0], 'expire_idle', side_effect=OSError('Injected maintenance error')):
                deadline = time.monotonic() + 2
                while pool.maintenance_failure is None and time.monotonic() < deadline:
                    time.sleep(.01)
            self.assertEqual(pool.maintenance_failure, 'OSError')
            with patch.object(agat_worker, 'CoordinatorClient', return_value=coordinator), \
                    patch.object(agat_worker, 'register_if_needed'), patch.object(agat_worker, 'discover_model_profiles', return_value=cfg.model_profiles), \
                    patch.object(agat_worker, 'heartbeat_loop', side_effect=heartbeat), patch.object(agat_worker.signal, 'signal'), \
                    self.assertRaisesRegex(RuntimeError, 'Embedding idle maintenance failed: OSError'):
                agat_worker._worker_loop_with_telemetry(cfg, Mock(), pool.request, check_embedding_transport=pool.check_health)
            coordinator.lease.assert_not_called(); coordinator.knowledge_lease.assert_not_called()
            self.assertTrue(started.is_set() and ended.is_set())
        self.assertFalse(pool._maintenance.is_alive())


if __name__ == '__main__':
    unittest.main()
