import React, { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { KnowledgeDocumentPreview as Preview } from "../types";

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
        <select value={selection} onChange={(event) => setSelection(Number(event.target.value))}>
          {document.pages.length ? document.pages.map((item) => <option key={item.pageNumber} value={item.pageNumber}>Страница {item.pageNumber}</option>)
            : document.chunks.map((item) => <option key={item.id} value={item.ordinal + 1}>Фрагмент {item.ordinal + 1}</option>)}
        </select>
      </label>
      <pre className="knowledge-preview__text">{highlight ? <>{document.content.slice(start, highlight.charStart)}<mark>{document.content.slice(highlight.charStart, highlight.charEnd)}</mark>{document.content.slice(highlight.charEnd, end)}</> : document.content.slice(start, end) || "На этой странице нет извлекаемого текста."}</pre>
      <small>Символы {start}–{end} · {document.pages.length ? `${document.pages.length} стр.` : `${document.chunks.length} фрагм.`}</small>
    </> : null}
  </>;
}

export function KnowledgeDocumentPreview({ documentId, projectId, requestedProjectId, chunkId, expectedSha256, pageNumber, revision, onClose }: {
  documentId: string; projectId: string; requestedProjectId: string | null;
  chunkId: string | null; expectedSha256: string | null; pageNumber: number; revision: string; onClose: () => void;
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
      <KnowledgePreviewContent key={`${document.id}:${document.updatedAt}:${chunkId}:${pageNumber}`} document={document} chunkId={chunkId} expectedSha256={expectedSha256} pageNumber={pageNumber} />
      <p className="knowledge-preview__hash">SHA-256 текста: <code>{document.contentSha256}</code>{document.originalSha256 ? <><br />SHA-256 файла: <code>{document.originalSha256}</code></> : null}</p>
      {document.hasOriginal ? <button className="button button--secondary" type="button" disabled={downloading} onClick={() => void download()}>{downloading ? "Скачиваем…" : "Скачать исходный файл"}</button> : null}
    </> : null}
  </section>;
}
