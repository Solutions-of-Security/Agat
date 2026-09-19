import type {
  ProcessBranch,
  ProcessCondition,
  ProcessConditionOperator,
  ProcessGraph,
  ProcessGraphEdge,
  ProcessGraphNode,
  ProcessNodeType,
} from "./types.js";

const NODE_TYPES = new Set<ProcessNodeType>([
  "start",
  "agent",
  "http",
  "transform",
  "wait",
  "approval",
  "artifact",
  "condition",
  "loop",
  "parallel_fork",
  "parallel_join",
  "signal",
  "subprocess",
  "end",
]);
const BRANCHES = new Set<ProcessBranch>(["default", "true", "false", "repeat", "exit"]);
const OPERATORS = new Set<ProcessConditionOperator>([
  "always",
  "contains",
  "not_contains",
  "equals",
  "not_equals",
]);

function requiredText(value: unknown, field: string, maxLength: number): string {
  if (typeof value !== "string") throw new Error(`${field} должен быть строкой`);
  const normalized = value.trim();
  if (!normalized) throw new Error(`${field} обязателен`);
  if (normalized.length > maxLength) throw new Error(`${field}: максимум ${maxLength} символов`);
  return normalized;
}

function optionalText(value: unknown, field: string, maxLength: number): string {
  if (value === undefined || value === null) return "";
  if (typeof value !== "string") throw new Error(`${field} должен быть строкой`);
  const normalized = value.trim();
  if (normalized.length > maxLength) throw new Error(`${field}: максимум ${maxLength} символов`);
  return normalized;
}

function finitePosition(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`${field} должен быть числом`);
  if (value < -100_000 || value > 100_000) throw new Error(`${field} находится вне допустимого диапазона`);
  return Math.round(value * 100) / 100;
}

function boundedInteger(value: unknown, field: string, fallback: number, min: number, max: number): number {
  const normalized = value ?? fallback;
  if (typeof normalized !== "number" || !Number.isInteger(normalized) || normalized < min || normalized > max) {
    throw new Error(`${field} должен быть целым числом от ${min} до ${max}`);
  }
  return normalized;
}

function normalizeHeaders(value: unknown, field: string): Record<string, string> {
  if (value === undefined || value === null) return {};
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${field} должны быть объектом`);
  const entries = Object.entries(value);
  if (entries.length > 20) throw new Error(`${field}: максимум 20 заголовков`);
  const headers: Record<string, string> = {};
  for (const [name, rawValue] of entries) {
    if (!/^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,80}$/.test(name)) throw new Error(`${field}: некорректное имя ${name}`);
    if (typeof rawValue !== "string" || rawValue.length > 8_000 || /[\r\n]/.test(rawValue)) {
      throw new Error(`${field}: некорректное значение ${name}`);
    }
    headers[name] = rawValue;
  }
  return headers;
}

function normalizeHttpMethod(value: unknown, field: string): ProcessGraphNode["config"]["method"] {
  const method = value ?? "GET";
  if (typeof method !== "string" || !["GET", "POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    throw new Error(`${field}: неизвестный HTTP-метод`);
  }
  return method as ProcessGraphNode["config"]["method"];
}

function normalizeHttpUrl(value: unknown, field: string, strict: boolean): string {
  const url = optionalText(value, field, 4_000);
  if (strict && !url) throw new Error(`${field} обязателен`);
  if (url && !/^https?:\/\//i.test(url) && !url.startsWith("{{")) {
    throw new Error(`${field}: разрешены только http:// и https:// URL`);
  }
  return url;
}

function normalizeCondition(raw: unknown, field: string, strict: boolean): ProcessCondition {
  const condition = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
  const operator = condition.operator ?? "contains";
  if (typeof operator !== "string" || !OPERATORS.has(operator as ProcessConditionOperator)) {
    throw new Error(`${field}: неизвестный оператор`);
  }
  const value = operator === "always" ? "" : optionalText(condition.value, `${field}: значение`, 2_000);
  if (strict && operator !== "always" && !value) throw new Error(`${field}: значение обязательно`);
  return {
    source: "last_output",
    operator: operator as ProcessConditionOperator,
    value,
    caseSensitive: condition.caseSensitive === true,
  };
}

