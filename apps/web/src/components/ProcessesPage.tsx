import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
} from "react";
import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  Controls,
  MarkerType,
  MiniMap,
  Panel,
  ReactFlow,
  type Connection,
  type EdgeChange,
  type NodeChange,
  type ReactFlowInstance,
  type XYPosition,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type {
  AgatEvent,
  Agent,
  CredentialSummary,
  ProcessBranch,
  ProcessDefinition,
  ProcessGraph,
  ProcessGraphEdge,
  ProcessGraphNode,
  ProcessInstance,
  ProcessNodeType,
  RunTrace,
  TestProcessNodeResult,
  UpdateProcessRequest,
} from "../types";
import { api } from "../lib/api";
import { useActionDialog } from "./ActionDialog";
import { Icon } from "./Icon";
import {
  processEdgeTypes,
  type ProcessEdgeData,
  type ProcessFlowEdge,
} from "./ProcessEdges";
import {
  ProcessInspector,
  type ProcessNodeExecutionDetails,
} from "./ProcessInspector";
import { ProcessInstances } from "./ProcessInstances";
import { ProcessReleasePanel } from "./ProcessReleasePanel";
import {
  ProcessNodePicker,
  processNodeTools,
} from "./ProcessNodePicker";
import { processNodeTypes, type ProcessFlowNode } from "./ProcessNodes";

type LeftPanel = "processes" | "nodes" | null;

interface PickerState {
  screenPosition: XYPosition;
  flowPosition: XYPosition;
  edgeId: string | null;
}

interface GraphHistory {
  entries: ProcessGraph[];
  index: number;
}

interface ClipboardGraph {
  nodes: ProcessGraphNode[];
  edges: ProcessGraphEdge[];
}

const PROCESS_NODE_TYPES = new Set<ProcessNodeType>([
  "start", "agent", "http", "transform", "wait", "approval", "artifact", "condition", "loop",
  "parallel_fork", "parallel_join", "signal", "subprocess", "end",
]);
const MAX_HISTORY = 60;

const branchLabels: Record<ProcessBranch, string> = {
  default: "",
  true: "да",
  false: "нет",
  repeat: "повтор",
  exit: "выход",
};

const executionStatusLabels: Record<ProcessNodeExecutionDetails["status"], string> = {
  pending: "Ожидает",
  queued: "В очереди",
  running: "Выполняется",
  waiting_approval: "Ждёт решения",
  waiting_external: "Ждёт signal",
  completed: "Готово",
  failed: "Ошибка",
  cancelled: "Остановлено",
  visited: "Пройдено",
};

function useMobileEditor(): boolean {
  const [mobile, setMobile] = useState(() => window.matchMedia("(max-width: 760px)").matches);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 760px)");
    const update = () => setMobile(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return mobile;
}

function cloneGraph(graph: ProcessGraph): ProcessGraph {
  return structuredClone(graph);
}

function graphSignature(graph: ProcessGraph): string {
  return JSON.stringify(graph);
}

function nodeSubtitle(node: ProcessGraphNode, agentsById: Map<string, Agent>): string {
  if (node.type === "agent") {
    const agent = agentsById.get(node.config.agentId ?? "");
    return agent ? `${agent.runtime} · ${agent.model ?? agent.role}` : "Выберите агента";
  }
  if (node.type === "condition") {
    const condition = node.config.condition;
    if (!condition) return "Настройте условие";
    if (condition.operator === "always") return "всегда";
    return condition.value ? `результат: «${condition.value}»` : "Настройте значение";
  }
  if (node.type === "loop") return `не более ${node.config.maxIterations ?? 3} итераций`;
  if (node.type === "http") return `${node.config.method ?? "GET"} · ${node.config.url || "Настройте URL"}`;
  if (node.type === "transform") return node.config.template ? "шаблон выражений" : "Настройте шаблон";
  if (node.type === "wait") return `${node.config.waitSeconds ?? 60} сек.`;
  if (node.type === "approval") return "решение оператора";
  if (node.type === "artifact") return node.config.artifactName || "Настройте файл";
  if (node.type === "parallel_fork") return "запустить все ветки";
  if (node.type === "parallel_join") return node.config.forkId ? "дождаться парного fork" : "выберите fork";
  if (node.type === "signal") return node.config.signalName || "Настройте signal";
  if (node.type === "subprocess") return node.config.subprocessVersion
    ? `закреплена v${node.config.subprocessVersion}`
    : "выберите процесс";
  return "";
}

function mobileNodeHeight(type: ProcessNodeType): number {
  if (type === "condition" || type === "parallel_fork" || type === "parallel_join") return 126;
  if (type === "loop") return 106;
  return 82;
}

function mobileNodeX(type: ProcessNodeType): number {
  if (type === "condition" || type === "parallel_fork" || type === "parallel_join") return 104;
  if (type === "start" || type === "end") return 137;
  if (type === "loop") return 101;
  return 98;
}

function mobileNodePositions(nodes: ProcessGraphNode[]): Map<string, XYPosition> {
  let y = 24;
  return new Map(nodes.map((node) => {
    const position = { x: mobileNodeX(node.type), y };
    y += mobileNodeHeight(node.type) + 34;
    return [node.id, position];
  }));
}

function graphToFlowNodes(
  graph: ProcessGraph,
  agentsById: Map<string, Agent>,
  onSelect: (nodeId: string) => void,
  mobile: boolean,
): ProcessFlowNode[] {
  const compactPositions = mobile ? mobileNodePositions(graph.nodes) : null;
  return graph.nodes.map((node) => ({
    id: node.id,
    type: node.type,
    position: compactPositions?.get(node.id) ?? node.position,
    data: {
      kind: node.type,
      name: node.name,
      subtitle: nodeSubtitle(node, agentsById),
      config: structuredClone(node.config),
      storedPosition: node.position,
      onSelect,
    },
  }));
}

function graphToFlowEdges(
  graph: ProcessGraph,
  onInsert: ProcessEdgeData["onInsert"],
): ProcessFlowEdge[] {
  return graph.edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    sourceHandle: edge.branch === "default" ? "default" : edge.branch,
    type: "process",
    animated: edge.branch === "repeat",
    data: { branch: edge.branch, onInsert },
    markerEnd: { type: MarkerType.ArrowClosed },
  }));
}

function flowNodeToGraph(node: ProcessFlowNode, mobile: boolean): ProcessGraphNode {
  return {
    id: node.id,
    type: node.data.kind,
    name: node.data.name,
    position: mobile
      ? { ...node.data.storedPosition }
      : { x: node.position.x, y: node.position.y },
    config: structuredClone(node.data.config),
  };
}

