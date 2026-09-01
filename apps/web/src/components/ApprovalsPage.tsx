import { useMemo, useState } from "react";

import {
  buildApprovalInboxItems,
  filterApprovalInboxItems,
  formatApprovalDeadline,
  formatApprovalWait,
  type ApprovalInboxFilters,
  type ApprovalInboxItem,
} from "../approvalInbox";
import { getRoleCapabilities } from "../navigation";
import type { AgatRole, Approval, ApprovalDecisionInput, Overview } from "../types";
import { ApprovalPanel } from "./ApprovalPanel";
import { Icon } from "./Icon";

interface ApprovalsPageProps {
  roles: AgatRole[];
  overview: Overview;
  selectedApprovalId: string | null;
  selectedRunId: string | null;
  detailOpen: boolean;
  busy: boolean;
  onSelect: (item: ApprovalInboxItem) => void;
  onBack: () => void;
  onDecision: (approval: Approval, decision: ApprovalDecisionInput) => Promise<void>;
}

const defaultFilters: ApprovalInboxFilters = {
  search: "",
  state: "pending",
  risk: "all",
  kind: "all",
  deadline: "all",
};

function statusLabel(item: ApprovalInboxItem) {
  if (item.status === "approved") return "Согласовано";
  if (item.status === "rejected") return "Отклонено";
  return formatApprovalDeadline(item.expiresAt);
}

