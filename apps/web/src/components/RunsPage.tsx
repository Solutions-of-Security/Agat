import type { AgatEvent, AgatRole, Approval, ApprovalDecisionInput, Overview, ReplayRunRequest, Run, SchedulerMode } from "../types";
import { getRoleCapabilities } from "../navigation";
import { Icon } from "./Icon";
import { RunDetail } from "./RunDetail";
import { RunQueue } from "./RunQueue";

interface RunsPageProps {
  mode: "runs" | "approvals";
  roles: AgatRole[];
  overview: Overview;
  selectedRun: Run | null;
  selectedRunId: string | null;
  approval: Approval | null;
  busy: boolean;
  detailOpen: boolean;
  onCreate: () => void;
  onSelect: (runId: string) => void;
  onBack: () => void;
  onCancel: (runId: string) => Promise<void>;
  onSchedulerChange: (mode: SchedulerMode) => void;
  onApproval: (approval: Approval, decision: ApprovalDecisionInput) => Promise<void>;
  onReplay: (runId: string, payload: ReplayRunRequest) => Promise<void>;
}

export function RunsPage({
  mode,
  roles,
  overview,
  selectedRun,
  selectedRunId,
  approval,
  busy,
  detailOpen,
  onCreate,
  onSelect,
  onBack,
  onCancel,
  onSchedulerChange,
  onApproval,
  onReplay,
}: RunsPageProps) {
  const { canCreateRuns, canCancelRuns, canDecideApprovals, canManageScheduler, canReplayRuns } = getRoleCapabilities(roles);
  const approvalRunIds = overview.approvals.map((item) => item.runId);

  return (
    <main className={`main-column section-page runs-page${detailOpen ? " is-detail-open" : ""}`} id={mode}>
      <div className="page-title page-title--section">
        <div>
          <h1>{mode === "approvals" ? "Согласования" : "Запуски"}</h1>
          <p>{mode === "approvals" ? "Действия, для которых требуется решение оператора" : "Очередь, цепочки, события и результаты выполнения"}</p>
        </div>
        {canCreateRuns && mode === "runs" ? (
          <button className="button button--primary page-title__action" type="button" onClick={onCreate}>
            <Icon name="play" size={17} />Новый запуск
          </button>
        ) : null}
      </div>
      <div className={`runs-master-detail${detailOpen ? " is-detail-open" : ""}`}>
        <RunQueue
          runs={overview.runs}
          selectedRunId={selectedRunId}
          mode={mode === "approvals" ? "approvals" : "all"}
          approvalRunIds={approvalRunIds}
          onSelect={onSelect}
        />
        <RunDetail
          run={selectedRun}
          events={overview.events as AgatEvent[]}
          schedulerMode={overview.scheduler.mode}
          approval={approval}
          busy={busy}
          canManageScheduler={canManageScheduler}
          canDecideApproval={canDecideApprovals}
          canReplayRuns={canReplayRuns}
          canCancelRuns={canCancelRuns}
          onBack={onBack}
          onCancel={onCancel}
          onSchedulerChange={onSchedulerChange}
          onApproval={onApproval}
          models={overview.models}
          onReplay={onReplay}
        />
      </div>
    </main>
  );
}
