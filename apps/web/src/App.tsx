import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AgentDialog } from "./components/AgentDialog";
import { AdminTokenDialog } from "./components/AdminTokenDialog";
import { AgentsPage } from "./components/AgentsPage";
import { BottomNav } from "./components/BottomNav";
import { Breadcrumbs } from "./components/Breadcrumbs";
import { CredentialsDialog } from "./components/CredentialsDialog";
import { NewRunDialog } from "./components/NewRunDialog";
import { NodeRail } from "./components/NodeRail";
import { NodesPage } from "./components/NodesPage";
import { OverviewPage } from "./components/OverviewPage";
import { ProjectDialog } from "./components/ProjectDialog";
import { NewProcessDialog, StartProcessDialog } from "./components/ProcessDialogs";
import { RunsPage } from "./components/RunsPage";
import { Sidebar } from "./components/Sidebar";
import { Topbar, type PrimaryAction } from "./components/Topbar";
import { api, provideAdminToken, subscribeToAdminTokenRequests, subscribeToEvents } from "./lib/api";
import { activeProjectId, logout, setActiveProjectId } from "./lib/auth";
import { canAccessView, formatAppRoute, getRoleCapabilities, parseAppRoute } from "./navigation";
import { buildNotifications, type AppNotification } from "./notifications";
import type {
  Agent,
  Approval,
  AuthUser,
  CreateAgentRequest,
  CreateCredentialRequest,
  CreateProcessRequest,
  CreateRunRequest,
  ModelRouterPolicy,
  Overview,
  ProjectSummary,
  ReplayRunRequest,
  ProcessDefinition,
  SchedulerMode,
  StartProcessRequest,
  UpdateProcessRequest,
  ViewId,
} from "./types";

const ProcessesPage = lazy(() => import("./components/ProcessesPage").then((module) => ({ default: module.ProcessesPage })));
const McpPage = lazy(() => import("./components/McpPage").then((module) => ({ default: module.McpPage })));
const ModelsPage = lazy(() => import("./components/ModelsPage").then((module) => ({ default: module.ModelsPage })));
const KnowledgePage = lazy(() => import("./components/KnowledgePage").then((module) => ({ default: module.KnowledgePage })));
const EvalsPage = lazy(() => import("./components/EvalsPage").then((module) => ({ default: module.EvalsPage })));
const A2APage = lazy(() => import("./components/A2APage").then((module) => ({ default: module.A2APage })));
const FleetPage = lazy(() => import("./components/FleetPage").then((module) => ({ default: module.FleetPage })));
function routeFromLocation() {
  return parseAppRoute(window.location.hash);
}

