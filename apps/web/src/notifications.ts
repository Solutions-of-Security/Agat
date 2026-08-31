import type { Overview, ViewId } from "./types";

export type NotificationTone = "action" | "warning" | "error";

export interface AppNotification {
  id: string;
  tone: NotificationTone;
  title: string;
  message: string;
  source: string;
  createdAt: string;
  targetView: Extract<ViewId, "overview" | "runs" | "approvals">;
  runId: string | null;
}

type NotificationSource = Pick<Overview, "approvals" | "events" | "generatedAt" | "runs">;

function timestamp(value: string): number {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function buildNotifications(source: NotificationSource): AppNotification[] {
  const runById = new Map(source.runs.map((run) => [run.id, run]));
  const approvalRunIds = new Set(source.approvals.map((approval) => approval.runId));

  const approvals: AppNotification[] = source.approvals.map((approval) => {
    const run = runById.get(approval.runId);
    const mcp = approval.kind === "mcp_tool";
    return {
      id: `approval:${mcp ? approval.callId : approval.stageId}`,
      tone: "action",
      title: mcp ? `MCP: требуется решение` : "Требуется согласование",
      message: `${approval.runName} · ${approval.agentName}. ${approval.summary}`,
      source: mcp ? approval.toolName : "Запуск",
      createdAt: mcp ? approval.createdAt : run?.updatedAt ?? source.generatedAt,
      targetView: "approvals",
      runId: approval.runId,
    };
  });

  const events: AppNotification[] = source.events
    .filter((event) => event.level === "warn" || event.level === "error")
    .filter((event) => !(event.runId && approvalRunIds.has(event.runId) && event.type.toLowerCase().includes("approval")))
    .slice(-20)
    .map((event) => {
      const run = event.runId ? runById.get(event.runId) : null;
      return {
        id: `event:${event.id}`,
        tone: event.level === "error" ? "error" : "warning",
        title: event.level === "error" ? "Ошибка выполнения" : "Требует внимания",
        message: event.message,
        source: run?.name ?? "Системное событие",
        createdAt: event.createdAt,
        targetView: event.runId ? "runs" : "overview",
        runId: event.runId,
      };
    });

  return [...approvals, ...events]
    .sort((left, right) => timestamp(right.createdAt) - timestamp(left.createdAt))
    .slice(0, 24);
}

export function unreadNotificationIds(
  notifications: readonly AppNotification[],
  seenIds: ReadonlySet<string>,
): string[] {
  return notifications.filter((notification) => !seenIds.has(notification.id)).map((notification) => notification.id);
}

export function notificationStorageKey(projectId: string): string {
  return `agat:notifications:v1:${projectId}`;
}
