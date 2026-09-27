"""Real subprocess and HTTP races for the qualification-only idle pool."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
sys.path.insert(0, str(ROOT / 'workers'))
from embedding_idle_pool import IdleEmbeddingSessionPool
from test_embedding_transport import Echo, observed_processes, request
from test_embedding_http_deadline import endpoint, slow_endpoint
from agat_worker import LocalModelClient
from telemetry import WorkerTelemetry

spec = importlib.util.spec_from_file_location('idle_pool_fixture', ROOT / 'scripts/profile-embedding-idle-budget.py')
fixture_support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_support)


class IdlePoolTests(unittest.TestCase):
    pool_type = IdleEmbeddingSessionPool

    def setUp(self):
        environment = patch.dict(os.environ, {'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'})
        environment.start()
        self.addCleanup(environment.stop)

    def eventually(self, predicate, message='Condition did not become true'):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(predicate(), message)

    def test_invalid_idle_timeout_creates_no_helpers_or_maintenance(self):
        before = {thread.ident for thread in threading.enumerate()}
        with observed_processes() as children:
            for value in (True, False, 0, -1, float('inf'), float('nan'), '1', None, 3601):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    self.pool_type(1, idle_timeout=value)
            for capacity in (0, 33, True):
                with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                    self.pool_type(capacity, idle_timeout=1)
        self.assertEqual(children, [])
        self.assertEqual({thread.ident for thread in threading.enumerate()}, before)

    def test_cancel_and_deadline_after_idle_reap_discard_only_the_fresh_helper(self):
        for cancellation in (False, True):
            with self.subTest(cancellation=cancellation), endpoint(Echo) as port, slow_endpoint('body') as (url, entered, release), observed_processes() as children:
                with self.pool_type(1, idle_timeout=.05) as pool, ThreadPoolExecutor(max_workers=1) as executor:
                    request(pool, f'http://127.0.0.1:{port}/echo')
                    self.eventually(lambda: pool._sessions[0].idle_reaps == 1)
                    cancelled = threading.Event()
                    fresh = executor.submit(request, pool, url, timeout=2 if cancellation else .3, cancelled=cancelled)
                    try:
                        self.assertTrue(entered.wait(2))
                        if cancellation:
                            cancelled.set()
                        with self.assertRaisesRegex(RuntimeError, 'cancelled' if cancellation else 'deadline'):
                            fresh.result(2)
                        self.assertFalse(release.is_set())
                        self.assertEqual(len(children), 2)
                        self.assertTrue(all(child.returncode is not None and child.stdin.closed and child.stdout.closed for child in children))
                        self.assertIn('request', json.loads(request(pool, f'http://127.0.0.1:{port}/echo')))
                        self.assertEqual(len(children), 3)
                    finally:
                        release.set()

    def test_unused_pool_is_lazy_and_close_is_idempotent(self):
        with observed_processes() as children:
            pool = self.pool_type(32, idle_timeout=.03)
            pool.close()
            pool.close()
            self.assertFalse(pool._maintenance.is_alive())
            self.assertIsNone(pool.maintenance_failure)
            self.assertEqual(children, [])
            with self.assertRaisesRegex(RuntimeError, 'closed'):
                request(pool, 'http://127.0.0.1:1')

    def test_idle_helper_is_reaped_then_next_request_starts_fresh_without_replay(self):
        class CountedEcho(Echo):
            calls = 0

            def do_POST(self):
                CountedEcho.calls += 1
                super().do_POST()

        with endpoint(CountedEcho) as port, observed_processes() as children, self.pool_type(1, idle_timeout=.08) as pool:
            url = f'http://127.0.0.1:{port}/echo'
            first = json.loads(request(pool, url))
            self.eventually(lambda: pool._sessions[0].idle_reaps == 1)
            self.assertEqual(children[0].returncode, 0)
            self.assertTrue(children[0].stdin.closed and children[0].stdout.closed)
            self.assertEqual(pool._sessions[0].idle_reaps, 1)
            second = json.loads(request(pool, url))
            self.assertEqual(first, second)
            self.assertEqual(len(children), 2)
            self.assertNotEqual(children[0].pid, children[1].pid)
            self.assertEqual(pool._sessions[0].starts, 2)
            self.assertEqual(CountedEcho.calls, 2)
        self.assertFalse(pool._maintenance.is_alive())

    def test_active_headers_and_body_outlive_idle_timeout_without_interruption(self):
        for mode in ('headers', 'body'):
            with self.subTest(mode=mode), slow_endpoint(mode) as (url, entered, release), observed_processes() as children:
                with self.pool_type(1, idle_timeout=.05) as pool, ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(request, pool, url)
                    try:
                        self.assertTrue(entered.wait(2))
                        time.sleep(.2)
                        self.assertIsNone(children[0].poll())
                        self.assertEqual(pool._sessions[0].idle_reaps, 0)
                        release.set()
                        self.assertEqual(json.loads(future.result(2))['data'][0]['embedding'], [1, 0])
                        self.assertEqual(len(children), 1)
                    finally:
                        release.set()

    def test_idle_neighbor_is_retired_while_another_slot_is_active(self):
        with endpoint(Echo) as port, slow_endpoint('body') as (url, entered, release), observed_processes() as children:
            with self.pool_type(2, idle_timeout=.08) as pool, ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(request, pool, url)
                try:
                    self.assertTrue(entered.wait(2))
                    request(pool, f'http://127.0.0.1:{port}/echo')
                    self.eventually(lambda: len(children) == 2 and children[1].poll() is not None)
                    self.assertIsNone(children[0].poll())
                    self.assertEqual(children[1].returncode, 0)
                    release.set()
                    self.assertEqual(json.loads(future.result(2))['data'][0]['embedding'], [1, 0])
                finally:
                    release.set()

    def test_admission_waiting_for_retirement_keeps_deadline_and_cancellation(self):
        for cancel_waiter in (False, True):
            with self.subTest(cancel_waiter=cancel_waiter), endpoint(Echo) as port, observed_processes() as children:
                with self.pool_type(1, idle_timeout=.08) as pool, ThreadPoolExecutor(max_workers=1) as executor:
                    url = f'http://127.0.0.1:{port}/echo'
                    request(pool, url)
                    session = pool._sessions[0]
                    original = session._retire
                    entered, release = threading.Event(), threading.Event()

                    def held_retire(*, force):
                        entered.set()
                        if not release.wait(3):
                            raise RuntimeError('Retirement scheduling gate timed out')
                        return original(force=force)

                    with patch.object(session, '_retire', side_effect=held_retire):
                        try:
                            self.assertTrue(entered.wait(2))
                            cancelled = threading.Event()
                            start = time.monotonic()
                            future = executor.submit(request, pool, url, timeout=1 if cancel_waiter else .08, cancelled=cancelled)
                            if cancel_waiter:
                                time.sleep(.03)
                                cancelled.set()
                            with self.assertRaisesRegex(RuntimeError, 'cancelled' if cancel_waiter else 'deadline'):
                                future.result(1)
                            self.assertLess(time.monotonic() - start, .7)
                            self.assertEqual(len(children), 1, 'Waiting caller started a second helper')
                        finally:
                            release.set()
                        self.eventually(lambda: session.process_id is None)
                    self.assertIn('request', json.loads(request(pool, url)))
                    self.assertEqual(len(children), 2)

    def test_caller_waits_for_retirement_then_uses_a_new_helper_once(self):
        with endpoint(Echo) as port, observed_processes() as children:
            with self.pool_type(1, idle_timeout=.08) as pool, ThreadPoolExecutor(max_workers=1) as executor:
                url = f'http://127.0.0.1:{port}/echo'
                request(pool, url)
                session, entered, release = pool._sessions[0], threading.Event(), threading.Event()
                original = session._retire

                def held_retire(*, force):
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError('Retirement scheduling gate timed out')
                    return original(force=force)

                with patch.object(session, '_retire', side_effect=held_retire):
                    try:
                        self.assertTrue(entered.wait(2))
                        future = executor.submit(request, pool, url, timeout=2)
                        time.sleep(.05)
                        self.assertFalse(future.done())
                        self.assertEqual(len(children), 1)
                        release.set()
                        self.assertIn('request', json.loads(future.result(2)))
                        self.assertEqual(len(children), 2)
                        self.assertEqual(children[0].returncode, 0)
                        self.assertTrue(children[0].stdin.closed and children[0].stdout.closed)
                    finally:
                        release.set()

    def test_queued_cancel_does_not_interrupt_active_lease_or_refresh_idle(self):
        with slow_endpoint('body') as (url, entered, release), observed_processes() as children:
            with self.pool_type(1, idle_timeout=.05) as pool, ThreadPoolExecutor(max_workers=2) as executor:
                owner = executor.submit(request, pool, url)
                try:
                    self.assertTrue(entered.wait(2))
                    cancelled = threading.Event()
                    waiter = executor.submit(request, pool, url, cancelled=cancelled)
                    cancelled.set()
                    with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                        waiter.result(1)
                    self.assertEqual(len(children), 1)
                    self.assertIsNone(children[0].poll())
                    release.set()
                    owner.result(2)
                finally:
                    release.set()

    def test_close_cancels_active_http_and_joins_maintenance_before_return(self):
        with slow_endpoint('body') as (url, entered, release), observed_processes() as children:
            pool = self.pool_type(1, idle_timeout=.03)
            with ThreadPoolExecutor(max_workers=2) as executor:
                active = executor.submit(request, pool, url)
                try:
                    self.assertTrue(entered.wait(2))
                    executor.submit(pool.close).result(2)
                    with self.assertRaisesRegex(RuntimeError, 'closed'):
                        active.result(1)
                    self.assertFalse(release.is_set())
                    self.assertFalse(pool._maintenance.is_alive())
                    self.assertIsNotNone(children[0].returncode)
                finally:
                    release.set()
                    pool.close()

    def test_close_racing_an_idle_retire_does_not_deadlock_or_reopen(self):
        with endpoint(Echo) as port, observed_processes() as children:
            pool = self.pool_type(1, idle_timeout=.08)
            session = pool._sessions[0]
            entered, release = threading.Event(), threading.Event()
            original = session._retire

            def held_retire(*, force):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError('Retirement scheduling gate timed out')
                return original(force=force)

            try:
                request(pool, f'http://127.0.0.1:{port}/echo')
                with patch.object(session, '_retire', side_effect=held_retire), ThreadPoolExecutor(max_workers=2) as executor:
                    self.assertTrue(entered.wait(2))
                    closers = [executor.submit(pool.close) for _ in range(2)]
                    release.set()
                    for future in closers:
                        future.result(2)
                self.assertFalse(pool._maintenance.is_alive())
                self.assertEqual(children[0].returncode, 0)
                with self.assertRaisesRegex(RuntimeError, 'closed'):
                    request(pool, f'http://127.0.0.1:{port}/echo')
            finally:
                release.set()
                pool.close()

    def test_close_signals_active_slot_before_waiting_for_another_slots_retire(self):
        with endpoint(Echo) as port, slow_endpoint('body') as (url, active_entered, body_release), observed_processes() as children:
            pool = self.pool_type(2, idle_timeout=.08)
            retire_entered, retire_release = threading.Event(), threading.Event()
            with ThreadPoolExecutor(max_workers=2) as executor:
                active = executor.submit(request, pool, url)
                try:
                    self.assertTrue(active_entered.wait(2))
                    request(pool, f'http://127.0.0.1:{port}/echo')
                    idle = next(session for session in pool._sessions if session.process_id == children[1].pid)
                    original = idle._retire

                    def held_retire(*, force):
                        retire_entered.set()
                        if not retire_release.wait(3):
                            raise RuntimeError('Retirement scheduling gate timed out')
                        return original(force=force)

                    with patch.object(idle, '_retire', side_effect=held_retire):
                        try:
                            self.assertTrue(retire_entered.wait(2))
                            closing = executor.submit(pool.close)
                            with self.assertRaisesRegex(RuntimeError, 'closed'):
                                active.result(1)
                            self.assertFalse(closing.done())
                            self.assertFalse(body_release.is_set())
                            self.assertIsNotNone(children[0].returncode)
                            retire_release.set()
                            closing.result(2)
                        finally:
                            retire_release.set()
                    self.assertFalse(pool._maintenance.is_alive())
                finally:
                    retire_release.set()
                    body_release.set()
                    pool.close()

    def test_maintenance_failure_closes_admission_and_remains_visible(self):
        with endpoint(Echo) as port, observed_processes(), self.pool_type(1, idle_timeout=.08) as pool:
            url = f'http://127.0.0.1:{port}/echo'
            request(pool, url)
            with patch.object(pool._sessions[0], 'expire_idle', side_effect=OSError('synthetic maintenance failure')):
                self.eventually(lambda: pool.maintenance_failure is not None)
                self.assertEqual(pool.maintenance_failure, 'OSError')
                with self.assertRaisesRegex(RuntimeError, 'closed'):
                    request(pool, url)
        self.assertFalse(pool._maintenance.is_alive())

    def test_maximum_burst_retires_to_zero_and_restarts_only_for_new_requests(self):
        fixture = fixture_support.Fixture(32)
        try:
            baseline_fd = fixture_support.guard.support.descriptors()
            with observed_processes() as children, self.pool_type(32, idle_timeout=.3) as pool:
                client = LocalModelClient(fixture.url, '', embedding_timeout=15, embedding_request=pool.request,
                                          telemetry=WorkerTelemetry(enabled=False))
                for round_id in range(2):
                    with ThreadPoolExecutor(max_workers=32) as executor:
                        results = list(executor.map(lambda actor: client.embed('idle-fixture', [f'{round_id}/{actor}']), range(32)))
                    self.assertEqual(results, [[[round_id + 1, actor + 1]] for actor in range(32)])
                    self.assertEqual(len(children), (round_id + 1) * 32)
                    self.eventually(lambda: sum(session.idle_reaps for session in pool._sessions) == (round_id + 1) * 32)
                    self.assertTrue(all(child.returncode == 0 and child.stdin.closed and child.stdout.closed for child in children))
                    self.assertEqual(fixture_support.guard.support.descriptors(), baseline_fd)
                self.assertEqual(sum(session.idle_reaps for session in pool._sessions), 64)
            self.assertEqual(len(fixture.rows), 64)
            self.assertEqual(fixture.errors, [])
            self.assertFalse(pool._maintenance.is_alive())
        finally:
            fixture.close()


if __name__ == '__main__':
    unittest.main()