function normalizeNode(raw: unknown, knownAgentIds: Set<string>, strict: boolean): ProcessGraphNode {
  if (!raw || typeof raw !== "object") throw new Error("Каждый шаг должен быть объектом");
  const node = raw as Record<string, unknown>;
  const id = requiredText(node.id, "ID шага", 100);
  const type = node.type;
  if (typeof type !== "string" || !NODE_TYPES.has(type as ProcessNodeType)) {
    throw new Error(`Шаг ${id}: неизвестный тип`);
  }
  const position = node.position && typeof node.position === "object"
    ? node.position as Record<string, unknown>
    : {};
  const config = node.config && typeof node.config === "object"
    ? node.config as Record<string, unknown>
    : {};
  const normalized: ProcessGraphNode = {
    id,
    type: type as ProcessNodeType,
    name: strict
      ? requiredText(node.name, `Шаг ${id}: название`, 80)
      : optionalText(node.name, `Шаг ${id}: название`, 80),
    position: {
      x: finitePosition(position.x, `Шаг ${id}: x`),
      y: finitePosition(position.y, `Шаг ${id}: y`),
    },
    config: {},
  };

  if (normalized.type === "agent") {
    const agentId = strict
      ? requiredText(config.agentId, `Шаг ${normalized.name || id}: агент`, 100)
      : optionalText(config.agentId, `Шаг ${normalized.name || id}: агент`, 100);
    if (agentId && !knownAgentIds.has(agentId)) throw new Error(`Шаг ${normalized.name || id}: агент не найден`);
    if (agentId) normalized.config.agentId = agentId;
    normalized.config.approvalRequired = config.approvalRequired === true;
  }
  if (normalized.type === "condition" || normalized.type === "loop") {
    normalized.config.condition = normalizeCondition(config.condition, `Шаг ${normalized.name || id}: условие`, strict);
  }
  if (normalized.type === "loop") {
    normalized.config.maxIterations = boundedInteger(
      config.maxIterations,
      `Шаг ${normalized.name}: максимум итераций`,
      3,
      1,
      50,
    );
  }
  if (normalized.type === "start") {
    const inputTemplate = optionalText(config.inputTemplate, `Шаг ${normalized.name || id}: шаблон входных данных`, 100_000);
    if (inputTemplate) normalized.config.inputTemplate = inputTemplate;
  }
  if (normalized.type === "transform") {
    const template = optionalText(config.template, `Шаг ${normalized.name || id}: шаблон`, 100_000);
    if (strict && !template) throw new Error(`Шаг ${normalized.name || id}: шаблон обязателен`);
    normalized.config.template = template;
  }
  if (normalized.type === "http") {
    const field = `Шаг ${normalized.name || id}`;
    normalized.config.url = normalizeHttpUrl(config.url, `${field}: URL`, strict);
    normalized.config.method = normalizeHttpMethod(config.method, field);
    normalized.config.headers = normalizeHeaders(config.headers, `Шаг ${normalized.name || id}: заголовки`);
    normalized.config.body = optionalText(config.body, `Шаг ${normalized.name || id}: тело запроса`, 100_000);
    const credentialId = optionalText(config.credentialId, `Шаг ${normalized.name || id}: credentials`, 100);
    if (credentialId) normalized.config.credentialId = credentialId;
    normalized.config.timeoutSeconds = boundedInteger(
      config.timeoutSeconds,
      `Шаг ${normalized.name || id}: timeout`,
      30,
      1,
      120,
    );
    const idempotencyHeader = optionalText(config.idempotencyHeader, `${field}: idempotency header`, 80)
      || "Idempotency-Key";
    if (!/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(idempotencyHeader)) {
      throw new Error(`${field}: некорректный idempotency header`);
    }
    normalized.config.idempotencyHeader = idempotencyHeader;
    if (config.compensation !== undefined && config.compensation !== null) {
      if (typeof config.compensation !== "object" || Array.isArray(config.compensation)) {
        throw new Error(`${field}: compensation должна быть объектом`);
      }
      const compensation = config.compensation as Record<string, unknown>;
      const compensationCredentialId = optionalText(
        compensation.credentialId,
        `${field}: compensation credentials`,
        100,
      );
      normalized.config.compensation = {
        url: normalizeHttpUrl(compensation.url, `${field}: compensation URL`, strict),
        method: normalizeHttpMethod(compensation.method ?? "POST", `${field}: compensation` )!,
        headers: normalizeHeaders(compensation.headers, `${field}: compensation headers`),
        body: optionalText(compensation.body, `${field}: compensation body`, 100_000),
        ...(compensationCredentialId ? { credentialId: compensationCredentialId } : {}),
        timeoutSeconds: boundedInteger(
          compensation.timeoutSeconds,
          `${field}: compensation timeout`,
          30,
          1,
          120,
        ),
      };
    }
  }
  if (normalized.type === "wait") {
    normalized.config.waitSeconds = boundedInteger(
      config.waitSeconds,
      `Шаг ${normalized.name || id}: время ожидания`,
      60,
      1,
      604_800,
    );
  }
  if (normalized.type === "approval") {
    normalized.config.approvalMessage = optionalText(
      config.approvalMessage,
      `Шаг ${normalized.name || id}: пояснение`,
      2_000,
    );
  }
  if (normalized.type === "artifact") {
    const artifactName = optionalText(config.artifactName, `Шаг ${normalized.name || id}: имя файла`, 160);
    const artifactContent = optionalText(config.artifactContent, `Шаг ${normalized.name || id}: содержимое`, 100_000);
    if (strict && !artifactName) throw new Error(`Шаг ${normalized.name || id}: имя файла обязательно`);
    if (strict && !artifactContent) throw new Error(`Шаг ${normalized.name || id}: содержимое обязательно`);
    normalized.config.artifactName = artifactName;
    normalized.config.artifactMediaType = optionalText(
      config.artifactMediaType,
      `Шаг ${normalized.name || id}: media type`,
      120,
    ) || "text/plain; charset=utf-8";
    normalized.config.artifactContent = artifactContent;
  }
  if (normalized.type === "parallel_join") {
    const forkId = strict
      ? requiredText(config.forkId, `Шаг ${normalized.name || id}: fork gateway`, 100)
      : optionalText(config.forkId, `Шаг ${normalized.name || id}: fork gateway`, 100);
    if (forkId) normalized.config.forkId = forkId;
  }
  if (normalized.type === "signal") {
    const signalName = strict
      ? requiredText(config.signalName, `Шаг ${normalized.name || id}: signal name`, 128)
      : optionalText(config.signalName, `Шаг ${normalized.name || id}: signal name`, 128);
    if (signalName && !/^[A-Za-z][A-Za-z0-9_.-]{0,127}$/.test(signalName)) {
      throw new Error(`Шаг ${normalized.name || id}: signal name содержит недопустимые символы`);
    }
    if (signalName) normalized.config.signalName = signalName;
    normalized.config.signalCorrelationKey = optionalText(
      config.signalCorrelationKey,
      `Шаг ${normalized.name || id}: correlation key`,
      1_000,
    );
    normalized.config.signalTimeoutSeconds = boundedInteger(
      config.signalTimeoutSeconds,
      `Шаг ${normalized.name || id}: signal timeout`,
      0,
      0,
      31_536_000,
    );
  }
  if (normalized.type === "subprocess") {
    const subprocessProcessId = strict
      ? requiredText(config.subprocessProcessId, `Шаг ${normalized.name || id}: subprocess`, 128)
      : optionalText(config.subprocessProcessId, `Шаг ${normalized.name || id}: subprocess`, 128);
    if (subprocessProcessId && !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(subprocessProcessId)) {
      throw new Error(`Шаг ${normalized.name || id}: некорректный ID subprocess`);
    }
    if (subprocessProcessId) normalized.config.subprocessProcessId = subprocessProcessId;
    if (config.subprocessVersion !== undefined && config.subprocessVersion !== null) {
      normalized.config.subprocessVersion = boundedInteger(
        config.subprocessVersion,
        `Шаг ${normalized.name || id}: версия subprocess`,
        1,
        1,
        1_000_000,
      );
    }
    normalized.config.subprocessInputTemplate = optionalText(
      config.subprocessInputTemplate,
      `Шаг ${normalized.name || id}: вход subprocess`,
      100_000,
    ) || "{{ lastOutput }}";
  }
  return normalized;
}