function flowEdgeToGraph(edge: ProcessFlowEdge): ProcessGraphEdge {
  const branch = edge.data?.branch ?? (edge.sourceHandle || "default") as ProcessBranch;
  return { id: edge.id, source: edge.source, target: edge.target, branch };
}

function graphFromFlow(nodes: ProcessFlowNode[], edges: ProcessFlowEdge[], mobile: boolean): ProcessGraph {
  return {
    nodes: nodes.map((node) => flowNodeToGraph(node, mobile)),
    edges: edges.map(flowEdgeToGraph),
  };
}

function defaultNode(
  type: ProcessNodeType,
  position: XYPosition,
  agents: Agent[],
): ProcessGraphNode {
  const id = crypto.randomUUID();
  const defaults: Record<ProcessNodeType, Pick<ProcessGraphNode, "name" | "config">> = {
    start: { name: "Старт", config: {} },
    agent: {
      name: agents[0]?.name ?? "Агент",
      config: { agentId: agents[0]?.id, approvalRequired: false },
    },
    http: {
      name: "HTTP-запрос",
      config: { method: "GET", url: "", headers: {}, body: "", timeoutSeconds: 30, idempotencyHeader: "Idempotency-Key" },
    },
    transform: {
      name: "Преобразование",
      config: { template: "{{ lastOutput }}" },
    },
    wait: {
      name: "Ожидание",
      config: { waitSeconds: 60 },
    },
    approval: {
      name: "Подтверждение",
      config: { approvalMessage: "Проверьте результат предыдущего шага" },
    },
    artifact: {
      name: "Сохранить артефакт",
      config: {
        artifactName: "result.md",
        artifactMediaType: "text/markdown; charset=utf-8",
        artifactContent: "{{ lastOutput }}",
      },
    },
    condition: {
      name: "Условие",
      config: {
        condition: { source: "last_output", operator: "contains", value: "готово", caseSensitive: false },
      },
    },
    loop: {
      name: "Цикл проверки",
      config: {
        condition: { source: "last_output", operator: "contains", value: "доработать", caseSensitive: false },
        maxIterations: 3,
      },
    },
    parallel_fork: { name: "Параллельный fork", config: {} },
    parallel_join: { name: "Параллельный join", config: {} },
    signal: {
      name: "Внешний signal",
      config: { signalName: "external.signal", signalCorrelationKey: "", signalTimeoutSeconds: 0 },
    },
    subprocess: {
      name: "Subprocess",
      config: { subprocessInputTemplate: "{{ lastOutput }}" },
    },
    end: { name: "Завершение", config: {} },
  };
  return {
    id,
    type,
    name: defaults[type].name,
    config: defaults[type].config,
    position: {
      x: Math.round((position.x - (type === "start" || type === "end" ? 38 : 76)) / 16) * 16,
      y: Math.round((position.y - 36) / 16) * 16,
    },
  };
}

function primaryBranch(type: ProcessNodeType): ProcessBranch | null {
  if (type === "end") return null;
  if (type === "condition") return "true";
  if (type === "loop") return "exit";
  return "default";
}

function autoLayout(graph: ProcessGraph): ProcessGraph {
  if (graph.nodes.length === 0) return graph;
  const outgoing = new Map<string, ProcessGraphEdge[]>();
  for (const edge of graph.edges) {
    if (edge.branch === "repeat") continue;
    outgoing.set(edge.source, [...(outgoing.get(edge.source) ?? []), edge]);
  }
  const start = graph.nodes.find((node) => node.type === "start") ?? graph.nodes[0]!;
  const levels = new Map<string, number>([[start.id, 0]]);
  const queue = [start.id];
  while (queue.length > 0) {
    const source = queue.shift()!;
    const nextLevel = (levels.get(source) ?? 0) + 1;
    for (const edge of outgoing.get(source) ?? []) {
      if (levels.has(edge.target)) continue;
      levels.set(edge.target, nextLevel);
      queue.push(edge.target);
    }
  }
  let fallbackLevel = Math.max(0, ...levels.values()) + 1;
  for (const node of graph.nodes) {
    if (!levels.has(node.id)) levels.set(node.id, fallbackLevel++);
  }
  const byLevel = new Map<number, ProcessGraphNode[]>();
  for (const node of graph.nodes) {
    const level = levels.get(node.id) ?? 0;
    byLevel.set(level, [...(byLevel.get(level) ?? []), node]);
  }
  const positions = new Map<string, XYPosition>();
  for (const [level, levelNodes] of byLevel) {
    const totalHeight = Math.max(0, levelNodes.length - 1) * 154;
    levelNodes.forEach((node, index) => {
      positions.set(node.id, { x: 72 + level * 248, y: 92 + index * 154 - totalHeight / 2 });
    });
  }
  return {
    ...graph,
    nodes: graph.nodes.map((node) => ({ ...node, position: positions.get(node.id) ?? node.position })),
  };
}

function isFormTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable;
}

function eventReferencesNode(event: AgatEvent, nodeId: string, stageIds: Set<string>): boolean {
  if (event.stageId && stageIds.has(event.stageId)) return true;
  const data = event.data;
  return data?.processNodeId === nodeId
    || data?.sourceProcessNodeId === nodeId
    || data?.targetProcessNodeId === nodeId;
}

function executionForNode(trace: RunTrace | null, nodeId: string): ProcessNodeExecutionDetails | null {
  if (!trace) return null;
  const stages = trace.run.stages.filter((stage) => stage.processNodeId === nodeId);
  const latestStage = stages.at(-1) ?? null;
  const stageIds = new Set(stages.map((stage) => stage.id));
  const events = trace.events.filter((event) => eventReferencesNode(event, nodeId, stageIds));
  const entered = events.some((event) => event.type === "process.node.entered");
  if (!latestStage && !entered) return null;
  const inputEvent = latestStage
    ? [...trace.events]
      .reverse()
      .find((event) => event.stageId === latestStage.id && (event.type === "trace.input" || event.data?.kind === "input"))
    : null;
  return {
    status: latestStage?.status ?? "visited",
    input: inputEvent?.data ?? trace.run.input,
    output: latestStage?.output ?? null,
    events,
    attempt: latestStage?.attempt ?? null,
    startedAt: latestStage?.startedAt ?? null,
    completedAt: latestStage?.completedAt ?? null,
  };
}

function transitionKey(source: unknown, target: unknown): string | null {
  return typeof source === "string" && typeof target === "string" ? `${source}\u0000${target}` : null;
}

