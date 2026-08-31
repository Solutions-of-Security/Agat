import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";

import { useModalFocus } from "../hooks/useModalFocus";
import {
  getMobilePinnedView,
  getNavigationGroups,
  getPrimaryNavigation,
  getRoleLabel,
  type NavigationItem,
} from "../navigation";
import type { AgatRole, ViewId } from "../types";
import { Icon } from "./Icon";

interface BottomNavProps {
  activeView: ViewId;
  roles: AgatRole[];
  onNavigate: (view: ViewId) => void;
}

export function BottomNav({ activeView, roles, onNavigate }: BottomNavProps) {
  const primaryItems = useMemo(() => getPrimaryNavigation(roles), [roles]);
  const groups = useMemo(() => getNavigationGroups(roles), [roles]);
  const pinnedView = getMobilePinnedView(roles);
  const groupedItems = groups.flatMap((group) => group.items);
  const pinnedItem = pinnedView ? groupedItems.find((item) => item.id === pinnedView) ?? null : null;
  const directItems: NavigationItem[] = pinnedItem ? [...primaryItems, pinnedItem] : primaryItems;
  const moreItems = groupedItems.filter((item) => item.id !== pinnedView);
  const hasMore = moreItems.length > 0;
  const moreActive = moreItems.some((item) => item.id === activeView);
  const [moreOpen, setMoreOpen] = useState(false);
  const sheetRef = useRef<HTMLElement>(null);
  useModalFocus(moreOpen, sheetRef, () => setMoreOpen(false));

  useEffect(() => setMoreOpen(false), [activeView]);

  function navigate(view: ViewId) {
    setMoreOpen(false);
    onNavigate(view);
  }

  const count = directItems.length + (hasMore ? 1 : 0);
  const style = { "--bottom-nav-count": count } as CSSProperties;

  return (
    <>
      <nav className="bottom-nav" aria-label="Мобильная навигация" style={style}>
        {directItems.map((item) => {
          const active = activeView === item.id;
          return (
            <button
              className={active ? "is-active" : ""}
              type="button"
              aria-current={active ? "page" : undefined}
              onClick={() => navigate(item.id)}
              key={item.id}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
            </button>
          );
        })}
        {hasMore ? (
          <button
            className={moreOpen || moreActive ? "is-active" : ""}
            type="button"
            aria-expanded={moreOpen}
            aria-controls="mobile-navigation-sheet"
            onClick={() => setMoreOpen((current) => !current)}
          >
            <Icon name="dots" />
            <span>Ещё</span>
          </button>
        ) : null}
      </nav>

      {moreOpen ? (
        <div className="mobile-navigation-layer">
          <button className="mobile-navigation-backdrop" type="button" aria-label="Закрыть меню разделов" onClick={() => setMoreOpen(false)} />
          <section className="mobile-navigation-sheet" id="mobile-navigation-sheet" ref={sheetRef} role="dialog" aria-modal="true" aria-labelledby="mobile-navigation-title" tabIndex={-1}>
            <header>
              <h2 id="mobile-navigation-title">Все разделы</h2>
              <button className="icon-button" type="button" aria-label="Закрыть меню" onClick={() => setMoreOpen(false)}>
                <Icon name="close" size={22} />
              </button>
            </header>
            <div className="mobile-navigation-sheet__body">
              {groups.map((group) => {
                const sections = group.sections ?? [{ id: group.id, label: "", items: group.items }];
                return (
                  <section className="mobile-navigation-group" aria-labelledby={`mobile-group-${group.id}`} key={group.id}>
                    <h3 id={`mobile-group-${group.id}`}>{group.label}</h3>
                    {sections.map((section) => (
                      <div className="mobile-navigation-section" key={section.id}>
                        {section.label ? <p>{section.label}</p> : null}
                        <div>
                          {section.items.map((item) => (
                            <button
                              className={activeView === item.id ? "is-active" : ""}
                              type="button"
                              aria-current={activeView === item.id ? "page" : undefined}
                              onClick={() => navigate(item.id)}
                              key={item.id}
                            >
                              <Icon name={item.icon} size={20} />
                              <span>{item.label}</span>
                              <Icon name="chevron" size={17} />
                            </button>
                          ))}
                        </div>
                      </div>
                    ))}
                  </section>
                );
              })}
            </div>
            <footer><Icon name="agents" size={19} /><span>{getRoleLabel(roles)}</span></footer>
          </section>
        </div>
      ) : null}
    </>
  );
}