function normalizeEdge(raw: unknown, nodeIds: Set<string>): ProcessGraphEdge {
  if (!raw || typeof raw !== "object") throw new Error("Каждая связь должна быть объектом");
  const edge = raw as Record<string, unknown>;
  const id = requiredText(edge.id, "ID связи", 100);
  const source = requiredText(edge.source, `Связь ${id}: источник`, 100);
  const target = requiredText(edge.target, `Связь ${id}: назначение`, 100);
  if (!nodeIds.has(source) || !nodeIds.has(target)) throw new Error(`Связь ${id} ссылается на неизвестный шаг`);
  if (source === target) throw new Error(`Связь ${id} не может замыкать шаг на самого себя`);
  const branch = edge.branch ?? "default";
  if (typeof branch !== "string" || !BRANCHES.has(branch as ProcessBranch)) {
    throw new Error(`Связь ${id}: неизвестная ветка`);
  }
  return { id, source, target, branch: branch as ProcessBranch };
}

function assertBranchSet(node: ProcessGraphNode, outgoing: ProcessGraphEdge[]): void {
  if (node.type === "parallel_fork") {
    if (outgoing.length < 2 || outgoing.length > 16 || outgoing.some((edge) => edge.branch !== "default")) {
      throw new Error(`Шаг ${node.name}: fork должен иметь от 2 до 16 веток default`);
    }
    if (new Set(outgoing.map((edge) => edge.target)).size !== outgoing.length) {
      throw new Error(`Шаг ${node.name}: fork-ветки должны вести к разным шагам`);
    }
    return;
  }
  const actual = outgoing.map((edge) => edge.branch);
  const expected: ProcessBranch[] = node.type === "condition"
    ? ["true", "false"]
    : node.type === "loop"
      ? ["repeat", "exit"]
      : node.type === "end"
        ? []
        : ["default"];
  if (actual.length !== expected.length || expected.some((branch) => actual.filter((value) => value === branch).length !== 1)) {
    const labels = expected.length ? expected.join(" и ") : "без исходящих связей";
    throw new Error(`Шаг ${node.name}: ожидаются ветки ${labels}`);
  }
  if (actual.some((branch) => !expected.includes(branch))) {
    throw new Error(`Шаг ${node.name}: найдена недопустимая ветка`);
  }
}