interface ProcessCatalogPanelProps {
  panel: Exclude<LeftPanel, null>;
  processes: ProcessDefinition[];
  selectedProcessId: string | null;
  processDescription: string;
  onClose: () => void;
  onCreate: () => void;
  onChooseProcess: (processId: string) => void;
  onDescriptionChange: (value: string) => void;
  onAddNode: (type: ProcessNodeType) => void;
}

function ProcessCatalogPanel({
  panel,
  processes,
  selectedProcessId,
  processDescription,
  onClose,
  onCreate,
  onChooseProcess,
  onDescriptionChange,
  onAddNode,
}: ProcessCatalogPanelProps) {
  const [query, setQuery] = useState("");
  const normalizedQuery = query.trim().toLocaleLowerCase("ru");
  const tools = processNodeTools.filter((tool) => !normalizedQuery || [tool.label, tool.description, ...tool.keywords]
    .some((value) => value.toLocaleLowerCase("ru").includes(normalizedQuery)));

  return (
    <aside className="process-side-panel">
      <header>
        <div>
          <small>{panel === "processes" ? "Библиотека" : "Каталог"}</small>
          <h2>{panel === "processes" ? "Процессы" : "Шаги"}</h2>
        </div>
        <button className="icon-button" type="button" aria-label="Закрыть панель" onClick={onClose}>
          <Icon name="close" size={17} />
        </button>
      </header>
      {panel === "processes" ? (
        <>
          <button className="process-side-panel__create" type="button" onClick={onCreate}>
            <Icon name="plus" size={16} />Новый процесс
          </button>
          <div className="process-side-panel__list">
            {processes.map((process) => (
              <button
                className={process.id === selectedProcessId ? "is-active" : ""}
                type="button"
                key={process.id}
                onClick={() => onChooseProcess(process.id)}
              >
                <span><strong>{process.name}</strong><small>{process.isTemplate ? "Шаблон" : process.activeInstances ? `${process.activeInstances} активн.` : `Версия ${process.publishedVersion}`}</small></span>
                {process.hasUnpublishedChanges ? <i aria-label="Есть черновик" /> : null}
              </button>
            ))}
          </div>
          <label className="process-side-panel__description">
            <span>Описание процесса</span>
            <textarea
              rows={5}
              maxLength={1_000}
              value={processDescription}
              placeholder="Что делает этот процесс"
              onChange={(event) => onDescriptionChange(event.target.value)}
            />
          </label>
        </>
      ) : (
        <>
          <label className="process-side-panel__search">
            <Icon name="search" size={15} />
            <input value={query} placeholder="Поиск шагов" onChange={(event) => setQuery(event.target.value)} />
          </label>
          <p className="process-side-panel__hint">Перетащите шаг на canvas или нажмите, чтобы добавить по центру.</p>
          <div className="process-side-panel__tools">
            {tools.map((tool) => (
              <button
                draggable
                type="button"
                key={tool.type}
                onClick={() => onAddNode(tool.type)}
                onDragStart={(event) => {
                  event.dataTransfer.effectAllowed = "copy";
                  event.dataTransfer.setData("application/agat-process-node", tool.type);
                }}
              >
                <span className={`process-node-picker__icon process-node-picker__icon--${tool.type}`}><Icon name={tool.icon} size={18} /></span>
                <span><strong>{tool.label}</strong><small>{tool.description}</small></span>
              </button>
            ))}
          </div>
        </>
      )}
    </aside>
  );
}

interface ProcessesPageProps {
  processes: ProcessDefinition[];
  instances: ProcessInstance[];
  agents: Agent[];
  credentials: CredentialSummary[];
  busy: boolean;
  error: string | null;
  onCreate: () => void;
  onSave: (processId: string, payload: UpdateProcessRequest) => Promise<ProcessDefinition>;
  onPublish: (processId: string, payload: UpdateProcessRequest, saveDraft: boolean) => Promise<ProcessDefinition>;
  onStart: (process: ProcessDefinition) => void;
  onCancel: (instanceId: string) => void;
  onReplay: (instanceId: string, mode: "safe" | "live") => void;
  onOpenRun: (runId: string) => void;
  onManageCredentials: () => void;
  onChanged: () => void | Promise<void>;
}

