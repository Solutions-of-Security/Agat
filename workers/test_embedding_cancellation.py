from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import threading
import unittest
from unittest.mock import Mock, patch

from agat_worker import (
    ApiError, CoordinatorClient, LocalModelClient, execute_knowledge_lease,
    knowledge_lease_renewer,
)


class EmbeddingCancellationTests(unittest.TestCase):
    def test_definitive_renewal_rejection_cancels_and_stops_polling(self) -> None:
        for status in (401, 403, 404, 409):
            with self.subTest(status=status), redirect_stderr(io.StringIO()):
                client = Mock(spec=CoordinatorClient)
                client.knowledge_renew.side_effect = [ApiError(status, "lease lost"), None]
                stop = Mock(spec=threading.Event)
                stop.wait.side_effect = [False, False, True]
                cancelled = threading.Event()
                knowledge_lease_renewer(client, "lease", stop, cancelled)
                self.assertTrue(cancelled.is_set())
                client.knowledge_renew.assert_called_once_with("lease")
                stop.wait.assert_called_once_with(45)

    def test_temporary_renewal_failure_keeps_the_lease_and_retries(self) -> None:
        for status in (0, 408, 429, 500, 502, 503, 504):
            with self.subTest(status=status), redirect_stderr(io.StringIO()):
                client = Mock(spec=CoordinatorClient)
                client.knowledge_renew.side_effect = [ApiError(status, "temporary"), None]
                stop = Mock(spec=threading.Event)
                stop.wait.side_effect = [False, False, True]
                cancelled = threading.Event()
                knowledge_lease_renewer(client, "lease", stop, cancelled)
                self.assertFalse(cancelled.is_set())
                self.assertEqual(client.knowledge_renew.call_count, 2)
                self.assertEqual(stop.wait.call_count, 3)

    def test_clean_stop_does_not_renew_or_cancel(self) -> None:
        client = Mock(spec=CoordinatorClient)
        stop = threading.Event()
        stop.set()
        cancelled = threading.Event()
        knowledge_lease_renewer(client, "lease", stop, cancelled)
        client.knowledge_renew.assert_not_called()
        self.assertFalse(cancelled.is_set())

    def test_definitive_rejection_stops_even_without_a_cancellation_receiver(self) -> None:
        client = Mock(spec=CoordinatorClient)
        client.knowledge_renew.side_effect = ApiError(404, "lease lost")
        stop = Mock(spec=threading.Event)
        stop.wait.side_effect = [False, False, True]
        with redirect_stderr(io.StringIO()):
            knowledge_lease_renewer(client, "lease", stop)
        client.knowledge_renew.assert_called_once_with("lease")

    def execute_during_renewal(self, status: int, *, model_error: bool = False) -> Mock:
        client = Mock(spec=CoordinatorClient)
        client.knowledge_renew.side_effect = ApiError(status, "renewal response")
        client.knowledge_complete.return_value = {"completed": True, "remainingChunks": 0}
        client.knowledge_fail.return_value = {"retrying": True}
        model = Mock(spec=LocalModelClient)
        model_entered, renewal_finished = threading.Event(), threading.Event()
        renewal_threads: list[threading.Thread] = []
        renewal_stops: list[threading.Event] = []
        background_errors: list[Exception] = []

        def renew(client_arg, lease_id, stop, cancelled):
            renewal_threads.append(threading.current_thread())
            renewal_stops.append(stop)
            try:
                if not model_entered.wait(2):
                    raise RuntimeError("Model did not start before the controlled renewal")
                clock = Mock(spec=threading.Event)
                clock.wait.side_effect = [False, True]
                # Keep the real status classification; only bypass the 45s wait.
                knowledge_lease_renewer(client_arg, lease_id, clock, cancelled)
            except Exception as error:
                background_errors.append(error)
            finally:
                renewal_finished.set()

        def embed(_model, _contents):
            model_entered.set()
            if not renewal_finished.wait(2):
                raise RuntimeError("Controlled renewal did not finish during the model call")
            if model_error:
                raise RuntimeError("controlled model error")
            return [[1.0, 0.0]]

        model.embed.side_effect = embed
        lease = {"leaseId": "lease", "collection": {"embeddingModel": "local"},
                 "document": {"name": "Synthetic"}, "chunks": [{"id": "chunk", "content": "Synthetic input"}]}
        with patch("agat_worker.knowledge_lease_renewer", side_effect=renew), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            execute_knowledge_lease(client, model, lease, dry_run=False)
        self.assertEqual(background_errors, [])
        model.embed.assert_called_once_with("local", ["Synthetic input"])
        client.knowledge_renew.assert_called_once_with("lease")
        self.assertEqual(len(renewal_threads), 1)
        self.assertFalse(renewal_threads[0].is_alive())
        self.assertTrue(renewal_stops[0].is_set())
        return client

    def test_lost_lease_discards_successful_model_result(self) -> None:
        client = self.execute_during_renewal(404)
        client.knowledge_complete.assert_not_called()
        client.knowledge_fail.assert_not_called()

    def test_lost_lease_discards_model_error_without_failing_new_owner(self) -> None:
        client = self.execute_during_renewal(404, model_error=True)
        client.knowledge_complete.assert_not_called()
        client.knowledge_fail.assert_not_called()

    def test_transient_renewal_error_allows_successful_completion(self) -> None:
        client = self.execute_during_renewal(503)
        client.knowledge_complete.assert_called_once_with("lease", [{"chunkId": "chunk", "embedding": [1.0, 0.0]}])
        client.knowledge_fail.assert_not_called()

    def test_transient_renewal_error_preserves_model_failure_reporting(self) -> None:
        client = self.execute_during_renewal(503, model_error=True)
        client.knowledge_complete.assert_not_called()
        client.knowledge_fail.assert_called_once_with("lease", "RuntimeError: controlled model error")


if __name__ == "__main__":
    unittest.main()
