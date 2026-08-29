import { useEffect, useMemo, useState } from "react";

import { api } from "../lib/api";
import type {
  AgatEvent,
  Approval,
  Artifact,
  EvaluationGate,
  ReplayRunRequest,
  Run,
  RunTrace,
  SchedulerMode,
  StageStatus,
} from "../types";
import { ApprovalPanel } from "./ApprovalPanel";
import { Icon } from "./Icon";
import { PolicyControl } from "./PolicyControl";

const stageStatusCopy: Record<StageStatus, string> = {
  pending: "Ожидает",
  queued: "В очереди",
  running: "Выполняется",
  waiting_approval: "Ждёт решения",
  waiting_external: "Ждёт signal",
  completed: "Завершено",
  failed: "Ошибка",
  cancelled: "Отклонено",
};

type DetailTab = "trace" | "io" | "artifacts" | "evaluation";
type TraceFilter = "all" | "progress" | "model" | "knowledge" | "tool" | "errors";
type TraceKind = "input" | "progress" | "model" | "knowledge" | "tool" | "output" | "artifact" | "error" | "lifecycle";

const traceKindCopy: Record<TraceKind, string> = {
  input: "INPUT",
  progress: "ХОД",
  model: "MODEL",
  knowledge: "RAG",
  tool: "TOOL",
  output: "OUTPUT",
  artifact: "FILE",
  error: "ERROR",
  lifecycle: "EVENT",
};

