import assert from "node:assert/strict";
import test from "node:test";

import {
  canAccessView,
  formatAppRoute,
  getBreadcrumbs,
  getMobilePinnedView,
  getNavigationGroups,
  getPrimaryNavigation,
  getRoleCapabilities,
  parseAppRoute,
} from "../src/navigation.ts";
import type { AgatRole, ViewId } from "../src/types.ts";

const roles: AgatRole[] = ["admin", "designer", "operator", "viewer", "auditor"];

const expectedGroupedViews: Record<AgatRole, ViewId[]> = {
  admin: ["agents", "processes", "knowledge", "evals", "nodes", "models", "fleet", "tools", "a2a"],
  designer: ["agents", "processes", "knowledge", "evals", "tools", "a2a"],
  operator: ["knowledge", "evals", "tools", "a2a"],
  viewer: [],
  auditor: ["knowledge", "evals", "nodes", "fleet", "tools", "a2a"],
};

test("каждая роль получает не более пяти пунктов первого уровня", () => {
  for (const role of roles) {
    assert.deepEqual(
      getPrimaryNavigation([role]).map((item) => item.id),
      ["overview", "runs", "approvals"],
    );
    assert.ok(getPrimaryNavigation([role]).length + getNavigationGroups([role]).length <= 5);
  }
});

test("группы навигации соответствуют матрице ролей", () => {
  for (const role of roles) {
    const actual = getNavigationGroups([role]).flatMap((group) => group.items.map((item) => item.id));
    assert.deepEqual(actual, expectedGroupedViews[role]);
  }
});

test("скрытые deep links отклоняются той же матрицей", () => {
  for (const role of roles) {
    const visible = new Set<ViewId>(["overview", "runs", "approvals", ...expectedGroupedViews[role]]);
    const allViews: ViewId[] = [
      "overview", "runs", "approvals", "agents", "processes", "knowledge", "evals", "nodes", "models", "fleet", "tools", "a2a",
    ];
    for (const view of allViews) assert.equal(canAccessView(view, [role]), visible.has(view), `${role}:${view}`);
  }
});

test("read-only роли не получают изменяющие действия", () => {
  for (const role of ["viewer", "auditor"] as const) {
    assert.deepEqual(getRoleCapabilities([role]), {
      canCreateRuns: false,
      canCancelRuns: false,
      canDecideApprovals: false,
      canDesign: false,
      canManageScheduler: false,
      canReplayRuns: false,
    });
  }

  assert.deepEqual(getRoleCapabilities(["operator"]), {
    canCreateRuns: true,
    canCancelRuns: true,
    canDecideApprovals: true,
    canDesign: false,
    canManageScheduler: false,
    canReplayRuns: true,
  });

  assert.deepEqual(getRoleCapabilities(["admin"]), {
    canCreateRuns: true,
    canCancelRuns: true,
    canDecideApprovals: true,
    canDesign: true,
    canManageScheduler: true,
    canReplayRuns: true,
  });
});

test("run deep links сохраняют выбранный запуск и безопасно разбираются", () => {
  const runId = "run/отчёт 42";
  const hash = formatAppRoute("runs", runId);

  assert.equal(hash, "#runs/run%2F%D0%BE%D1%82%D1%87%D1%91%D1%82%2042");
  assert.deepEqual(parseAppRoute(hash), { view: "runs", runId });
  assert.deepEqual(parseAppRoute("#approvals/approval-run"), { view: "approvals", runId: "approval-run" });
  assert.deepEqual(parseAppRoute("#runs"), { view: "runs", runId: null });
  assert.deepEqual(parseAppRoute("#runs/broken/extra"), { view: "runs", runId: null });
  assert.deepEqual(parseAppRoute("#runs/%E0%A4%A"), { view: "runs", runId: null });
  assert.deepEqual(parseAppRoute("#unknown"), { view: "overview", runId: null });
});

test("закреплённый mobile-раздел всегда доступен роли", () => {
  const expected: Record<AgatRole, ViewId | null> = {
    admin: "processes",
    designer: "processes",
    operator: "knowledge",
    viewer: null,
    auditor: "evals",
  };

  for (const role of roles) {
    const pinned = getMobilePinnedView([role]);
    assert.equal(pinned, expected[role]);
    if (pinned) assert.equal(canAccessView(pinned, [role]), true);
  }
});

test("navigation ids не дублируются, breadcrumbs сохраняют контекст", () => {
  for (const role of roles) {
    const ids = [
      ...getPrimaryNavigation([role]),
      ...getNavigationGroups([role]).flatMap((group) => group.items),
    ].map((item) => item.id);
    assert.equal(new Set(ids).size, ids.length);
  }

  assert.deepEqual(getBreadcrumbs("overview"), ["Обзор"]);
  assert.deepEqual(getBreadcrumbs("processes"), ["Обзор", "Создание", "Процессы"]);
  assert.deepEqual(getBreadcrumbs("fleet"), ["Обзор", "Администрирование", "Инфраструктура", "Fleet / HA"]);
});
