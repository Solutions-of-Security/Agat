import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { InMemorySpanExporter } from "@opentelemetry/sdk-trace-node";

import { CoordinatorTelemetry } from "../src/telemetry.js";

describe("OpenTelemetry export", () => {
  it("exports a connected run and dispatch trace without prompt content", async () => {
    const exporter = new InMemorySpanExporter();
    const telemetry = new CoordinatorTelemetry({
      enabled: true,
      serviceName: "agat-coordinator-test",
      exporterEndpoint: "",
      exporter,
    });
    const run = telemetry.startRun({ runId: "run-1", projectId: "default" });
    const stage = telemetry.startStage({
      leaseId: "lease-1",
      runId: "run-1",
      stageId: "stage-1",
      traceId: run.traceId,
      parentSpanId: run.spanId,
      agentName: "Collector",
      agentId: "collector",
      model: "test-model",
      runtime: "single",
      runtimeProfile: "tool_loop_v1",
      specialistCount: 0,
      attempt: 1,
      nodeId: "node-1",
    });
    assert.equal(stage.traceId, run.traceId);
    assert.match(stage.traceparent, /^00-[0-9a-f]{32}-[0-9a-f]{16}-01$/);
    await telemetry.withMcpRequest({
      serverName: "CRM",
      toolName: "crm__delete_customer",
      callId: "call-1",
      risk: "destructive",
      riskTier: "critical",
      requiredApprovals: 2,
      policyVersion: 4,
      policySha256: "policy-sha",
      traceparent: stage.traceparent,
    }, async () => ({ ok: true }));
    telemetry.endStage("lease-1", { status: "completed", attributes: { "agat.model.calls": 1 } });
    telemetry.endRun("run-1", { status: "completed" });
    await telemetry.forceFlush();

    const spans = exporter.getFinishedSpans();
    const runSpan = spans.find((span) => span.name === "invoke_workflow agat");
    const stageSpan = spans.find((span) => span.name === "dispatch_agent Collector");
    const mcpSpan = spans.find((span) => span.name === "execute_tool crm__delete_customer");
    assert.ok(runSpan);
    assert.ok(stageSpan);
    assert.ok(mcpSpan);
    assert.equal(stageSpan.spanContext().traceId, runSpan.spanContext().traceId);
    assert.equal(stageSpan.parentSpanContext?.spanId, runSpan.spanContext().spanId);
    assert.equal(stageSpan.attributes["agat.model.calls"], 1);
    assert.equal(stageSpan.attributes["agat.agent.runtime_profile"], "tool_loop_v1");
    assert.equal(stageSpan.attributes["agat.agent.specialist_count"], 0);
    assert.equal("gen_ai.input.messages" in stageSpan.attributes, false);
    assert.equal("gen_ai.output.messages" in stageSpan.attributes, false);
    assert.equal(mcpSpan.attributes["agat.mcp.risk_tier"], "critical");
    assert.equal(mcpSpan.attributes["agat.mcp.required_approvals"], 2);
    assert.equal(mcpSpan.attributes["agat.mcp.policy.version"], 4);
    assert.equal(mcpSpan.attributes["agat.mcp.policy.sha256"], "policy-sha");

    await telemetry.shutdown();
  });
});
