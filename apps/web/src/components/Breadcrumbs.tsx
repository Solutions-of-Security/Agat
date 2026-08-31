import { getBreadcrumbs } from "../navigation";
import type { ViewId } from "../types";
import { Icon } from "./Icon";

interface BreadcrumbsProps {
  activeView: ViewId;
  onNavigate: (view: ViewId) => void;
}

export function Breadcrumbs({ activeView, onNavigate }: BreadcrumbsProps) {
  const items = getBreadcrumbs(activeView);

  return (
    <nav className="context-bar" aria-label="Хлебные крошки">
      {items.map((item, index) => {
        const current = index === items.length - 1;
        return (
          <span key={`${item}-${index}`}>
            {index > 0 ? <Icon name="chevron" size={14} /> : null}
            {index === 0 && !current ? (
              <button type="button" onClick={() => onNavigate("overview")}>
                <Icon name="dashboard" size={15} />{item}
              </button>
            ) : current ? <strong aria-current="page">{item}</strong> : <span>{item}</span>}
          </span>
        );
      })}
    </nav>
  );
}
