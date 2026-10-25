import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import { api, ApiError } from "../lib/api";
import { correctionTaskBrief, normalizeReviewReason, reportDecisionRequest, verifiedDecisionHistory, verifiedDecisionReceipt, verifiedReworkTask } from "../reportDecisions";
import type { Artifact, ReportDecisionHistory, ReportDecisionKind, ReportReworkTask, RecordReportDecisionRequest } from "../types";
import { ModalLayer } from "./ModalLayer";

type Draft = { kind: ReportDecisionKind; reason: string; revision: number };
const copy: Record<ReportDecisionKind, string> = { accepted: "Отчёт принят", rework_requested: "Нужны исправления" };
const stamp = (value: string) => new Date(value).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
const sameIntent = (a: RecordReportDecisionRequest, b: RecordReportDecisionRequest) => a.expectedSha256 === b.expectedSha256
  && a.expectedRevision === b.expectedRevision && a.decision === b.decision && a.reason === b.reason;

export function ReportDecisionPanel({ artifact, canReview, onDownloadSource }: {
  artifact: Artifact; canReview: boolean; onDownloadSource: () => Promise<void>;
}) {
  const titleId = useId(), reasonId = useId(), taskTitleId = useId();
  const live = useRef(true), pending = useRef<RecordReportDecisionRequest | null>(null);
  const pageRequest = useRef<AbortController | null>(null);
  const [history, setHistory] = useState<ReportDecisionHistory | null>(null);
  const [loading, setLoading] = useState(true), [readRetry, setReadRetry] = useState(0), [readError, setReadError] = useState<string | null>(null);
  const [paging, setPaging] = useState(false), [pageError, setPageError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null), [busy, setBusy] = useState(false), [uncertain, setUncertain] = useState(false);
  const [reasons, setReasons] = useState<Record<ReportDecisionKind, string>>({ accepted: "", rework_requested: "" });
  const [writeError, setWriteError] = useState<string | null>(null), [notice, setNotice] = useState<string | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null), [taskRetry, setTaskRetry] = useState(0), [task, setTask] = useState<ReportReworkTask | null>(null);
  const [taskError, setTaskError] = useState<string | null>(null), [downloadBusy, setDownloadBusy] = useState(false);
  const [copyNotice, setCopyNotice] = useState<string | null>(null), [briefOpen, setBriefOpen] = useState(false);
  const briefRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { live.current = true; return () => { live.current = false; pageRequest.current?.abort(); }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setReadError(null);
    void api.reportDecisions(artifact.id, null, controller.signal).then(value => {
      const checked = verifiedDecisionHistory(value, artifact);
      if (!controller.signal.aborted) setHistory(checked);
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) { setHistory(null); setReadError(error instanceof Error ? error.message : "Не удалось прочитать решения"); }
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [artifact.id, artifact.sha256, artifact.runId, readRetry]);

  useEffect(() => {
    if (!taskId) return;
    const controller = new AbortController();
    setTask(null); setTaskError(null); setCopyNotice(null); setBriefOpen(false);
    void api.reportReworkTask(taskId, controller.signal).then(value => {
      const checked = verifiedReworkTask(value, artifact, taskId);
      if (!controller.signal.aborted) setTask(checked);
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setTaskError(error instanceof Error ? error.message : "Не удалось открыть задачу");
    });
    return () => controller.abort();
  }, [taskId, taskRetry, artifact.id, artifact.sha256, artifact.runId]);

  const closeTask = useCallback(() => setTaskId(null), []);
  const latest = history?.decisions[0];
  const canChoose = canReview && Boolean(history?.sourceReady) && !loading && !busy && !readError && (history?.revision ?? 1000) < 1000;
  let validation: string | null = null;
  if (draft) try { reportDecisionRequest(artifact, draft.revision, draft.kind, draft.reason, "00000000-0000-4000-8000-000000000000"); }
  catch (error) { validation = error instanceof Error ? error.message : "Проверьте комментарий"; }
  const unchanged = Boolean(draft && latest && draft.kind === latest.decision && normalizeReviewReason(draft.reason) === latest.reason);

  function refresh() {
    pageRequest.current?.abort(); setPaging(false); setPageError(null); setLoading(true); setReadRetry(value => value + 1);
  }
  function choose(kind: ReportDecisionKind) {
    if (!history || !canChoose) return;
    setDraft({ kind, reason: reasons[kind], revision: history.revision }); setUncertain(false); setWriteError(null); setNotice(null);
  }
  function cancelDraft() { setDraft(null); setUncertain(false); if (pending.current) refresh(); }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!draft || !canReview || busy || validation || unchanged || !history?.sourceReady) return;
    const body = reportDecisionRequest(artifact, draft.revision, draft.kind, draft.reason, crypto.randomUUID());
    if (pending.current && sameIntent(pending.current, body)) body.requestId = pending.current.requestId;
    pending.current = body; setBusy(true); setWriteError(null);
    try {
      verifiedDecisionReceipt(await api.recordReportDecision(artifact.id, body), artifact, body);
      if (!live.current) return;
      pending.current = null; setDraft(null); setUncertain(false); setNotice("Решение записано в историю."); refresh();
    } catch (error) {
      if (!live.current) return;
      if (error instanceof ApiError && error.status < 500) {
        pending.current = null; setUncertain(false);
        if (error.status === 409) { setDraft(null); setWriteError("История или версия отчёта изменилась. Прочитайте новое решение и выберите действие заново."); refresh(); }
        else setWriteError(error.message);
      } else {
        setUncertain(true); setWriteError("Не удалось подтвердить сохранение. Повторите запрос: вторая задача не будет создана.");
      }
    } finally { if (live.current) setBusy(false); }
  }

  async function older() {
    if (!history || history.nextBeforeRevision === null || paging || loading) return;
    const before = history.nextBeforeRevision, revision = history.revision;
    const controller = new AbortController(); pageRequest.current?.abort(); pageRequest.current = controller;
    setPaging(true); setPageError(null);
    try {
      const value = verifiedDecisionHistory(await api.reportDecisions(artifact.id, before, controller.signal), artifact, before);
      if (controller.signal.aborted) return;
      if (value.revision !== revision) { setDraft(null); setWriteError("Появилось новое решение. История обновлена."); refresh(); return; }
      setHistory(current => current?.revision === revision ? { ...current, decisions: [...current.decisions, ...value.decisions.filter(row => !current.decisions.some(saved => saved.id === row.id))], nextBeforeRevision: value.nextBeforeRevision } : current);
    } catch (error) { if (!controller.signal.aborted) setPageError(error instanceof Error ? error.message : "Не удалось прочитать прошлые решения"); }
    finally { if (!controller.signal.aborted && live.current) setPaging(false); }
  }

  async function copyTask() {
    if (!task) return;
    try { await navigator.clipboard.writeText(correctionTaskBrief(task, artifact.name, window.location.origin)); if (live.current) setCopyNotice("Задание скопировано."); }
    catch { if (live.current) { setBriefOpen(true); setCopyNotice("Скопируйте текст задания вручную."); window.setTimeout(() => { briefRef.current?.focus(); briefRef.current?.select(); }, 0); } }
  }
  async function sourceDownload() {
    setDownloadBusy(true); setTaskError(null);
    try { await onDownloadSource(); }
    catch (error) { if (live.current) setTaskError(error instanceof Error ? error.message : "Не удалось скачать исходный файл"); }
    finally { if (live.current) setDownloadBusy(false); }
  }

  return <section className="report-review" aria-labelledby={titleId}>
    <header className="report-review__header"><div><h4 id={titleId}>Решение о результате</h4>
      <p className="report-review__status" data-decision={latest?.decision}>{latest ? copy[latest.decision] : loading ? "Загружаем историю…" : readError ? "История недоступна" : "Решение ещё не сохранено"}</p></div>
      <button className="button button--secondary" type="button" disabled={loading || busy} onClick={() => { setDraft(null); refresh(); }}>Обновить историю</button>
    </header>
    {readError ? <p role="alert">Не удалось загрузить решения: {readError} <button className="button button--secondary" type="button" onClick={refresh}>Повторить загрузку решений</button></p> : null}
    {loading && history ? <p role="status">Обновляем историю…</p> : null}
    {history && !history.sourceReady ? <p>Исходный файл сейчас недоступен. История сохранена; новое решение записать нельзя.</p> : null}
    {history && history.revision >= 1000 ? <p>Для этого файла достигнут предел истории решений.</p> : null}
    {latest ? <p className="report-review__stamp">Решение №{latest.revision} · <time dateTime={latest.createdAt}>{stamp(latest.createdAt)}</time> · {latest.actor}</p> : null}
    {latest?.reason ? <details className="report-review__comment"><summary>Комментарий к решению</summary><p>{latest.reason}</p></details> : null}
    {latest?.reworkTaskId ? <p className="report-review__task-link">Создана задача исправления. <button className="button button--secondary" type="button" onClick={() => setTaskId(latest.reworkTaskId)}>Открыть задачу</button></p> : null}
    {canReview ? <div className="report-review__actions" role="group" aria-label="Выбрать решение о результате">
      <button className="button button--secondary" type="button" disabled={!canChoose} onClick={() => choose("accepted")}>Принять отчёт</button>
      <button className="button button--secondary" type="button" disabled={!canChoose} onClick={() => choose("rework_requested")}>Нужны исправления</button>
    </div> : <p className="report-review__hint">Решение может записать оператор или администратор.</p>}
    {draft ? <form className="report-review__form" aria-label={draft.kind === "accepted" ? "Принять отчёт" : "Запрос исправлений"} onSubmit={event => void submit(event)}>
      <strong>{draft.kind === "accepted" ? "Принять сохранённую версию отчёта" : "Что нужно исправить"}</strong>
      <p>{draft.kind === "accepted" ? `Решение относится к файлу ${artifact.name}.` : "Будет создана связанная ручная задача с вашим комментарием."}</p>
      <label htmlFor={reasonId}>Комментарий {draft.kind === "rework_requested" ? "(обязательно)" : "(необязательно)"}</label>
      <textarea id={reasonId} rows={4} maxLength={4000} required={draft.kind === "rework_requested"} value={draft.reason} disabled={busy || uncertain}
        onChange={event => { const reason = event.target.value; setDraft({ ...draft, reason }); setReasons(values => ({ ...values, [draft.kind]: reason })); }} />
      <small>{draft.reason.length} / 4000</small>
      {validation && draft.reason ? <p role="alert">{validation}</p> : null}
      {unchanged ? <p>Такое решение уже сохранено. Измените комментарий или выберите другое действие.</p> : null}
      <div className="report-review__actions"><button className="button" type="submit" disabled={busy || Boolean(validation) || unchanged || !canReview || !history?.sourceReady}>{busy ? "Сохраняем…" : uncertain ? "Повторить сохранение" : "Сохранить решение"}</button>
        <button className="button button--secondary" type="button" disabled={busy} onClick={cancelDraft}>Отмена</button></div>
    </form> : null}
    {writeError ? <p role="alert">{writeError}</p> : null}{notice ? <p role="status">{notice}</p> : null}
    {history?.decisions.length ? <details className="report-review__history"><summary>История решений · {history.revision}</summary>
      <ol aria-label="История решений">{history.decisions.map(row => <li key={row.id}><strong>№{row.revision} · {copy[row.decision]}</strong>
        <small>{stamp(row.createdAt)} · {row.actor}</small>{row.reason ? <p>{row.reason}</p> : null}
        {row.reworkTaskId ? <button className="button button--secondary" type="button" onClick={() => setTaskId(row.reworkTaskId)}>Задача исправления №{row.revision}</button> : null}</li>)}</ol>
      {history.nextBeforeRevision !== null ? <button className="button button--secondary" type="button" disabled={paging || loading || busy} onClick={() => void older()}>{paging ? "Загружаем…" : "Показать предыдущие решения"}</button> : null}
      {pageError ? <p role="alert">{pageError}</p> : null}
    </details> : null}
    {taskId ? createPortal(<div className="report-task-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) closeTask(); }}>
      <ModalLayer className="report-task-dialog" labelledBy={taskTitleId} onClose={closeTask}>
        <header><h2 id={taskTitleId}>Задача исправления</h2><button className="button button--secondary" type="button" onClick={closeTask}>Закрыть</button></header>
        {!task && !taskError ? <p role="status">Загружаем задачу…</p> : null}
        {task ? <><p className="report-review__status">{task.state === "open" ? "Требуется ручное исправление" : "Запрос заменён более новым решением"}</p>
          <p className="report-task__reason">{task.reason}</p><p>Исходный файл: <strong>{artifact.name}</strong></p>
          <p>Версия процесса: {task.binding.processVersion}. Исправление ещё не выполнено; новый результат требует проверки.</p>
          {!task.sourceAvailable ? <p>Исходный файл сейчас недоступен.</p> : null}
          <div className="report-review__actions"><button className="button button--secondary" type="button" disabled={!task.sourceAvailable || downloadBusy} onClick={() => void sourceDownload()}>{downloadBusy ? "Скачиваем…" : "Скачать исходный отчёт"}</button>
            <button className="button" type="button" disabled={task.state !== "open"} onClick={() => void copyTask()}>Скопировать задание</button>
            <a className="button button--secondary" href={`#runs/${encodeURIComponent(task.runId)}`} onClick={closeTask}>Исходный запуск</a></div>
          <details open={briefOpen} onToggle={event => setBriefOpen(event.currentTarget.open)}><summary>Текст задания для передачи</summary><textarea ref={briefRef} aria-label="Текст задания" readOnly rows={10} value={correctionTaskBrief(task, artifact.name, window.location.origin)} /></details>
          {copyNotice ? <p role="status">{copyNotice}</p> : null}
        </> : null}
        {taskError ? <p role="alert">{taskError} {!task ? <button className="button button--secondary" type="button" onClick={() => setTaskRetry(value => value + 1)}>Повторить загрузку задачи</button> : null}</p> : null}
      </ModalLayer>
    </div>, document.body) : null}
  </section>;
}
