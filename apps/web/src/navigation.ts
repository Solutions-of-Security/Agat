import type { IconName } from "./components/Icon";
import type { AgatRole, ViewId } from "./types";

export interface NavigationItem {
  id: ViewId;
  label: string;
  icon: IconName;
  roles: readonly AgatRole[];
}

export interface NavigationSection {
  id: "infrastructure" | "integrations";
  label: string;
  items: NavigationItem[];
}

export interface NavigationGroup {
  id: "creation" | "administration";
  label: string;
  icon: IconName;
  items: NavigationItem[];
  sections?: NavigationSection[];
}

export interface RoleCapabilities {
  canCreateRuns: boolean;
  canCancelRuns: boolean;
  canDecideApprovals: boolean;
  canDesign: boolean;
  canManageScheduler: boolean;
  canReplayRuns: boolean;
}

export interface AppRoute {
  view: ViewId;
  runId: string | null;
}

const ALL_ROLES: readonly AgatRole[] = ["admin", "designer", "operator", "viewer", "auditor"];
const CREATOR_ROLES: readonly AgatRole[] = ["admin", "designer"];
const WORK_ROLES: readonly AgatRole[] = ["admin", "designer", "operator", "auditor"];
const ADMIN_AUDIT_ROLES: readonly AgatRole[] = ["admin", "auditor"];

export function getRoleCapabilities(roles: readonly AgatRole[]): RoleCapabilities {
  const canDesign = roles.some((role) => role === "admin" || role === "designer");
  const canCreateRuns = canDesign || roles.includes("operator");

  return {
    canCreateRuns,
    canCancelRuns: canCreateRuns,
    canDecideApprovals: roles.some((role) => role === "admin" || role === "operator"),
    canDesign,
    canManageScheduler: roles.includes("admin"),
    canReplayRuns: canCreateRuns,
  };
}

export function parseAppRoute(hash: string): AppRoute {
  const [rawView = "", rawRunId, ...rest] = hash.replace(/^#\/?/, "").split("/");
  const view = rawView as ViewId;
  const knownView = Object.prototype.hasOwnProperty.call(viewMeta, view) ? view : "overview";
  if ((knownView !== "runs" && knownView !== "approvals") || !rawRunId || rest.length > 0) {
    return { view: knownView, runId: null };
  }

  try {
    return { view: knownView, runId: decodeURIComponent(rawRunId) || null };
  } catch {
    return { view: knownView, runId: null };
  }
}

export function formatAppRoute(view: ViewId, runId: string | null = null): string {
  if ((view === "runs" || view === "approvals") && runId) {
    return `#${view}/${encodeURIComponent(runId)}`;
  }
  return `#${view}`;
}

export const primaryNavigation: readonly NavigationItem[] = [
  { id: "overview", label: "Обзор", icon: "dashboard", roles: ALL_ROLES },
  { id: "runs", label: "Запуски", icon: "runs", roles: ALL_ROLES },
  { id: "approvals", label: "Согласования", icon: "check", roles: ALL_ROLES },
];

const creationItems: readonly NavigationItem[] = [
  { id: "agents", label: "Агенты", icon: "agents", roles: CREATOR_ROLES },
  { id: "processes", label: "Процессы", icon: "workflow", roles: CREATOR_ROLES },
  { id: "knowledge", label: "Знания", icon: "knowledge", roles: WORK_ROLES },
  { id: "evals", label: "Качество", icon: "repeat", roles: WORK_ROLES },
];

const infrastructureItems: readonly NavigationItem[] = [
  { id: "nodes", label: "Узлы", icon: "nodes", roles: ADMIN_AUDIT_ROLES },
  { id: "models", label: "Модели", icon: "models", roles: ["admin"] },
  { id: "fleet", label: "Fleet / HA", icon: "shield", roles: ADMIN_AUDIT_ROLES },
];

const integrationItems: readonly NavigationItem[] = [
  { id: "tools", label: "MCP", icon: "plug", roles: WORK_ROLES },
  { id: "a2a", label: "A2A", icon: "network", roles: WORK_ROLES },
];

const viewMeta: Record<ViewId, { label: string; parents: string[] }> = {
  overview: { label: "Обзор", parents: [] },
  runs: { label: "Запуски", parents: [] },
  approvals: { label: "Согласования", parents: [] },
  agents: { label: "Агенты", parents: ["Создание"] },
  processes: { label: "Процессы", parents: ["Создание"] },
  knowledge: { label: "Знания", parents: ["Создание"] },
  evals: { label: "Качество", parents: ["Создание"] },
  nodes: { label: "Узлы", parents: ["Администрирование", "Инфраструктура"] },
  models: { label: "Модели", parents: ["Администрирование", "Инфраструктура"] },
  fleet: { label: "Fleet / HA", parents: ["Администрирование", "Инфраструктура"] },
  tools: { label: "MCP", parents: ["Администрирование", "Интеграции"] },
  a2a: { label: "A2A", parents: ["Администрирование", "Интеграции"] },
};

function allowed(item: NavigationItem, roles: readonly AgatRole[]): boolean {
  return roles.some((role) => item.roles.includes(role));
}

function visibleItems(items: readonly NavigationItem[], roles: readonly AgatRole[]): NavigationItem[] {
  return items.filter((item) => allowed(item, roles));
}

export function getPrimaryNavigation(roles: readonly AgatRole[]): NavigationItem[] {
  return visibleItems(primaryNavigation, roles);
}

export function getNavigationGroups(roles: readonly AgatRole[]): NavigationGroup[] {
  const visibleCreation = visibleItems(creationItems, roles);
  const infrastructure = visibleItems(infrastructureItems, roles);
  const integrations = visibleItems(integrationItems, roles);
  const groups: NavigationGroup[] = [];

  if (visibleCreation.length > 0) {
    groups.push({ id: "creation", label: "Создание", icon: "plus", items: visibleCreation });
  }

  if (infrastructure.length > 0 || integrations.length > 0) {
    const sections: NavigationSection[] = [];
    if (infrastructure.length > 0) sections.push({ id: "infrastructure", label: "Инфраструктура", items: infrastructure });
    if (integrations.length > 0) sections.push({ id: "integrations", label: "Интеграции", items: integrations });
    groups.push({
      id: "administration",
      label: "Администрирование",
      icon: "shield",
      items: sections.flatMap((section) => section.items),
      sections,
    });
  }

  return groups;
}

export function canAccessView(view: ViewId, roles: readonly AgatRole[]): boolean {
  return [
    ...getPrimaryNavigation(roles),
    ...getNavigationGroups(roles).flatMap((group) => group.items),
  ].some((item) => item.id === view);
}

export function getBreadcrumbs(view: ViewId): string[] {
  const meta = viewMeta[view];
  return view === "overview" ? [meta.label] : ["Обзор", ...meta.parents, meta.label];
}

export function getViewLabel(view: ViewId): string {
  return viewMeta[view].label;
}

export function getMobilePinnedView(roles: readonly AgatRole[]): ViewId | null {
  if (roles.includes("admin") || roles.includes("designer")) return "processes";
  if (roles.includes("operator")) return "knowledge";
  if (roles.includes("auditor")) return "evals";
  return null;
}

export function getRoleLabel(roles: readonly AgatRole[]): string {
  if (roles.includes("admin")) return "Администратор";
  if (roles.includes("designer")) return "Проектировщик";
  if (roles.includes("operator")) return "Оператор";
  if (roles.includes("auditor")) return "Аудитор";
  return "Наблюдатель";
}
