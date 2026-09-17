import { useEffect, useMemo, useState } from "react";

import { getNavigationGroups, getPrimaryNavigation, type NavigationItem } from "../navigation";
import type { AgatRole, ViewId } from "../types";
import { Brand } from "./Brand";
import { Icon } from "./Icon";

interface SidebarProps {
  activeView: ViewId;
  health: { label: string; status: "online" | "waiting" | "offline" };
  roles: AgatRole[];
  onNavigate: (view: ViewId) => void;
}

interface SidebarLinkProps {
  activeView: ViewId;
  item: NavigationItem;
  nested?: boolean;
  onNavigate: (view: ViewId) => void;
}

function SidebarLink({ activeView, item, nested = false, onNavigate }: SidebarLinkProps) {
  const active = activeView === item.id;
  return (
    <button
      className={`sidebar__link${nested ? " sidebar__link--child" : ""}${active ? " is-active" : ""}`}
      type="button"
      aria-current={active ? "page" : undefined}
      onClick={() => onNavigate(item.id)}
    >
      <Icon name={item.icon} size={nested ? 18 : 20} />
      <span>{item.label}</span>
    </button>
  );
}

export function Sidebar({ activeView, health, roles, onNavigate }: SidebarProps) {
  const primaryItems = useMemo(() => getPrimaryNavigation(roles), [roles]);
  const groups = useMemo(() => getNavigationGroups(roles), [roles]);
  const [expanded, setExpanded] = useState<Record<"creation" | "administration", boolean>>({
    creation: true,
    administration: false,
  });

  useEffect(() => {
    const activeGroup = groups.find((group) => group.items.some((item) => item.id === activeView));
    if (activeGroup) setExpanded((current) => ({ ...current, [activeGroup.id]: true }));
  }, [activeView, groups]);

  return (
    <aside className="sidebar">
      <div className="sidebar__brand"><Brand /></div>
      <nav className="sidebar__nav" aria-label="Основная навигация">
        <div className="sidebar__primary">
          {primaryItems.map((item) => (
            <SidebarLink activeView={activeView} item={item} onNavigate={onNavigate} key={item.id} />
          ))}
        </div>

        {groups.map((group) => {
          const open = expanded[group.id];
          const childActive = group.items.some((item) => item.id === activeView);
          const sections = group.sections ?? [{ id: group.id, label: "", items: group.items }];
          return (
            <div className={`sidebar__group sidebar__group--${group.id}${childActive ? " has-active-child" : ""}`} key={group.id}>
              {group.id === "creation" ? <p className="sidebar__group-label">{group.label}</p> : <button
                className="sidebar__group-toggle"
                type="button"
                aria-expanded={open}
                aria-controls={`sidebar-group-${group.id}`}
                onClick={() => setExpanded((current) => ({ ...current, [group.id]: !current[group.id] }))}
              >
                <Icon name={group.icon} size={19} />
                <span>{group.label}</span>
                <Icon className="sidebar__group-chevron" name={open ? "up" : "down"} size={15} />
              </button>}
              <div className="sidebar__group-content" id={`sidebar-group-${group.id}`} hidden={group.id !== "creation" && !open}>
                {sections.map((section) => (
                  <div className="sidebar__subgroup" key={section.id}>
                    {section.label ? <p>{section.label}</p> : null}
                    {section.items.map((item) => (
                      <SidebarLink activeView={activeView} item={item} nested onNavigate={onNavigate} key={item.id} />
                    ))}
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </nav>
      <div className="sidebar__footer">
        <div className="health-line"><span className={`status-dot status-dot--${health.status}`} />Контур {health.label}</div>
        <p>АГАТ · локальный контур</p>
        <span className="version">v1.7.0</span>
      </div>
    </aside>
  );
}