export function ProcessesPage({
  processes,
  instances,
  agents,
  credentials,
  busy,
  error,
  onCreate,
  onSave,
  onPublish,
  onStart,
  onCancel,
  onReplay,
  onOpenRun,
  onManageCredentials,
  onChanged,
}: ProcessesPageProps) {
  const requestAction = useActionDialog();
  const mobile = useMobileEditor();
  const agentSignature = agents.map((agent) => `${agent.id}:${agent.name}:${agent.model ?? ""}:${agent.updatedAt}`).join("|");
  const agentsById = useMemo(() => new Map(agents.map((agent) => [agent.id, agent])), [agentSignature]);
  const [selectedProcessId, setSelectedProcessId] = useState<string | null>(processes[0]?.id ?? null);
  const selectedProcess = useMemo(
    () => processes.find((process) => process.id === selectedProcessId) ?? null,
    [processes, selectedProcessId],
  );
  const [nodes, setNodes] = useState<ProcessFlowNode[]>([]);
  const [edges, setEdges] = useState<ProcessFlowEdge[]>([]);
  const nodesRef = useRef<ProcessFlowNode[]>([]);
  const edgesRef = useRef<ProcessFlowEdge[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [processName, setProcessName] = useState("");
  const [processDescription, setProcessDescription] = useState("");
  const [dirty, setDirty] = useState(false);
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null);
  const [editorNotice, setEditorNotice] = useState<string | null>(null);
  const [leftPanel, setLeftPanel] = useState<LeftPanel>(null);
  const [executionsOpen, setExecutionsOpen] = useState(false);
  const [releaseOpen, setReleaseOpen] = useState(false);
  const [selectedInstanceId, setSelectedInstanceId] = useState<string | null>(null);
  const [executionTrace, setExecutionTrace] = useState<RunTrace | null>(null);
  const [executionLoading, setExecutionLoading] = useState(false);
  const [executionError, setExecutionError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<TestProcessNodeResult | null>(null);
  const [testBusy, setTestBusy] = useState(false);
  const [picker, setPicker] = useState<PickerState | null>(null);
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance<ProcessFlowNode, ProcessFlowEdge> | null>(null);
  const canvasRef = useRef<HTMLElement>(null);
  const loadedProcessIdRef = useRef<string | null>(null);
  const previousMobileRef = useRef(mobile);
  const revisionRef = useRef(0);
  const historyRef = useRef<GraphHistory>({ entries: [], index: -1 });
  const clipboardRef = useRef<ClipboardGraph | null>(null);
  const [, setHistoryRevision] = useState(0);

  useEffect(() => { nodesRef.current = nodes; }, [nodes]);
  useEffect(() => { edgesRef.current = edges; }, [edges]);

  const markDirty = useCallback(() => {
    revisionRef.current += 1;
    setDirty(true);
  }, []);

  const selectNode = useCallback((nodeId: string) => {
    setSelectedNodeId(nodeId);
    setNodes((current) => current.map((node) => ({ ...node, selected: node.id === nodeId })));
  }, []);

  const clearNodeSelection = useCallback(() => {
    setSelectedNodeId(null);
    setNodes((current) => current.map((node) => node.selected ? { ...node, selected: false } : node));
  }, []);

  const openInsertPicker = useCallback<ProcessEdgeData["onInsert"]>((edgeId, flowPosition, screenPosition) => {
    setPicker({ edgeId, flowPosition, screenPosition });
  }, []);

  const setGraphState = useCallback((graph: ProcessGraph, selection: string | null = null) => {
    const nextNodes = graphToFlowNodes(graph, agentsById, selectNode, mobile)
      .map((node) => ({ ...node, selected: node.id === selection }));
    const nextEdges = graphToFlowEdges(graph, openInsertPicker);
    nodesRef.current = nextNodes;
    edgesRef.current = nextEdges;
    setNodes(nextNodes);
    setEdges(nextEdges);
    setSelectedNodeId(selection);
  }, [agentsById, mobile, openInsertPicker, selectNode]);

  const updateHistoryAvailability = useCallback(() => setHistoryRevision((value) => value + 1), []);

  const resetHistory = useCallback((graph: ProcessGraph) => {
    historyRef.current = { entries: [cloneGraph(graph)], index: 0 };
    updateHistoryAvailability();
  }, [updateHistoryAvailability]);

  const recordHistory = useCallback((graph: ProcessGraph) => {
    const history = historyRef.current;
    const current = history.entries[history.index];
    if (current && graphSignature(current) === graphSignature(graph)) return;
    const entries = history.entries.slice(0, history.index + 1);
    entries.push(cloneGraph(graph));
    if (entries.length > MAX_HISTORY) entries.shift();
    historyRef.current = { entries, index: entries.length - 1 };
    updateHistoryAvailability();
  }, [updateHistoryAvailability]);

  const commitGraph = useCallback((graph: ProcessGraph, selection: string | null = selectedNodeId) => {
    recordHistory(graph);
    setGraphState(graph, selection);
    markDirty();
  }, [markDirty, recordHistory, selectedNodeId, setGraphState]);

  useEffect(() => {
    const previousMobile = previousMobileRef.current;
    if (previousMobile === mobile) return;
    previousMobileRef.current = mobile;
    if (!loadedProcessIdRef.current || nodesRef.current.length === 0) return;
    const graph = graphFromFlow(nodesRef.current, edgesRef.current, previousMobile);
    setGraphState(graph, selectedNodeId);
    window.requestAnimationFrame(() => void flowInstance?.fitView({ padding: 0.2, duration: 180 }));
  }, [flowInstance, mobile, selectedNodeId, setGraphState]);

  useEffect(() => {
    if (selectedProcessId && processes.some((process) => process.id === selectedProcessId)) return;
    setSelectedProcessId(processes[0]?.id ?? null);
  }, [processes, selectedProcessId]);

  useEffect(() => {
    if (!selectedProcess) {
      loadedProcessIdRef.current = null;
      setNodes([]);
      setEdges([]);
      setProcessName("");
      setProcessDescription("");
      setDirty(false);
      return;
    }
    if (loadedProcessIdRef.current === selectedProcess.id) return;
    loadedProcessIdRef.current = selectedProcess.id;
    revisionRef.current = 0;
    setGraphState(selectedProcess.draftGraph);
    resetHistory(selectedProcess.draftGraph);
    setProcessName(selectedProcess.name);
    setProcessDescription(selectedProcess.description);
    setDirty(false);
    setLastSavedAt(selectedProcess.updatedAt);
    setEditorNotice(null);
    setPicker(null);
    setExecutionsOpen(false);
    setSelectedInstanceId(null);
    setExecutionTrace(null);
    setExecutionError(null);
    setTestResult(null);
  }, [resetHistory, selectedProcess, setGraphState]);

  useEffect(() => {
    setNodes((current) => current.map((node) => {
      const graphNode = flowNodeToGraph(node, mobile);
      return {
        ...node,
        data: { ...node.data, subtitle: nodeSubtitle(graphNode, agentsById) },
      };
    }));
  }, [agentsById, mobile]);

  const selectedFlowNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) ?? null,
    [nodes, selectedNodeId],
  );
  const selectedGraphNode = selectedFlowNode ? flowNodeToGraph(selectedFlowNode, mobile) : null;
  const selectedInstances = useMemo(
    () => selectedProcess ? instances.filter((instance) => instance.processId === selectedProcess.id).slice(0, 20) : [],
    [instances, selectedProcess?.id],
  );
  const selectedInstance = useMemo(
    () => selectedInstances.find((instance) => instance.id === selectedInstanceId) ?? null,
    [selectedInstanceId, selectedInstances],
  );
  const selectedNodeExecution = useMemo(
    () => selectedNodeId ? executionForNode(executionTrace, selectedNodeId) : null,
    [executionTrace, selectedNodeId],
  );
  const activeInstanceCount = selectedInstances.filter((instance) => ["queued", "running", "waiting_approval", "waiting_external", "compensating"].includes(instance.status)).length;
  const history = historyRef.current;
  const canUndo = history.index > 0;
  const canRedo = history.index >= 0 && history.index < history.entries.length - 1;

  useEffect(() => {
    if (!selectedInstance) {
      setExecutionTrace(null);
      setExecutionError(null);
      setExecutionLoading(false);
      return;
    }
    const controller = new AbortController();
    setExecutionLoading(true);
    setExecutionError(null);
    void api.runTrace(selectedInstance.runId, controller.signal)
      .then((trace) => setExecutionTrace(trace))
      .catch((requestError: unknown) => {
        if (controller.signal.aborted) return;
        setExecutionError(requestError instanceof Error ? requestError.message : "Не удалось загрузить выполнение");
      })
      .finally(() => {
        if (!controller.signal.aborted) setExecutionLoading(false);
      });
    return () => controller.abort();
  }, [selectedInstance?.runId, selectedInstance?.updatedAt]);

  useEffect(() => {
    const nodeExecutions = new Map<string, ProcessNodeExecutionDetails>();
    for (const node of nodesRef.current) {
      const details = executionForNode(executionTrace, node.id);
      if (details) nodeExecutions.set(node.id, details);
    }
    const nextNodes = nodesRef.current.map((node) => {
      const details = nodeExecutions.get(node.id);
      return {
        ...node,
        data: {
          ...node.data,
          execution: details ? {
            status: details.status,
            label: executionStatusLabels[details.status],
            attempt: details.attempt,
          } : undefined,
        },
      };
    });
    const transitions = new Set((executionTrace?.events ?? []).flatMap((event) => {
      if (event.type !== "process.transition") return [];
      const key = transitionKey(event.data?.sourceProcessNodeId, event.data?.targetProcessNodeId);
      return key ? [key] : [];
    }));
    const activeNodeIds = new Set(selectedInstance?.activeNodes?.map((node) => node.id) ?? (selectedInstance?.currentNode ? [selectedInstance.currentNode.id] : []));
    const nextEdges = edgesRef.current.map((edge) => ({
      ...edge,
      data: edge.data ? {
        ...edge.data,
        executed: transitions.has(`${edge.source}\u0000${edge.target}`),
        active: activeNodeIds.has(edge.target) && ["queued", "running", "waiting_approval", "waiting_external", "compensating"].includes(selectedInstance?.status ?? ""),
      } : edge.data,
    }));
    nodesRef.current = nextNodes;
    edgesRef.current = nextEdges;
    setNodes(nextNodes);
    setEdges(nextEdges);
  }, [executionTrace, selectedInstance?.activeNodes, selectedInstance?.currentNode?.id, selectedInstance?.status]);

  useEffect(() => setTestResult(null), [selectedNodeId]);

  function currentGraph(): ProcessGraph {
    return graphFromFlow(nodesRef.current, edgesRef.current, mobile);
  }

  function currentPayload(): UpdateProcessRequest | null {
    if (!selectedProcess) return null;
    return { name: processName, description: processDescription, graph: currentGraph(), isTemplate: selectedProcess.isTemplate };
  }

  function pickerAtCanvasCenter(edgeId: string | null = null): PickerState | null {
    const canvas = canvasRef.current;
    if (!canvas || !flowInstance) return null;
    const bounds = canvas.getBoundingClientRect();
    const screenPosition = {
      x: Math.min(bounds.right - 340, bounds.left + Math.max(72, bounds.width * 0.46)),
      y: Math.min(bounds.bottom - 460, bounds.top + Math.max(68, bounds.height * 0.24)),
    };
    return {
      edgeId,
      screenPosition,
      flowPosition: flowInstance.screenToFlowPosition({
        x: bounds.left + bounds.width / 2,
        y: bounds.top + bounds.height / 2,
      }),
    };
  }

  function openNodePicker() {
    const next = pickerAtCanvasCenter();
    if (next) setPicker(next);
  }

  async function chooseProcess(processId: string) {
    if (processId === selectedProcessId) return;
    if (dirty) {
      const target = processes.find((process) => process.id === processId);
      const decision = await requestAction({
        title: "Перейти без сохранения?",
        description: target ? `Откроется процесс «${target.name}».` : "Откроется другой процесс.",
        subject: processName || selectedProcess?.name,
        subjectLabel: "Несохранённый процесс",
        impact: "Локальные изменения схемы, названия и описания будут отброшены.",
        recovery: "Последняя сохранённая версия останется доступна, но текущие несохранённые изменения восстановить нельзя.",
        confirmLabel: "Отбросить и перейти",
        cancelLabel: "Остаться",
        tone: "warning",
      });
      if (!decision.confirmed) return;
    }
    loadedProcessIdRef.current = null;
    setSelectedProcessId(processId);
  }

  function insertNode(type: ProcessNodeType, state: PickerState) {
    setEditorNotice(null);
    const graph = currentGraph();
    if (type === "start" && graph.nodes.some((node) => node.type === "start")) {
      setEditorNotice("В процессе может быть только один старт");
      return;
    }
    if (type === "agent" && agents.length === 0) {
      setEditorNotice("Сначала создайте хотя бы одного агента");
      return;
    }
    const node = defaultNode(type, state.flowPosition, agents);
    let nextEdges = [...graph.edges];
    if (state.edgeId) {
      const replaced = nextEdges.find((edge) => edge.id === state.edgeId);
      if (replaced) {
        nextEdges = nextEdges.filter((edge) => edge.id !== replaced.id);
        nextEdges.push({
          id: crypto.randomUUID(),
          source: replaced.source,
          target: node.id,
          branch: replaced.branch,
        });
        const branch = primaryBranch(type);
        if (branch) {
          nextEdges.push({ id: crypto.randomUUID(), source: node.id, target: replaced.target, branch });
        }
      }
    }
    const next = { nodes: [...graph.nodes, node], edges: nextEdges };
    commitGraph(next, node.id);
    setPicker(null);
  }

  function addNodeAtCenter(type: ProcessNodeType) {
    const state = pickerAtCanvasCenter();
    if (state) insertNode(type, state);
  }

  function changeSelectedNode(next: ProcessGraphNode) {
    const graph = currentGraph();
    commitGraph({
      ...graph,
      nodes: graph.nodes.map((node) => node.id === next.id ? next : node),
    }, next.id);
  }

  function deleteNodes(nodeIds: string[]) {
    if (nodeIds.length === 0) return;
    const ids = new Set(nodeIds);
    const graph = currentGraph();
    commitGraph({
      nodes: graph.nodes.filter((node) => !ids.has(node.id)),
      edges: graph.edges.filter((edge) => !ids.has(edge.source) && !ids.has(edge.target)),
    }, null);
  }

  function undo() {
    const current = historyRef.current;
    if (current.index <= 0) return;
    current.index -= 1;
    setGraphState(cloneGraph(current.entries[current.index]!));
    markDirty();
    updateHistoryAvailability();
  }

  function redo() {
    const current = historyRef.current;
    if (current.index >= current.entries.length - 1) return;
    current.index += 1;
    setGraphState(cloneGraph(current.entries[current.index]!));
    markDirty();
    updateHistoryAvailability();
  }

  function copySelection(): boolean {
    const selectedIds = new Set(nodesRef.current.filter((node) => node.selected).map((node) => node.id));
    if (selectedIds.size === 0 && selectedNodeId) selectedIds.add(selectedNodeId);
    if (selectedIds.size === 0) return false;
    const graph = currentGraph();
    clipboardRef.current = {
      nodes: graph.nodes.filter((node) => selectedIds.has(node.id)).map((node) => structuredClone(node)),
      edges: graph.edges.filter((edge) => selectedIds.has(edge.source) && selectedIds.has(edge.target)).map((edge) => structuredClone(edge)),
    };
    setEditorNotice(`Скопировано шагов: ${selectedIds.size}`);
    return true;
  }

  function pasteSelection() {
    const copied = clipboardRef.current;
    if (!copied || copied.nodes.length === 0) return;
    const graph = currentGraph();
    const ids = new Map(copied.nodes.map((node) => [node.id, crypto.randomUUID()]));
    const pastedNodes = copied.nodes
      .filter((node) => node.type !== "start" || !graph.nodes.some((item) => item.type === "start"))
      .map((node) => ({
        ...structuredClone(node),
        id: ids.get(node.id)!,
        name: `${node.name} — копия`,
        config: {
          ...structuredClone(node.config),
          ...(node.config.forkId && ids.has(node.config.forkId) ? { forkId: ids.get(node.config.forkId) } : {}),
        },
        position: { x: node.position.x + 48, y: node.position.y + 48 },
      }));
    const pastedIds = new Set(pastedNodes.map((node) => node.id));
    const pastedEdges = copied.edges.flatMap((edge) => {
      const source = ids.get(edge.source);
      const target = ids.get(edge.target);
      if (!source || !target || !pastedIds.has(source) || !pastedIds.has(target)) return [];
      return [{ ...structuredClone(edge), id: crypto.randomUUID(), source, target }];
    });
    commitGraph({ nodes: [...graph.nodes, ...pastedNodes], edges: [...graph.edges, ...pastedEdges] }, pastedNodes[0]?.id ?? null);
  }

  function duplicateSelection() {
    if (copySelection()) pasteSelection();
  }

  function applyAutomaticLayout() {
    const graph = autoLayout(currentGraph());
    commitGraph(graph, selectedNodeId);
    window.requestAnimationFrame(() => void flowInstance?.fitView({ padding: 0.18, duration: 260 }));
  }

  async function testNode(node: ProcessGraphNode, input: string) {
    if (!selectedProcess) return;
    setTestBusy(true);
    setTestResult(null);
    setEditorNotice(null);
    try {
      const result = await api.testProcessNode(selectedProcess.id, node, input);
      setTestResult(result);
      setEditorNotice(result.runId
        ? "Тест шага добавлен в очередь"
        : `Тест завершён${result.branch ? ` · ветка ${branchLabels[result.branch] || result.branch}` : ""}`);
    } catch (requestError) {
      setEditorNotice(requestError instanceof Error ? requestError.message : "Не удалось протестировать шаг");
    } finally {
      setTestBusy(false);
    }
  }

  async function runFromNode(node: ProcessGraphNode, input: string) {
    if (!selectedProcess) return;
    setTestBusy(true);
    setEditorNotice(null);
    try {
      const instance = await api.startProcess(selectedProcess.id, {
        input,
        priority: 50,
        resultDestination: "history",
        artifactPath: "",
        startNodeId: node.id,
      });
      setSelectedInstanceId(instance.id);
      setExecutionsOpen(true);
      setEditorNotice(`Запуск продолжен с шага «${node.name}»`);
    } catch (requestError) {
      setEditorNotice(requestError instanceof Error ? requestError.message : "Не удалось запустить процесс с выбранного шага");
    } finally {
      setTestBusy(false);
    }
  }

  async function save(quiet = false) {
    const payload = currentPayload();
    if (!selectedProcess || !payload) return;
    const revision = revisionRef.current;
    try {
      const updated = await onSave(selectedProcess.id, payload);
      if (revisionRef.current === revision) setDirty(false);
      setLastSavedAt(updated.updatedAt);
      if (!quiet) setEditorNotice("Черновик сохранён");
    } catch {
      // App exposes the actionable server error in the editor banner.
    }
  }

  async function publish() {
    const payload = currentPayload();
    if (!selectedProcess || !payload) return;
    try {
      const published = await onPublish(selectedProcess.id, payload, dirty);
      setDirty(false);
      setLastSavedAt(published.updatedAt);
      setEditorNotice(`Опубликована версия ${published.publishedVersion}`);
    } catch {
      // App exposes the actionable server error in the editor banner.
    }
  }

  useEffect(() => {
    if (!dirty || busy || !selectedProcess) return;
    const timer = window.setTimeout(() => void save(true), 1_300);
    return () => window.clearTimeout(timer);
  }, [busy, dirty, edges, nodes, processDescription, processName, selectedProcess?.id]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (isFormTarget(event.target)) return;
      const modifier = event.metaKey || event.ctrlKey;
      const key = event.key.toLocaleLowerCase("en");
      if (modifier && key === "s") {
        event.preventDefault();
        void save();
      } else if (modifier && key === "z") {
        event.preventDefault();
        if (event.shiftKey) redo(); else undo();
      } else if (modifier && key === "c") {
        event.preventDefault();
        copySelection();
      } else if (modifier && key === "v") {
        event.preventDefault();
        pasteSelection();
      } else if (modifier && key === "d") {
        event.preventDefault();
        duplicateSelection();
      } else if (event.key === "Delete" || event.key === "Backspace") {
        const ids = nodesRef.current.filter((node) => node.selected).map((node) => node.id);
        if (ids.length > 0) {
          event.preventDefault();
          deleteNodes(ids);
        }
      } else if (event.key === "Tab" && !picker) {
        event.preventDefault();
        openNodePicker();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  if (processes.length === 0) {
    return (
      <main className="process-empty-page">
        <div>
          <Icon name="workflow" size={38} />
          <h1>Процессы</h1>
          <p>Создавайте цепочки агентов, условия и безопасные циклы на визуальном полотне.</p>
          <button className="button button--primary" type="button" onClick={onCreate}><Icon name="plus" />Новый процесс</button>
        </div>
      </main>
    );
  }

  const runDisabled = !selectedProcess
    || selectedProcess.publishedVersion === 0
    || selectedProcess.hasUnpublishedChanges
    || dirty;
  const publishLabel = selectedProcess?.publishedVersion ? "Новая версия" : "Опубликовать";
  const savedLabel = dirty
    ? "Сохраняем…"
    : lastSavedAt
      ? `Сохранено ${new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" }).format(new Date(lastSavedAt))}`
      : "Сохранено";

  return (
    <main className={`process-page${selectedNodeId ? " has-selected-node" : ""}${executionsOpen ? " has-open-executions" : ""}`}>
      <header className="process-commandbar">
        <div className="process-commandbar__identity">
          <button
            className="process-commandbar__library"
            type="button"
            aria-label="Открыть библиотеку процессов"
            onClick={() => setLeftPanel((current) => current === "processes" ? null : "processes")}
          >
            <Icon name="menu" size={18} />
          </button>
          <select
            className="process-mobile-select"
            aria-label="Выбранный процесс"
            value={selectedProcessId ?? ""}
            onChange={(event) => void chooseProcess(event.target.value)}
          >
            {processes.map((process) => <option value={process.id} key={process.id}>{process.name}</option>)}
          </select>
          <input
            className="process-title-input"
            value={processName}
            maxLength={100}
            aria-label="Название процесса"
            onChange={(event) => { setProcessName(event.target.value); markDirty(); }}
          />
          <span className={`process-draft-state${dirty ? " is-dirty" : ""}`}>{savedLabel}</span>
          <span className="mono process-version">v{selectedProcess?.publishedVersion ?? 0}</span>
        </div>
        <div className="process-commandbar__history" aria-label="История изменений">
          <button type="button" disabled={!canUndo} title="Отменить (⌘Z)" onClick={undo}><Icon name="undo" size={16} /></button>
          <button type="button" disabled={!canRedo} title="Повторить (⇧⌘Z)" onClick={redo}><Icon name="redo" size={16} /></button>
        </div>
        <div className="process-commandbar__actions">
          <button className="button button--secondary process-save-action" type="button" disabled={busy || !dirty} onClick={() => void save()}>
            <Icon name="save" size={16} /><span>Сохранить</span>
          </button>
          <button className="button button--secondary" type="button" disabled={busy} onClick={() => void publish()}>
            <Icon name="publish" size={16} /><span>{publishLabel}</span>
          </button>
          <button className="button button--secondary" type="button" disabled={busy || !selectedProcess} onClick={() => setReleaseOpen(true)} title="Triggers, версии и BPMN">
            <Icon name="dots" size={16} /><span>Релиз</span>
          </button>
          <button
            className="button button--primary"
            type="button"
            disabled={busy || runDisabled}
            title={runDisabled ? "Сохраните и опубликуйте текущий граф" : "Запустить процесс"}
            onClick={() => selectedProcess && onStart(selectedProcess)}
          >
            <Icon name="play" size={16} /><span>Запустить</span>
          </button>
        </div>
      </header>

      {(error || editorNotice) ? (
        <button className={`process-editor-notice${error ? " is-error" : ""}`} type="button" onClick={() => setEditorNotice(null)}>
          {error ?? editorNotice}
        </button>
      ) : null}

      <div className="process-editor-layout">
        <section ref={canvasRef} className="process-canvas" aria-label="Визуальный редактор процесса">
          <ReactFlow<ProcessFlowNode, ProcessFlowEdge>
            nodes={nodes}
            edges={edges}
            nodeTypes={processNodeTypes}
            edgeTypes={processEdgeTypes}
            onInit={setFlowInstance}
            onNodesChange={(changes: NodeChange<ProcessFlowNode>[]) => {
              const nextNodes = applyNodeChanges(changes, nodesRef.current);
              const removedIds = new Set(changes.filter((change) => change.type === "remove").map((change) => change.id));
              const nextEdges = removedIds.size
                ? edgesRef.current.filter((edge) => !removedIds.has(edge.source) && !removedIds.has(edge.target))
                : edgesRef.current;
              nodesRef.current = nextNodes;
              edgesRef.current = nextEdges;
              setNodes(nextNodes);
              if (removedIds.size) {
                setEdges(nextEdges);
                recordHistory(graphFromFlow(nextNodes, nextEdges, mobile));
                markDirty();
                setSelectedNodeId(null);
              }
            }}
            onEdgesChange={(changes: EdgeChange<ProcessFlowEdge>[]) => {
              const nextEdges = applyEdgeChanges(changes, edgesRef.current);
              edgesRef.current = nextEdges;
              setEdges(nextEdges);
              if (changes.some((change) => change.type === "remove")) {
                recordHistory(graphFromFlow(nodesRef.current, nextEdges, mobile));
                markDirty();
              }
            }}
            onConnect={(connection: Connection) => {
              if (!connection.source || !connection.target || connection.source === connection.target) return;
              const sourceNode = nodesRef.current.find((node) => node.id === connection.source);
              const targetNode = nodesRef.current.find((node) => node.id === connection.target);
              if (!sourceNode || !targetNode || sourceNode.data.kind === "end" || targetNode.data.kind === "start") return;
              const branch = (connection.sourceHandle || "default") as ProcessBranch;
              const nextEdge: ProcessFlowEdge = {
                id: crypto.randomUUID(),
                source: connection.source,
                target: connection.target,
                sourceHandle: connection.sourceHandle || "default",
                targetHandle: connection.targetHandle,
                type: "process",
                animated: branch === "repeat",
                data: { branch, onInsert: openInsertPicker },
                markerEnd: { type: MarkerType.ArrowClosed },
              };
              const parallelFork = sourceNode.data.kind === "parallel_fork";
              const retainedEdges = parallelFork
                ? edgesRef.current.filter((edge) => !(edge.source === nextEdge.source && edge.target === nextEdge.target))
                : edgesRef.current.filter((edge) => !(edge.source === nextEdge.source && edge.data?.branch === branch));
              const nextEdges = addEdge(nextEdge, retainedEdges);
              edgesRef.current = nextEdges;
              setEdges(nextEdges);
              recordHistory(graphFromFlow(nodesRef.current, nextEdges, mobile));
              markDirty();
            }}
            isValidConnection={(connection) => {
              if (!connection.source || !connection.target || connection.source === connection.target) return false;
              const source = nodesRef.current.find((node) => node.id === connection.source);
              const target = nodesRef.current.find((node) => node.id === connection.target);
              return Boolean(source && target && source.data.kind !== "end" && target.data.kind !== "start");
            }}
            onNodeClick={(_, node) => selectNode(node.id)}
            onPaneClick={(event) => {
              if (event.detail === 2 && flowInstance) {
                setPicker({
                  edgeId: null,
                  screenPosition: { x: event.clientX + 8, y: event.clientY + 8 },
                  flowPosition: flowInstance.screenToFlowPosition({ x: event.clientX, y: event.clientY }),
                });
              } else {
                clearNodeSelection();
              }
            }}
            onNodeDragStop={(_, node) => {
              if (mobile) return;
              const nextNodes = nodesRef.current.map((item) => item.id === node.id
                ? { ...item, position: node.position, data: { ...item.data, storedPosition: node.position } }
                : item);
              nodesRef.current = nextNodes;
              setNodes(nextNodes);
              recordHistory(graphFromFlow(nextNodes, edgesRef.current, false));
              markDirty();
            }}
            onDragOver={(event: DragEvent) => {
              event.preventDefault();
              event.dataTransfer.dropEffect = "copy";
            }}
            onDrop={(event: DragEvent) => {
              event.preventDefault();
              const rawType = event.dataTransfer.getData("application/agat-process-node") as ProcessNodeType;
              if (!PROCESS_NODE_TYPES.has(rawType) || !flowInstance) return;
              insertNode(rawType, {
                edgeId: null,
                screenPosition: { x: event.clientX, y: event.clientY },
                flowPosition: flowInstance.screenToFlowPosition({ x: event.clientX, y: event.clientY }),
              });
            }}
            fitView
            fitViewOptions={{ padding: mobile ? 0.22 : 0.16, maxZoom: mobile ? 0.8 : 1.1 }}
            minZoom={0.16}
            maxZoom={2}
            nodesDraggable={!mobile}
            snapToGrid
            snapGrid={[16, 16]}
            selectionOnDrag
            panOnScroll
            deleteKeyCode={null}
            defaultEdgeOptions={{ type: "process", markerEnd: { type: MarkerType.ArrowClosed } }}
            colorMode="dark"
            proOptions={{ hideAttribution: true }}
          >
            <Background color="#344252" gap={22} size={1} />
            <Controls showInteractive={false} />
            {mobile ? null : <MiniMap pannable zoomable maskColor="rgb(5 12 21 / 72%)" />}
            <Panel position="top-left" className="process-canvas-rail">
              <button
                className={leftPanel === "processes" ? "is-active" : ""}
                type="button"
                title="Процессы"
                onClick={() => setLeftPanel((current) => current === "processes" ? null : "processes")}
              ><Icon name="workflow" size={18} /></button>
              <button
                className={leftPanel === "nodes" ? "is-active" : ""}
                type="button"
                title="Добавить шаг"
                onClick={() => setLeftPanel((current) => current === "nodes" ? null : "nodes")}
              ><Icon name="plus" size={19} /></button>
              <span />
              <button type="button" title="Автоматически расположить" onClick={applyAutomaticLayout}><Icon name="layout" size={18} /></button>
            </Panel>
            <Panel position="top-center" className="process-canvas-hint">
              <button type="button" onClick={openNodePicker}><Icon name="plus" size={14} />Добавить шаг <kbd>Tab</kbd></button>
            </Panel>
          </ReactFlow>

          {leftPanel ? (
            <ProcessCatalogPanel
              panel={leftPanel}
              processes={processes}
              selectedProcessId={selectedProcessId}
              processDescription={processDescription}
              onClose={() => setLeftPanel(null)}
              onCreate={onCreate}
              onChooseProcess={chooseProcess}
              onDescriptionChange={(value) => { setProcessDescription(value); markDirty(); }}
              onAddNode={addNodeAtCenter}
            />
          ) : null}

          {selectedInstance ? (
            <div className={`process-execution-banner process-execution-banner--${selectedInstance.status}`}>
              <span><i /><strong>Выполнение v{selectedInstance.processVersion}</strong><small>{selectedInstance.status}{executionLoading ? " · загрузка трассы" : ""}</small></span>
              {executionError ? <em>{executionError}</em> : null}
              <button type="button" onClick={() => onOpenRun(selectedInstance.runId)}>Полный журнал <Icon name="chevron" size={13} /></button>
              <button className="icon-button" type="button" aria-label="Закрыть просмотр выполнения" onClick={() => setSelectedInstanceId(null)}><Icon name="close" size={15} /></button>
            </div>
          ) : null}

          <ProcessInspector
            node={selectedGraphNode}
            agents={agents}
            credentials={credentials}
            processes={processes}
            currentProcessId={selectedProcess?.id ?? null}
            processNodes={currentGraph().nodes}
            execution={selectedNodeExecution}
            executionLoading={executionLoading}
            defaultInput={selectedInstance?.input ?? ""}
            testResult={testResult}
            testBusy={testBusy}
            canRunFrom={Boolean(selectedProcess && selectedProcess.publishedVersion > 0 && !selectedProcess.hasUnpublishedChanges && !dirty)}
            onChange={changeSelectedNode}
            onDelete={(nodeId) => deleteNodes([nodeId])}
            onClose={clearNodeSelection}
            onTest={(node, input) => void testNode(node, input)}
            onRunFrom={(node, input) => void runFromNode(node, input)}
            onOpenRun={onOpenRun}
            onManageCredentials={onManageCredentials}
          />

          <button
            className={`process-executions-toggle${executionsOpen ? " is-active" : ""}`}
            type="button"
            onClick={() => setExecutionsOpen((value) => {
              const next = !value;
              if (next && !selectedInstanceId && selectedInstances[0]) setSelectedInstanceId(selectedInstances[0].id);
              return next;
            })}
          >
            <Icon name="runs" size={15} />
            Выполнения
            <span>{selectedInstances.length}</span>
            {activeInstanceCount ? <i>{activeInstanceCount} активн.</i> : null}
            <Icon name={executionsOpen ? "down" : "up"} size={14} />
          </button>
          {executionsOpen ? (
            <div className="process-executions-drawer">
              <ProcessInstances
                instances={selectedInstances}
                busy={busy}
                selectedInstanceId={selectedInstanceId}
                onSelect={(instance) => setSelectedInstanceId(instance.id)}
                onOpenRun={onOpenRun}
                onCancel={onCancel}
                onReplay={onReplay}
              />
            </div>
          ) : null}
        </section>
      </div>

      <ProcessNodePicker
        open={picker !== null}
        position={picker?.screenPosition ?? null}
        excludedTypes={picker?.edgeId ? ["start", "end"] : []}
        onClose={() => setPicker(null)}
        onSelect={(type) => picker && insertNode(type, picker)}
      />
      <ProcessReleasePanel
        open={releaseOpen}
        process={selectedProcess}
        onClose={() => setReleaseOpen(false)}
        onChanged={onChanged}
      />
    </main>
  );
}
