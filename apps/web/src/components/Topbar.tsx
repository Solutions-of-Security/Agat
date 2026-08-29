import type { AuthUser, Overview, ProjectSummary } from "../types";
import { Brand } from "./Brand";
import { Icon } from "./Icon";

interface TopbarProps {
  counts: Overview["counts"];
  health: { label: string; status: "online" | "waiting" | "offline" };
  primaryAction: "run" | "agent" | "process" | "mcp" | "a2a";
  onPrimary: () => void;
  user: AuthUser | null;
  projects: ProjectSummary[];
  activeProjectId: string;
  onProjectChange: (projectId: string) => void;
  onCreateProject: () => void;
  onLogout: () => void;
}

export function Topbar({ counts, health, primaryAction, onPrimary, user, projects, activeProjectId, onProjectChange, onCreateProject, onLogout }: TopbarProps) {
  const canCreateProject = user?.roles.includes("admin") ?? false;
  return (
    <header className="topbar">
      <div className="topbar__mobile-brand"><Brand /></div>
      <div className="topbar__metrics" aria-label="Состояние платформы">
        <span><Icon name="list" size={18} /><strong>{counts.queued}</strong><span className="metric-copy"><span>в очереди</span><span>очередь</span></span></span>
        <span><Icon name="nodes" size={18} /><strong>{counts.onlineNodes}/{counts.nodes}</strong><span className="metric-copy"><span>узлов онлайн</span><span>узлы</span></span></span>
        <span><Icon name="agents" size={18} /><strong>{counts.readyAgents}/{counts.agents}</strong><span className="metric-copy"><span>агентов готовы</span><span>агенты</span></span></span>
      </div>
      <div className="mobile-health">
        <span className={`status-dot status-dot--${health.status}`} />
        <span>Контур <strong>{health.label}</strong></span>
      </div>
      <div className="topbar__project">
        <Icon name="layers" size={15} />
        <select aria-label="Активный проект" value={activeProjectId} onChange={(event) => onProjectChange(event.target.value)}>
          {projects.map((project) => <option value={project.id} key={project.id}>{project.name}</option>)}
        </select>
        {canCreateProject ? <button className="icon-button" type="button" aria-label="Создать проект" title="Создать проект" onClick={onCreateProject}><Icon name="plus" size={14} /></button> : null}
      </div>
      <button className="button button--primary topbar__create" type="button" onClick={onPrimary}>
        <Icon name={primaryAction === "run" ? "play" : "plus"} size={17} />
        {primaryAction === "agent" ? "Новый агент" : primaryAction === "process" ? "Новый процесс" : primaryAction === "mcp" ? "MCP-сервер" : primaryAction === "a2a" ? "A2A endpoint" : "Новый запуск"}
      </button>
      <div className="topbar__tools" aria-label="Служебные действия">
        <button className="icon-button" type="button" aria-label="Уведомления">
          <Icon name="bell" size={20} />
          {counts.waitingApprovals > 0 ? <span className="notification-dot" /> : null}
        </button>
        <span className="terminal-mark" aria-hidden="true"><Icon name="terminal" size={20} /></span>
        <button className="avatar" type="button" title={user?.email ?? "Локальный администратор"} aria-label="Выйти" onClick={onLogout}>
          {(user?.username || "A").slice(0, 1).toLocaleUpperCase("ru")}
        </button>
      </div>
    </header>
  );
}