function canReachNode(
  initialNodeId: string,
  targetNodeId: string,
  outgoingByNode: Map<string, ProcessGraphEdge[]>,
  nodesById: Map<string, ProcessGraphNode>,
): boolean {
  const seen = new Set<string>();
  const queue = [initialNodeId];
  while (queue.length > 0) {
    const nodeId = queue.shift()!;
    if (nodeId === targetNodeId) return true;
    if (seen.has(nodeId)) continue;
    seen.add(nodeId);
    const node = nodesById.get(nodeId);
    if (!node || node.type === "end") continue;
    queue.push(...(outgoingByNode.get(nodeId) ?? []).map((edge) => edge.target));
  }
  return false;
}

function canReachEndBeforeNode(
  initialNodeId: string,
  targetNodeId: string,
  outgoingByNode: Map<string, ProcessGraphEdge[]>,
  nodesById: Map<string, ProcessGraphNode>,
): boolean {
  const seen = new Set<string>();
  const queue = [initialNodeId];
  while (queue.length > 0) {
    const nodeId = queue.shift()!;
    if (nodeId === targetNodeId || seen.has(nodeId)) continue;
    seen.add(nodeId);
    const node = nodesById.get(nodeId);
    if (!node) continue;
    if (node.type === "end") return true;
    queue.push(...(outgoingByNode.get(nodeId) ?? []).map((edge) => edge.target));
  }
  return false;
}

function reachableFrom(startId: string, outgoingByNode: Map<string, ProcessGraphEdge[]>): Set<string> {
  const seen = new Set<string>();
  const queue = [startId];
  while (queue.length > 0) {
    const id = queue.shift()!;
    if (seen.has(id)) continue;
    seen.add(id);
    for (const edge of outgoingByNode.get(id) ?? []) queue.push(edge.target);
  }
  return seen;
}

function canReachEnd(endIds: string[], edges: ProcessGraphEdge[]): Set<string> {
  const incoming = new Map<string, string[]>();
  for (const edge of edges) incoming.set(edge.target, [...(incoming.get(edge.target) ?? []), edge.source]);
  const seen = new Set<string>();
  const queue = [...endIds];
  while (queue.length > 0) {
    const id = queue.shift()!;
    if (seen.has(id)) continue;
    seen.add(id);
    queue.push(...(incoming.get(id) ?? []));
  }
  return seen;
}

export function normalizeProcessIdentity(
  name: unknown,
  description: unknown,
): { name: string; description: string } {
  return {
    name: requiredText(name, "Название процесса", 100),
    description: optionalText(description, "Описание процесса", 1_000),
  };
}

