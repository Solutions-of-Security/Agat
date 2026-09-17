import { getRoleLabel } from "../navigation";
import type { AppNotification } from "../notifications";
import type { AuthUser, ProjectSummary } from "../types";
import { Brand } from "./Brand";
import { Icon } from "./Icon";
import { NotificationCenter } from "./NotificationCenter";

export type PrimaryAction = "run" | "agent" | "process" | "mcp" | "a2a" | null;

interface TopbarProps {
  notifications: AppNotification[];
  primaryAction: PrimaryAction;
  onPrimary: () => void;
  onNotification: (notification: AppNotification) => void;
  onApprovals: () => void;
  user: AuthUser | null;
  projects: ProjectSummary[];
  activeProjectId: string;
  onProjectChange: (projectId: string) => void;
  onCreateProject: () => void;
  onLogout: () => void;
}

const actionCopy: Record<Exclude<PrimaryAction, null>, string> = {
  run: "Новый запуск",
  agent: "Новый агент",
  process: "Новый процесс",
  mcp: "MCP-сервер",
  a2a: "A2A endpoint",
};

export function Topbar({
  notifications,
  primaryAction,
  onPrimary,
  onNotification,
  onApprovals,
  user,
  projects,
  activeProjectId,
  onProjectChange,
  onCreateProject,
  onLogout,
}: TopbarProps) {
  const canCreateProject = user?.roles.includes("admin") ?? false;
  const initials = (user?.username || "A").slice(0, 1).toLocaleUpperCase("ru");

  return (
    <header className="topbar">
      <div className="topbar__mobile-brand"><Brand /></div>
      <div className="topbar__project">
        <Icon name="layers" size={17} />
        <select aria-label="Активный проект" value={activeProjectId} onChange={(event) => onProjectChange(event.target.value)}>
          {projects.map((project) => <option value={project.id} key={project.id}>{project.name}</option>)}
        </select>
        {canCreateProject ? (
          <button className="icon-button" type="button" aria-label="Создать проект" title="Создать проект" onClick={onCreateProject}>
            <Icon name="plus" size={16} />
          </button>
        ) : null}
      </div>

      {primaryAction ? (
        <button className="button button--primary topbar__create" type="button" onClick={onPrimary}>
          <Icon name={primaryAction === "run" ? "play" : "plus"} size={17} />
          {actionCopy[primaryAction]}
        </button>
      ) : <span className="topbar__spacer" />}

      <div className="topbar__tools" aria-label="Служебные действия">
        <NotificationCenter
          notifications={notifications}
          projectId={activeProjectId}
          onOpen={onNotification}
          onOpenApprovals={onApprovals}
          key={activeProjectId}
        />
        <details className="profile-menu">
          <summary aria-label="Открыть меню профиля">
            <span className="avatar" aria-hidden="true">{initials}</span>
            <Icon name="down" size={15} />
          </summary>
          <div className="profile-menu__popover">
            <div>
              <strong>{user?.username ?? "Локальный пользователь"}</strong>
              <span>{user?.email ?? getRoleLabel(user?.roles ?? [])}</span>
              {user?.email ? <small>{getRoleLabel(user.roles)}</small> : null}
            </div>
            <button type="button" onClick={onLogout}>
              <Icon name="back" size={17} />Выйти
            </button>
          </div>
        </details>
      </div>
    </header>
  );
}
