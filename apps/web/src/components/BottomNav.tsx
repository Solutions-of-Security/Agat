import type { ViewId } from "../types";
import { Icon, type IconName } from "./Icon";

const items: Array<{ id: ViewId; label: string; icon: IconName }> = [
  { id: "overview", label: "Обзор", icon: "dashboard" },
  { id: "runs", label: "Запуски", icon: "list" },
  { id: "processes", label: "Процессы", icon: "workflow" },
  { id: "knowledge", label: "Знания", icon: "knowledge" },
  { id: "evals", label: "Eval", icon: "repeat" },
  { id: "tools", label: "MCP", icon: "plug" },
  { id: "a2a", label: "A2A", icon: "network" },
  { id: "agents", label: "Агенты", icon: "agents" },
  { id: "nodes", label: "Узлы", icon: "nodes" },
  { id: "models", label: "Модели", icon: "models" },
];

export function BottomNav({ activeView, onNavigate }: { activeView: ViewId; onNavigate: (view: ViewId) => void }) {
  return (
    <nav className="bottom-nav" aria-label="Мобильная навигация">
      {items.map((item) => (
        <button
          className={activeView === item.id ? "is-active" : ""}
          type="button"
          aria-current={activeView === item.id ? "page" : undefined}
          onClick={() => onNavigate(item.id)}
          key={item.id}
        >
          <Icon name={item.icon} />
          <span>{item.label}</span>
        </button>
      ))}
    </nav>
  );
}