function normalizeGraphShape(raw: unknown, knownAgentIds: Set<string>, strict: boolean): ProcessGraph {
  if (!raw || typeof raw !== "object") throw new Error("Граф процесса обязателен");
  const graph = raw as Record<string, unknown>;
  if (!Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) {
    throw new Error("Граф процесса должен содержать массивы nodes и edges");
  }
  if (graph.nodes.length > 200 || (strict && graph.nodes.length < 2)) {
    throw new Error(strict ? "Процесс должен содержать от 2 до 200 шагов" : "В процессе может быть не более 200 шагов");
  }
  if (graph.edges.length > 400) throw new Error("В процессе может быть не более 400 связей");

  const nodes = graph.nodes.map((node) => normalizeNode(node, knownAgentIds, strict));
  const nodeIds = new Set(nodes.map((node) => node.id));
  if (nodeIds.size !== nodes.length) throw new Error("ID шагов должны быть уникальными");
  const edges = graph.edges.map((edge) => normalizeEdge(edge, nodeIds));
  if (new Set(edges.map((edge) => edge.id)).size !== edges.length) throw new Error("ID связей должны быть уникальными");
  if (graph.requiredTools !== undefined && (!Array.isArray(graph.requiredTools) || graph.requiredTools.length > 100
    || graph.requiredTools.some((tool) => typeof tool !== "string" || !/^[A-Za-z0-9_-]+__[A-Za-z0-9_.-]+$/.test(tool) || tool.length > 200))) {
    throw new Error("requiredTools должен содержать до 100 MCP public names namespace__tool");
  }
  const requirements: Pick<ProcessGraph, "requiredTools" | "requiredKnowledgeCollectionIds" | "mcpToolAllowlist" | "allowPartialStart"> =
    graph.requiredTools === undefined ? {} : { requiredTools: [...new Set(graph.requiredTools as string[])].sort() };
  if (graph.allowPartialStart !== undefined) {
    if (typeof graph.allowPartialStart !== "boolean") throw new Error("allowPartialStart должен быть boolean");
    requirements.allowPartialStart = graph.allowPartialStart;
  }
  for (const field of ["requiredKnowledgeCollectionIds", "mcpToolAllowlist"] as const) {
    const values = graph[field];
    if (values === undefined) continue;
    if (!Array.isArray(values) || values.length > 100 || values.some((value) => typeof value !== "string" || !value.trim() || value.length > 200
      || (field === "mcpToolAllowlist" && !/^[A-Za-z0-9_-]+__[A-Za-z0-9_.-]+$/.test(value)))) throw new Error(`Некорректный ${field}`);
    requirements[field] = [...new Set(values as string[])].sort();
  }
  if (requirements.mcpToolAllowlist && requirements.requiredTools?.some((tool) => !requirements.mcpToolAllowlist!.includes(tool))) {
    throw new Error("Обязательный инструмент запрещён mcpToolAllowlist процесса");
  }

  if (!strict) return { nodes, edges, ...requirements };

  const starts = nodes.filter((node) => node.type === "start");
  const ends = nodes.filter((node) => node.type === "end");
  if (starts.length !== 1) throw new Error("В процессе должен быть ровно один старт");
  if (ends.length === 0) throw new Error("В процессе должно быть хотя бы одно завершение");
  if (edges.some((edge) => edge.target === starts[0]!.id)) throw new Error("Старт не может иметь входящих связей");

  const outgoingByNode = new Map<string, ProcessGraphEdge[]>();
  for (const edge of edges) outgoingByNode.set(edge.source, [...(outgoingByNode.get(edge.source) ?? []), edge]);
  for (const node of nodes) assertBranchSet(node, outgoingByNode.get(node.id) ?? []);

  const incomingByNode = new Map<string, ProcessGraphEdge[]>();
  for (const edge of edges) incomingByNode.set(edge.target, [...(incomingByNode.get(edge.target) ?? []), edge]);
  const nodesById = new Map(nodes.map((node) => [node.id, node]));
  const forks = nodes.filter((node) => node.type === "parallel_fork");
  const joins = nodes.filter((node) => node.type === "parallel_join");
  for (const join of joins) {
    const fork = nodesById.get(join.config.forkId ?? "");
    if (!fork || fork.type !== "parallel_fork") {
      throw new Error(`Шаг ${join.name}: выбранный fork gateway не найден`);
    }
    const forkBranches = outgoingByNode.get(fork.id) ?? [];
    const arrivals = incomingByNode.get(join.id) ?? [];
    if (arrivals.length !== forkBranches.length) {
      throw new Error(`Шаг ${join.name}: join ожидает ${forkBranches.length} входящих веток от fork «${fork.name}»`);
    }
    for (const branch of forkBranches) {
      if (!canReachNode(branch.target, join.id, outgoingByNode, nodesById)) {
        throw new Error(`Fork «${fork.name}»: каждая ветка должна достигать join «${join.name}»`);
      }
      if (canReachEndBeforeNode(branch.target, join.id, outgoingByNode, nodesById)) {
        throw new Error(`Fork «${fork.name}»: ветка не может обойти join «${join.name}» и завершиться раньше`);
      }
    }
  }
  for (const fork of forks) {
    if (joins.filter((join) => join.config.forkId === fork.id).length !== 1) {
      throw new Error(`Fork «${fork.name}» должен иметь ровно один парный join`);
    }
  }

  const reachable = reachableFrom(starts[0]!.id, outgoingByNode);
  const unreachable = nodes.filter((node) => !reachable.has(node.id));
  if (unreachable.length > 0) {
    throw new Error(`Недостижимые шаги: ${unreachable.map((node) => node.name).join(", ")}`);
  }
  const endingPaths = canReachEnd(ends.map((node) => node.id), edges);
  const trapped = nodes.filter((node) => !endingPaths.has(node.id));
  if (trapped.length > 0) {
    throw new Error(`Нет пути к завершению из шагов: ${trapped.map((node) => node.name).join(", ")}`);
  }
  return { nodes, edges, ...requirements };
}

