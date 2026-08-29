import type { AgatEvent, Approval, Overview, ReplayRunRequest, Run, SchedulerMode } from "../types";
import { Icon } from "./Icon";
import { RunDetail } from "./RunDetail";
import { RunQueue } from "./RunQueue";

interface RunsPageProps {
  overview: Overview;
  selectedRun: Run | null;
  selectedRunId: string | null;
  approval: Approval | null;
  busy: boolean;
  onCreate: () => void;
  onSelect: (runId: string) => void;
  onSchedulerChange: (mode: SchedulerMode) => void;
  onApproval: (approval: Approval, decision: "approve" | "reject") => void;
  onReplay: (runId: string, payload: ReplayRunRequest) => Promise<void>;
}

export function RunsPage({
  overview,
  selectedRun,
  selectedRunId,
  approval,
  busy,
  onCreate,
  onSelect,
  onSchedulerChange,
  onApproval,
  onReplay,
}: RunsPageProps) {
  return (
    <main className="main-column section-page" id="runs">
      <div className="page-title page-title--section">
        <div><h1>Запуски</h1><p>Очередь, цепочки, события и решения оператора</p></div>
        <button className="button button--primary page-title__action" type="button" onClick={onCreate}>
          <Icon name="play" size={17} />Новый запуск
        </button>
      </div>
      <RunQueue runs={overview.runs} selectedRunId={selectedRunId} onSelect={onSelect} />
      <RunDetail
        run={selectedRun}
        events={overview.events as AgatEvent[]}
        schedulerMode={overview.scheduler.mode}
        approval={approval}
        busy={busy}
        onSchedulerChange={onSchedulerChange}
        onApproval={onApproval}
        models={overview.models}
        onReplay={onReplay}
      />
    </main>
  );
}
