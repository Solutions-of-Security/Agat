import { randomBytes } from "node:crypto";
import { createRequire } from "node:module";

import {
  context,
  SpanKind,
  SpanStatusCode,
  trace,
  TraceFlags,
  type Span,
  type SpanContext,
} from "@opentelemetry/api";
import type { NodeTracerProvider, SpanExporter } from "@opentelemetry/sdk-trace-node";

const require = createRequire(import.meta.url);

const INSTRUMENTATION_NAME = "io.agat.coordinator";
const INSTRUMENTATION_VERSION = "1.4.0";

export interface TelemetryOptions {
  enabled: boolean;
  serviceName: string;
  exporterEndpoint: string;
  exporter?: SpanExporter;
}

export interface RunSpanInput {
  runId: string;
  projectId: string;
  replayOfRunId?: string | null;
  evaluationGroupId?: string | null;
  traceparent?: string | null;
}

export interface StageSpanInput {
  leaseId: string;
  runId: string;
  stageId: string;
  traceId: string;
  parentSpanId: string;
  agentName: string;
  agentId: string;
  model: string | null;
  runtime: string;
  runtimeProfile: string;
  specialistCount: number;
  attempt: number;
  nodeId: string;
}

export interface TraceIdentity {
  traceId: string;
  spanId: string;
  traceparent: string;
}

export interface SpanCompletion {
  status: "completed" | "failed" | "cancelled";
  errorType?: string;
  attributes?: Record<string, string | number | boolean>;
}

export interface McpSpanInput {
  serverName: string;
  toolName: string;
  callId: string;
  risk: string;
  riskTier?: string;
  requiredApprovals?: number;
  policyVersion?: number;
  policySha256?: string;
  traceparent?: string | null;
}

function randomHex(bytes: number): string {
  return randomBytes(bytes).toString("hex");
}

function validTraceId(value: string): boolean {
  return /^[0-9a-f]{32}$/.test(value) && value !== "00000000000000000000000000000000";
}

function validSpanId(value: string): boolean {
  return /^[0-9a-f]{16}$/.test(value) && value !== "0000000000000000";
}

function sampledFlag(spanContext: SpanContext): string {
  return (spanContext.traceFlags & TraceFlags.SAMPLED) === TraceFlags.SAMPLED ? "01" : "00";
}

function identityFromContext(spanContext: SpanContext): TraceIdentity {
  return {
    traceId: spanContext.traceId,
    spanId: spanContext.spanId,
    traceparent: `00-${spanContext.traceId}-${spanContext.spanId}-${sampledFlag(spanContext)}`,
  };
}

function generatedIdentity(traceId = randomHex(16)): TraceIdentity {
  const spanId = randomHex(8);
  return { traceId, spanId, traceparent: `00-${traceId}-${spanId}-01` };
}

function remoteParent(traceId: string, spanId: string) {
  const safeTraceId = validTraceId(traceId) ? traceId : randomHex(16);
  const safeSpanId = validSpanId(spanId) ? spanId : randomHex(8);
  return trace.setSpanContext(context.active(), {
    traceId: safeTraceId,
    spanId: safeSpanId,
    traceFlags: TraceFlags.SAMPLED,
    isRemote: true,
  });
}

function traceparentComponents(value: string | null | undefined): { traceId: string; spanId: string; sampled: boolean } | null {
  const match = /^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$/i.exec(value ?? "");
  if (!match?.[1] || !match[2] || !validTraceId(match[1].toLowerCase()) || !validSpanId(match[2].toLowerCase())) {
    return null;
  }
  return {
    traceId: match[1].toLowerCase(),
    spanId: match[2].toLowerCase(),
    sampled: (Number.parseInt(match[3]!, 16) & 1) === 1,
  };
}

function parentFromTraceparent(value: string | null | undefined) {
  const parsed = traceparentComponents(value);
  if (!parsed) return context.active();
  return trace.setSpanContext(context.active(), {
    traceId: parsed.traceId,
    spanId: parsed.spanId,
    traceFlags: parsed.sampled ? TraceFlags.SAMPLED : TraceFlags.NONE,
    isRemote: true,
  });
}

function normalizedExporterUrl(value: string): string | undefined {
  const trimmed = value.trim();
  return trimmed || undefined;
}

export class CoordinatorTelemetry {
  private readonly enabled: boolean;
  private readonly provider: NodeTracerProvider | null;
  private readonly tracer;
  private readonly runSpans = new Map<string, Span>();
  private readonly stageSpans = new Map<string, Span>();

  constructor(options: TelemetryOptions) {
    this.enabled = options.enabled;
    if (options.enabled) {
      const { resourceFromAttributes } = require("@opentelemetry/resources") as typeof import("@opentelemetry/resources");
      const { BatchSpanProcessor, NodeTracerProvider } = require("@opentelemetry/sdk-trace-node") as typeof import("@opentelemetry/sdk-trace-node");
      const exporterUrl = normalizedExporterUrl(options.exporterEndpoint);
      const exporter = options.exporter ?? (() => {
        const { OTLPTraceExporter } = require("@opentelemetry/exporter-trace-otlp-http") as typeof import("@opentelemetry/exporter-trace-otlp-http");
        return new OTLPTraceExporter(exporterUrl ? { url: exporterUrl } : undefined);
      })();
      this.provider = new NodeTracerProvider({
        resource: resourceFromAttributes({
          "service.name": options.serviceName,
          "service.version": INSTRUMENTATION_VERSION,
        }),
        spanProcessors: [new BatchSpanProcessor(exporter)],
      });
      this.provider.register();
    } else {
      this.provider = null;
    }
    this.tracer = trace.getTracer(INSTRUMENTATION_NAME, INSTRUMENTATION_VERSION);
  }