export function ApprovalsPage({
  roles,
  overview,
  selectedApprovalId,
  selectedRunId,
  detailOpen,
  busy,
  onSelect,
  onBack,
  onDecision,
}: ApprovalsPageProps) {
  const { canDecideApprovals } = getRoleCapabilities(roles);
  const [filters, setFilters] = useState<ApprovalInboxFilters>(defaultFilters);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const items = useMemo(() => buildApprovalInboxItems({
    approvals: overview.approvals,
    runs: overview.runs,
    events: overview.events,
    generatedAt: overview.generatedAt,
  }), [overview.approvals, overview.events, overview.generatedAt, overview.runs]);
  const filtered = useMemo(() => filterApprovalInboxItems(items, filters), [filters, items]);
  const selected = filtered.find((item) => item.id === selectedApprovalId)
    ?? filtered.find((item) => item.approval.runId === selectedRunId)
    ?? filtered[0]
    ?? null;
  const pending = items.filter((item) => item.status === "pending");
  const highRisk = pending.filter((item) => item.riskTier === "high" || item.riskTier === "critical").length;
  const withoutDeadline = pending.filter((item) => !item.expiresAt).length;
  const filtersChanged = JSON.stringify(filters) !== JSON.stringify(defaultFilters);

  function updateFilter<Key extends keyof ApprovalInboxFilters>(key: Key, value: ApprovalInboxFilters[Key]) {
    setFilters((current) => ({ ...current, [key]: value }));
  }

  async function decide(item: ApprovalInboxItem, decision: ApprovalDecisionInput) {
    await onDecision(item.approval, decision);
    setFilters((current) => ({ ...current, state: "all" }));
  }

  return (
    <main className={`main-column section-page approvals-page${detailOpen ? " is-detail-open" : ""}`} id="approvals">
      <div className="page-title page-title--section approvals-page__title">
        <div>
          <h1>Согласования</h1>
          <p>Решения, которые ждут оператора</p>
        </div>
      </div>

      <section className="approval-summary" aria-label="Сводка согласований">
        <div><span>Требуют решения</span><strong>{pending.length}</strong></div>
        <div><span>Высокий риск</span><strong className={highRisk ? "is-danger" : ""}>{highRisk}</strong></div>
        <div><span>Без срока</span><strong>{withoutDeadline}</strong></div>
        <p><Icon name="shield" size={16} />Проект: <strong>{overview.project?.name ?? "Текущий проект"}</strong></p>
        <button className="approval-filters__mobile-toggle" type="button" aria-expanded={filtersOpen} onClick={() => setFiltersOpen((open) => !open)}>
          <Icon name="search" size={16} />{filtersOpen ? "Скрыть фильтры" : "Фильтры"}
        </button>
      </section>

      <section className={`approval-filters${filtersOpen ? " is-open" : ""}`} aria-label="Фильтры согласований">
        <label className="approval-filter approval-filter--search">
          <span>Поиск</span>
          <span className="approval-filter__control"><Icon name="search" size={16} /><input type="search" placeholder="Действие, агент или запуск" value={filters.search} onChange={(event) => updateFilter("search", event.target.value)} /></span>
        </label>
        <label className="approval-filter">
          <span>Риск</span>
          <select value={filters.risk} onChange={(event) => updateFilter("risk", event.target.value as ApprovalInboxFilters["risk"])}>
            <option value="all">Любой</option>
            <option value="high">Высокий</option>
            <option value="standard">Стандартный</option>
          </select>
        </label>
        <label className="approval-filter">
          <span>Тип</span>
          <select value={filters.kind} onChange={(event) => updateFilter("kind", event.target.value as ApprovalInboxFilters["kind"])}>
            <option value="all">Любой</option>
            <option value="mcp_tool">MCP-вызов</option>
            <option value="stage">Этап процесса</option>
          </select>
        </label>
        <label className="approval-filter">
          <span>Срок</span>
          <select value={filters.deadline} onChange={(event) => updateFilter("deadline", event.target.value as ApprovalInboxFilters["deadline"])}>
            <option value="all">Любой</option>
            <option value="soon">До 15 минут</option>
            <option value="later">Позже 15 минут</option>
            <option value="none">Не задан</option>
          </select>
        </label>
        <label className="approval-filter">
          <span>Состояние</span>
          <select value={filters.state} onChange={(event) => updateFilter("state", event.target.value as ApprovalInboxFilters["state"])}>
            <option value="pending">Ожидают</option>
            <option value="resolved">Решены</option>
            <option value="all">Все</option>
          </select>
        </label>
        {filtersChanged ? <button className="approval-filters__reset" type="button" onClick={() => setFilters(defaultFilters)}>Сбросить</button> : null}
      </section>

      <div className={`approvals-master-detail${detailOpen ? " is-detail-open" : ""}`}>
        <section className="approval-queue" aria-labelledby="approval-queue-title">
          <header>
            <div><h2 id="approval-queue-title">Очередь согласований</h2><p>{filtered.length} из {items.length}</p></div>
            <span>Риск → срок</span>
          </header>
          {filtered.length ? (
            <ol>
              {filtered.map((item) => (
                <li key={item.id}>
                  <button
                    className={`approval-queue__item approval-queue__item--${item.status}${selected?.id === item.id ? " is-selected" : ""}`}
                    type="button"
                    aria-current={selected?.id === item.id ? "true" : undefined}
                    onClick={() => onSelect(item)}
                  >
                    <span className="approval-queue__topline">
                      <strong>{item.action}</strong>
                      <span className={`approval-risk approval-risk--${item.riskTier}`}><i aria-hidden="true" />{item.riskLabel}</span>
                    </span>
                    <span className="approval-queue__source">{item.requester} · {item.runName}</span>
                    <span className="approval-queue__meta"><span>{item.kindLabel}</span><span>{item.status === "pending" ? `Ждёт ${formatApprovalWait(item.requestedAt)}` : statusLabel(item)}</span></span>
                  </button>
                </li>
              ))}
            </ol>
          ) : (
            <div className="approval-queue__empty">
              <Icon name="check" size={25} />
              <strong>Ничего не найдено</strong>
              <p>Измените фильтры или вернитесь к ожидающим запросам.</p>
              {filtersChanged ? <button className="button button--secondary" type="button" onClick={() => setFilters(defaultFilters)}>Сбросить фильтры</button> : null}
            </div>
          )}
        </section>
        <ApprovalPanel
          item={selected}
          busy={busy}
          canDecide={canDecideApprovals}
          onBack={onBack}
          onDecision={decide}
        />
      </div>
    </main>
  );
}
