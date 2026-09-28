"""RAG query embedding must observe its ordinary process lease cancellation."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import io
import threading
import unittest
from unittest.mock import Mock, patch

from agat_worker import CoordinatorClient, LocalModelClient, execute_lease
from embedding_transport import EmbeddingSessionPool
from test_embedding_http_deadline import slow_endpoint
from test_embedding_transport import observed_processes


def query_lease(groups=1):
    return {"leaseId": "query-lease", "agent": {"name": "primary", "model": "local", "systemPrompt": "Answer.", "runtime": "single"},
            "run": {"name": "synthetic", "input": "synthetic query"}, "stage": {"attempt": 1},
            "knowledge": {"groups": [{"embeddingModel": "embedding", "collectionIds": [f"collection-{i}"], "topK": 2}
                                     for i in range(groups)], "memory": []}}


class RagQueryCancellationTests(unittest.TestCase):
    def test_cancelled_query_reaps_helper_before_reusing_both_transports(self):
        for transport in ("isolated", "session"):
            for mode in ("headers", "body", "error"):
                with self.subTest(transport=transport, mode=mode), slow_endpoint(mode) as (url, entered, release), \
                        observed_processes() as children, EmbeddingSessionPool(1) as sessions, ThreadPoolExecutor(max_workers=1) as executor:
                    coordinator = Mock(spec=CoordinatorClient)
                    coordinator.knowledge_search.return_value = {"hits": []}
                    model = LocalModelClient(url, "", coordinator_client=coordinator,
                                             embedding_request=sessions.request if transport == "session" else None)
                    cancelled = threading.Event()
                    future = executor.submit(model.retrieve_knowledge, query_lease(), cancelled=cancelled)
                    try:
                        self.assertTrue(entered.wait(3))
                        cancelled.set()
                        with self.assertRaisesRegex(RuntimeError, "Embedding request cancelled"):
                            future.result(timeout=2)
                        self.assertFalse(release.is_set())
                        self.assertEqual(len(children), 1)
                        self.assertIsNotNone(children[0].poll())
                        coordinator.knowledge_search.assert_not_called()
                    finally:
                        release.set()
                    # Header/body fixtures become valid embedding endpoints after release.
                    if mode != "error":
                        self.assertEqual(executor.submit(model.retrieve_knowledge, query_lease()).result(timeout=3), "")
                        coordinator.knowledge_search.assert_called_once()
                        self.assertEqual(len(children), 2)

    def test_real_worker_passes_ordinary_lease_event_before_primary(self):
        with slow_endpoint("headers") as (url, entered, release), observed_processes(), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), ThreadPoolExecutor(max_workers=1) as executor:
            coordinator = Mock(spec=CoordinatorClient)
            model = LocalModelClient(url, "", coordinator_client=coordinator)
            model.complete = Mock()

            def renew(_client, _lease, _stop, cancelled):
                if entered.wait(3):
                    cancelled.set()

            with patch("agat_worker.lease_renewer", side_effect=renew):
                future = executor.submit(execute_lease, coordinator, model, query_lease(), "local", False)
                try:
                    self.assertTrue(entered.wait(3))
                    future.result(timeout=2)
                    self.assertFalse(release.is_set())
                finally:
                    release.set()
            model.complete.assert_not_called()
            coordinator.knowledge_search.assert_not_called()
            coordinator.complete.assert_not_called()
            coordinator.record_decision_shadow.assert_not_called()

    def test_cancellation_between_groups_prevents_later_embedding_and_retrieval(self):
        cancelled = threading.Event()
        coordinator = Mock(spec=CoordinatorClient)
        model = LocalModelClient("http://unused.invalid/v1", "", coordinator_client=coordinator)

        def first_embedding(*_args, **kwargs):
            self.assertIs(kwargs["cancelled"], cancelled)
            cancelled.set()
            return [[1, 0]]

        with patch.object(model, "embed", side_effect=first_embedding) as embed:
            with self.assertRaisesRegex(RuntimeError, "Knowledge retrieval cancelled"):
                model.retrieve_knowledge(query_lease(2), cancelled=cancelled)
        embed.assert_called_once()
        coordinator.knowledge_search.assert_not_called()

    def test_precancel_and_direct_complete_cannot_start_query_or_primary(self):
        cancelled = threading.Event()
        cancelled.set()
        coordinator = Mock(spec=CoordinatorClient)
        model = LocalModelClient("http://unused.invalid/v1", "", coordinator_client=coordinator)
        with patch.object(model, "embed") as embed, patch.object(model, "_chat") as primary:
            with self.assertRaisesRegex(RuntimeError, "Knowledge retrieval cancelled"):
                model.retrieve_knowledge(query_lease(), cancelled=cancelled)
            with self.assertRaisesRegex(RuntimeError, "Knowledge retrieval cancelled"):
                model.complete(query_lease(), "local", cancelled=cancelled)
            embed.assert_not_called()
            primary.assert_not_called()
            coordinator.knowledge_search.assert_not_called()

    def test_query_result_after_cancellation_is_not_passed_to_primary(self):
        cancelled = threading.Event()
        coordinator = Mock(spec=CoordinatorClient)
        model = LocalModelClient("http://unused.invalid/v1", "", coordinator_client=coordinator)

        def search(*_args, **_kwargs):
            cancelled.set()
            return {"hits": [{"content": "late result"}]}

        coordinator.knowledge_search.side_effect = search
        with patch.object(model, "embed", return_value=[[1, 0]]), patch.object(model, "_chat") as primary:
            with self.assertRaisesRegex(RuntimeError, "Knowledge retrieval cancelled"):
                model.complete(query_lease(), "local", cancelled=cancelled)
            primary.assert_not_called()


if __name__ == "__main__":
    unittest.main()
