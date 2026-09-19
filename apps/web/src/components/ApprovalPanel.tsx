import { useEffect, useId, useRef, useState } from "react";

import { formatApprovalDeadline, formatApprovalWait, type ApprovalInboxItem } from "../approvalInbox";
import type { ApprovalDecisionInput, ProcessFormData } from "../types";
import { prepareProcessFormData } from "../processForms";
import { Icon } from "./Icon";
import { ProcessFormFields } from "./ProcessFormFields";

interface ApprovalPanelProps {
  item: ApprovalInboxItem | null;
  busy: boolean;
  canDecide: boolean;
  variant?: "full" | "compact";
  onBack?: () => void;
  onDecision: (item: ApprovalInboxItem, decision: ApprovalDecisionInput) => Promise<void>;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

const statusCopy = {
  pending: "Ожидает решения",
  approved: "Согласовано",
  rejected: "Отклонено",
} as const;

export function ApprovalPanel({ item, busy, canDecide, variant = "full", onBack, onDecision }: ApprovalPanelProps) {
  const commentId = useId();
  const reasonId = useId();
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const decisionRef = useRef<HTMLFormElement>(null);
  const submitting = useRef(false);
  const [formValues, setFormValues] = useState<ProcessFormData>({});
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [comment, setComment] = useState("");
  const [reason, setReason] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setComment("");
    setReason("");
    setRejecting(false);
    setError(null);
    setFormValues({});
    setFieldErrors({});
  }, [item?.id, item?.status]);

  useEffect(() => {
    if (rejecting) reasonRef.current?.focus();
  }, [rejecting]);

  if (!item) {
    return (
      <section className={`approval-detail approval-detail--empty approval-detail--${variant}`} aria-label="Согласование">
        <Icon name="check" size={26} />
        <div><h2>Очередь разобрана</h2><p>Для выбранных фильтров нет запросов, требующих внимания.</p></div>
      </section>
    );
  }

  const { approval } = item;
  const resolved = item.status !== "pending";

  async function submit(decision: "approve" | "reject") {
    if (!item || busy || submitting.current) return;
    if (decision === "reject" && !reason.trim()) {
      setError("Укажите причину отклонения");
      reasonRef.current?.focus();
      return;
    }
    const form = item.approval.kind === "stage" ? item.approval.form : undefined;
    const prepared = decision === "approve" && form ? prepareProcessFormData(form, formValues) : undefined;
    if (prepared && Object.keys(prepared.errors).length) {
      setFieldErrors(prepared.errors);
      setError("Проверьте поля формы");
      window.requestAnimationFrame(() => decisionRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus());
      return;
    }
    setError(null);
    submitting.current = true;
    try {
      await onDecision(item, {
        decision,
        ...(comment.trim() ? { comment: comment.trim() } : {}),
        ...(decision === "reject" ? { reason: reason.trim() } : {}),
        ...(prepared ? { formData: prepared.data } : {}),
      });
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось сохранить решение");
    } finally {
      submitting.current = false;
    }
  }

  return (
    <section className={`approval-detail approval-detail--${variant}`} aria-labelledby="approval-detail-title">
      {onBack ? (
        <button className="approval-detail__back" type="button" onClick={onBack}>
          <Icon name="back" size={18} />Все согласования
        </button>
      ) : null}

      <header className="approval-detail__header">
        <div className="approval-detail__heading">
          <span className={`approval-risk approval-risk--${item.riskTier}`}>
            <i aria-hidden="true" />{item.riskLabel} риск
          </span>
          <span className={`approval-state approval-state--${item.status}`}>{statusCopy[item.status]}</span>
        </div>
        <h2 id="approval-detail-title">{item.action}</h2>
        <p>{item.kindLabel} · запуск «{item.runName}»</p>
      </header>

      {variant === "full" ? (
        <dl className="approval-context" aria-label="Контекст согласования">
          <div><dt>Запросил</dt><dd>{item.requester}</dd></div>
          <div><dt>Затронет</dt><dd>{item.target}</dd></div>
          <div><dt>Риск</dt><dd>{item.riskLabel}</dd></div>
          <div><dt>Ожидает</dt><dd>{formatApprovalWait(item.requestedAt)}</dd></div>
          <div><dt>Срок</dt><dd>{formatApprovalDeadline(item.expiresAt)}</dd></div>
        </dl>
      ) : null}

      <div className="approval-explanation">
        <section>
          <h3>Что произойдёт</h3>
          <p>{item.effect}</p>
        </section>
        <section>
          <h3>Почему это риск</h3>
          <p>{item.riskReason}</p>
        </section>
        {approval.kind === "stage" && approval.input ? <details className="approval-form-input"><summary>Данные предыдущего шага</summary><pre>{approval.input}</pre></details> : null}
      </div>

      {approval.kind === "mcp_tool" ? (
        <details className="approval-technical">
          <summary>
            <span><Icon name="terminal" size={18} /><strong>Технические детали (MCP)</strong></span>
            <span>{approval.approvalCount} из {approval.requiredApprovals}<Icon name="chevron" size={16} /></span>
          </summary>
          <div className="approval-technical__body">
            <section>
              <h3>Redacted arguments</h3>
              <pre>{JSON.stringify(approval.arguments, null, 2)}</pre>
            </section>
            <section>
              <h3>Preview diff</h3>
              <pre>{approval.previewDiff.length ? JSON.stringify(approval.previewDiff, null, 2) : "Изменения не заявлены"}</pre>
            </section>
            <dl>
              <div><dt>Policy reason</dt><dd>{approval.policyReason}</dd></div>
              <div><dt>Policy</dt><dd>v{approval.policyVersion} · {approval.policyRuleId ?? "default rule"}</dd></div>
              <div><dt>SHA-256</dt><dd className="mono">{approval.policySha256}</dd></div>
              <div><dt>Подтверждения</dt><dd>{approval.approvalCount}/{approval.requiredApprovals}{approval.approvers.length ? ` · ${approval.approvers.join(", ")}` : ""}</dd></div>
            </dl>
          </div>
        </details>
      ) : null}

      {resolved ? (
        <div className={`approval-resolution approval-resolution--${item.status}`} role="status">
          <Icon name={item.status === "approved" ? "check" : "close"} size={19} />
          <div>
            <strong>{statusCopy[item.status]}{item.decisionActor ? ` · ${item.decisionActor}` : ""}</strong>
            {item.rejectionReason ? <p>Причина: {item.rejectionReason}</p> : null}
            {item.decisionComment ? <p>Комментарий: {item.decisionComment}</p> : null}
            {item.decidedAt ? <time dateTime={item.decidedAt}>{formatDate(item.decidedAt)}</time> : null}
            {approval.kind === "stage" && approval.form ? <ProcessFormFields form={approval.form} values={item.formData ?? {}} disabled onChange={() => {}} /> : null}
          </div>
        </div>
      ) : canDecide ? (
        <form ref={decisionRef} className={`approval-decision${rejecting ? " is-rejecting" : ""}`} noValidate onSubmit={(event) => { event.preventDefault(); void submit(rejecting ? "reject" : "approve"); }}>
          {approval.kind === "stage" && approval.form ? <ProcessFormFields form={approval.form} values={formValues} errors={fieldErrors} disabled={busy || rejecting} onChange={(values) => { setFormValues(values); setFieldErrors({}); setError(null); }} /> : null}
          <label htmlFor={commentId}>Комментарий к решению <span>необязательно</span></label>
          <textarea
            id={commentId}
            maxLength={1_000}
            placeholder="Добавьте контекст для audit trail"
            value={comment}
            onChange={(event) => setComment(event.target.value)}
          />
          {rejecting ? (
            <div className="approval-decision__reason">
              <label htmlFor={reasonId}>Причина отклонения <span>обязательно</span></label>
              <textarea
                ref={reasonRef}
                id={reasonId}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? `${reasonId}-error` : undefined}
                maxLength={1_000}
                placeholder="Что нужно исправить перед повторным запросом"
                value={reason}
                onChange={(event) => { setReason(event.target.value); if (error) setError(null); }}
              />
            </div>
          ) : null}
          {error ? <p className="approval-decision__error" id={`${reasonId}-error`} role="alert">{error}</p> : null}
          <div className="approval-decision__actions">
            {rejecting ? (
              <>
                <button className="button button--secondary" type="button" disabled={busy} onClick={() => { setRejecting(false); setError(null); }}>Отмена</button>
                <button className="button button--danger" type="submit" disabled={busy}>
                  {busy ? "Сохраняем…" : "Подтвердить отклонение"}
                </button>
              </>
            ) : (
              <>
                <button className="button button--secondary" type="button" disabled={busy} onClick={() => setRejecting(true)}>Отклонить</button>
                <button className="button button--primary" type="submit" disabled={busy}>
                  {busy ? "Сохраняем…" : approval.kind === "mcp_tool" ? "Разрешить вызов" : approval.mode === "input" ? "Передать данные и продолжить" : "Разрешить продолжение"}
                </button>
              </>
            )}
          </div>
        </form>
      ) : (
        <div className="approval-decision">
        {approval.kind === "stage" && approval.form ? <ProcessFormFields form={approval.form} values={{}} disabled onChange={() => {}} /> : null}
        <p className="approval__readonly"><Icon name="shield" size={16} />Режим просмотра: решение может принять оператор или администратор.</p>
        </div>
      )}

      {variant === "full" ? (
        <section className="approval-audit-section" aria-labelledby="approval-audit-title">
          <h3 id="approval-audit-title">История решения</h3>
          <ol className="approval-audit">
            {item.audit.map((entry) => (
              <li className={`approval-audit__entry approval-audit__entry--${entry.tone}`} key={entry.id}>
                <span className="approval-audit__marker" aria-hidden="true"><Icon name={entry.tone === "positive" ? "check" : entry.tone === "negative" ? "close" : "clock"} size={14} /></span>
                <div>
                  <strong>{entry.title}</strong>
                  <span>{entry.actor}</span>
                  {entry.note ? <p>{entry.note}</p> : null}
                  <time dateTime={entry.createdAt}>{formatDate(entry.createdAt)}</time>
                </div>
              </li>
            ))}
          </ol>
        </section>
      ) : null}
    </section>
  );
}
