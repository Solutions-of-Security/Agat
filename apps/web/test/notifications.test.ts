import assert from "node:assert/strict";
import test from "node:test";

import { buildNotifications, notificationStorageKey, unreadNotificationIds } from "../src/notifications.ts";
import type { Overview } from "../src/types.ts";

const run = {
  id: "run-1",
  name: "Проверка релиза",
  updatedAt: "2026-08-31T12:00:00.000Z",
} as Overview["runs"][number];

test("notification feed объединяет согласования и важные события в обратном порядке", () => {
  const notifications = buildNotifications({
    generatedAt: "2026-08-31T14:00:00.000Z",
    runs: [run],
    approvals: [{
      kind: "stage",
      stageId: "stage-1",
      runId: run.id,
      runName: run.name,
      agentName: "Редактор",
      summary: "Разрешить публикацию отчёта",
    }],
    events: [
      { id: 10, runId: run.id, stageId: null, nodeId: null, level: "warn", type: "approval.waiting", message: "Ожидается решение", data: null, createdAt: "2026-08-31T12:30:00.000Z" },
      { id: 11, runId: run.id, stageId: null, nodeId: null, level: "error", type: "run.failed", message: "Модель недоступна", data: null, createdAt: "2026-08-31T13:00:00.000Z" },
      { id: 12, runId: null, stageId: null, nodeId: null, level: "info", type: "worker.online", message: "Узел подключён", data: null, createdAt: "2026-08-31T13:30:00.000Z" },
    ],
  });

  assert.deepEqual(notifications.map((notification) => notification.id), ["event:11", "approval:stage-1"]);
  assert.equal(notifications[0]?.targetView, "runs");
  assert.equal(notifications[1]?.targetView, "approvals");
  assert.match(notifications[1]?.message ?? "", /Редактор/u);
});

test("unread state хранит только идентификаторы и изолируется по проекту", () => {
  const notifications = [
    { id: "one" },
    { id: "two" },
  ] as ReturnType<typeof buildNotifications>;

  assert.deepEqual(unreadNotificationIds(notifications, new Set(["one"])), ["two"]);
  assert.equal(notificationStorageKey("main"), "agat:notifications:v1:main");
  assert.notEqual(notificationStorageKey("main"), notificationStorageKey("audit"));
});
