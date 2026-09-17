import assert from "node:assert/strict";
import test from "node:test";

import {
  approvalDecisionAuditData,
  parseApprovalDecision,
  withApprovalDeadline,
} from "../src/approval-decisions.js";

const approval = {
  kind: "mcp_tool",
  callId: "call-1",
  stageId: "stage-1",
  runId: "run-1",
  runName: "MCP · finance",
  agentName: "finance.update_forecast",
  summary: "Обновить прогноз",
  toolName: "finance.update_forecast",
  risk: "write",
  riskTier: "high",
  policy: "approval",
  requiredApprovals: 2,
  approvalCount: 1,
  approvers: ["Мария"],
  policyVersion: 4,
  policySha256: "abc123",
  policyRuleId: "finance_write",
  policyReason: "Изменяет плановые значения",
  arguments: { apiToken: "[redacted]" },
  previewDiff: [{ op: "replace", path: "/revenue", before: 1, after: 2 }],
  createdAt: "2026-09-01T09:50:00.000Z",
};

test("approve допускает комментарий, но не сохраняет rejection reason", () => {
  assert.deepEqual(parseApprovalDecision({ decision: "approve", comment: "  Проверено  ", reason: "не используется" }), {
    decision: "approve",
    comment: "Проверено",
    reason: null,
  });
});

test("reject требует непустую причину и ограничивает длину", () => {
  assert.throws(() => parseApprovalDecision({ decision: "reject" }), /причину/u);
  assert.throws(() => parseApprovalDecision({ decision: "reject", reason: "x".repeat(1_001) }), /1000/u);
  assert.deepEqual(parseApprovalDecision({ decision: "reject", reason: "  Неверная сумма  " }), {
    decision: "reject",
    comment: null,
    reason: "Неверная сумма",
  });
});

test("MCP deadline следует bounded TTL, stage не получает вымышленный срок", () => {
  assert.equal(withApprovalDeadline(approval, 600)?.expiresAt, "2026-09-01T10:00:00.000Z");
  assert.equal(withApprovalDeadline({ kind: "stage", stageId: "stage-2" }, 600)?.expiresAt, null);
});

test("audit payload сохраняет actor, note и только redacted snapshot", () => {
  const data = approvalDecisionAuditData({
    projectId: "main",
    approval,
    decision: parseApprovalDecision({ decision: "reject", reason: "Нужна сверка", comment: "Повторить после закрытия дня" }),
    actorSubject: "operator-42",
    actorDisplay: "Оператор",
    mcpApprovalTtlSeconds: 600,
  });
  assert.equal(data.approvalId, "mcp:call-1");
  assert.equal(data.reason, "Нужна сверка");
  assert.equal(data.actor, "Оператор");
  assert.match(JSON.stringify(data.snapshot), /\[redacted\]/u);
  assert.equal(JSON.stringify(data).includes("raw-secret"), false);
});

test("финальный MCP approve отражает нового approver в resolved snapshot", () => {
  const data = approvalDecisionAuditData({
    projectId: "main",
    approval,
    decision: parseApprovalDecision({ decision: "approve", comment: "Сверено" }),
    outcome: { callId: "call-1", status: "completed" },
    actorSubject: "operator-43",
    actorDisplay: "Олег",
    mcpApprovalTtlSeconds: 600,
  });
  const snapshot = data.snapshot as Record<string, unknown>;
  assert.equal(snapshot.approvalCount, 2);
  assert.deepEqual(snapshot.approvers, ["Мария", "Олег"]);
});
