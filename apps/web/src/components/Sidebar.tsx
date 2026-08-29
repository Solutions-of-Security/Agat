import type { ViewId } from "../types";
import { Brand } from "./Brand";
import { Icon, type IconName } from "./Icon";

const navItems: Array<{ id: ViewId; label: string; icon: IconName }> = [
  { id: "overview", label: "Обзор", icon: "dashboard" },
  { id: "agents", label: "Агенты", icon: "agents" },
  { id: "runs", label: "Запуски", icon: "runs" },
  { id: "processes", label: "Процессы", icon: "workflow" },
  { id: "knowledge", label: "Knowledge", icon: "knowledge" },
  { id: "evals", label: "Golden eval", icon: "repeat" },
  { id: "tools", label: "MCP", icon: "plug" },
  { id: "a2a", label: "A2A", icon: "network" },
  { id: "nodes", label: "Узлы", icon: "nodes" },
  { id: "models", label: "Модели", icon: "models" },
];

interface SidebarProps {
  activeView: ViewId;
  health: { label: string; status: "online" | "waiting" | "offline" };
  onNavigate: (view: ViewId) => void;
}

export function Sidebar({ activeView, health, onNavigate }: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="sidebar__brand"><Brand /></div>
      <nav className="sidebar__nav" aria-label="Основная навигация">
        {navItems.map((item) => (
          <button
            className={`sidebar__link${activeView === item.id ? " is-active" : ""}`}
            type="button"
            aria-current={activeView === item.id ? "page" : undefined}
            onClick={() => onNavigate(item.id)}
            key={item.id}
          >
            <Icon name={item.icon} size={21} />
            <span>{item.label}</span>
          </button>
        ))}
      </nav>
      <div className="sidebar__footer">
        <div className="health-line"><span className={`status-dot status-dot--${health.status}`} />{health.label}</div>
        <p>АГАТ · локальный контур</p>
        <span className="version">v1.3.0</span>
      </div>
    </aside>
  );
}
