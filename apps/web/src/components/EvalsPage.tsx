import { useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";

import { api } from "../lib/api";
import type {
  Agent,
  AgatRole,
  CreateEvalExperimentRequest,
  CreateGoldenDatasetRequest,
  EvalRubricCriterion,
  EvalSnapshot,
  GoldenDataset,
  GoldenEvalExperiment,
  GoldenEvalItem,
  KnowledgeCollection,
  PromptRegistryEntry,
} from "../types";
import { AccessibleTabList, TabPanel, type TabDefinition } from "./AccessibleTabs";
import { Icon } from "./Icon";
import { ModalLayer } from "./ModalLayer";

type EvalTab = "experiments" | "datasets" | "prompts";

interface EvalsPageProps {
  projectId: string;
  agents: Agent[];
  models: string[];
  collections: KnowledgeCollection[];
  roles: AgatRole[];
  onChanged: () => Promise<void>;
}

interface DatasetExampleDraft {
  name: string;
  input: string;
  referenceOutput: string;
  requiredTerms: string;
  forbiddenTerms: string;
  knowledgeCollectionIds: string[];
}

interface DatasetDraft {
  datasetId: string | null;
  name: string;
  description: string;
  changeNote: string;
  rubric: EvalRubricCriterion[];
  examples: DatasetExampleDraft[];
}

const emptyExample = (): DatasetExampleDraft => ({
  name: "",
  input: "",
  referenceOutput: "",
  requiredTerms: "",
  forbiddenTerms: "",
  knowledgeCollectionIds: [],
});

const emptyDataset = (): DatasetDraft => ({
  datasetId: null,
  name: "",
  description: "",
  changeNote: "",
  rubric: [],
  examples: [emptyExample()],
});

const statusCopy: Record<string, string> = {
  queued: "В очереди",
  running: "Выполняется",
  scoring: "Нужна оценка",
  completed: "Завершён",
  failed: "Ошибка",
  cancelled: "Отменён",
  pending: "Ожидает",
};

const gateCopy = { pass: "PASS", fail: "FAIL", pending: "PENDING" } as const;

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function splitTerms(value: string): string[] {
  return value.split(/[,\n]/u).map((term) => term.trim()).filter(Boolean);
}

function can(roles: AgatRole[], allowed: AgatRole[]): boolean {
  return roles.some((role) => allowed.includes(role));
}

function Modal({ title, subtitle, onClose, children }: {
  title: string;
  subtitle: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <ModalLayer className="eval-modal" label={title} onClose={onClose}>
      <div className="eval-modal__surface">
        <header>
          <div><h2>{title}</h2><p>{subtitle}</p></div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Закрыть"><Icon name="close" /></button>
        </header>
        {children}
      </div>
    </ModalLayer>
  );
}

function Gate({ name, value }: { name: string; value: "pass" | "fail" | "pending" }) {
  return <span className={`eval-gate eval-gate--${value}`}><small>{name}</small>{gateCopy[value]}</span>;
}

export function EvalsPage({ projectId, agents, models, collections, roles, onChanged }: EvalsPageProps) {
  const [tab, setTab] = useState<EvalTab>("experiments");
  const [snapshot, setSnapshot] = useState<EvalSnapshot | null>(null);
  const [detail, setDetail] = useState<GoldenEvalExperiment | null>(null);
  const [selectedExperimentId, setSelectedExperimentId] = useState<string | null>(null);
  const [selectedPromptId, setSelectedPromptId] = useState<string | null>(null);
  const [selectedDatasetId, setSelectedDatasetId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [promptEditor, setPromptEditor] = useState<{ promptId: string | null; content: string; note: string; name: string } | null>(null);
  const [datasetEditor, setDatasetEditor] = useState<DatasetDraft | null>(null);
  const [experimentEditor, setExperimentEditor] = useState<CreateEvalExperimentRequest | null>(null);
  const [reviewItem, setReviewItem] = useState<GoldenEvalItem | null>(null);
  const [reviewScores, setReviewScores] = useState<Record<string, number>>({});
  const [reviewRationale, setReviewRationale] = useState("");
  const [judgeModel, setJudgeModel] = useState("");

  const canDesign = can(roles, ["admin", "designer"]);
  const canRun = can(roles, ["admin", "designer", "operator"]);
  const canReview = can(roles, ["admin", "designer", "operator", "auditor"]);
  const experimentPrerequisite = !canRun
    ? "Текущая роль может только просматривать качество."
    : loading
      ? "Загружаем доступные datasets и prompts."
      : !snapshot?.datasets.length
        ? "Сначала создайте golden dataset."
        : !snapshot.prompts.length
          ? "Сначала зафиксируйте prompt в registry."
          : null;
  const evalTabs: readonly TabDefinition<EvalTab>[] = [
    { id: "experiments", label: <>Experiments <span>{snapshot?.counts.experiments ?? 0}</span></> },
    { id: "datasets", label: <>Datasets <span>{snapshot?.counts.datasets ?? 0}</span></> },
    { id: "prompts", label: <>Prompt registry <span>{snapshot?.counts.prompts ?? 0}</span></> },
  ];

  const load = useCallback(async (signal?: AbortSignal, requestedExperimentId: string | null = selectedExperimentId) => {
    try {
      const snapshotPromise = api.evals(signal);
      const detailPromise = requestedExperimentId ? api.evalExperiment(requestedExperimentId, signal) : Promise.resolve(null);
      const [next, selectedDetail] = await Promise.all([snapshotPromise, detailPromise]);
      if (signal?.aborted) return;
      const targetId = requestedExperimentId && next.experiments.some((item) => item.id === requestedExperimentId)
        ? requestedExperimentId
        : next.experiments[0]?.id ?? null;
      setSnapshot(next);
      setSelectedExperimentId(targetId);
      setSelectedPromptId((current) => next.prompts.some((prompt) => prompt.id === current)
        ? current
        : next.prompts[0]?.id ?? null);
      setSelectedDatasetId((current) => next.datasets.some((dataset) => dataset.id === current)
        ? current
        : next.datasets[0]?.id ?? null);
      if (selectedDetail && selectedDetail.id === targetId) setDetail(selectedDetail);
      else if (targetId) setDetail(await api.evalExperiment(targetId, signal));
      else setDetail(null);
      setError(null);
    } catch (requestError) {
      if (requestError instanceof DOMException && requestError.name === "AbortError") return;
      setError(requestError instanceof Error ? requestError.message : "Не удалось загрузить golden eval");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [selectedExperimentId]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setSnapshot(null);
    setDetail(null);
    setSelectedExperimentId(null);
    void load(controller.signal, null);
    return () => controller.abort();
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const active = snapshot?.experiments.some((experiment) =>
      ["queued", "running", "scoring"].includes(experiment.status),
    );
    if (!active) return;
    const timer = window.setInterval(() => void load(), 4_000);
    return () => window.clearInterval(timer);
  }, [load, snapshot?.experiments]);

  async function mutate(action: () => Promise<unknown>, close?: () => void) {
    setBusy(true);
    setError(null);
    try {
      await action();
      close?.();
      await Promise.all([load(), onChanged()]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Операция eval завершилась ошибкой");
    } finally {
      setBusy(false);
    }
  }

  async function selectExperiment(id: string) {
    setSelectedExperimentId(id);
    setBusy(true);
    try {
      setDetail(await api.evalExperiment(id));
      setError(null);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось открыть experiment");
    } finally {
      setBusy(false);
    }
  }

  const selectedPrompt = snapshot?.prompts.find((prompt) => prompt.id === selectedPromptId) ?? null;
  const selectedDataset = snapshot?.datasets.find((dataset) => dataset.id === selectedDatasetId) ?? null;
  const detailDataset = snapshot?.datasets.find((dataset) => dataset.id === detail?.datasetId) ?? null;
  const detailDatasetVersion = detailDataset?.versions.find((version) => version.version === detail?.datasetVersion) ?? null;

  function openPromptVersion(prompt: PromptRegistryEntry) {
    const source = prompt.versions.find((version) => version.version === prompt.activeVersion) ?? prompt.versions[0];
    setPromptEditor({ promptId: prompt.id, content: source?.content ?? "", note: "", name: prompt.name });
  }

  function savePrompt(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!promptEditor) return;
    if (promptEditor.promptId) {
      void mutate(() => api.createPromptVersion(promptEditor.promptId!, {
        content: promptEditor.content,
        changeNote: promptEditor.note,
      }), () => setPromptEditor(null));
    } else {
      void mutate(() => api.createPrompt({
        name: promptEditor.name,
        description: promptEditor.note,
        content: promptEditor.content,
      }), () => setPromptEditor(null));
    }
  }

  function openDatasetVersion(dataset: GoldenDataset) {
    const version = dataset.versions.find((candidate) => candidate.version === dataset.currentVersion) ?? dataset.versions[0];
    setDatasetEditor({
      datasetId: dataset.id,
      name: dataset.name,
      description: dataset.description,
      changeNote: "",
      rubric: version?.rubric.map((criterion) => ({ ...criterion })) ?? [],
      examples: version?.examples.map((example) => ({
        name: example.name,
        input: example.input,
        referenceOutput: example.referenceOutput,
        requiredTerms: example.requiredTerms.join(", "),
        forbiddenTerms: example.forbiddenTerms.join(", "),
        knowledgeCollectionIds: [...example.knowledgeCollectionIds],
      })) ?? [emptyExample()],
    });
  }

  function saveDataset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!datasetEditor) return;
    const payload: CreateGoldenDatasetRequest = {
      name: datasetEditor.name,
      description: datasetEditor.description,
      changeNote: datasetEditor.changeNote,
      rubric: datasetEditor.rubric,
      examples: datasetEditor.examples.map((example) => ({
        name: example.name,
        input: example.input,
        referenceOutput: example.referenceOutput,
        requiredTerms: splitTerms(example.requiredTerms),
        forbiddenTerms: splitTerms(example.forbiddenTerms),
        knowledgeCollectionIds: example.knowledgeCollectionIds,
      })),
    };
    void mutate(
      () => datasetEditor.datasetId
        ? api.createEvalDatasetVersion(datasetEditor.datasetId, payload)
        : api.createEvalDataset(payload),
      () => setDatasetEditor(null),
    );
  }

  function openExperiment() {
    const dataset = snapshot?.datasets[0];
    const prompt = snapshot?.prompts[0];
    const agentId = prompt?.agentId ?? agents[0]?.id ?? "";
    setExperimentEditor({
      name: "",
      datasetId: dataset?.id ?? "",
      datasetVersion: dataset?.currentVersion ?? 1,
      agentId,
      promptId: prompt?.id ?? "",
      promptVersion: prompt?.activeVersion ?? 1,
      model: prompt?.activeModel ?? null,
      minQualityScore: 80,
    });
  }

  function saveExperiment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!experimentEditor) return;
    void mutate(async () => {
      const created = await api.createEvalExperiment(experimentEditor);
      setSelectedExperimentId(created.id);
      setDetail(created);
      return created;
    }, () => setExperimentEditor(null));
  }

  function openReview(item: GoldenEvalItem) {
    const scores = Object.fromEntries((detailDatasetVersion?.rubric ?? []).map((criterion) => [criterion.id, 80]));
    setReviewScores(scores);
    setReviewRationale("");
    setReviewItem(item);
  }

  function saveReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!reviewItem) return;
    const rubric = detailDatasetVersion?.rubric ?? [];
    void mutate(() => api.reviewEvalItem(reviewItem.id, {
      ...(rubric.length > 0 ? { scores: reviewScores } : { overallScore: reviewScores.overall ?? 80 }),
      rationale: reviewRationale,
    }), () => setReviewItem(null));
  }

  function startJudge() {
    if (!detail || !judgeModel.trim()) return;
    void mutate(() => api.judgeEvalExperiment(detail.id, judgeModel.trim()));
  }

  function promote(prompt: PromptRegistryEntry, version: number, experiment: GoldenEvalExperiment) {
    if (!window.confirm(`Активировать ${prompt.name} v${version} и модель ${experiment.model ?? "auto"}?`)) return;
    void mutate(() => api.promotePrompt(prompt.id, {
      version,
      model: experiment.model,
      experimentId: experiment.id,
    }));
  }

  const passingByPromptVersion = useMemo(() => {
    const result = new Map<string, GoldenEvalExperiment>();
    for (const experiment of snapshot?.experiments ?? []) {
      if (experiment.gates.overall !== "pass") continue;
      result.set(`${experiment.promptId}:${experiment.promptVersion}`, experiment);
    }
    return result;
  }, [snapshot?.experiments]);

  if (loading && !snapshot) {
    return <main className="main-column section-page evals-page"><div className="process-page-loading"><span className="boot-mark" /><strong>Загружаем оценку качества</strong></div></main>;
  }

  return (
    <main className="main-column section-page evals-page" id="evals">
      <div className="page-title page-title--section evals-page__title">
        <div>
          <h1>Качество</h1>
          <p>Неизменяемые datasets, prompt registry и release gate для prompt/model/RAG изменений</p>
        </div>
        <div className="page-title__action-stack">
          <button className="button button--primary" type="button" onClick={openExperiment} disabled={Boolean(experimentPrerequisite)} aria-describedby={experimentPrerequisite ? "experiment-prerequisite" : undefined}>
            <Icon name="play" size={17} />Новый experiment
          </button>
          {experimentPrerequisite ? <p id="experiment-prerequisite">{experimentPrerequisite}</p> : null}
        </div>
      </div>

      {error ? <button className="evals-error" type="button" onClick={() => setError(null)}><Icon name="warning" size={16} />{error}</button> : null}

      <section className="evals-summary" aria-label="Сводка golden eval">
        <div><span>Prompt versions</span><strong>{snapshot?.counts.promptVersions ?? 0}</strong><small>{snapshot?.counts.prompts ?? 0} registries</small></div>
        <div><span>Golden examples</span><strong>{snapshot?.datasets.reduce((sum, dataset) => sum + (dataset.versions[0]?.examples.length ?? 0), 0) ?? 0}</strong><small>{snapshot?.counts.datasetVersions ?? 0} dataset versions</small></div>
        <div><span>Experiments</span><strong>{snapshot?.counts.experiments ?? 0}</strong><small>{snapshot?.counts.passingExperiments ?? 0} прошли gate</small></div>
        <div><span>Активный gate</span><strong>{detail?.qualityScore === null || detail?.qualityScore === undefined ? "N/A" : `${detail.qualityScore}`}</strong><small>порог {detail?.minQualityScore ?? 80}/100</small></div>
      </section>

      <AccessibleTabList activeTab={tab} ariaLabel="Разделы оценки качества" className="evals-tabs" idPrefix="quality" tabs={evalTabs} onChange={setTab} />

      <TabPanel active={tab === "experiments"} className="evals-workspace" idPrefix="quality" tabId="experiments">
          <aside className="evals-list">
            <header><div><strong>Batch runs</strong><small>Dataset × prompt × model</small></div><button type="button" onClick={openExperiment} disabled={Boolean(experimentPrerequisite)} aria-label="Новый experiment" aria-describedby={experimentPrerequisite ? "experiment-prerequisite" : undefined}><Icon name="plus" size={15} /></button></header>
            {(snapshot?.experiments ?? []).map((experiment) => (
              <button className={selectedExperimentId === experiment.id ? "is-active" : ""} type="button" onClick={() => void selectExperiment(experiment.id)} key={experiment.id}>
                <span><strong>{experiment.name}</strong><small>{experiment.datasetName} v{experiment.datasetVersion} · {experiment.model ?? "auto"}</small></span>
                <i className={`eval-status eval-status--${experiment.gates.overall}`}>{experiment.qualityScore ?? "—"}</i>
              </button>
            ))}
            {!snapshot?.experiments.length ? <p className="evals-list__empty">Создайте первый experiment после dataset.</p> : null}
          </aside>

          <div className="eval-detail">
            {detail ? (
              <>
                <header className="eval-detail__head">
                  <div><span>EXPERIMENT · {statusCopy[detail.status] ?? detail.status}</span><h2>{detail.name}</h2><p>{detail.datasetName} v{detail.datasetVersion} · {detail.promptName} v{detail.promptVersion} · {detail.model ?? "model auto"}</p></div>
                  <div className="eval-detail__score"><strong>{detail.qualityScore ?? "N/A"}</strong><small>quality / 100</small></div>
                </header>
                <div className="eval-gates">
                  <Gate name="completion" value={detail.gates.completion} />
                  <Gate name="quality" value={detail.gates.quality} />
                  <Gate name="knowledge" value={detail.gates.knowledge} />
                  <Gate name="release" value={detail.gates.overall} />
                </div>
                <div className="eval-detail__meta">
                  <span><small>Порог</small>{detail.minQualityScore}/100</span>
                  <span><small>Примеры</small>{detail.counts.completed}/{detail.counts.total}</span>
                  <span><small>Human</small>{detail.counts.humanReviewed}</span>
                  <span><small>Judge</small>{detail.counts.judgeReviewed}</span>
                  <span><small>Создан</small>{formatDate(detail.createdAt)}</span>
                </div>
                <div className="eval-judge-bar">
                  <div><strong>Optional model judge</strong><small>Запускается как отдельный локальный run; raw output hash остаётся в audit trail.</small></div>
                  <input value={judgeModel} list="eval-model-options" placeholder="judge model" onChange={(event) => setJudgeModel(event.target.value)} disabled={!canRun || detail.gates.completion !== "pass"} />
                  <button className="button button--secondary" type="button" disabled={busy || !canRun || !judgeModel.trim() || detail.gates.completion !== "pass"} onClick={startJudge}>Запустить judge</button>
                </div>
                <div className="eval-items">
                  {detail.items.map((item) => (
                    <article className="eval-item" key={item.id}>
                      <header>
                        <span className={`status-pill status-pill--${item.runStatus}`}>{statusCopy[item.runStatus] ?? item.runStatus}</span>
                        <div><strong>{item.position + 1}. {item.name}</strong><small>run {item.runId.slice(0, 8)} · {item.outputCharacters} chars</small></div>
                        <div className="eval-item__score"><strong>{item.qualityScore ?? "—"}</strong><small>{item.qualitySource ?? "нужна оценка"}</small></div>
                        <button className="button button--secondary" type="button" onClick={() => openReview(item)} disabled={!canReview || item.runStatus !== "completed"}>Оценить</button>
                      </header>
                      <div className="eval-item__body">
                        <div><span>INPUT</span><p>{item.input}</p></div>
                        <div><span>REFERENCE</span><p>{item.referenceOutput || "Не задан — используйте rubric"}</p></div>
                        <div><span>OUTPUT</span><p>{item.output || "Output ещё не получен"}</p></div>
                      </div>
                      {item.deterministicDetails.length ? <div className="eval-checks">{item.deterministicDetails.map((check, index) => <span className={check.pass ? "is-pass" : "is-fail"} key={`${check.kind}-${check.term}-${index}`}>{check.pass ? "✓" : "×"} {check.kind === "required_term" ? "must" : "must not"}: {check.term}</span>)}</div> : null}
                      {item.judgeError ? <p className="eval-item__error">Judge: {item.judgeError}</p> : null}
                      {item.reviews?.length ? <details className="eval-audit"><summary>Audit trail · {item.reviews.length}</summary>{item.reviews.map((review) => <div key={review.id}><strong>{review.kind} · {review.overallScore}</strong><span>{review.reviewer} · {formatDate(review.createdAt)}</span><p>{review.rationale}</p></div>)}</details> : null}
                    </article>
                  ))}
                </div>
              </>
            ) : <div className="eval-empty"><Icon name="repeat" size={28} /><strong>Нет experiments</strong><p>Сначала создайте golden dataset, затем зафиксируйте prompt/model candidate.</p></div>}
          </div>
      </TabPanel>

      <TabPanel active={tab === "datasets"} className="registry-layout" idPrefix="quality" tabId="datasets">
          <aside className="registry-list">
            <header><div><strong>Golden datasets</strong><small>Каждая версия неизменяема</small></div><button type="button" onClick={() => setDatasetEditor(emptyDataset())} disabled={!canDesign} aria-label="Новый golden dataset"><Icon name="plus" size={15} /></button></header>
            {(snapshot?.datasets ?? []).map((dataset) => <button className={selectedDatasetId === dataset.id ? "is-active" : ""} type="button" onClick={() => setSelectedDatasetId(dataset.id)} key={dataset.id}><span><strong>{dataset.name}</strong><small>v{dataset.currentVersion} · {dataset.versions[0]?.examples.length ?? 0} examples</small></span><code>{dataset.versions[0]?.contentSha256.slice(0, 7)}</code></button>)}
          </aside>
          <div className="registry-detail">
            {selectedDataset ? <>
              <header><div><span>GOLDEN DATASET</span><h2>{selectedDataset.name}</h2><p>{selectedDataset.description || "Без описания"}</p></div><button className="button button--secondary" type="button" onClick={() => openDatasetVersion(selectedDataset)} disabled={!canDesign}><Icon name="plus" size={15} />Новая версия</button></header>
              <div className="registry-versions">{selectedDataset.versions.map((version) => <article key={version.version}><div className="registry-version__head"><strong>v{version.version}{version.active ? " · CURRENT" : ""}</strong><code>{version.contentSha256.slice(0, 12)}</code><time>{formatDate(version.createdAt)}</time></div><p>{version.changeNote || "Без комментария"}</p><div className="dataset-version__stats"><span>{version.examples.length} examples</span><span>{version.rubric.length} rubric criteria</span><span>{version.knowledgeSnapshot.collectionIds?.length ?? 0} knowledge collections</span></div><details><summary>Примеры и rubric</summary>{version.rubric.map((criterion) => <p key={criterion.id}><strong>{criterion.label}</strong> · weight {criterion.weight} · {criterion.description}</p>)}{version.examples.map((example) => <p key={example.id}><strong>{example.position + 1}. {example.name}</strong> · {example.input}</p>)}</details></article>)}</div>
            </> : <div className="eval-empty"><strong>Dataset не выбран</strong></div>}
          </div>
      </TabPanel>

      <TabPanel active={tab === "prompts"} className="registry-layout" idPrefix="quality" tabId="prompts">
          <aside className="registry-list">
            <header><div><strong>Prompt registry</strong><small>Immutable versions + active alias</small></div><button type="button" onClick={() => setPromptEditor({ promptId: null, content: "", note: "", name: "" })} disabled={!canDesign} aria-label="Новый prompt"><Icon name="plus" size={15} /></button></header>
            {(snapshot?.prompts ?? []).map((prompt) => <button className={selectedPromptId === prompt.id ? "is-active" : ""} type="button" onClick={() => setSelectedPromptId(prompt.id)} key={prompt.id}><span><strong>{prompt.name}</strong><small>{prompt.agentName ?? "standalone"} · active v{prompt.activeVersion}</small></span><code>{prompt.versions.length}v</code></button>)}
          </aside>
          <div className="registry-detail">
            {selectedPrompt ? <>
              <header><div><span>PROMPT REGISTRY</span><h2>{selectedPrompt.name}</h2><p>{selectedPrompt.agentName ? `Агент: ${selectedPrompt.agentName}` : "Standalone prompt"} · model {selectedPrompt.activeModel ?? "auto"}</p></div><button className="button button--secondary" type="button" onClick={() => openPromptVersion(selectedPrompt)} disabled={!canDesign}><Icon name="plus" size={15} />Новая версия</button></header>
              <div className="registry-versions">{selectedPrompt.versions.map((version) => {
                const passing = passingByPromptVersion.get(`${selectedPrompt.id}:${version.version}`);
                return <article className={version.active ? "is-active" : ""} key={version.version}><div className="registry-version__head"><strong>v{version.version}{version.active ? " · ACTIVE" : ""}</strong><code>{version.contentSha256.slice(0, 12)}</code><time>{formatDate(version.createdAt)}</time></div><pre>{version.content}</pre><footer><span>{version.changeNote || "Без комментария"} · {version.createdBy}</span>{!version.active ? <button className="button button--secondary" type="button" disabled={!canDesign || !passing || busy} onClick={() => passing && promote(selectedPrompt, version.version, passing)}>{passing ? `Promote · score ${passing.qualityScore}` : "Нужен PASS experiment"}</button> : null}</footer></article>;
              })}</div>
            </> : <div className="eval-empty"><strong>Prompt не выбран</strong></div>}
          </div>
      </TabPanel>

      <datalist id="eval-model-options">{models.map((model) => <option value={model} key={model} />)}</datalist>

      {promptEditor ? <Modal title={promptEditor.promptId ? `Новая версия · ${promptEditor.name}` : "Новый standalone prompt"} subtitle="Сохранённую версию нельзя изменить; promotion требует PASS experiment." onClose={() => setPromptEditor(null)}><form className="eval-form" onSubmit={savePrompt}>{!promptEditor.promptId ? <label className="field"><span>Название</span><input required maxLength={120} value={promptEditor.name} onChange={(event) => setPromptEditor((current) => current ? { ...current, name: event.target.value } : current)} /></label> : null}<label className="field"><span>Prompt</span><textarea required rows={12} maxLength={20_000} value={promptEditor.content} onChange={(event) => setPromptEditor((current) => current ? { ...current, content: event.target.value } : current)} /></label><label className="field"><span>Комментарий версии</span><input maxLength={1_000} value={promptEditor.note} onChange={(event) => setPromptEditor((current) => current ? { ...current, note: event.target.value } : current)} /></label><div className="dialog-actions"><button className="button button--secondary" type="button" onClick={() => setPromptEditor(null)}>Отмена</button><button className="button button--primary" type="submit" disabled={busy}>Зафиксировать версию</button></div></form></Modal> : null}

      {datasetEditor ? <Modal title={datasetEditor.datasetId ? `Новая версия · ${datasetEditor.name}` : "Новый golden dataset"} subtitle="Reference хранится для reviewer/judge; deterministic checks используют только явные must/must not фразы." onClose={() => setDatasetEditor(null)}><form className="eval-form eval-dataset-form" onSubmit={saveDataset}><div className="eval-form__grid"><label className="field"><span>Название</span><input required disabled={Boolean(datasetEditor.datasetId)} maxLength={120} value={datasetEditor.name} onChange={(event) => setDatasetEditor((current) => current ? { ...current, name: event.target.value } : current)} /></label><label className="field"><span>Комментарий версии</span><input maxLength={1_000} value={datasetEditor.changeNote} onChange={(event) => setDatasetEditor((current) => current ? { ...current, changeNote: event.target.value } : current)} /></label></div><label className="field"><span>Описание</span><input maxLength={2_000} value={datasetEditor.description} onChange={(event) => setDatasetEditor((current) => current ? { ...current, description: event.target.value } : current)} /></label><section className="eval-form-section"><header><div><strong>Human rubric</strong><small>Weighted score 0–100</small></div><button type="button" onClick={() => setDatasetEditor((current) => current ? { ...current, rubric: [...current.rubric, { id: `criterion_${current.rubric.length + 1}`, label: "", description: "", weight: 1 }] } : current)}><Icon name="plus" size={14} />Критерий</button></header>{datasetEditor.rubric.map((criterion, index) => <div className="rubric-row" key={`${criterion.id}-${index}`}><input required placeholder="ID" value={criterion.id} onChange={(event) => setDatasetEditor((current) => current ? { ...current, rubric: current.rubric.map((item, itemIndex) => itemIndex === index ? { ...item, id: event.target.value } : item) } : current)} /><input required placeholder="Название критерия" value={criterion.label} onChange={(event) => setDatasetEditor((current) => current ? { ...current, rubric: current.rubric.map((item, itemIndex) => itemIndex === index ? { ...item, label: event.target.value } : item) } : current)} /><input placeholder="Описание" value={criterion.description} onChange={(event) => setDatasetEditor((current) => current ? { ...current, rubric: current.rubric.map((item, itemIndex) => itemIndex === index ? { ...item, description: event.target.value } : item) } : current)} /><input type="number" min={0.1} max={100} step={0.1} value={criterion.weight} onChange={(event) => setDatasetEditor((current) => current ? { ...current, rubric: current.rubric.map((item, itemIndex) => itemIndex === index ? { ...item, weight: Number(event.target.value) } : item) } : current)} /><button type="button" aria-label="Удалить критерий" onClick={() => setDatasetEditor((current) => current ? { ...current, rubric: current.rubric.filter((_item, itemIndex) => itemIndex !== index) } : current)}><Icon name="trash" size={14} /></button></div>)}</section><section className="eval-form-section"><header><div><strong>Golden examples</strong><small>От 1 до 200</small></div><button type="button" onClick={() => setDatasetEditor((current) => current ? { ...current, examples: [...current.examples, emptyExample()] } : current)}><Icon name="plus" size={14} />Пример</button></header>{datasetEditor.examples.map((example, index) => <article className="example-editor" key={index}><header><strong>Пример {index + 1}</strong>{datasetEditor.examples.length > 1 ? <button type="button" onClick={() => setDatasetEditor((current) => current ? { ...current, examples: current.examples.filter((_item, itemIndex) => itemIndex !== index) } : current)}><Icon name="trash" size={14} /></button> : null}</header><input placeholder="Название" maxLength={120} value={example.name} onChange={(event) => setDatasetEditor((current) => current ? { ...current, examples: current.examples.map((item, itemIndex) => itemIndex === index ? { ...item, name: event.target.value } : item) } : current)} /><textarea required rows={3} placeholder="Input" value={example.input} onChange={(event) => setDatasetEditor((current) => current ? { ...current, examples: current.examples.map((item, itemIndex) => itemIndex === index ? { ...item, input: event.target.value } : item) } : current)} /><textarea rows={3} placeholder="Reference output для human/judge" value={example.referenceOutput} onChange={(event) => setDatasetEditor((current) => current ? { ...current, examples: current.examples.map((item, itemIndex) => itemIndex === index ? { ...item, referenceOutput: event.target.value } : item) } : current)} /><div className="eval-form__grid"><input placeholder="Must contain: через запятую" value={example.requiredTerms} onChange={(event) => setDatasetEditor((current) => current ? { ...current, examples: current.examples.map((item, itemIndex) => itemIndex === index ? { ...item, requiredTerms: event.target.value } : item) } : current)} /><input placeholder="Must not contain: через запятую" value={example.forbiddenTerms} onChange={(event) => setDatasetEditor((current) => current ? { ...current, examples: current.examples.map((item, itemIndex) => itemIndex === index ? { ...item, forbiddenTerms: event.target.value } : item) } : current)} /></div>{collections.length ? <div className="example-editor__collections"><span>Knowledge snapshot</span>{collections.map((collection) => <label key={collection.id}><input type="checkbox" checked={example.knowledgeCollectionIds.includes(collection.id)} onChange={(event) => setDatasetEditor((current) => current ? { ...current, examples: current.examples.map((item, itemIndex) => itemIndex === index ? { ...item, knowledgeCollectionIds: event.target.checked ? [...item.knowledgeCollectionIds, collection.id] : item.knowledgeCollectionIds.filter((id) => id !== collection.id) } : item) } : current)} />{collection.name}</label>)}</div> : null}</article>)}</section><div className="dialog-actions"><button className="button button--secondary" type="button" onClick={() => setDatasetEditor(null)}>Отмена</button><button className="button button--primary" type="submit" disabled={busy}>Зафиксировать dataset</button></div></form></Modal> : null}

      {experimentEditor ? <Modal title="Новый golden experiment" subtitle="Каждый run получает exact dataset, prompt version и model pin." onClose={() => setExperimentEditor(null)}><form className="eval-form" onSubmit={saveExperiment}><label className="field"><span>Название</span><input required maxLength={120} autoFocus value={experimentEditor.name} onChange={(event) => setExperimentEditor((current) => current ? { ...current, name: event.target.value } : current)} /></label><div className="eval-form__grid"><label className="field"><span>Dataset</span><select required value={experimentEditor.datasetId} onChange={(event) => { const dataset = snapshot?.datasets.find((item) => item.id === event.target.value); setExperimentEditor((current) => current ? { ...current, datasetId: event.target.value, datasetVersion: dataset?.currentVersion ?? 1 } : current); }}>{snapshot?.datasets.map((dataset) => <option value={dataset.id} key={dataset.id}>{dataset.name} · v{dataset.currentVersion}</option>)}</select></label><label className="field"><span>Prompt version</span><select required value={`${experimentEditor.promptId}:${experimentEditor.promptVersion}`} onChange={(event) => { const [promptId, rawVersion] = event.target.value.split(":"); const prompt = snapshot?.prompts.find((item) => item.id === promptId); setExperimentEditor((current) => current && promptId ? { ...current, promptId, promptVersion: Number(rawVersion), agentId: prompt?.agentId ?? current.agentId, model: prompt?.activeModel ?? current.model } : current); }}>{snapshot?.prompts.flatMap((prompt) => prompt.versions.map((version) => <option value={`${prompt.id}:${version.version}`} key={`${prompt.id}:${version.version}`}>{prompt.name} · v{version.version}{version.active ? " · active" : ""}</option>))}</select></label></div><div className="eval-form__grid"><label className="field"><span>Agent runtime</span><select required value={experimentEditor.agentId} onChange={(event) => setExperimentEditor((current) => current ? { ...current, agentId: event.target.value } : current)}>{agents.map((agent) => <option value={agent.id} key={agent.id}>{agent.name}</option>)}</select></label><label className="field"><span>Model pin</span><input list="eval-model-options" maxLength={200} value={experimentEditor.model ?? ""} placeholder="auto" onChange={(event) => setExperimentEditor((current) => current ? { ...current, model: event.target.value || null } : current)} /></label></div><label className="field"><span>Quality threshold · {experimentEditor.minQualityScore}/100</span><input type="range" min={0} max={100} step={1} value={experimentEditor.minQualityScore} onChange={(event) => setExperimentEditor((current) => current ? { ...current, minQualityScore: Number(event.target.value) } : current)} /></label><div className="dialog-actions"><button className="button button--secondary" type="button" onClick={() => setExperimentEditor(null)}>Отмена</button><button className="button button--primary" type="submit" disabled={busy}>Запустить batch</button></div></form></Modal> : null}

      {reviewItem ? <Modal title={`Human rubric · ${reviewItem.name}`} subtitle="Оценка добавляется в audit trail; прежние review не перезаписываются." onClose={() => setReviewItem(null)}><form className="eval-form" onSubmit={saveReview}>{(detailDatasetVersion?.rubric ?? []).length ? detailDatasetVersion?.rubric.map((criterion) => <label className="field eval-score-field" key={criterion.id}><span>{criterion.label} · weight {criterion.weight}<b>{reviewScores[criterion.id] ?? 80}</b></span><small>{criterion.description}</small><input type="range" min={0} max={100} step={1} value={reviewScores[criterion.id] ?? 80} onChange={(event) => setReviewScores((current) => ({ ...current, [criterion.id]: Number(event.target.value) }))} /></label>) : <label className="field eval-score-field"><span>Итоговая оценка<b>{reviewScores.overall ?? 80}</b></span><input type="range" min={0} max={100} step={1} value={reviewScores.overall ?? 80} onChange={(event) => setReviewScores({ overall: Number(event.target.value) })} /></label>}<label className="field"><span>Обоснование</span><textarea required rows={5} maxLength={4_000} value={reviewRationale} onChange={(event) => setReviewRationale(event.target.value)} /></label><div className="dialog-actions"><button className="button button--secondary" type="button" onClick={() => setReviewItem(null)}>Отмена</button><button className="button button--primary" type="submit" disabled={busy}>Сохранить review</button></div></form></Modal> : null}
    </main>
  );
}
