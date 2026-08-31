import type { Approval } from "../types";
import { Icon } from "./Icon";

interface ApprovalPanelProps {
  approval: Approval | null;
  busy: boolean;
  canDecide: boolean;
  onDecision: (approval: Approval, decision: "approve" | "reject") => void;
}

export function ApprovalPanel({ approval, busy, canDecide, onDecision }: ApprovalPanelProps) {
  if (!approval) {
    return (
      <div className="approval approval--clear">
        <Icon name="shield" size={20} />
        <div><h3>Подтверждения</h3><p>Нет действий, ожидающих решения</p></div>
      </div>
    );
  }

  return (
    <div className="approval">
      <div className="approval__title">
        <Icon name="warning" size={20} />
        <div><h3>Нужно подтверждение</h3><p>{approval.runName} · {approval.agentName}</p></div>
      </div>
      <p className="approval__summary">{approval.summary}</p>
      {approval.kind === "mcp_tool" ? (
        <div className="approval__mcp-detail">
          <code>{approval.toolName}</code>
          <span>risk: {approval.risk} · tier: {approval.riskTier}</span>
          <span>approvals: {approval.approvalCount}/{approval.requiredApprovals}{approval.approvers.length ? ` · ${approval.approvers.join(", ")}` : ""}</span>
          <span>policy v{approval.policyVersion} · {approval.policyReason}</span>
          <pre>{JSON.stringify(approval.arguments, null, 2)}</pre>
          <strong>Preview diff</strong>
          <pre>{JSON.stringify(approval.previewDiff, null, 2)}</pre>
        </div>
      ) : null}
      {canDecide ? (
        <div className="approval__actions">
          <button className="button button--secondary" type="button" disabled={busy} onClick={() => onDecision(approval, "reject")}>
            Отклонить
          </button>
          <button className="button button--primary" type="button" disabled={busy} onClick={() => onDecision(approval, "approve")}>
            Разрешить
          </button>
        </div>
      ) : (
        <p className="approval__readonly"><Icon name="shield" size={16} />Режим просмотра: решение может принять оператор или администратор.</p>
      )}
    </div>
  );
}