export function normalizeProcessDraftGraph(raw: unknown, knownAgentIds: Set<string>): ProcessGraph {
  return normalizeGraphShape(raw, knownAgentIds, false);
}

export function normalizeProcessGraph(raw: unknown, knownAgentIds: Set<string>): ProcessGraph {
  return normalizeGraphShape(raw, knownAgentIds, true);
}

export function defaultProcessGraph(): ProcessGraph {
  return {
    nodes: [
      { id: "start", type: "start", name: "Старт", position: { x: 24, y: 100 }, config: {} },
      {
        id: "collector-step",
        type: "agent",
        name: "Сборщик",
        position: { x: 124, y: 70 },
        config: { agentId: "collector", approvalRequired: false },
      },
      {
        id: "quality-check",
        type: "condition",
        name: "Нужна доработка?",
        position: { x: 310, y: 60 },
        config: {
          condition: { source: "last_output", operator: "contains", value: "доработать", caseSensitive: false },
        },
      },
      {
        id: "editor-step",
        type: "agent",
        name: "Редактор",
        position: { x: 492, y: 260 },
        config: { agentId: "editor", approvalRequired: false },
      },
      {
        id: "review-loop",
        type: "loop",
        name: "Цикл проверки",
        position: { x: 680, y: 250 },
        config: {
          condition: { source: "last_output", operator: "contains", value: "доработать", caseSensitive: false },
          maxIterations: 3,
        },
      },
      { id: "end", type: "end", name: "Завершение", position: { x: 850, y: 100 }, config: {} },
    ],
    edges: [
      { id: "start-collector", source: "start", target: "collector-step", branch: "default" },
      { id: "collector-condition", source: "collector-step", target: "quality-check", branch: "default" },
      { id: "condition-editor", source: "quality-check", target: "editor-step", branch: "true" },
      { id: "condition-end", source: "quality-check", target: "end", branch: "false" },
      { id: "editor-loop", source: "editor-step", target: "review-loop", branch: "default" },
      { id: "loop-editor", source: "review-loop", target: "editor-step", branch: "repeat" },
      { id: "loop-end", source: "review-loop", target: "end", branch: "exit" },
    ],
  };
}

export function evaluateProcessCondition(condition: ProcessCondition, lastOutput: string | null): boolean {
  if (condition.operator === "always") return true;
  const source = lastOutput ?? "";
  const left = condition.caseSensitive ? source : source.toLocaleLowerCase("ru");
  const right = condition.caseSensitive ? condition.value : condition.value.toLocaleLowerCase("ru");
  if (condition.operator === "contains") return left.includes(right);
  if (condition.operator === "not_contains") return !left.includes(right);
  if (condition.operator === "equals") return left.trim() === right.trim();
  return left.trim() !== right.trim();
}

export function outgoingEdge(
  graph: ProcessGraph,
  nodeId: string,
  branch: ProcessBranch,
): ProcessGraphEdge | undefined {
  return graph.edges.find((edge) => edge.source === nodeId && edge.branch === branch);
}

export function outgoingEdges(graph: ProcessGraph, nodeId: string): ProcessGraphEdge[] {
  return graph.edges.filter((edge) => edge.source === nodeId);
}
