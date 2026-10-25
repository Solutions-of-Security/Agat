import React, { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { KnowledgeDocumentPreview as Preview } from "../types";

const featureNames: Record<string, string> = { embedded_content: "встроенные изображения или объекты", tracked_changes: "исправления с отслеживанием",
  fields: "поля с вычисляемыми или сохранёнными значениями", math: "математические объекты", table_layout_not_preserved: "таблицы без сохранения геометрии ячеек" };

export function KnowledgeExtractionCoverage({ document, canReview = false, onReviewed }: {
  document: Preview; canReview?: boolean; onReviewed?: () => Promise<void>;
}) {
  const extraction = document.extraction;
  const [confirmed, setConfirmed] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const report = extraction?.report;
  if (!report) return document.hasOriginal && extraction?.state === "legacy_unknown"
    ? <p role="alert">История извлечения неизвестна. Переиндексируйте файл, чтобы получить отчёт разбора и проверить текст.</p> : null;

  async function accept() {
    if (!confirmed || !reason.trim() || !extraction?.revision || !extraction.reportSha256 || !document.originalSha256 || busy) return;
    setBusy(true); setError(null);
    try {
      await api.reviewKnowledgeExtraction(document.id, { originalSha256: document.originalSha256, contentSha256: document.contentSha256,
        reportSha256: extraction.reportSha256, extractionRevision: extraction.revision, decision: "use_extracted_text_only", reason });
      await onReviewed?.();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось подтвердить разбор. Обновите предпросмотр."); }
    finally { setBusy(false); }
  }

  return <section className="knowledge-extraction" aria-label="Полнота извлечения">
    <h3>Что извлечено из документа</h3>
    <p>{report.profile === "pdf-text-layer.v1" ? "Только текстовый слой PDF. Сканы, изображения и визуальное расположение не проверены."
      : report.profile === "docx-main-document.v1" ? "Основной текст DOCX. Дополнительные части и визуальное расположение не включены в эту проверку."
        : report.profile === "xlsx-values.v1" ? "Значения ячеек XLSX. Изображения, графики, даты и оформление не интерпретируются."
          : report.profile === "csv-values.v1" ? "Текстовые значения CSV с адресами логических строк и ячеек." : "Сохранён переданный текст."}</p>
    <p>{report.textCharacters} символов{report.totalPages !== null ? ` · ${report.totalPages} страниц` : ""}. Автоматическая проверка не доказывает визуальную полноту или истинность данных.</p>
    {report.emptyPages.length ? <p role="alert">Страницы без извлечённого текста: {report.emptyPages.join(", ")}. Сверьте их с оригиналом: это могут быть пустые страницы или сканы.</p> : null}
    {report.omittedParts.length ? <details><summary>Дополнительные части вне извлечённого текста ({report.omittedParts.length})</summary><ul>{report.omittedParts.map(part => <li key={part}><code>{part}</code></li>)}</ul></details> : null}
    {report.features.length ? <ul>{report.features.map(feature => <li key={feature}>{featureNames[feature] ?? feature}</li>)}</ul> : null}
    {extraction?.review ? <p role="status">Разрешено использование только извлечённого текста. {new Date(extraction.review.reviewedAt).toLocaleString("ru-RU")} · {extraction.review.actor}. Ограничения разбора сохраняются.</p>
      : report.reviewRequired && document.status === "review_required" ? <>
        <p>До подтверждения документ не индексируется и не передаётся модели. Скачайте оригинал, сравните его с предпросмотром и оцените пропущенные части.</p>
        {canReview ? <div className="knowledge-extraction__review">
          <label className="knowledge-extraction__confirm"><input type="checkbox" checked={confirmed} disabled={busy} onChange={event => setConfirmed(event.target.checked)} /><span>Я сверил оригинал и разрешаю использовать только извлечённый текст с указанными ограничениями</span></label>
          <label className="field"><span>Причина использования этого текста</span><textarea aria-label="Причина использования этого текста" value={reason} maxLength={1000} rows={2} disabled={busy} onChange={event => setReason(event.target.value)} /></label>
          {error ? <p role="alert" className="form-error">{error}</p> : null}
          <button type="button" className="button button--primary" disabled={busy || !confirmed || !reason.trim()} onClick={() => void accept()}>{busy ? "Подтверждаем…" : "Подтвердить текст и индексировать"}</button>
        </div> : <p>Подтвердить использование текста может администратор или проектировщик проекта.</p>}
      </> : null}
  </section>;
}

export function KnowledgePreviewContent({ document, chunkId, expectedSha256, pageNumber }: {
  document: Preview; chunkId?: string | null; expectedSha256?: string | null; pageNumber?: number;
}) {
  const target = document.chunks.find((chunk) => chunk.id === chunkId);
  const [selection, setSelection] = useState(document.pages.length
    ? target?.pageNumber ?? (document.pages.some((page) => page.pageNumber === pageNumber) ? pageNumber! : 1)
    : (target?.ordinal ?? 0) + 1);
  const page = document.pages.find((item) => item.pageNumber === selection);
  const chunk = document.chunks.find((item) => item.ordinal + 1 === selection);
  const start = page?.charStart ?? chunk?.charStart ?? 0;
  const end = page?.charEnd ?? chunk?.charEnd ?? 0;
  const changed = Boolean(expectedSha256 && expectedSha256 !== document.contentSha256);
  const highlight = !changed && target && target.charStart >= start && target.charEnd <= end ? target : null;
  return <>
    {document.parseError ? <p role="alert">{document.parseError}</p> : null}
    {changed ? <p role="alert">Текст документа изменился после запуска. Для проверки отчёта используйте сохранённую цитату и SHA-256 в результате.</p> : null}
    {chunkId && !target ? <p role="alert">Фрагмент из отчёта отсутствует в текущем индексе. Сохранённая цитата доступна в результате запуска.</p> : null}
    {!document.parseError && document.chunks.length > 0 ? <>
      <label className="field"><span>{document.pages.length ? "Страница PDF" : "Фрагмент документа"}</span>
        <select aria-label={document.pages.length ? "Страница PDF" : "Фрагмент документа"} value={selection} onChange={(event) => setSelection(Number(event.target.value))}>
          {document.pages.length ? document.pages.map((item) => <option key={item.pageNumber} value={item.pageNumber}>Страница {item.pageNumber}</option>)
            : document.chunks.map((item) => <option key={item.id} value={item.ordinal + 1}>Фрагмент {item.ordinal + 1}</option>)}
        </select>
      </label>
      <pre className="knowledge-preview__text">{highlight ? <>{document.content.slice(start, highlight.charStart)}<mark>{document.content.slice(highlight.charStart, highlight.charEnd)}</mark>{document.content.slice(highlight.charEnd, end)}</> : document.content.slice(start, end) || "На этой странице нет извлекаемого текста."}</pre>
      <small>Символы {start}–{end} · {document.pages.length ? `${document.pages.length} стр.` : `${document.chunks.length} фрагм.`}</small>
    </> : null}
  </>;
}

export function KnowledgeDocumentPreview({ documentId, projectId, requestedProjectId, chunkId, expectedSha256, pageNumber, revision, onClose, canReview = false, onReviewed }: {
  documentId: string; projectId: string; requestedProjectId: string | null;
  chunkId: string | null; expectedSha256: string | null; pageNumber: number; revision: string; onClose: () => void;
  canReview?: boolean; onReviewed?: () => Promise<void>;
}) {
  const [document, setDocument] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState(false);
  const panel = useRef<HTMLElement>(null);
  const wrongProject = Boolean(requestedProjectId && requestedProjectId !== projectId);
  useEffect(() => {
    const controller = new AbortController();
    setDocument(null);
    setError(null);
    if (wrongProject) { setLoading(false); return; }
    setLoading(true);
    void api.knowledgeDocument(documentId, controller.signal).then((value) => {
      if (!controller.signal.aborted) setDocument(value);
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Не удалось загрузить предпросмотр");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [documentId, projectId, wrongProject, revision]);
  useEffect(() => { panel.current?.focus(); }, [documentId, chunkId, loading]);

  async function download() {
    if (!document) return;
    setDownloading(true);
    setError(null);
    try {
      const blob = await api.knowledgeDocumentFile(documentId);
      const url = URL.createObjectURL(blob);
      const anchor = window.document.createElement("a");
      anchor.href = url; anchor.download = document.name;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось скачать файл"); }
    finally { setDownloading(false); }
  }

  return <section className="knowledge-preview" aria-label="Предпросмотр документа" tabIndex={-1} ref={panel} aria-busy={loading}>
    <header><div><h2>{document?.name ?? "Предпросмотр документа"}</h2><p>Извлечённый текст для проверки источника</p></div>
      <button type="button" className="button button--secondary" onClick={onClose}>Закрыть предпросмотр</button></header>
    {wrongProject ? <p role="alert">Источник относится к проекту «{requestedProjectId}». Выберите этот проект в верхней панели.</p> : null}
    {loading ? <p role="status">Загружаем документ…</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    {document && !wrongProject ? <>
      <KnowledgeExtractionCoverage key={`${document.id}:${document.extraction?.revision}`} document={document} canReview={canReview} onReviewed={async () => {
        setDocument(await api.knowledgeDocument(documentId)); await onReviewed?.();
      }} />
      <KnowledgePreviewContent key={`${document.id}:${document.updatedAt}:${chunkId}:${pageNumber}`} document={document} chunkId={chunkId} expectedSha256={expectedSha256} pageNumber={pageNumber} />
      <p className="knowledge-preview__hash">SHA-256 текста: <code>{document.contentSha256}</code>{document.originalSha256 ? <><br />SHA-256 файла: <code>{document.originalSha256}</code></> : null}</p>
      {document.hasOriginal ? <button className="button button--secondary" type="button" disabled={downloading} onClick={() => void download()}>{downloading ? "Скачиваем…" : "Скачать исходный файл"}</button> : null}
    </> : null}
  </section>;
}