function eventTime(value: string) {
  return new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function formatBytes(bytes: number) {
  if (bytes < 1_024) return `${bytes} Б`;
  if (bytes < 1_048_576) return `${(bytes / 1_024).toFixed(1)} КБ`;
  return `${(bytes / 1_048_576).toFixed(1)} МБ`;
}

function formatDuration(milliseconds: number | null) {
  if (milliseconds === null) return "—";
  if (milliseconds < 1_000) return `${milliseconds} мс`;
  return `${(milliseconds / 1_000).toFixed(milliseconds < 10_000 ? 1 : 0)} с`;
}

const gateCopy: Record<EvaluationGate, string> = {
  pass: "PASS",
  fail: "FAIL",
  pending: "WAIT",
  not_evaluated: "N/A",
};

function eventKind(event: AgatEvent): TraceKind {
  const structuredKind = typeof event.data?.kind === "string" ? event.data.kind : "";
  if (event.level === "error" || event.type.includes("failed") || event.type.includes("expired")) return "error";
  if (structuredKind === "input" || event.type === "trace.input") return "input";
  if (structuredKind === "model_call" || event.type === "routing.selected") return "model";
  if (structuredKind === "knowledge" || event.type === "knowledge.retrieved") return "knowledge";
  if (structuredKind === "tool_call") return "tool";
  if (structuredKind === "progress") return "progress";
  if (structuredKind === "output" || event.type === "stage.completed") return "output";
  if (structuredKind === "artifact" || event.type === "artifact.created") return "artifact";
  return "lifecycle";
}

function eventMatchesFilter(event: AgatEvent, filter: TraceFilter) {
  if (filter === "all") return true;
  const kind = eventKind(event);
  if (filter === "progress") return ["input", "progress", "output", "lifecycle", "artifact"].includes(kind);
  if (filter === "model") return kind === "model";
  if (filter === "knowledge") return kind === "knowledge";
  if (filter === "tool") return kind === "tool";
  return kind === "error";
}

function triggerDownload(blob: Blob, filename: string) {
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

function finalOutput(run: Run): string | null {
  for (let index = run.stages.length - 1; index >= 0; index -= 1) {
    const output = run.stages[index]?.output;
    if (output) return output;
  }
  return null;
}

interface TraceTimelineProps {
  run: Run;
  events: AgatEvent[];
  loading: boolean;
  error: string | null;
  truncated: boolean;
  onExport: () => void;
}

function TraceTimeline({ run, events, loading, error, truncated, onExport }: TraceTimelineProps) {
  const [filter, setFilter] = useState<TraceFilter>("all");
  const stageNames = useMemo(
    () => new Map(run.stages.map((stage) => [stage.id, stage.agent.name])),
    [run.stages],
  );
  const filteredEvents = useMemo(
    () => events.filter((event) => eventMatchesFilter(event, filter)),
    [events, filter],
  );

  return (
    <div className="trace-panel">
      <div className="trace-policy-note">
        <Icon name="shield" size={18} />
        <span>
          <strong>Показывается наблюдаемый ход выполнения</strong>
          <small>Входы, контекст, model/tool calls, безопасные аргументы, ошибки и output. Скрытая chain-of-thought модели не сохраняется.</small>
        </span>
      </div>
      <div className="trace-toolbar">
        <div className="trace-filters" aria-label="Фильтр журнала">
          {([
            ["all", "Все"],
            ["progress", "Ход"],
            ["model", "Модели"],
            ["knowledge", "Knowledge"],
            ["tool", "Tools"],
            ["errors", "Ошибки"],
          ] as Array<[TraceFilter, string]>).map(([value, label]) => (
            <button
              className={filter === value ? "is-active" : ""}
              type="button"
              key={value}
              onClick={() => setFilter(value)}
            >
              {label}
            </button>
          ))}
        </div>
        <button className="trace-export" type="button" onClick={onExport} disabled={events.length === 0}>
          <Icon name="save" size={15} />trace.json
        </button>
      </div>
      {error ? <p className="trace-state trace-state--error">{error}</p> : null}
      {truncated ? <p className="trace-state trace-state--warn">Показаны первые 10 000 событий этого запуска.</p> : null}
      <div className="trace-list" role="log" aria-live="polite" aria-busy={loading}>
        {filteredEvents.length === 0 ? (
          <p className="trace-state">{loading ? "Загружаем полный журнал…" : "Событий для выбранного фильтра пока нет."}</p>
        ) : filteredEvents.map((event) => {
          const kind = eventKind(event);
          const stageName = event.stageId ? stageNames.get(event.stageId) : null;
          return (
            <details className={`trace-entry trace-entry--${kind}`} key={event.id}>
              <summary>
                <time>{eventTime(event.createdAt)}</time>
                <span className="trace-entry__kind">{traceKindCopy[kind]}</span>
                <span className="trace-entry__message">{event.message}</span>
                {stageName ? <small>{stageName}</small> : null}
                <Icon name="chevron" size={14} />
              </summary>
              <div className="trace-entry__details">
                <dl>
                  <div><dt>Тип</dt><dd className="mono">{event.type}</dd></div>
                  <div><dt>Event ID</dt><dd className="mono">{event.id}</dd></div>
                  {event.nodeId ? <div><dt>Node ID</dt><dd className="mono">{event.nodeId}</dd></div> : null}
                </dl>
                {event.data ? <pre>{JSON.stringify(event.data, null, 2)}</pre> : <p>Дополнительных данных нет.</p>}
              </div>
            </details>
          );
        })}
      </div>
    </div>
  );
}

interface InputOutputPanelProps {
  run: Run;
}

function InputOutputPanel({ run }: InputOutputPanelProps) {
  const result = finalOutput(run);
  return (
    <div className="io-panel">
      <section className="io-card">
        <div className="io-card__head"><span>Вход запуска</span><small>{run.input.length} символов</small></div>
        <pre>{run.input}</pre>
      </section>
      {run.stages.map((stage) => (
        <section className="io-card" key={stage.id}>
          <div className="io-card__head">
            <span>{stage.position + 1}. {stage.agent.name} · output</span>
            <small>{stage.output ? `${stage.output.length} символов` : stageStatusCopy[stage.status]}</small>
          </div>
          {stage.output ? <pre>{stage.output}</pre> : <p>Результат этапа ещё не сформирован.</p>}
        </section>
      ))}
      <div className="io-download">
        <div>
          <strong>Финальный результат</strong>
          <small>{result ? "Доступен как Markdown-файл независимо от выбранного хранилища." : "Появится после первого завершённого этапа."}</small>
        </div>
        <button
          className="button button--secondary"
          type="button"
          disabled={!result}
          onClick={() => result && triggerDownload(new Blob([result], { type: "text/markdown;charset=utf-8" }), "result.md")}
        >
          <Icon name="save" size={16} />Скачать result.md
        </button>
      </div>
    </div>
  );
}

interface ArtifactsPanelProps {
  run: Run;
  artifacts: Artifact[];
  downloadingId: string | null;
  error: string | null;
  onDownload: (artifact: Artifact) => void;
}

function ArtifactsPanel({ run, artifacts, downloadingId, error, onDownload }: ArtifactsPanelProps) {
  return (
    <div className="artifacts-panel">
      <div className="artifact-destination">
        <Icon name={run.resultDestination === "artifacts" ? "save" : "list"} size={21} />
        <span>
          <strong>{run.resultDestination === "artifacts" ? "Artifact Store включён" : "Результат хранится в журнале АГАТ"}</strong>
          <small className="mono">
            {run.artifactBasePath ?? "SQLite · output каждого этапа"}
          </small>
        </span>
      </div>
      {error ? <p className="trace-state trace-state--error">{error}</p> : null}
      {artifacts.length === 0 ? (
        <div className="artifact-empty">
          <Icon name="box" size={30} />
          <strong>Файловых артефактов пока нет</strong>
          <p>{run.resultDestination === "artifacts"
            ? "Файл этапа появится сразу после его завершения."
            : "Для будущего запуска выберите «Журнал + Artifact Store»; текущий output можно скачать на вкладке «Вход и результат»."}</p>
        </div>
      ) : (
        <div className="artifact-list">
          {artifacts.map((artifact) => (
            <article key={artifact.id}>
              <span className={`artifact-kind artifact-kind--${artifact.kind}`}><Icon name="box" size={18} /></span>
              <div>
                <strong>{artifact.name}</strong>
                <small>{artifact.kind} · {formatBytes(artifact.sizeBytes)} · <span className="mono">sha256:{artifact.sha256.slice(0, 12)}</span></small>
                <code>{artifact.relativePath}</code>
              </div>
              <button
                className="button button--secondary"
                type="button"
                disabled={downloadingId === artifact.id}
                onClick={() => onDownload(artifact)}
              >
                <Icon name="save" size={15} />{downloadingId === artifact.id ? "Скачиваем…" : "Скачать"}
              </button>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}

interface EvaluationPanelProps {
  run: Run;
  trace: RunTrace | null;
  models: string[];
  busy: boolean;
  onReplay: (runId: string, payload: ReplayRunRequest) => Promise<void>;
}

function EvaluationPanel({ run, trace, models, busy, onReplay }: EvaluationPanelProps) {
  const [primaryModel, setPrimaryModel] = useState("");
  const [secondaryModel, setSecondaryModel] = useState("");
  const [compare, setCompare] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const modelOptions = useMemo(() => [...new Set(models)].sort(), [models]);
  const canReplay = ["completed", "failed", "cancelled"].includes(run.status)
    && run.process === null
    && run.stages.length > 0
    && run.stages.every((stage) => stage.kind === "agent");

  useEffect(() => {
    setPrimaryModel("");
    setSecondaryModel("");
    setCompare(false);
    setError(null);
  }, [run.id]);

  async function submitReplay() {
    const agentIds = [...new Set(run.stages.map((stage) => stage.agent.id))];
    if (compare && primaryModel === secondaryModel) {
      setError("Для A/B сравнения выберите разные модели или разные режимы manifest.");
      return;
    }
    const variant = (name: string, model: string) => ({
      name,
      ...(model ? { modelOverrides: Object.fromEntries(agentIds.map((agentId) => [agentId, model])) } : {}),
    });
    const variants = [variant(primaryModel || "Manifest replay", primaryModel)];
    if (compare) variants.push(variant(secondaryModel || "Manifest replay B", secondaryModel));
    setError(null);
    try {
      await onReplay(run.id, { variants });
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось создать replay");
    }
  }

  const manifest = trace?.manifest;
  const comparison = trace?.comparison;
  const golden = trace?.goldenEvaluation;
  const migratedSnapshot = manifest?.stages.some((stage) => stage.agent.source === "migration_backfill") ?? false;
  const specialistSnapshots = manifest?.stages.flatMap((stage) => stage.agent.specialists ?? []) ?? [];

  return (
    <div className="evaluation-panel">
      <div className="manifest-card">
        <div className="manifest-card__title">
          <span><Icon name="shield" size={17} /></span>
          <div><strong>Execution manifest v{manifest?.schemaVersion ?? 1}</strong><small>Prompt, runtime, model pin и решение router зафиксированы на уровне stage</small></div>
        </div>
        <dl>
          <div><dt>Trace ID</dt><dd className="mono">{run.traceId || "загружается"}</dd></div>
          <div><dt>Manifest SHA-256</dt><dd className="mono">{manifest?.manifestSha256 ?? "загружается"}</dd></div>
          <div><dt>Input SHA-256</dt><dd className="mono">{manifest?.inputSha256 ?? "загружается"}</dd></div>
        </dl>
        {specialistSnapshots.length > 0 ? (
          <p className="evaluation-note">
            Specialist snapshots · {specialistSnapshots.map((specialist) => `${specialist.name}@${specialist.definitionVersion.slice(0, 8)}`).join(" · ")}
          </p>
        ) : null}
        {migratedSnapshot ? <p className="evaluation-note evaluation-note--warn">Legacy stage был зафиксирован при миграции; snapshot мог быть создан позже исходного выполнения.</p> : null}
        <p className="evaluation-note">External tool results не считаются детерминированными; process/HTTP replay намеренно запрещён.</p>
      </div>

      {golden ? (
        <div className="comparison-card comparison-card--golden">
          <div className="comparison-card__header"><strong>Golden experiment · {golden.experimentName}</strong><code>{golden.experimentId.slice(0, 8)}</code></div>
          <div className="evaluation-gates">
            <span className={`evaluation-gate evaluation-gate--${golden.gates.completion}`}>Завершение · {gateCopy[golden.gates.completion]}</span>
            <span className={`evaluation-gate evaluation-gate--${golden.gates.quality}`}>Качество · {gateCopy[golden.gates.quality]}</span>
            <span className={`evaluation-gate evaluation-gate--${golden.gates.knowledge}`}>Knowledge · {gateCopy[golden.gates.knowledge]}</span>
            <span className={`evaluation-gate evaluation-gate--${golden.gates.overall}`}>Release · {gateCopy[golden.gates.overall]}</span>
          </div>
          <p className="evaluation-note">{golden.runKind === "model_judge" ? "Model judge run" : "Golden candidate"} · prompt v{golden.promptVersion} · score {golden.qualityScore ?? "N/A"}/{golden.minQualityScore} · источник {golden.qualitySource ?? "ожидает review"}.</p>
        </div>
      ) : null}

      {comparison ? (
        <div className="comparison-card">
          <div className="comparison-card__header"><strong>Группа сравнения</strong><code>{comparison.evaluationGroupId.slice(0, 8)}</code></div>
          <div className="comparison-table" role="table" aria-label="Сравнение replay">
            {comparison.runs.map((candidate) => (
              <article className={candidate.runId === run.id ? "is-current" : ""} key={candidate.runId}>
                <div><strong>{candidate.variantName}</strong><small>{candidate.status}</small></div>
                <dl>
                  <div><dt>Latency</dt><dd>{formatDuration(candidate.wallDurationMs)}</dd></div>
                  <div><dt>Tokens in/out</dt><dd>{candidate.inputTokens ?? "—"} / {candidate.outputTokens ?? "—"}</dd></div>
                  <div><dt>Model / tools</dt><dd>{candidate.modelCalls} / {candidate.toolCalls}</dd></div>
                  <div><dt>Output</dt><dd>{candidate.outputCharacters} симв.</dd></div>
                </dl>
                {candidate.gates ? (
                  <div className="evaluation-gates">
                    {([
                      ["completion", "Завершение"],
                      ["latency", "Latency ≤ 1.25×"],
                      ["tokenBudget", "Tokens ≤ 1.25×"],
                      ["quality", "Качество"],
                    ] as const).map(([key, label]) => (
                      <span className={`evaluation-gate evaluation-gate--${candidate.gates![key]}`} key={key}>
                        {label} · {gateCopy[candidate.gates![key]]}
                      </span>
                    ))}
                  </div>
                ) : <span className="evaluation-baseline">BASELINE</span>}
              </article>
            ))}
          </div>
          <p className="evaluation-note">Ad-hoc replay сравнивает latency/tokens. Для quality gate создайте versioned golden experiment — длина ответа не подменяет качество.</p>
        </div>
      ) : null}

      <div className="replay-card">
        <div><strong>Replay / A/B</strong><small>Новый запуск получит точный input и immutable agent snapshots.</small></div>
        {canReplay ? (
          <div className="replay-form">
            <label><span>Версия A</span><select value={primaryModel} onChange={(event) => setPrimaryModel(event.target.value)}>
              <option value="">Модели из manifest</option>
              {modelOptions.map((model: string) => <option value={model} key={model}>{model}</option>)}
            </select></label>
            <label className="replay-compare"><input type="checkbox" checked={compare} onChange={(event) => setCompare(event.target.checked)} /><span>A/B: добавить версию B</span></label>
            {compare ? <label><span>Версия B</span><select value={secondaryModel} onChange={(event) => setSecondaryModel(event.target.value)}>
              <option value="">Модели из manifest</option>
              {modelOptions.map((model: string) => <option value={model} key={model}>{model}</option>)}
            </select></label> : null}
            {error ? <p className="trace-state trace-state--error">{error}</p> : null}
            <button className="button button--primary" type="button" disabled={busy} onClick={() => void submitReplay()}>
              <Icon name="play" size={15} />{busy ? "Создаём…" : compare ? "Запустить A/B" : "Запустить replay"}
            </button>
          </div>
        ) : <p className="evaluation-note evaluation-note--warn">Этот запуск нельзя безопасно повторить: нужен терминальный run только из agent stages.</p>}
      </div>
    </div>
  );
}

interface RunDetailProps {
  run: Run | null;
  events: AgatEvent[];
  schedulerMode: SchedulerMode;
  approval: Approval | null;
  busy: boolean;
  onSchedulerChange: (mode: SchedulerMode) => void;
  onApproval: (approval: Approval, decision: "approve" | "reject") => void;
  models: string[];
  onReplay: (runId: string, payload: ReplayRunRequest) => Promise<void>;
}

export function RunDetail({
  run,
  events,
  schedulerMode,
  approval,
  busy,
  onSchedulerChange,
  onApproval,
  models,
  onReplay,
}: RunDetailProps) {
  const [activeTab, setActiveTab] = useState<DetailTab>("trace");
  const [trace, setTrace] = useState<RunTrace | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const runId = run?.id ?? null;
  const runUpdatedAt = run?.updatedAt ?? null;
  const eventRevision = events.at(-1)?.id ?? 0;

  useEffect(() => {
    setActiveTab("trace");
    setTrace(null);
    setTraceError(null);
    setDownloadError(null);
  }, [runId]);

  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    setTraceLoading(true);
    api.runTrace(runId, controller.signal)
      .then((next) => {
        setTrace(next);
        setTraceError(null);
      })
      .catch((requestError: unknown) => {
        if (requestError instanceof DOMException && requestError.name === "AbortError") return;
        setTraceError(requestError instanceof Error ? requestError.message : "Не удалось загрузить полный журнал");
      })
      .finally(() => {
        if (!controller.signal.aborted) setTraceLoading(false);
      });
    return () => controller.abort();
  }, [eventRevision, runId, runUpdatedAt]);

  if (!run) {
    return <section className="run-detail empty-state" id="active-run">Выберите запуск, чтобы увидеть цепочку агентов.</section>;
  }

  const detailedRun = trace?.run ?? run;
  const fallbackEvents = events.filter((event) => event.runId === run.id);
  const traceEvents = trace?.events ?? fallbackEvents;
  const current = detailedRun.stages.find((stage) => !["completed", "cancelled"].includes(stage.status));
  const relevantApproval = approval?.runId === run.id ? approval : null;
  const currentLabel = detailedRun.status === "completed"
    ? "Цепочка завершена"
    : detailedRun.status === "failed"
      ? "Запуск завершился ошибкой"
      : detailedRun.status === "cancelled"
        ? "Запуск отклонён"
        : current?.agent.name ?? "Ожидает планировщик";
  const currentTone = detailedRun.status === "running" || detailedRun.status === "completed"
    ? "online"
    : detailedRun.status === "failed" || detailedRun.status === "cancelled"
      ? "offline"
      : "waiting";

  function exportTrace() {
    const payload = trace ?? {
      run: detailedRun,
      events: traceEvents,
      artifacts: [],
      truncated: false,
      tracePolicy: { rawReasoningStored: false },
    };
    triggerDownload(
      new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" }),
      `trace-${detailedRun.id}.json`,
    );
  }

  async function downloadArtifact(artifact: Artifact) {
    setDownloadingId(artifact.id);
    setDownloadError(null);
    try {
      const blob = await api.downloadArtifact(artifact.id);
      triggerDownload(blob, artifact.name);
    } catch (requestError) {
      setDownloadError(requestError instanceof Error ? requestError.message : "Не удалось скачать артефакт");
    } finally {
      setDownloadingId(null);
    }
  }

  return (
    <section className="run-detail" id="active-run" aria-labelledby="active-run-title">
      <div className="run-detail__header">
        <div>
          <h2 id="active-run-title">{detailedRun.name}</h2>
          <p>Цепочка агентов: <span>{detailedRun.stages.map((stage) => stage.agent.name).join(" → ")}</span></p>
        </div>
        <div className="current-stage">
          <small>Текущий этап</small>
          <strong><span className={`status-dot status-dot--${currentTone}`} />{currentLabel}</strong>
        </div>
      </div>

      <ol className="stage-rail" aria-label="Этапы запуска">
        {detailedRun.stages.map((stage) => (
          <li className={`stage stage--${stage.status}`} key={stage.id}>
            <span className="stage__marker">
              {stage.status === "completed" ? <Icon name="check" size={17} /> : <span />}
            </span>
            <div className="stage__copy">
              <strong>{stage.agent.name}</strong>
              <span>{stageStatusCopy[stage.status]}{stage.nodeName ? ` · ${stage.nodeName}` : ""}</span>
              {stage.routing ? (
                <small className="stage__routing">
                  {stage.routing.selectedModel ?? "worker fallback"} · {stage.routing.strategy} · {stage.routing.alternativesConsidered} кандидат(а)
                </small>
              ) : null}
            </div>
          </li>
        ))}
      </ol>

      <div className="detail-grid detail-grid--trace">
        <div className="run-workspace">
          <div className="run-tabs" role="tablist" aria-label="Детали запуска">
            <button className={activeTab === "trace" ? "is-active" : ""} type="button" onClick={() => setActiveTab("trace")}>
              <Icon name="terminal" size={15} />Ход и логи <span>{traceEvents.length}</span>
            </button>
            <button className={activeTab === "io" ? "is-active" : ""} type="button" onClick={() => setActiveTab("io")}>
              <Icon name="list" size={15} />Вход и результат
            </button>
            <button className={activeTab === "artifacts" ? "is-active" : ""} type="button" onClick={() => setActiveTab("artifacts")}>
              <Icon name="box" size={15} />Артефакты <span>{trace?.artifacts.length ?? 0}</span>
            </button>
            <button className={activeTab === "evaluation" ? "is-active" : ""} type="button" onClick={() => setActiveTab("evaluation")}>
              <Icon name="repeat" size={15} />Replay / Eval <span>{trace?.comparison?.runs.length ?? 0}</span>
            </button>
          </div>
          {activeTab === "trace" ? (
            <TraceTimeline
              run={detailedRun}
              events={traceEvents}
              loading={traceLoading}
              error={traceError}
              truncated={trace?.truncated ?? false}
              onExport={exportTrace}
            />
          ) : null}
          {activeTab === "io" ? <InputOutputPanel run={detailedRun} /> : null}
          {activeTab === "artifacts" ? (
            <ArtifactsPanel
              run={detailedRun}
              artifacts={trace?.artifacts ?? []}
              downloadingId={downloadingId}
              error={downloadError}
              onDownload={(artifact) => void downloadArtifact(artifact)}
            />
          ) : null}
          {activeTab === "evaluation" ? (
            <EvaluationPanel run={detailedRun} trace={trace} models={models} busy={busy} onReplay={onReplay} />
          ) : null}
        </div>
        <div className="detail-side">
          <PolicyControl value={schedulerMode} disabled={busy} onChange={onSchedulerChange} />
          <ApprovalPanel approval={relevantApproval} busy={busy} onDecision={onApproval} />
        </div>
      </div>
    </section>
  );
}
