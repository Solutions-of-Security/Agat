import assert from "node:assert/strict";
import test from "node:test";

import {
  approvalActionLabel,
  approvalIdentity,
  buildApprovalInboxItems,
  filterApprovalInboxItems,
  formatApprovalDeadline,
} from "../src/approvalInbox.ts";
import type { AgatEvent, Approval, Overview } from "../src/types.ts";

const now = new Date("2026-09-01T10:00:00.000Z").getTime();
const run = {
  id: "run-1",
  name: "Финансовый прогноз",
  createdAt: "2026-09-01T09:40:00.000Z",
  updatedAt: "2026-09-01T09:48:00.000Z",
  stages: [{ id: "stage-1", agent: { name: "Аналитик" } }],
} as Overview["runs"][number];

const stageApproval: Approval = {
  kind: "stage",
  stageId: "stage-2",
  runId: run.id,
  runName: run.name,
  agentName: "Редактор",
  summary: "Опубликует итоговый отчёт.",
};

const mcpApproval: Approval = {
  kind: "mcp_tool",
  callId: "call-1",
  stageId: "stage-1",
  runId: run.id,
  runName: "MCP · finance",
  agentName: "finance.update_forecast",
  summary: "Обновит прогноз",
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
  arguments: { token: "[redacted]" },
  previewDiff: [{ op: "replace", path: "/revenue", before: 1, after: 2 }],
  createdAt: "2026-09-01T09:50:00.000Z",
  expiresAt: "2026-09-01T10:05:00.000Z",
};

test("inbox даёт запросам устойчивый id и конкретный action label", () => {
  assert.equal(approvalIdentity(mcpApproval), "mcp:call-1");
  assert.equal(approvalIdentity(stageApproval), "stage:stage-2");
  assert.equal(approvalActionLabel(mcpApproval), "Разрешить вызов finance.update_forecast");
  assert.match(approvalActionLabel(stageApproval), /Разрешить Редактор/u);
});

test("pending MCP содержит context, deadline и audit", () => {
  const events: AgatEvent[] = [{
    id: 1,
    runId: run.id,
    stageId: "stage-1",
    nodeId: null,
    level: "warn",
    type: "mcp.approval.requested",
    message: "Нужно решение",
    data: { callId: "call-1" },
    createdAt: mcpApproval.createdAt,
  }];
  const [item] = buildApprovalInboxItems({ approvals: [mcpApproval], runs: [run], events, generatedAt: new Date(now).toISOString() });
  assert.equal(item?.status, "pending");
  assert.equal(item?.requester, "Аналитик");
  assert.equal(item?.target, "finance · finance.update_forecast");
  assert.equal(item?.audit[0]?.title, "Запрос создан");
  assert.equal(formatApprovalDeadline(item?.expiresAt ?? null, now), "Осталось 5 мин");
});

test("решённый запрос восстанавливается из безопасного snapshot без action", () => {
  const events: AgatEvent[] = [{
    id: 2,
    runId: null,
    stageId: null,
    nodeId: null,
    level: "warn",
    type: "approval.decision.recorded",
    message: "Отклонено",
    data: {
      approvalId: "stage:stage-2",
      stageId: "stage-2",
      runId: run.id,
      decision: "reject",
      actor: "Оператор",
      comment: "Проверено вместе с владельцем бюджета",
      reason: "Нужно сверить сумму",
      snapshot: stageApproval,
    },
    createdAt: "2026-09-01T09:58:00.000Z",
  }];
  const [item] = buildApprovalInboxItems({ approvals: [], runs: [run], events, generatedAt: new Date(now).toISOString() });
  assert.equal(item?.status, "rejected");
  assert.equal(item?.decisionActor, "Оператор");
  assert.equal(item?.decisionComment, "Проверено вместе с владельцем бюджета");
  assert.equal(item?.rejectionReason, "Нужно сверить сумму");
  assert.equal(item?.audit.at(-1)?.title, "Решение: отклонено");
  assert.match(item?.audit.at(-1)?.note ?? "", /Комментарий: Проверено вместе/u);
});

test("pending запись имеет приоритет над промежуточным multi-approver решением", () => {
  const event: AgatEvent = {
    id: 3,
    runId: null,
    stageId: null,
    nodeId: null,
    level: "info",
    type: "approval.decision.recorded",
    message: "Согласовано",
    data: { approvalId: "mcp:call-1", callId: "call-1", decision: "approve", actor: "Мария", snapshot: mcpApproval },
    createdAt: "2026-09-01T09:55:00.000Z",
  };
  const [item] = buildApprovalInboxItems({ approvals: [mcpApproval], runs: [run], events: [event], generatedAt: new Date(now).toISOString() });
  assert.equal(item?.status, "pending");
  assert.ok(item?.audit.some((entry) => entry.title === "Решение: согласовано"));
});

test("risk, type, deadline и state filters комбинируются", () => {
  const items = buildApprovalInboxItems({ approvals: [stageApproval, mcpApproval], runs: [run], events: [], generatedAt: new Date(now).toISOString() });
  const filtered = filterApprovalInboxItems(items, {
    search: "finance",
    state: "pending",
    risk: "high",
    kind: "mcp_tool",
    deadline: "soon",
  }, now);
  assert.deepEqual(filtered.map((item) => item.id), ["mcp:call-1"]);
});

test("история формы сохраняет схему и ответы отдельно для каждого повторного согласования", () => {
  const form = { title: "Проверка", description: "", fields: [{ id: "decision", label: "Решение", type: "text" as const, required: true }] };
  const snapshot = { ...stageApproval, mode: "input" as const, form };
  const next = { ...snapshot, stageId: "stage-next" };
  const event: AgatEvent = {
    id: 10, runId: null, stageId: null, nodeId: null, level: "info", type: "approval.decision.recorded", message: "Данные приняты",
    data: { approvalId: "stage:stage-2", stageId: "stage-2", runId: run.id, decision: "approve", actor: "Редактор", snapshot, formData: { decision: "Доработать" } },
    createdAt: "2026-09-01T09:58:00.000Z",
  };
  const items = buildApprovalInboxItems({ approvals: [next], runs: [run], events: [event], generatedAt: new Date(now).toISOString() });
  assert.equal(items[0]?.status, "pending");
  assert.equal(items[0]?.id, "stage:stage-next");
  assert.equal(items[0]?.formData, undefined);
  const resolved = items.find((item) => item.id === "stage:stage-2")!;
  assert.equal(resolved.status, "approved");
  assert.deepEqual(resolved.formData, { decision: "Доработать" });
  assert.ok(resolved.approval.kind === "stage");
  assert.deepEqual(resolved.approval.form, form);
  assert.equal(resolved.kindLabel, "Ввод данных");
});
