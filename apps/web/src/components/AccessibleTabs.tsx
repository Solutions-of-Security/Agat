import { useRef, type KeyboardEvent, type ReactNode } from "react";

export interface TabDefinition<T extends string> {
  id: T;
  label: ReactNode;
}

export function getNextTabIndex(current: number, key: string, count: number): number {
  if (count <= 0) return -1;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  if (key === "ArrowRight") return (current + 1) % count;
  if (key === "ArrowLeft") return (current - 1 + count) % count;
  return current;
}

interface AccessibleTabListProps<T extends string> {
  activeTab: T;
  ariaLabel: string;
  className: string;
  idPrefix: string;
  tabs: readonly TabDefinition<T>[];
  onChange: (tab: T) => void;
}

export function AccessibleTabList<T extends string>({
  activeTab,
  ariaLabel,
  className,
  idPrefix,
  tabs,
  onChange,
}: AccessibleTabListProps<T>) {
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const nextIndex = getNextTabIndex(index, event.key, tabs.length);
    const nextTab = tabs[nextIndex];
    if (!nextTab) return;
    onChange(nextTab.id);
    tabRefs.current[nextIndex]?.focus();
  }

  return (
    <div className={className} role="tablist" aria-label={ariaLabel} aria-orientation="horizontal">
      {tabs.map((tab, index) => (
        <button
          key={tab.id}
          className={activeTab === tab.id ? "is-active" : ""}
          id={`${idPrefix}-tab-${tab.id}`}
          type="button"
          role="tab"
          aria-controls={`${idPrefix}-panel-${tab.id}`}
          aria-selected={activeTab === tab.id}
          tabIndex={activeTab === tab.id ? 0 : -1}
          ref={(element) => { tabRefs.current[index] = element; }}
          onClick={() => onChange(tab.id)}
          onKeyDown={(event) => onKeyDown(event, index)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

interface TabPanelProps {
  active: boolean;
  className?: string;
  idPrefix: string;
  tabId: string;
  children: ReactNode;
}

export function TabPanel({ active, className, idPrefix, tabId, children }: TabPanelProps) {
  return (
    <section
      className={className}
      id={`${idPrefix}-panel-${tabId}`}
      role="tabpanel"
      aria-labelledby={`${idPrefix}-tab-${tabId}`}
      tabIndex={active ? 0 : -1}
      hidden={!active}
    >
      {active ? children : null}
    </section>
  );
}
