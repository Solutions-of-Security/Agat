import type { Agent, ProcessGraph } from "./types";
import { formDefinitionIssues } from "./processForms";

export interface ProcessIssue {
  id: string;
  message: string;
  nodeId?: string;
}

/** Fast, local guidance. The coordinator remains authoritative at publication. */
export function processReadiness(graph: ProcessGraph, agents: Pick<Agent, "id">[]): ProcessIssue[] {
  const issues: ProcessIssue[] = [];
  const nodeIds = new Set(graph.nodes.map((node) => node.id));
  const agentIds = new Set(agents.map((agent) => agent.id));
  const starts = graph.nodes.filter((node) => node.type === "start");
  if (starts.length !== 1) issues.push({ id: "start", message: "Добавьте одну точку входа в процесс." });
  if (!graph.nodes.some((node) => node.type === "end")) issues.push({ id: "end", message: "Добавьте шаг завершения." });
  const outgoing = new Map<string, typeof graph.edges>();
  for (const edge of graph.edges) {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) {
      issues.push({ id: edge.id, message: "Удалите связь с отсутствующим шагом." });
      continue;
    }
    const edges = outgoing.get(edge.source) ?? [];
    edges.push(edge);
    outgoing.set(edge.source, edges);
  }
  const reached = new Set<string>();
  const queue = starts.map((node) => node.id);
  while (queue.length) {
    const id = queue.shift()!;
    if (reached.has(id)) continue;
    reached.add(id);
    for (const edge of outgoing.get(id) ?? []) queue.push(edge.target);
  }
  for (const node of graph.nodes) {
    const add = (reason: string, message: string) => issues.push({ id: `${node.id}:${reason}`, nodeId: node.id, message: `«${node.name || "Без названия"}»: ${message}` });
    if (!node.name.trim()) add("name", "задайте название шага.");
    if (starts.length === 1 && !reached.has(node.id)) add("unreachable", "соедините шаг с основным потоком.");
    if (node.type !== "end" && !outgoing.get(node.id)?.length) add("next", "подключите следующий шаг.");
    if (node.type === "agent" && !agentIds.has(node.config.agentId ?? "")) add("agent", "выберите существующего агента.");
    if (node.type === "http" && !node.config.url?.trim()) add("url", "укажите URL запроса.");
    if (node.type === "transform" && !node.config.template?.trim()) add("template", "задайте шаблон преобразования.");
    if (node.type === "signal" && !node.config.signalName?.trim()) add("signal", "укажите имя ожидаемого события.");
    if (node.type === "subprocess" && !node.config.subprocessProcessId) add("subprocess", "выберите вложенный процесс.");
    if (node.type === "parallel_join" && !node.config.forkId) add("fork", "выберите начало параллельных веток.");
    if (node.config.approvalForm) {
      formDefinitionIssues(node.config.approvalForm).forEach((issue, index) => add(`form-${index}`, issue));
      if (node.type === "agent" && !node.config.approvalRequired) add("form-approval", "включите подтверждение оператора для формы.");
    }
    if (node.type === "approval" && node.config.approvalMode === "input" && !node.config.approvalForm) add("form", "добавьте форму для ввода данных.");
    if (node.type === "loop" || node.type === "condition") {
      const condition = node.config.condition;
      if (!condition || (condition.operator !== "always" && !condition.value.trim())) add("condition", "задайте условие.");
      if (condition?.source === "json" && !condition.path?.trim()) add("path", "укажите путь к полю JSON.");
    }
    if (node.type === "loop") {
      const edges = outgoing.get(node.id) ?? [];
      if (["repeat", "exit"].some((branch) => edges.filter((edge) => edge.branch === branch).length !== 1)) add("branches", "подключите ветки «повтор» и «выход».");
      const limit = node.config.maxIterations ?? 3;
      if (!Number.isInteger(limit) || limit < 1 || limit > 50) add("limit", "задайте от 1 до 50 повторов.");
      const repeat = edges.find((edge) => edge.branch === "repeat");
      if (repeat) {
        const seen = new Set<string>();
        const pending = [repeat.target];
        while (pending.length) {
          const id = pending.pop()!;
          if (seen.has(id)) continue;
          seen.add(id);
          pending.push(...(outgoing.get(id) ?? []).map((edge) => edge.target));
        }
        if (!seen.has(node.id)) add("return", "соедините конец повторяемого фрагмента с этим циклом.");
      }
    }
    if (node.type === "condition") {
      const branches = new Set((outgoing.get(node.id) ?? []).map((edge) => edge.branch));
      if (!branches.has("true") || !branches.has("false")) add("branches", "подключите обе ветки: «да» и «нет».");
    }
  }
  return issues;
}
