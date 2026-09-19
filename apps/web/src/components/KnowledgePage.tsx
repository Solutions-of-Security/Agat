import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from "react";

import { knowledgeDocumentHref, readKnowledgeFile } from "../knowledge";
import { KnowledgeDocumentPreview } from "./KnowledgeDocumentPreview";
import { api } from "../lib/api";
import type {
  Agent,
  AgatRole,
  ComputeNode,
  KnowledgeDocument,
  KnowledgeOverview,
  KnowledgeSnapshot,
  MemoryEntry,
} from "../types";
import { useActionDialog } from "./ActionDialog";
import { Icon } from "./Icon";
import { useRecoveryTarget, useRecoveryFocus } from "../hooks/useRecoveryTarget";

interface KnowledgePageProps {
  overview: KnowledgeOverview;
  projectId: string;
  agents: Agent[];
  nodes: ComputeNode[];
  roles: AgatRole[];
  onChanged: () => Promise<void>;
}

const documentStatusCopy: Record<KnowledgeDocument["status"], string> = {
  pending: "В очереди",
  indexing: "Индексируется",
  ready: "Готов",
  failed: "Ошибка",
};

function formatDate(value: string | null): string {
  if (!value) return "без срока";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.hidden = true;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

function accessAllowed(roles: AgatRole[], allowed: AgatRole[]): boolean {
  return roles.some((role) => allowed.includes(role));
}

export function KnowledgePage({ overview, projectId, agents, nodes, roles, onChanged }: KnowledgePageProps) {
  const recovery = useRecoveryTarget();
  const requestAction = useActionDialog();
  const [snapshot, setSnapshot] = useState<KnowledgeSnapshot | null>(null);
  const [snapshotProjectId, setSnapshotProjectId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [ingestCollectionId, setIngestCollectionId] = useState<string | null>(null);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [documentContent, setDocumentContent] = useState("");
  const [documentName, setDocumentName] = useState("");
  const [documentBase64, setDocumentBase64] = useState<string | null>(null);
  const [fileReading, setFileReading] = useState(false);
  const fileReadId = useRef(0);
  const [documentMediaType, setDocumentMediaType] = useState("text/plain");
  useRecoveryFocus(recovery.get("collectionId") ? `knowledge-${recovery.get("collectionId")}` : null, snapshot);

  const revision = `${overview.counts.collections}:${overview.counts.documents}:${overview.counts.embeddedChunks}:${overview.counts.pendingJobs}:${overview.counts.activeMemory}:${overview.collections.map((collection) => collection.updatedAt).join(",")}`;
  const embeddingModels = useMemo(
    () => [...new Set(nodes.flatMap((node) => node.embeddingModels))].sort(),
    [nodes],
  );
  const canManage = accessAllowed(roles, ["admin", "designer"]);
  const canSaveMemory = accessAllowed(roles, ["admin", "designer", "operator"]);
  const canExport = accessAllowed(roles, ["admin", "designer", "auditor"]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    void api.knowledge(controller.signal)
      .then((next) => {
        if (!controller.signal.aborted) {
          setSnapshot(next);
          setSnapshotProjectId(projectId);
          setError(null);
        }
      })
      .catch((requestError: unknown) => {
        if (requestError instanceof DOMException && requestError.name === "AbortError") return;
        setError(requestError instanceof Error ? requestError.message : "Не удалось загрузить knowledge base");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [projectId, revision]);

  async function refreshDetailed() {
    const next = await api.knowledge();
    setSnapshot(next);
    setSnapshotProjectId(projectId);
  }

  async function mutate(action: () => Promise<unknown>, after?: () => void) {
    setBusy(true);
    setError(null);
    try {
      await action();
      after?.();
      await Promise.all([refreshDetailed(), onChanged()]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Операция knowledge base завершилась ошибкой");
    } finally {
      setBusy(false);
    }
  }

  function createCollection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    void mutate(() => api.createKnowledgeCollection({
      name: String(data.get("name") ?? ""),
      description: String(data.get("description") ?? ""),
      embeddingModel: String(data.get("embeddingModel") ?? ""),
      chunkSize: Number(data.get("chunkSize") ?? 1_200),
      chunkOverlap: Number(data.get("chunkOverlap") ?? 160),
      topK: Number(data.get("topK") ?? 6),
    }), () => setCreateOpen(false));
  }

  function resetDocumentForm() {
    fileReadId.current += 1;
    setFileReading(false);
    setDocumentContent("");
    setDocumentName("");
    setDocumentMediaType("text/plain");
    setDocumentBase64(null);
  }

  function ingestDocument(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ingestCollectionId || fileReading) return;
    const data = new FormData(event.currentTarget);
    const metadata = { name: documentName, sourceUri: String(data.get("sourceUri") ?? ""), mediaType: documentMediaType };
    void mutate(async () => {
      const document = documentBase64 !== null
        ? await api.uploadKnowledgeDocument(ingestCollectionId, { ...metadata, contentBase64: documentBase64 })
        : await api.ingestKnowledgeDocument(ingestCollectionId, { ...metadata, content: documentContent });
      window.location.hash = knowledgeDocumentHref(projectId, document.id);
    }, () => {
      setIngestCollectionId(null);
      resetDocumentForm();
    });
  }

  async function readDocumentFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    resetDocumentForm();
    if (!file) return;
    const readId = fileReadId.current;
    setFileReading(true);
    setError(null);
    try {
      const selection = await readKnowledgeFile(file);
      if (readId !== fileReadId.current) return;
      setDocumentName(selection.name);
      setDocumentMediaType(selection.mediaType);
      if ("contentBase64" in selection) setDocumentBase64(selection.contentBase64);
      else setDocumentContent(selection.content);
    } catch (reason) {
      if (readId === fileReadId.current) setError(reason instanceof Error ? reason.message : "Не удалось прочитать файл");
    } finally {
      if (readId === fileReadId.current) setFileReading(false);
    }
  }

  function saveMemory(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const kind = data.get("kind") === "episodic" ? "episodic" : "working";
    void mutate(() => api.saveMemory({
      kind,
      content: String(data.get("content") ?? ""),
      agentId: String(data.get("agentId") ?? "") || null,
      ttlSeconds: kind === "working" ? Number(data.get("ttlSeconds") ?? 86_400) : undefined,
    }), () => setMemoryOpen(false));
  }

  async function removeCollection(id: string, name: string) {
    const decision = await requestAction({
      title: "Удалить коллекцию знаний?",
      description: "Агенты перестанут находить материалы этой коллекции при следующих запусках.",
      subject: name,
      subjectLabel: "Коллекция",
      impact: "Коллекция, документы, chunks и embeddings будут удалены. Артефакты и trace уже завершённых запусков не изменятся.",
      recovery: "Создайте коллекцию заново, повторно загрузите источники и дождитесь индексации.",
      confirmLabel: "Удалить коллекцию",
      tone: "danger",
    });
    if (!decision.confirmed) return;
    void mutate(() => api.deleteKnowledgeCollection(id));
  }

  async function removeDocument(document: KnowledgeDocument) {
    const decision = await requestAction({
      title: "Удалить документ?",
      description: "Документ больше не будет участвовать в поиске по базе знаний.",
      subject: document.name,
      subjectLabel: "Документ",
      impact: "Исходный документ, его chunks и embeddings будут удалены из коллекции.",
      recovery: "Загрузите документ повторно и дождитесь новой индексации.",
      confirmLabel: "Удалить документ",
      tone: "danger",
    });
    if (!decision.confirmed) return;
    void mutate(() => api.deleteKnowledgeDocument(document.id));
  }

  async function removeMemory(entry: MemoryEntry) {
    const memoryKind = entry.kind === "episodic" ? "эпизодическую" : "рабочую";
    const decision = await requestAction({
      title: `Удалить ${memoryKind} память?`,
      description: "Эта запись больше не будет доступна агентам в текущем проекте.",
      subject: entry.content.slice(0, 120),
      subjectLabel: "Запись памяти",
      impact: "Запись и её индекс будут удалены из активной памяти.",
      recovery: "Содержимое можно сохранить повторно только вручную, если исходный текст остался у оператора.",
      confirmLabel: "Удалить память",
      tone: "danger",
    });
    if (!decision.confirmed) return;
    void mutate(() => api.deleteMemory(entry.id));
  }

  async function exportKnowledge() {
    setBusy(true);
    setError(null);
    try {
      const blob = await api.exportKnowledge();
      downloadBlob(blob, `agat-knowledge-${projectId}.json`);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось экспортировать knowledge base");
    } finally {
      setBusy(false);
    }
  }

  const data = snapshot && snapshotProjectId === projectId
    ? snapshot
    : { ...overview, documents: [], memory: [] };

  return (
    <main className="main-column section-page knowledge-page" id="knowledge">
      <div className="page-title page-title--section knowledge-page__title">
        <div>
          <h1>Локальные знания</h1>
          <p>Project-scoped RAG, управляемая память и provenance каждого найденного фрагмента</p>
        </div>
        <div className="knowledge-page__actions">
          <button className="button button--secondary" type="button" disabled={busy || !canExport} onClick={() => void exportKnowledge()}>
            <Icon name="save" size={16} />Экспорт
          </button>
          <button className="button button--secondary" type="button" disabled={busy || !canSaveMemory} onClick={() => setMemoryOpen((value) => !value)}>
            <Icon name="plus" size={16} />Память
          </button>
          <button className="button button--primary" type="button" disabled={busy || !canManage} onClick={() => setCreateOpen((value) => !value)}>
            <Icon name="plus" size={16} />Коллекция
          </button>
        </div>
      </div>

      <div className="live-source knowledge-locality">
        <span><i />LOCAL ONLY</span>
        <p>Worker вычисляет embeddings на своём model endpoint; coordinator хранит chunks и vectors, но не обращается к модели.</p>
        <strong>{embeddingModels.length} embedding models</strong>
      </div>

      <section className="knowledge-summary" aria-label="Состояние локальной базы знаний">
        <div><strong>{data.counts.collections}</strong><span>коллекций</span></div>
        <div><strong>{data.counts.documents}</strong><span>документов</span></div>
        <div><strong>{data.counts.embeddedChunks}/{data.counts.chunks}</strong><span>chunks готовы</span></div>
        <div><strong>{data.counts.activeMemory}</strong><span>memory entries</span></div>
      </section>

      {error ? <button className="connection-toast knowledge-page__error" type="button" onClick={() => setError(null)}>{error}</button> : null}

      {recovery.get("documentId") ? <KnowledgeDocumentPreview
        key={`${projectId}:${recovery.get("documentId")}`} documentId={recovery.get("documentId")!} projectId={projectId}
        requestedProjectId={recovery.get("projectId")} chunkId={recovery.get("chunkId")} expectedSha256={recovery.get("sha256")}
        pageNumber={Number(recovery.get("page") || 1)} revision={revision}
        onClose={() => { window.location.hash = "#knowledge"; }} /> : null}

      {createOpen ? (
        <form className="knowledge-form" onSubmit={createCollection}>
          <header><div><strong>Новая коллекция</strong><small>Embedding-модель закрепляется и не меняется после индексации</small></div><button className="icon-button" type="button" onClick={() => setCreateOpen(false)} aria-label="Закрыть"><Icon name="close" /></button></header>
          <div className="knowledge-form__grid">
            <label className="field"><span>Название</span><input name="name" required maxLength={120} autoFocus placeholder="Product handbook" /></label>
            <label className="field"><span>Embedding-модель</span><input name="embeddingModel" required maxLength={200} defaultValue={embeddingModels[0] ?? "embeddinggemma"} list="embedding-model-options" /></label>
            <label className="field"><span>Chunk size, символов</span><input name="chunkSize" type="number" min={400} max={4_000} defaultValue={1_200} /></label>
            <label className="field"><span>Overlap, символов</span><input name="chunkOverlap" type="number" min={0} max={1_000} defaultValue={160} /></label>
            <label className="field"><span>Top K</span><input name="topK" type="number" min={1} max={20} defaultValue={6} /></label>
          </div>
          <label className="field"><span>Описание</span><textarea name="description" rows={2} maxLength={1_000} placeholder="Какие знания входят и для каких задач" /></label>
          <footer><button className="button button--primary" type="submit" disabled={busy}>{busy ? "Создаём…" : "Создать коллекцию"}</button></footer>
          <datalist id="embedding-model-options">{embeddingModels.map((model) => <option value={model} key={model} />)}</datalist>
        </form>
      ) : null}

      {memoryOpen ? (
        <form className="knowledge-form" onSubmit={saveMemory}>
          <header><div><strong>Сохранить память явно</strong><small>Working memory имеет TTL; episodic не истекает и удаляется только вручную</small></div><button className="icon-button" type="button" onClick={() => setMemoryOpen(false)} aria-label="Закрыть"><Icon name="close" /></button></header>
          <div className="knowledge-form__grid">
            <label className="field"><span>Тип</span><select name="kind" defaultValue="working"><option value="working">Working · с TTL</option><option value="episodic">Episodic · постоянная</option></select></label>
            <label className="field"><span>Агент</span><select name="agentId" defaultValue=""><option value="">Все агенты проекта</option>{agents.map((agent) => <option value={agent.id} key={agent.id}>{agent.name}</option>)}</select></label>
            <label className="field"><span>TTL working memory, сек.</span><input name="ttlSeconds" type="number" min={60} max={2_592_000} defaultValue={86_400} /></label>
          </div>
          <label className="field"><span>Содержимое</span><textarea name="content" rows={4} required maxLength={20_000} placeholder="Проверенный факт, решение оператора или контекст для следующих запусков" /></label>
          <footer><button className="button button--primary" type="submit" disabled={busy}>{busy ? "Сохраняем…" : "Сохранить явно"}</button></footer>
        </form>
      ) : null}

      {data.collections.length === 0 ? (
        <section className="large-empty-state">
          <Icon name="knowledge" size={34} />
          <h2>Коллекций пока нет</h2>
          <p>Создайте коллекцию, выберите локальную embedding-модель и добавьте PDF, DOCX или текстовый документ. После индексации коллекцию можно подключить к запуску или процессу.</p>
          {canManage ? <button className="button button--primary" type="button" onClick={() => setCreateOpen(true)}><Icon name="plus" size={16} />Создать первую коллекцию</button> : null}
        </section>
      ) : (
        <section className="knowledge-collection-list" aria-label="Knowledge collections">
          {data.collections.map((collection) => {
            const documents = data.documents.filter((document) => document.collectionId === collection.id);
            const progress = collection.chunkCount === 0 ? 0 : Math.round(collection.embeddedChunks / collection.chunkCount * 100);
            return (
              <article className="knowledge-collection" id={`knowledge-${collection.id}`} tabIndex={-1} key={collection.id}>
                <header>
                  <span className="knowledge-collection__icon"><Icon name="knowledge" size={21} /></span>
                  <div>
                    <h2>{collection.name}</h2>
                    <p>{collection.description || "Описание не задано"}</p>
                  </div>
                  <span className={`knowledge-worker-state${collection.embeddingWorkers > 0 ? " is-online" : ""}`}><i />{collection.embeddingWorkers > 0 ? `${collection.embeddingWorkers} worker` : "нет embedding worker"}</span>
                  <div className="knowledge-collection__actions">
                    <button className="button button--secondary" type="button" disabled={!canManage || busy} onClick={() => { resetDocumentForm(); setIngestCollectionId(collection.id); }}><Icon name="plus" size={14} />Документ</button>
                    <button className="icon-button icon-button--danger" type="button" disabled={!canManage || busy} onClick={() => void removeCollection(collection.id, collection.name)} aria-label={`Удалить коллекцию ${collection.name}`}><Icon name="trash" size={16} /></button>
                  </div>
                </header>
                <div className="knowledge-collection__meta">
                  <span><small>MODEL</small><code>{collection.embeddingModel}</code></span>
                  <span><small>CHUNK</small><strong>{collection.chunkSize} / {collection.chunkOverlap}</strong></span>
                  <span><small>TOP K</small><strong>{collection.topK}</strong></span>
                  <span><small>DOCS</small><strong>{collection.readyDocuments}/{collection.documentCount}</strong></span>
                </div>
                <div className="knowledge-progress"><span><i style={{ width: `${progress}%` }} /></span><small>{collection.embeddedChunks} из {collection.chunkCount} chunks · {progress}%</small></div>

                {ingestCollectionId === collection.id ? (
                  <form className="knowledge-ingest" onSubmit={ingestDocument}>
                    <header><strong>Добавить документ</strong><button className="icon-button" type="button" onClick={() => { resetDocumentForm(); setIngestCollectionId(null); }} aria-label="Закрыть"><Icon name="close" size={17} /></button></header>
                    <div className="knowledge-form__grid">
                      <label className="field"><span>Название</span><input required maxLength={180} value={documentName} onChange={(event) => setDocumentName(event.target.value)} placeholder="Privacy policy.md" /></label>
                      <label className="field"><span>Source URI</span><input name="sourceUri" maxLength={2_048} placeholder="agat://handbook/privacy" /></label>
                      <label className="field"><span>Media type</span><input required readOnly={documentBase64 !== null} maxLength={120} value={documentMediaType} onChange={(event) => setDocumentMediaType(event.target.value)} /></label>
                      <label className="field knowledge-file"><span>PDF/DOCX до 5 МиБ · текст до 2 МБ</span><input disabled={busy} type="file" accept="application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/*,.pdf,.docx,.md,.txt,.csv,.json" onChange={(event) => void readDocumentFile(event)} /></label>
                    </div>
                    {documentBase64 !== null ? <p>Файл выбран. После загрузки откроется предпросмотр извлечённого текста.</p> : <label className="field"><span>Текст</span><textarea required rows={7} maxLength={2_000_000} value={documentContent} onChange={(event) => setDocumentContent(event.target.value)} placeholder="Вставьте текст или выберите файл" /></label>}
                    <footer><small>Текст извлекается локально. PDF-сканы без текстового слоя требуют OCR.</small><button className="button button--primary" type="submit" disabled={busy || fileReading}>{fileReading ? "Читаем файл…" : busy ? "Разбираем документ…" : "Загрузить и индексировать"}</button></footer>
                  </form>
                ) : null}

                <div className="knowledge-documents">
                  {documents.length === 0 ? <p>Документов пока нет.</p> : documents.map((document) => (
                    <div className="knowledge-document" key={document.id}>
                      <span className={`knowledge-document__status is-${document.status}`}><i />{documentStatusCopy[document.status]}</span>
                      <div><a href={knowledgeDocumentHref(projectId, document.id)}>{document.name}</a><small>{document.sourceUri ?? document.mediaType} · SHA {document.contentSha256.slice(0, 10)}</small>{document.error ? <em role="alert">{document.error}</em> : null}</div>
                      <span>{document.embeddedCount}/{document.chunkCount} chunks</span>
                      <time>{formatDate(document.updatedAt)}</time>
                      <button className="button button--secondary" type="button" disabled={!canManage || busy} onClick={() => void mutate(() => api.reindexKnowledgeDocument(document.id))} aria-label={`Переиндексировать ${document.name}`}>Переиндексировать</button>
                      <button className="icon-button icon-button--danger" type="button" disabled={!canManage || busy} onClick={() => void removeDocument(document)} aria-label={`Удалить документ ${document.name}`}><Icon name="trash" size={15} /></button>
                    </div>
                  ))}
                </div>
              </article>
            );
          })}
        </section>
      )}

      <section className="knowledge-memory">
        <header><div><span>MEMORY</span><h2>Управляемая память</h2></div><small>Автоматической вечной памяти нет: episodic записи появляются только после явного сохранения.</small></header>
        {data.memory.length === 0 ? <p className="knowledge-memory__empty">Активных memory entries нет.</p> : (
          <div>{data.memory.map((entry) => (
            <article key={entry.id}>
              <span className={`memory-kind memory-kind--${entry.kind}`}>{entry.kind}</span>
              <p>{entry.content}</p>
              <small>{entry.agentName ?? "Все агенты"} · {entry.expiresAt ? `истекает ${formatDate(entry.expiresAt)}` : "без срока"} · SHA {entry.contentSha256.slice(0, 10)}</small>
              <button className="icon-button icon-button--danger" type="button" disabled={!canManage || busy} onClick={() => void removeMemory(entry)} aria-label="Удалить memory entry"><Icon name="trash" size={15} /></button>
            </article>
          ))}</div>
        )}
      </section>

      {loading ? <p className="knowledge-loading">Обновляем состояние индексации…</p> : null}
    </main>
  );
}