export default function App() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [activeView, setActiveView] = useState<ViewId>(() => routeFromLocation().view);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(() => routeFromLocation().runId);
  const [runDetailOpen, setRunDetailOpen] = useState(() => routeFromLocation().runId !== null);
  const [runDialogOpen, setRunDialogOpen] = useState(false);
  const [runInitialAgentIds, setRunInitialAgentIds] = useState<string[] | null>(null);
  const [agentDialogOpen, setAgentDialogOpen] = useState(false);
  const [editingAgent, setEditingAgent] = useState<Agent | null>(null);
  const [processDialogOpen, setProcessDialogOpen] = useState(false);
  const [startingProcess, setStartingProcess] = useState<ProcessDefinition | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runFormError, setRunFormError] = useState<string | null>(null);
  const [agentFormError, setAgentFormError] = useState<string | null>(null);
  const [processError, setProcessError] = useState<string | null>(null);
  const [adminTokenDialogOpen, setAdminTokenDialogOpen] = useState(false);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [currentProjectId, setCurrentProjectId] = useState(activeProjectId);
  const [projectDialogOpen, setProjectDialogOpen] = useState(false);
  const [projectError, setProjectError] = useState<string | null>(null);
  const [credentialsDialogOpen, setCredentialsDialogOpen] = useState(false);
  const [mcpCreateRequest, setMcpCreateRequest] = useState(0);
  const [a2aCreateRequest, setA2ACreateRequest] = useState(0);
  const [credentialsError, setCredentialsError] = useState<string | null>(null);
  const refreshTimer = useRef<number | null>(null);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const next = await api.overview(signal);
      setOverview(next);
      setError(null);
      setSelectedRunId((current) => {
        if (current && next.runs.some((run) => run.id === current)) return current;
        return next.runs.find((run) => run.status === "running")?.id ?? next.runs[0]?.id ?? null;
      });
    } catch (requestError) {
      if (requestError instanceof DOMException && requestError.name === "AbortError") return;
      setError(requestError instanceof Error ? requestError.message : "Координатор недоступен");
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let unsubscribe: () => void = () => undefined;
    let interval: number | null = null;

    void (async () => {
      try {
        const nextProjects = await api.projects();
        if (controller.signal.aborted) return;
        setActiveProjectId(nextProjects.activeProjectId);
        setProjects(nextProjects.projects);
        setCurrentProjectId(nextProjects.activeProjectId);
        const nextUser = await api.me();
        if (controller.signal.aborted) return;
        setUser(nextUser);
        await refresh(controller.signal);
        if (controller.signal.aborted) return;
        unsubscribe = subscribeToEvents(() => {
          if (refreshTimer.current !== null) window.clearTimeout(refreshTimer.current);
          refreshTimer.current = window.setTimeout(() => void refresh(), 120);
        });
        interval = window.setInterval(() => void refresh(), 15_000);
      } catch (requestError) {
        if (requestError instanceof DOMException && requestError.name === "AbortError") return;
        setError(requestError instanceof Error ? requestError.message : "Не удалось загрузить профиль и проекты");
      }
    })();

    return () => {
      controller.abort();
      unsubscribe();
      if (interval !== null) window.clearInterval(interval);
      if (refreshTimer.current !== null) window.clearTimeout(refreshTimer.current);
    };
  }, [refresh]);

  async function changeProject(projectId: string) {
    setActiveProjectId(projectId);
    setCurrentProjectId(projectId);
    setSelectedRunId(null);
    setRunDetailOpen(false);
    window.history.replaceState(null, "", formatAppRoute(activeView));
    setOverview(null);
    await refresh();
  }

  async function createProject(name: string, id: string) {
    setBusy(true);
    setProjectError(null);
    try {
      const project = await api.createProject(name, id);
      const next = await api.projects();
      setProjects(next.projects);
      setProjectDialogOpen(false);
      await changeProject(project.id);
    } catch (requestError) {
      setProjectError(requestError instanceof Error ? requestError.message : "Не удалось создать проект");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => subscribeToAdminTokenRequests(setAdminTokenDialogOpen), []);

  useEffect(() => {
    const onHistoryChange = () => {
      const route = routeFromLocation();
      setActiveView(route.view);
      setRunDetailOpen(route.runId !== null);
      if (route.runId) setSelectedRunId(route.runId);
    };
    window.addEventListener("hashchange", onHistoryChange);
    window.addEventListener("popstate", onHistoryChange);
    return () => {
      window.removeEventListener("hashchange", onHistoryChange);
      window.removeEventListener("popstate", onHistoryChange);
    };
  }, []);

  useEffect(() => {
    if (!user || canAccessView(activeView, user.roles)) return;
    setActiveView("overview");
    window.history.replaceState(null, "", "#overview");
  }, [activeView, user]);

  const navigate = useCallback((view: ViewId) => {
    if (view === "approvals") {
      const approvalRunIds = new Set(overview?.approvals.map((approval) => approval.runId) ?? []);
      setSelectedRunId((current) => {
        if (current && approvalRunIds.has(current)) return current;
        return overview?.approvals[0]?.runId
          ?? overview?.runs.find((run) => run.status === "waiting_approval")?.id
          ?? null;
      });
    }
    setRunDetailOpen(false);
    setActiveView(view);
    window.history.pushState(null, "", formatAppRoute(view));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [overview?.approvals, overview?.runs]);

  const openRun = useCallback((runId: string, view: "runs" | "approvals" = "runs") => {
    setSelectedRunId(runId);
    setRunDetailOpen(true);
    setActiveView(view);
    window.history.pushState(null, "", formatAppRoute(view, runId));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const selectedRun = useMemo(
    () => overview?.runs.find((run) => run.id === selectedRunId) ?? null,
    [overview?.runs, selectedRunId],
  );

  useEffect(() => {
    if (!overview || !runDetailOpen) return;
    const route = routeFromLocation();
    if (!route.runId || overview.runs.some((run) => run.id === route.runId)) return;
    setRunDetailOpen(false);
    window.history.replaceState(null, "", formatAppRoute(route.view));
  }, [overview, runDetailOpen]);

  const activeApproval = overview?.approvals.find((approval) => approval.runId === selectedRunId) ?? null;
  const notifications = useMemo(() => overview ? buildNotifications(overview) : [], [overview]);
  const health = useMemo(() => {
    if (!overview || overview.counts.nodes === 0) return { label: "без узлов", status: "waiting" as const };
    if (overview.counts.onlineNodes === 0) return { label: "офлайн", status: "offline" as const };
    if (overview.counts.onlineNodes < overview.counts.nodes) return { label: "частично доступен", status: "waiting" as const };
    return { label: "в норме", status: "online" as const };
  }, [overview]);

  function openRunDialog(agentIds: string[] | null = null) {
    setRunInitialAgentIds(agentIds);
    setRunFormError(null);
    setRunDialogOpen(true);
  }

  function openAgentDialog(agent: Agent | null = null) {
    setEditingAgent(agent);
    setAgentFormError(null);
    setAgentDialogOpen(true);
  }

  function openProcessDialog() {
    setProcessError(null);
    setProcessDialogOpen(true);
  }

  function openStartProcessDialog(process: ProcessDefinition) {
    setProcessError(null);
    setStartingProcess(process);
  }

  async function createRun(payload: CreateRunRequest) {
    setBusy(true);
    setRunFormError(null);
    try {
      const created = await api.createRun(payload);
      setRunDialogOpen(false);
      await refresh();
      openRun(created.id);
    } catch (requestError) {
      setRunFormError(requestError instanceof Error ? requestError.message : "Не удалось создать запуск");
    } finally {
      setBusy(false);
    }
  }

  async function replayRun(runId: string, payload: ReplayRunRequest) {
    setBusy(true);
    try {
      const replay = await api.replayRun(runId, payload);
      await refresh();
      const firstRun = replay.runs[0];
      if (firstRun) openRun(firstRun.id);
    } finally {
      setBusy(false);
    }
  }

  async function saveAgent(payload: CreateAgentRequest) {
    setBusy(true);
    setAgentFormError(null);
    try {
      if (editingAgent) await api.updateAgent(editingAgent.id, payload);
      else await api.createAgent(payload);
      setAgentDialogOpen(false);
      setEditingAgent(null);
      await refresh();
      navigate("agents");
    } catch (requestError) {
      setAgentFormError(requestError instanceof Error ? requestError.message : "Не удалось сохранить агента");
    } finally {
      setBusy(false);
    }
  }

  async function saveCredential(id: string | null, payload: CreateCredentialRequest) {
    setBusy(true);
    setCredentialsError(null);
    try {
      if (id) await api.updateCredential(id, payload);
      else await api.createCredential(payload);
      await refresh();
    } catch (requestError) {
      setCredentialsError(requestError instanceof Error ? requestError.message : "Не удалось сохранить credentials");
    } finally {
      setBusy(false);
    }
  }

  async function deleteCredential(id: string) {
    if (!window.confirm("Удалить credentials? Восстановить секрет будет невозможно.")) return;
    setBusy(true);
    setCredentialsError(null);
    try {
      await api.deleteCredential(id);
      await refresh();
    } catch (requestError) {
      setCredentialsError(requestError instanceof Error ? requestError.message : "Не удалось удалить credentials");
    } finally {
      setBusy(false);
    }
  }

  async function createProcess(payload: CreateProcessRequest) {
    setBusy(true);
    setProcessError(null);
    try {
      await api.createProcess(payload);
      setProcessDialogOpen(false);
      await refresh();
      navigate("processes");
    } catch (requestError) {
      setProcessError(requestError instanceof Error ? requestError.message : "Не удалось создать процесс");
    } finally {
      setBusy(false);
    }
  }

  async function saveProcess(processId: string, payload: UpdateProcessRequest): Promise<ProcessDefinition> {
    setBusy(true);
    setProcessError(null);
    try {
      const updated = await api.updateProcess(processId, payload);
      await refresh();
      return updated;
    } catch (requestError) {
      setProcessError(requestError instanceof Error ? requestError.message : "Не удалось сохранить процесс");
      throw requestError;
    } finally {
      setBusy(false);
    }
  }

  async function publishProcess(
    processId: string,
    payload: UpdateProcessRequest,
    saveDraft: boolean,
  ): Promise<ProcessDefinition> {
    setBusy(true);
    setProcessError(null);
    try {
      if (saveDraft) await api.updateProcess(processId, payload);
      const published = await api.publishProcess(processId);
      await refresh();
      return published;
    } catch (requestError) {
      setProcessError(requestError instanceof Error ? requestError.message : "Не удалось опубликовать процесс");
      throw requestError;
    } finally {
      setBusy(false);
    }
  }

  async function startProcess(payload: StartProcessRequest) {
    if (!startingProcess) return;
    setBusy(true);
    setProcessError(null);
    try {
      const instance = await api.startProcess(startingProcess.id, payload);
      setStartingProcess(null);
      await refresh();
      setSelectedRunId(instance.runId);
    } catch (requestError) {
      setProcessError(requestError instanceof Error ? requestError.message : "Не удалось запустить процесс");
    } finally {
      setBusy(false);
    }
  }

  async function cancelProcessInstance(instanceId: string) {
    setBusy(true);
    setProcessError(null);
    try {
      await api.cancelProcessInstance(instanceId);
      await refresh();
    } catch (requestError) {
      setProcessError(requestError instanceof Error ? requestError.message : "Не удалось остановить процесс");
    } finally {
      setBusy(false);
    }
  }

  async function cancelRun(runId: string) {
    setBusy(true);
    try {
      await api.cancelRun(runId);
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  async function replayProcessInstance(instanceId: string, mode: "safe" | "live") {
    setBusy(true);
    setProcessError(null);
    try {
      const instance = await api.replayProcessInstance(instanceId, mode);
      await refresh();
      setSelectedRunId(instance.runId);
    } catch (requestError) {
      setProcessError(requestError instanceof Error ? requestError.message : "Не удалось повторить экземпляр процесса");
    } finally {
      setBusy(false);
    }
  }

  async function changeScheduler(mode: SchedulerMode) {
    setBusy(true);
    try {
      await api.setScheduler(mode);
      await refresh();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось изменить режим");
    } finally {
      setBusy(false);
    }
  }

  async function saveModelRouter(policy: ModelRouterPolicy) {
    setBusy(true);
    try {
      await api.setModelRouter(policy);
      await refresh();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось сохранить политику Model Router");
      throw requestError;
    } finally {
      setBusy(false);
    }
  }

  async function decideApproval(approval: Approval, decision: "approve" | "reject") {
    setBusy(true);
    try {
      if (approval.kind === "mcp_tool") await api.decideMcpToolCall(approval.callId, decision);
      else await api.decideApproval(approval.stageId, decision);
      await refresh();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось сохранить решение");
    } finally {
      setBusy(false);
    }
  }

  if (!overview) {
    return (
      <div className="boot-screen">
        <div className="boot-mark" />
        <strong>{error ? "Координатор недоступен" : "Запускаем АГАТ"}</strong>
        <p>{error ?? "Подключаем очередь и вычислительные узлы…"}</p>
        {error ? <button className="button button--secondary" type="button" onClick={() => void refresh()}>Повторить</button> : null}
      </div>
    );
  }

  const roles = user?.roles ?? [];
  const { canCreateRuns, canDesign } = getRoleCapabilities(roles);
  const showNodeRail = activeView === "overview";
  const primaryAction: PrimaryAction = activeView === "agents" && canDesign
    ? "agent"
    : activeView === "processes" && canDesign
      ? "process"
      : activeView === "tools" && canDesign
        ? "mcp"
        : activeView === "a2a" && canDesign
          ? "a2a"
          : (activeView === "overview" || activeView === "runs") && canCreateRuns
            ? "run"
            : null;

  function triggerPrimaryAction() {
    if (primaryAction === "agent") openAgentDialog();
    else if (primaryAction === "process") openProcessDialog();
    else if (primaryAction === "mcp") setMcpCreateRequest((value) => value + 1);
    else if (primaryAction === "a2a") setA2ACreateRequest((value) => value + 1);
    else if (primaryAction === "run") openRunDialog();
  }

  function openNotification(notification: AppNotification) {
    if (notification.runId && (notification.targetView === "runs" || notification.targetView === "approvals")) {
      openRun(notification.runId, notification.targetView);
      return;
    }
    navigate(notification.targetView);
  }

  function focusMainContent() {
    const mainContent = document.getElementById("main-content");
    mainContent?.focus({ preventScroll: true });
    mainContent?.scrollIntoView();
  }

  return (
    <div className={`app-shell${activeView === "processes" ? " app-shell--processes" : ""}`}>
      <a
        className="skip-link"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault();
          focusMainContent();
        }}
      >
        Перейти к основному содержимому
      </a>
      <Sidebar activeView={activeView} health={health} roles={roles} onNavigate={navigate} />
      <div className="workspace">
        <Topbar
          notifications={notifications}
          primaryAction={primaryAction}
          onPrimary={triggerPrimaryAction}
          onNotification={openNotification}
          onApprovals={() => navigate("approvals")}
          user={user}
          projects={projects}
          activeProjectId={currentProjectId}
          onProjectChange={(projectId) => void changeProject(projectId)}
          onCreateProject={() => { setProjectError(null); setProjectDialogOpen(true); }}
          onLogout={() => void logout()}
        />
        <Breadcrumbs activeView={activeView} onNavigate={navigate} />
        <div className={`workspace__body${showNodeRail ? "" : " workspace__body--full"}`} id="main-content" tabIndex={-1}>
          {activeView === "overview" ? (
            <OverviewPage
              overview={overview}
              onCreateRun={() => openRunDialog()}
              onNavigate={navigate}
              onOpenRun={(runId) => openRun(runId)}
            />
          ) : null}
          {activeView === "runs" || activeView === "approvals" ? (
            <RunsPage
              mode={activeView === "approvals" ? "approvals" : "runs"}
              roles={roles}
              overview={overview}
              selectedRun={selectedRun}
              selectedRunId={selectedRunId}
              approval={activeApproval}
              busy={busy}
              detailOpen={runDetailOpen}
              onCreate={() => openRunDialog()}
              onSelect={(runId) => openRun(runId, activeView === "approvals" ? "approvals" : "runs")}
              onBack={() => navigate(activeView)}
              onCancel={(runId) => cancelRun(runId)}
              onSchedulerChange={(mode) => void changeScheduler(mode)}
              onApproval={(approval, decision) => void decideApproval(approval, decision)}
              onReplay={(runId, payload) => replayRun(runId, payload)}
            />
          ) : null}
          {activeView === "agents" ? (
            <AgentsPage
              agents={overview.agents}
              nodes={overview.nodes}
              onCreate={() => openAgentDialog()}
              onEdit={(agent) => openAgentDialog(agent)}
              onRun={(agentId) => openRunDialog([agentId])}
            />
          ) : null}
          {activeView === "processes" ? (
            <Suspense fallback={<div className="process-page-loading"><span className="boot-mark" /><strong>Загружаем редактор процессов</strong></div>}>
              <ProcessesPage
                processes={overview.processes}
                instances={overview.processInstances}
                agents={overview.agents}
                credentials={overview.credentials}
                busy={busy}
                error={processError}
                onCreate={openProcessDialog}
                onSave={saveProcess}
                onPublish={publishProcess}
                onStart={openStartProcessDialog}
                onCancel={(instanceId) => void cancelProcessInstance(instanceId)}
                onReplay={(instanceId, mode) => void replayProcessInstance(instanceId, mode)}
                onOpenRun={(runId) => { setSelectedRunId(runId); navigate("runs"); }}
                onManageCredentials={() => { setCredentialsError(null); setCredentialsDialogOpen(true); }}
                onChanged={refresh}
              />
            </Suspense>
          ) : null}
          {activeView === "tools" ? (
            <Suspense fallback={<div className="process-page-loading"><span className="boot-mark" /><strong>Загружаем MCP gateway</strong></div>}>
              <McpPage
                mcp={overview.mcp}
                credentials={overview.credentials}
                roles={user?.roles ?? []}
                createRequest={mcpCreateRequest}
                onChanged={refresh}
                onManageCredentials={() => { setCredentialsError(null); setCredentialsDialogOpen(true); }}
              />
            </Suspense>
          ) : null}
          {activeView === "knowledge" ? (
            <Suspense fallback={<div className="process-page-loading"><span className="boot-mark" /><strong>Загружаем локальные знания</strong></div>}>
              <KnowledgePage
                overview={overview.knowledge}
                projectId={currentProjectId}
                agents={overview.agents}
                nodes={overview.nodes}
                roles={user?.roles ?? []}
                onChanged={refresh}
              />
            </Suspense>
          ) : null}
          {activeView === "evals" ? (
            <Suspense fallback={<div className="process-page-loading"><span className="boot-mark" /><strong>Загружаем оценку качества</strong></div>}>
              <EvalsPage
                projectId={currentProjectId}
                agents={overview.agents}
                models={overview.models}
                collections={overview.knowledge.collections}
                roles={user?.roles ?? []}
                onChanged={refresh}
              />
            </Suspense>
          ) : null}
          {activeView === "a2a" ? (
            <Suspense fallback={<div className="process-page-loading"><span className="boot-mark" /><strong>Загружаем A2A adapter</strong></div>}>
              <A2APage
                projectId={currentProjectId}
                agents={overview.agents}
                collections={overview.knowledge.collections}
                roles={user?.roles ?? []}
                createRequest={a2aCreateRequest}
                onChanged={refresh}
                onOpenRun={(runId) => { setSelectedRunId(runId); navigate("runs"); }}
              />
            </Suspense>
          ) : null}
          {activeView === "nodes" ? (
            <NodesPage
              nodes={overview.nodes}
              models={overview.models}
              roles={user?.roles ?? []}
              onChanged={refresh}
            />
          ) : null}
          {activeView === "models" ? (
            <Suspense fallback={<div className="process-page-loading"><span className="boot-mark" /><strong>Загружаем Model Router</strong></div>}>
              <ModelsPage
                models={overview.models}
                nodes={overview.nodes}
                agents={overview.agents}
                modelRouter={overview.modelRouter}
                busy={busy}
                onSavePolicy={saveModelRouter}
              />
            </Suspense>
          ) : null}
          {activeView === "fleet" ? (
            <Suspense fallback={<div className="process-page-loading"><span className="boot-mark" /><strong>Загружаем Fleet HA-cell</strong></div>}>
              <FleetPage
                projectId={currentProjectId}
                nodes={overview.nodes}
                roles={user?.roles ?? []}
              />
            </Suspense>
          ) : null}
          {showNodeRail ? <NodeRail nodes={overview.nodes} onManage={() => navigate("nodes")} /> : null}
        </div>
      </div>
      <BottomNav activeView={activeView} roles={roles} onNavigate={navigate} />
      <NewRunDialog
        open={runDialogOpen}
        agents={overview.agents}
        collections={overview.knowledge.collections}
        initialAgentIds={runInitialAgentIds}
        busy={busy}
        error={runFormError}
        onClose={() => { setRunDialogOpen(false); setRunFormError(null); }}
        onSubmit={(payload) => void createRun(payload)}
      />
      <AdminTokenDialog
        open={adminTokenDialogOpen}
        onCancel={() => provideAdminToken(null)}
        onSubmit={provideAdminToken}
      />
      <AgentDialog
        open={agentDialogOpen}
        agent={editingAgent}
        agents={overview.agents}
        models={overview.models}
        busy={busy}
        error={agentFormError}
        onClose={() => { setAgentDialogOpen(false); setEditingAgent(null); setAgentFormError(null); }}
        onSubmit={(payload) => void saveAgent(payload)}
      />
      <CredentialsDialog
        open={credentialsDialogOpen}
        credentials={overview.credentials}
        busy={busy}
        error={credentialsError}
        onClose={() => { setCredentialsDialogOpen(false); setCredentialsError(null); }}
        onSave={(id, payload) => void saveCredential(id, payload)}
        onDelete={(credential) => void deleteCredential(credential.id)}
      />
      <ProjectDialog
        open={projectDialogOpen}
        busy={busy}
        error={projectError}
        onClose={() => { setProjectDialogOpen(false); setProjectError(null); }}
        onSubmit={(name, id) => void createProject(name, id)}
      />
      <NewProcessDialog
        open={processDialogOpen}
        processes={overview.processes}
        busy={busy}
        error={processError}
        onClose={() => { setProcessDialogOpen(false); setProcessError(null); }}
        onSubmit={(payload) => void createProcess(payload)}
      />
      <StartProcessDialog
        open={startingProcess !== null}
        process={startingProcess}
        collections={overview.knowledge.collections}
        busy={busy}
        error={processError}
        onClose={() => { setStartingProcess(null); setProcessError(null); }}
        onSubmit={(payload) => void startProcess(payload)}
      />
      {error ? <button className="connection-toast" type="button" onClick={() => { setError(null); void refresh(); }}>{error}</button> : null}
    </div>
  );
}
