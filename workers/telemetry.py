"""Optional OpenTelemetry tracing for the portable AGAT worker.

Imports stay lazy so the dependency-free ``single`` worker mode keeps working
when telemetry is disabled and workers/requirements.txt is not installed.
"""

from __future__ import annotations

import contextlib
import os
import time
import urllib.parse
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, MutableMapping


def _enabled_from_env() -> bool:
    raw = os.getenv("AGAT_OTEL_ENABLED")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(
        os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    )


def _exporter_endpoint() -> str | None:
    traces_endpoint = (os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or "").strip()
    if traces_endpoint:
        return traces_endpoint
    base_endpoint = (os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip().rstrip("/")
    return f"{base_endpoint}/v1/traces" if base_endpoint else None


class _NoopSpan:
    def set_attribute(self, _name: str, _value: Any) -> None:
        return

    def record_exception(self, _error: BaseException) -> None:
        return

    def set_status(self, _status: Any) -> None:
        return


@dataclass
class ExecutionMetrics:
    started_at: float
    model: str = ""
    provider: str = "openai-compatible"
    tool_schema_version: str = "none"
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tokens_observed: bool = False
    tool_calls: int = 0
    model_duration_ms: float = 0
    power_watts: float | None = None

    @classmethod
    def start(cls, model: str, tool_schema_version: str) -> "ExecutionMetrics":
        raw_power = os.getenv("AGAT_WORKER_POWER_WATTS", "").strip()
        try:
            power_watts = max(0.0, float(raw_power)) if raw_power else None
        except ValueError:
            power_watts = None
        return cls(
            started_at=time.monotonic(),
            model=model,
            tool_schema_version=tool_schema_version,
            power_watts=power_watts,
        )

    def record_model(
        self,
        input_tokens: int | None,
        output_tokens: int | None,
        duration_ms: float | None = None,
    ) -> None:
        self.model_calls += 1
        if input_tokens is not None:
            self.input_tokens += max(0, input_tokens)
            self.tokens_observed = True
        if output_tokens is not None:
            self.output_tokens += max(0, output_tokens)
            self.tokens_observed = True
        if duration_ms is not None:
            self.model_duration_ms += max(0.0, duration_ms)

    def payload(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "durationMs": max(0, round((time.monotonic() - self.started_at) * 1000)),
            "modelCalls": self.model_calls,
            "toolCalls": self.tool_calls,
            "model": self.model,
            "provider": self.provider,
            "toolSchemaVersion": self.tool_schema_version,
        }
        if self.tokens_observed:
            result["inputTokens"] = self.input_tokens
            result["outputTokens"] = self.output_tokens
        if self.model_duration_ms > 0:
            result["modelDurationMs"] = round(self.model_duration_ms)
            if self.power_watts is not None:
                result["energyJoules"] = round(
                    self.power_watts * self.model_duration_ms / 1_000,
                    3,
                )
        return result


_CURRENT_METRICS: ContextVar[ExecutionMetrics | None] = ContextVar(
    "agat_execution_metrics", default=None
)


@contextlib.contextmanager
def use_execution_metrics(metrics: ExecutionMetrics) -> Iterator[ExecutionMetrics]:
    token = _CURRENT_METRICS.set(metrics)
    try:
        yield metrics
    finally:
        _CURRENT_METRICS.reset(token)


def current_execution_metrics() -> ExecutionMetrics | None:
    return _CURRENT_METRICS.get()


class WorkerTelemetry:
    def __init__(self, enabled: bool | None = None, service_name: str | None = None) -> None:
        self.enabled = _enabled_from_env() if enabled is None else enabled
        self._provider: Any = None
        self._tracer: Any = None
        self._propagator: Any = None
        self._span_kind: Any = None
        self._status_code: Any = None
        if not self.enabled:
            return
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.trace import SpanKind, Status, StatusCode
            from opentelemetry.trace.propagation.tracecontext import (
                TraceContextTextMapPropagator,
            )
        except ImportError as error:
            raise RuntimeError(
                "OpenTelemetry включён, но зависимости не установлены. "
                "Установите workers/requirements.txt."
            ) from error

        endpoint = _exporter_endpoint()
        exporter = OTLPSpanExporter(endpoint=endpoint) if endpoint else OTLPSpanExporter()
        self._provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": service_name
                    or os.getenv("OTEL_SERVICE_NAME", "agat-worker"),
                    "service.version": "1.6.0",
                }
            )
        )
        self._provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(self._provider)
        self._tracer = trace.get_tracer("io.agat.worker", "1.6.0")
        self._propagator = TraceContextTextMapPropagator()
        self._span_kind = SpanKind
        self._status_code = (Status, StatusCode)

    def inject(self, headers: MutableMapping[str, str]) -> None:
        if self.enabled:
            self._propagator.inject(headers)

    @contextlib.contextmanager
    def lease_span(self, lease: dict[str, Any]) -> Iterator[Any]:
        if not self.enabled:
            yield _NoopSpan()
            return
        carrier = lease.get("traceContext")
        parent = self._propagator.extract(carrier=carrier if isinstance(carrier, dict) else {})
        agent = lease.get("agent", {})
        stage = lease.get("stage", {})
        run = lease.get("run", {})
        runtime_config = agent.get("runtimeConfig")
        runtime_profile = (
            str(runtime_config.get("profile", "tool_loop_v1"))
            if isinstance(runtime_config, dict)
            else "tool_loop_v1"
        )
        specialists = agent.get("specialists")
        attributes: dict[str, Any] = {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": str(agent.get("name", "agent")),
            "agat.agent.id": str(agent.get("id", "")),
            "agat.agent.runtime": str(agent.get("runtime", "single")),
            "agat.agent.runtime_profile": runtime_profile,
            "agat.agent.specialist_count": len(specialists) if isinstance(specialists, list) else 0,
            "agat.run.id": str(run.get("id", "")),
            "agat.stage.id": str(stage.get("id", "")),
            "agat.stage.attempt": int(stage.get("attempt", 1)),
        }
        model = agent.get("model")
        if isinstance(model, str) and model:
            attributes["gen_ai.request.model"] = model
        with self._tracer.start_as_current_span(
            f"invoke_agent {agent.get('name', 'agent')}",
            context=parent,
            kind=self._span_kind.INTERNAL,
            attributes=attributes,
        ) as span:
            try:
                yield span
            except Exception as error:
                self._mark_error(span, error)
                raise

    @contextlib.contextmanager
    def model_span(self, model: str, endpoint: str) -> Iterator[Any]:
        if not self.enabled:
            yield _NoopSpan()
            return
        parsed = urllib.parse.urlsplit(endpoint)
        attributes: dict[str, Any] = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "openai-compatible",
            "gen_ai.request.model": model,
        }
        if parsed.hostname:
            attributes["server.address"] = parsed.hostname
        if parsed.port:
            attributes["server.port"] = parsed.port
        with self._tracer.start_as_current_span(
            f"chat {model}",
            kind=self._span_kind.CLIENT,
            attributes=attributes,
        ) as span:
            try:
                yield span
            except Exception as error:
                self._mark_error(span, error)
                raise

    @contextlib.contextmanager
    def tool_span(self, name: str, audit: dict[str, Any] | None = None) -> Iterator[Any]:
        if not self.enabled:
            yield _NoopSpan()
            return
        attributes: dict[str, Any] = {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": name,
        }
        host = (audit or {}).get("host")
        if isinstance(host, str) and host:
            attributes["server.address"] = host
        with self._tracer.start_as_current_span(
            f"execute_tool {name}",
            kind=self._span_kind.INTERNAL,
            attributes=attributes,
        ) as span:
            try:
                yield span
            except Exception as error:
                self._mark_error(span, error)
                raise

    def _mark_error(self, span: Any, error: BaseException) -> None:
        status, status_code = self._status_code
        error_type = type(error).__name__
        span.set_attribute("error.type", error_type)
        span.set_status(status(status_code.ERROR, error_type))

    def mark_failure(self, span: Any, error_type: str) -> None:
        if not self.enabled:
            return
        status, status_code = self._status_code
        span.set_attribute("error.type", error_type)
        span.set_status(status(status_code.ERROR, error_type))

    def shutdown(self) -> None:
        if self._provider is not None:
            self._provider.shutdown()