  isEnabled(): boolean {
    return this.enabled;
  }

  startRun(input: RunSpanInput): TraceIdentity {
    if (!this.enabled) return generatedIdentity(traceparentComponents(input.traceparent)?.traceId);
    const span = this.tracer.startSpan("invoke_workflow agat", {
      kind: SpanKind.INTERNAL,
      attributes: {
        "gen_ai.operation.name": "invoke_workflow",
        "gen_ai.workflow.name": "agat.agent_chain",
        "agat.run.id": input.runId,
        "agat.project.id": input.projectId,
        ...(input.replayOfRunId ? { "agat.replay.source_run_id": input.replayOfRunId } : {}),
        ...(input.evaluationGroupId ? { "agat.evaluation.group_id": input.evaluationGroupId } : {}),
      },
    }, parentFromTraceparent(input.traceparent));
    this.runSpans.set(input.runId, span);
    return identityFromContext(span.spanContext());
  }

  startStage(input: StageSpanInput): TraceIdentity {
    if (!this.enabled) return generatedIdentity(validTraceId(input.traceId) ? input.traceId : undefined);
    const span = this.tracer.startSpan(`dispatch_agent ${input.agentName}`, {
      kind: SpanKind.INTERNAL,
      attributes: {
        "agat.stage.operation": "dispatch_agent",
        "agat.agent.name": input.agentName,
        ...(input.model ? { "agat.agent.model": input.model } : {}),
        "agat.agent.id": input.agentId,
        "agat.agent.runtime": input.runtime,
        "agat.agent.runtime_profile": input.runtimeProfile,
        "agat.agent.specialist_count": input.specialistCount,
        "agat.run.id": input.runId,
        "agat.stage.id": input.stageId,
        "agat.stage.attempt": input.attempt,
        "agat.worker.node_id": input.nodeId,
      },
    }, remoteParent(input.traceId, input.parentSpanId));
    this.stageSpans.set(input.leaseId, span);
    return identityFromContext(span.spanContext());
  }

  endStage(leaseId: string, completion: SpanCompletion): void {
    const span = this.stageSpans.get(leaseId);
    if (!span) return;
    this.stageSpans.delete(leaseId);
    for (const [key, value] of Object.entries(completion.attributes ?? {})) span.setAttribute(key, value);
    span.setAttribute("agat.stage.status", completion.status);
    if (completion.status === "completed") {
      span.setStatus({ code: SpanStatusCode.OK });
    } else {
      span.setStatus({ code: SpanStatusCode.ERROR, message: completion.errorType ?? completion.status });
      span.setAttribute("error.type", completion.errorType ?? completion.status);
    }
    span.end();
  }

  endRun(runId: string, completion: SpanCompletion): void {
    const span = this.runSpans.get(runId);
    if (!span) return;
    this.runSpans.delete(runId);
    for (const [key, value] of Object.entries(completion.attributes ?? {})) span.setAttribute(key, value);
    span.setAttribute("agat.run.status", completion.status);
    if (completion.status === "completed") {
      span.setStatus({ code: SpanStatusCode.OK });
    } else {
      span.setStatus({ code: SpanStatusCode.ERROR, message: completion.errorType ?? completion.status });
      span.setAttribute("error.type", completion.errorType ?? completion.status);
    }
    span.end();
  }

  async withMcpRequest<T>(input: McpSpanInput, operation: () => Promise<T>): Promise<T> {
    if (!this.enabled) return operation();
    const parent = parentFromTraceparent(input.traceparent);
    const span = this.tracer.startSpan(`execute_tool ${input.toolName}`, {
      kind: SpanKind.CLIENT,
      attributes: {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": input.toolName,
        "agat.mcp.server.name": input.serverName,
        "agat.mcp.call.id": input.callId,
        "agat.mcp.tool.risk": input.risk,
        "agat.mcp.risk_tier": input.riskTier ?? "unknown",
        "agat.mcp.required_approvals": input.requiredApprovals ?? 0,
        "agat.mcp.policy.version": input.policyVersion ?? 0,
        "agat.mcp.policy.sha256": input.policySha256 ?? "",
      },
    }, parent);
    try {
      const result = await context.with(trace.setSpan(parent, span), operation);
      span.setStatus({ code: SpanStatusCode.OK });
      return result;
    } catch (error) {
      span.setStatus({
        code: SpanStatusCode.ERROR,
        message: error instanceof Error ? error.message.slice(0, 500) : "MCP request failed",
      });
      span.setAttribute("error.type", error instanceof Error ? error.name : "McpRequestError");
      throw error;
    } finally {
      span.end();
    }
  }

  async shutdown(): Promise<void> {
    for (const span of this.stageSpans.values()) span.end();
    for (const span of this.runSpans.values()) span.end();
    this.stageSpans.clear();
    this.runSpans.clear();
    await this.provider?.shutdown();
  }

  async forceFlush(): Promise<void> {
    await this.provider?.forceFlush();
  }
}
