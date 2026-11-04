import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import { api, ApiError } from "../lib/api";
import { verifiedDecisionHistory } from "../reportDecisions";
import { reportEvalDraftError, verifiedReportEvalCandidate, verifiedReportEvalReceipt, type ReportEvalBasis } from "../reportEval";
import type { Artifact, EvalRubricCriterion, ReportEvalCandidate, ReportEvalImportReceipt, ReportEvalImportRequest } from "../types";
import { ModalLayer } from "./ModalLayer";

async function readCandidate(artifact: Artifact, projectId: string, basis: ReportEvalBasis, signal: AbortSignal, fresh = false) {
  if (fresh) {
    const history = verifiedDecisionHistory(await api.reportDecisions(artifact.id, null, signal), artifact);
    const latest = history.decisions[0];
    if (!history.sourceReady || !latest) throw new Error("Нужны доступный исходный отчёт и сохранённое решение");
    basis = { revision: latest.revision, decisionId: latest.id };
  }
  return { basis, candidate: await verifiedReportEvalCandidate(await api.prepareReportEvalCandidate(artifact.id, artifact.sha256, basis.revision, signal), artifact, basis, projectId) };
}

export function ReportEvalHandoffDialog({ artifact, projectId, initialBasis, open, onClose, onSaved, onHistoryRefresh }: {
  artifact: Artifact; projectId: string; initialBasis: ReportEvalBasis; open: boolean;
  onClose: () => void; onSaved: (receipt: ReportEvalImportReceipt) => void; onHistoryRefresh: () => void;
}) {
  const titleId = useId(), formId = useId();
  const live = useRef(true), read = useRef<AbortController | null>(null), pending = useRef<ReportEvalImportRequest | null>(null);
  const nextCriterion = useRef(2);
  const errorRef = useRef<HTMLParagraphElement>(null), sourceRef = useRef<HTMLParagraphElement>(null);
  const shownCandidate = useRef<ReportEvalCandidate | null>(null);
  const [candidate, setCandidate] = useState<ReportEvalCandidate | null>(null), [basis, setBasis] = useState(initialBasis);
  const [loading, setLoading] = useState(true), [error, setError] = useState<string | null>(null), [stale, setStale] = useState(false);
  const [name, setName] = useState(() => `${artifact.name.replace(/\.md$/i, "")} · ${artifact.id.slice(0, 8)}`.slice(0, 120));
  const [description, setDescription] = useState(""), [reference, setReference] = useState("");
  const [rubric, setRubric] = useState<EvalRubricCriterion[]>(() => [{ id: "criterion_1", label: "", description: "", weight: 1 }]);
  const [reviewed, setReviewed] = useState(false), [busy, setBusy] = useState(false), [uncertain, setUncertain] = useState(false);
  const [receipt, setReceipt] = useState<ReportEvalImportReceipt | null>(null);
  useEffect(() => { live.current = true; return () => { live.current = false; read.current?.abort(); }; }, []);
  useEffect(() => { if (open && error) errorRef.current?.scrollIntoView({ block: "nearest" }); }, [open, error]);
  useEffect(() => {
    if (open && candidate && !loading && shownCandidate.current !== candidate) { shownCandidate.current = candidate; sourceRef.current?.scrollIntoView({ block: "start" }); }
  }, [open, candidate, loading]);
  useEffect(() => {
    const controller = new AbortController(); read.current = controller;
    void readCandidate(artifact, projectId, initialBasis, controller.signal).then(value => {
      if (!controller.signal.aborted) { setCandidate(value.candidate); setBasis(value.basis); }
    }).catch((reason: unknown) => { if (!controller.signal.aborted) { setError(reason instanceof Error ? reason.message : "Не удалось подготовить пример"); setStale(true); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [artifact.id, artifact.sha256, artifact.runId, projectId, initialBasis.revision, initialBasis.decisionId]);
  const observed = useMemo(() => {
    if (!candidate) return "";
    try { const pretty = JSON.stringify(JSON.parse(candidate.observedOutput), null, 2); return pretty.length <= 100_000 ? pretty : candidate.observedOutput; } catch { return candidate.observedOutput; }
  }, [candidate]);
  const locked = loading || busy || uncertain;
  const validation = reportEvalDraftError(name, reference, rubric.map(item => item.label), reviewed);
  const close = () => { if (!busy) onClose(); };
  const editDraft = () => { setReviewed(false); setError(null); };

  async function refreshSource() {
    if (busy || uncertain) return;
    const controller = new AbortController(); read.current?.abort(); read.current = controller;
    setLoading(true); setError(null); setReviewed(false);
    try {
      const value = await readCandidate(artifact, projectId, basis, controller.signal, true);
      if (!controller.signal.aborted) { setCandidate(value.candidate); setBasis(value.basis); setStale(false); pending.current = null; onHistoryRefresh(); }
    } catch (reason) { if (!controller.signal.aborted) { setStale(true); setError(reason instanceof Error ? reason.message : "Не удалось обновить контекст"); } }
    finally { if (!controller.signal.aborted) setLoading(false); }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || loading || (!pending.current && (validation || stale || !candidate))) return;
    const body: ReportEvalImportRequest = pending.current ?? { requestId: crypto.randomUUID(), expectedSha256: artifact.sha256, expectedRevision: basis.revision,
      expectedCandidateSha256: candidate!.contentSha256, name: name.trim(), description: description.trim(), referenceOutput: reference.trim(),
      rubric: rubric.map(item => ({ ...item, label: item.label.trim(), description: item.description.trim() })), reviewConfirmed: true };
    pending.current = body; setBusy(true); setError(null);
    try {
      const confirmed = verifiedReportEvalReceipt(await api.importReportEvalDataset(artifact.id, body), body);
      if (!live.current) return;
      pending.current = null; setUncertain(false); setReceipt(confirmed); onSaved(confirmed);
    } catch (reason) {
      if (!live.current) return;
      if (reason instanceof ApiError && reason.status < 500) {
        pending.current = null; setUncertain(false);
        if (reason.status === 409 && reason.code !== "dataset_name_conflict") { setStale(true); setReviewed(false); }
        setError(reason.message);
      } else { setUncertain(true); setError("Не удалось подтвердить сохранение. Повторите тот же запрос: второй dataset не будет создан."); }
    } finally { if (live.current) setBusy(false); }
  }

  if (!open) return null;
  return createPortal(<div className="report-task-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}>
    <ModalLayer className="report-task-dialog report-eval-dialog" labelledBy={titleId} onClose={close}>
      <header><h2 id={titleId}>Пример для Golden eval</h2><button className="button button--secondary" type="button" disabled={busy} onClick={close}>Закрыть</button></header>
      <div className="report-eval-dialog__body">
      {receipt ? <div className="report-eval-success" role="status"><strong>Пример сохранён · dataset v1</strong><p>Эталон и критерии сохранены вместе с происхождением отчёта.</p>
        <p>Development пример. Квалификация модели и независимый holdout проверяются отдельно.</p>
        <a className="button button--primary" href={`#evals?datasetId=${encodeURIComponent(receipt.datasetId)}&datasetVersion=${receipt.datasetVersion}`} onClick={onClose}>Открыть dataset v1</a>
        <details><summary>Квитанция сохранения</summary><p>Dataset: {receipt.datasetId}</p><p className="report-eval-hash">SHA-256 версии: {receipt.datasetContentSha256}</p></details></div> : <>
        <p>Подготовьте эталонный JSON и критерии. Проверьте, что вход и источники подходят отдельному агенту.</p>
        {loading ? <p role="status">{candidate ? "Обновляем контекст примера…" : "Проверяем происхождение примера…"}</p> : null}
        {candidate ? <><p ref={sourceRef}><strong>{artifact.name}</strong> · версия процесса {candidate.source.processVersion} · решение №{basis.revision}</p>
          {candidate.source.reason ? <details><summary>Комментарий к исходному решению</summary><p className="report-task__reason">{candidate.source.reason}</p></details> : null}
          <details><summary>Контекст отдельного агента</summary><p>Агент: {candidate.source.agentId}. Модель: {candidate.source.requestedModel ?? "auto"}. Runtime: {candidate.source.runtimeProfile}.</p>
            <p>Проверьте prompt, маркеры источников и доступный corpus перед запуском experiment.</p><label>Сохранённый вход<textarea readOnly rows={5} value={candidate.example.input} /></label>
            <p className="report-eval-hash">SHA-256 исходного отчёта: {artifact.sha256}</p></details>
          <details><summary>Наблюдаемый JSON</summary><textarea aria-label="Наблюдаемый JSON" readOnly rows={8} value={observed} /></details>
          <form className="report-eval-form" aria-label="Сохранить Golden пример" onSubmit={event => void submit(event)}>
            <fieldset disabled={locked}><label>Название набора<input required maxLength={120} value={name} onChange={event => { setName(event.target.value); editDraft(); }} /></label>
              <label>Описание (необязательно)<textarea rows={2} maxLength={2000} value={description} onChange={event => { setDescription(event.target.value); editDraft(); }} /></label>
              <label htmlFor={`${formId}-reference`}>Эталонный JSON</label>
              <button className="button button--secondary" type="button" disabled={Boolean(reference.trim())} onClick={() => { setReference(observed); editDraft(); }}>Взять JSON как черновик</button>
              <textarea id={`${formId}-reference`} rows={10} required maxLength={100_000} spellCheck={false} value={reference} onChange={event => { setReference(event.target.value); editDraft(); }} />
              <section className="report-eval-rubric" aria-label="Критерии проверки"><h3>Критерии проверки</h3>{rubric.map((item, index) => <div key={item.id}>
                <label>Критерий {index + 1}<input required maxLength={120} value={item.label} onChange={event => { setRubric(values => values.map(row => row.id === item.id ? { ...row, label: event.target.value } : row)); editDraft(); }} /></label>
                <label>Как проверять критерий {index + 1}<textarea rows={2} maxLength={1000} value={item.description} onChange={event => { setRubric(values => values.map(row => row.id === item.id ? { ...row, description: event.target.value } : row)); editDraft(); }} /></label>
                {rubric.length > 1 ? <button className="button button--secondary" type="button" onClick={() => { setRubric(values => values.filter(row => row.id !== item.id)); editDraft(); }}>Удалить критерий {index + 1}</button> : null}</div>)}
                <button className="button button--secondary" type="button" disabled={rubric.length >= 20} onClick={() => { const id = `criterion_${nextCriterion.current++}`; setRubric(values => [...values, { id, label: "", description: "", weight: 1 }]); editDraft(); }}>Добавить критерий</button></section>
              <label className="report-eval-consent"><input type="checkbox" checked={reviewed} disabled={stale} onChange={event => setReviewed(event.target.checked)} />Я проверил(а) эталон, критерии и контекст. Сохранить копию input/reference в development dataset проекта отдельно от исходного запуска.</label>
            </fieldset>
            {stale ? <p role="alert">Обновите контекст и повторно проверьте пример. Ваш эталон и критерии сохранены в форме.</p> : null}
            {validation && !uncertain ? <p className="report-review__hint">{validation}</p> : null}
            <div className="report-review__actions"><button className="button button--primary" type="submit" disabled={busy || loading || (!uncertain && (Boolean(validation) || stale))}>{busy ? "Сохраняем…" : uncertain ? "Повторить сохранение dataset" : "Сохранить dataset v1"}</button>
              <button className="button button--secondary" type="button" disabled={locked} onClick={() => void refreshSource()}>Обновить контекст</button></div>
          </form></> : !loading ? <button className="button button--secondary" type="button" onClick={() => void refreshSource()}>Обновить контекст</button> : null}
        {error ? <p ref={errorRef} role="alert">{error}</p> : null}
      </>}
      </div>
    </ModalLayer>
  </div>, document.body);
}
