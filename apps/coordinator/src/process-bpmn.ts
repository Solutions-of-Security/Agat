import { createHash } from "node:crypto";

import type { ProcessBranch, ProcessGraph, ProcessGraphNode, ProcessNodeType } from "./types.js";

const BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL";
const BPMNDI_NS = "http://www.omg.org/spec/BPMN/20100524/DI";
const DC_NS = "http://www.omg.org/spec/DD/20100524/DC";
const DI_NS = "http://www.omg.org/spec/DD/20100524/DI";
const AGAT_NS = "https://agat.local/bpmn/1.2";
const XML_LIMIT = 1_048_576;

function xmlEscape(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function xmlUnescape(value: string): string {
  return value
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&gt;", ">")
    .replaceAll("&lt;", "<")
    .replaceAll("&amp;", "&");
}

function bpmnId(prefix: "Process" | "Node" | "Flow" | "Definitions" | "Diagram" | "Plane", value: string): string {
  return `${prefix}_${createHash("sha256").update(value).digest("hex")}`;
}

function attributeMap(source: string): Record<string, string> {
  const attributes: Record<string, string> = {};
  const pattern = /([:\w.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/gu;
  for (const match of source.matchAll(pattern)) {
    if (match[1]) attributes[match[1]] = xmlUnescape(match[2] ?? match[3] ?? "");
  }
  return attributes;
}

function elementTag(node: ProcessGraphNode): string {
  if (node.type === "start") return "startEvent";
  if (node.type === "end") return "endEvent";
  if (node.type === "approval") return "userTask";
  if (node.type === "transform") return "scriptTask";
  if (node.type === "condition" || node.type === "loop") return "exclusiveGateway";
  if (node.type === "parallel_fork" || node.type === "parallel_join") return "parallelGateway";
  if (node.type === "signal" || node.type === "wait") return "intermediateCatchEvent";
  if (node.type === "subprocess") return "callActivity";
  return "serviceTask";
}

function encodedConfig(node: ProcessGraphNode): string {
  return encodeURIComponent(JSON.stringify(node.config));
}

function nodeXml(node: ProcessGraphNode, id: string): string {
  const tag = elementTag(node);
  const attributes = [
    `id="${id}"`,
    `name="${xmlEscape(node.name)}"`,
    `agat:id="${xmlEscape(node.id)}"`,
    `agat:type="${node.type}"`,
    `agat:config="${xmlEscape(encodedConfig(node))}"`,
  ];
  if (node.type === "parallel_fork") attributes.push('gatewayDirection="Diverging"');
  if (node.type === "parallel_join") attributes.push('gatewayDirection="Converging"');
  if (node.type === "subprocess" && node.config.subprocessProcessId) {
    attributes.push(`calledElement="${xmlEscape(node.config.subprocessProcessId)}"`);
  }
  if (node.type === "signal") {
    return `    <bpmn:${tag} ${attributes.join(" ")}><bpmn:signalEventDefinition /></bpmn:${tag}>`;
  }
  if (node.type === "wait") {
    return `    <bpmn:${tag} ${attributes.join(" ")}><bpmn:timerEventDefinition /></bpmn:${tag}>`;
  }
  return `    <bpmn:${tag} ${attributes.join(" ")} />`;
}

function shapeSize(node: ProcessGraphNode): { width: number; height: number } {
  if (node.type === "start" || node.type === "end") return { width: 42, height: 42 };
  if (["condition", "loop", "parallel_fork", "parallel_join"].includes(node.type)) return { width: 64, height: 64 };
  return { width: 154, height: 74 };
}

export function exportProcessBpmn(input: {
  processId: string;
  name: string;
  version: number | "draft";
  graph: ProcessGraph;
}): string {
  const processElementId = bpmnId("Process", input.processId);
  const nodeIds = new Map(input.graph.nodes.map((node) => [node.id, bpmnId("Node", node.id)]));
  const edgeIds = new Map(input.graph.edges.map((edge) => [edge.id, bpmnId("Flow", edge.id)]));
  const nodes = input.graph.nodes.map((node) => nodeXml(node, nodeIds.get(node.id)!)).join("\n");
  const edges = input.graph.edges.map((edge) => `    <bpmn:sequenceFlow id="${edgeIds.get(edge.id)!}" sourceRef="${nodeIds.get(edge.source)!}" targetRef="${nodeIds.get(edge.target)!}" agat:id="${xmlEscape(edge.id)}" agat:branch="${edge.branch}" />`).join("\n");
  const shapes = input.graph.nodes.map((node) => {
    const size = shapeSize(node);
    const nodeId = nodeIds.get(node.id)!;
    return `      <bpmndi:BPMNShape id="Shape_${nodeId}" bpmnElement="${nodeId}"><dc:Bounds x="${node.position.x}" y="${node.position.y}" width="${size.width}" height="${size.height}" /></bpmndi:BPMNShape>`;
  }).join("\n");
  const edgeShapes = input.graph.edges.map((edge) => {
    const source = input.graph.nodes.find((node) => node.id === edge.source);
    const target = input.graph.nodes.find((node) => node.id === edge.target);
    const start = source ? { x: source.position.x + shapeSize(source).width, y: source.position.y + shapeSize(source).height / 2 } : { x: 0, y: 0 };
    const end = target ? { x: target.position.x, y: target.position.y + shapeSize(target).height / 2 } : { x: 0, y: 0 };
    const edgeId = edgeIds.get(edge.id)!;
    return `      <bpmndi:BPMNEdge id="Edge_${edgeId}" bpmnElement="${edgeId}"><di:waypoint x="${start.x}" y="${start.y}" /><di:waypoint x="${end.x}" y="${end.y}" /></bpmndi:BPMNEdge>`;
  }).join("\n");
  return `<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="${BPMN_NS}" xmlns:bpmndi="${BPMNDI_NS}" xmlns:dc="${DC_NS}" xmlns:di="${DI_NS}" xmlns:agat="${AGAT_NS}" id="${bpmnId("Definitions", input.processId)}" targetNamespace="${AGAT_NS}">
  <bpmn:process id="${processElementId}" name="${xmlEscape(input.name)}" isExecutable="true" agat:version="${input.version}">
${nodes}
${edges}
  </bpmn:process>
  <bpmndi:BPMNDiagram id="${bpmnId("Diagram", input.processId)}">
    <bpmndi:BPMNPlane id="${bpmnId("Plane", input.processId)}" bpmnElement="${processElementId}">
${shapes}
${edgeShapes}
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>
`;
}

const SUPPORTED_TAGS = new Set([
  "startEvent",
  "endEvent",
  "serviceTask",
  "scriptTask",
  "userTask",
  "exclusiveGateway",
  "parallelGateway",
  "intermediateCatchEvent",
  "callActivity",
  "task",
]);

function inferredType(tag: string, attrs: Record<string, string>, elementBody: string): ProcessNodeType {
  const explicit = attrs["agat:type"];
  const supported: ProcessNodeType[] = [
    "start", "agent", "http", "transform", "wait", "approval", "artifact", "condition", "loop",
    "parallel_fork", "parallel_join", "signal", "subprocess", "end",
  ];
  if (supported.includes(explicit as ProcessNodeType)) return explicit as ProcessNodeType;
  if (tag === "startEvent") return "start";
  if (tag === "endEvent") return "end";
  if (tag === "userTask") return "approval";
  if (tag === "scriptTask") return "transform";
  if (tag === "exclusiveGateway") return "condition";
  if (tag === "parallelGateway") return attrs.gatewayDirection === "Converging" ? "parallel_join" : "parallel_fork";
  if (tag === "callActivity") return "subprocess";
  if (tag === "intermediateCatchEvent") return /signalEventDefinition/u.test(elementBody) ? "signal" : "wait";
  return "http";
}

function decodedConfig(attrs: Record<string, string>): ProcessGraphNode["config"] {
  const encoded = attrs["agat:config"];
  if (!encoded) return {};
  try {
    const parsed = JSON.parse(decodeURIComponent(encoded)) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as ProcessGraphNode["config"]
      : {};
  } catch {
    throw new Error("BPMN содержит некорректный agat:config");
  }
}

function defaultConfig(type: ProcessNodeType, attrs: Record<string, string>): ProcessGraphNode["config"] {
  if (type === "http") return { method: "GET", url: "https://example.invalid/", headers: {}, body: "", timeoutSeconds: 30, idempotencyHeader: "Idempotency-Key" };
  if (type === "transform") return { template: "{{ lastOutput }}" };
  if (type === "wait") return { waitSeconds: 60 };
  if (type === "approval") return { approvalMessage: "Проверьте результат предыдущего шага" };
  if (type === "condition") return { condition: { source: "last_output", operator: "contains", value: "готово", caseSensitive: false } };
  if (type === "loop") return { condition: { source: "last_output", operator: "contains", value: "доработать", caseSensitive: false }, maxIterations: 3 };
  if (type === "signal") return { signalName: "external.signal", signalCorrelationKey: "", signalTimeoutSeconds: 0 };
  if (type === "subprocess") return { subprocessProcessId: attrs.calledElement ?? "", subprocessInputTemplate: "{{ lastOutput }}" };
  return {};
}

function fallbackPositions(nodes: ProcessGraphNode[]): Map<string, { x: number; y: number }> {
  return new Map(nodes.map((node, index) => [node.id, { x: 72 + (index % 6) * 220, y: 90 + Math.floor(index / 6) * 150 }]));
}

export function importProcessBpmn(xml: string): { name: string; graph: ProcessGraph } {
  if (typeof xml !== "string" || !xml.trim() || Buffer.byteLength(xml, "utf8") > XML_LIMIT) {
    throw new Error("BPMN XML должен содержать от 1 до 1048576 байт");
  }
  if (/<!DOCTYPE|<!ENTITY/iu.test(xml)) throw new Error("BPMN DTD и XML entities запрещены");
  const processMatches = [...xml.matchAll(/<(?:[\w.-]+:)?process\b/giu)];
  if (processMatches.length !== 1) throw new Error("BPMN import поддерживает ровно один process");
  const processMatch = /<(?:[\w.-]+:)?process\b([^>]*)>([\s\S]*?)<\/(?:[\w.-]+:)?process\s*>/iu.exec(xml);
  if (!processMatch) throw new Error("BPMN process задан некорректно");
  const processAttributes = attributeMap(processMatch?.[1] ?? "");
  const processBody = processMatch[2] ?? "";
  const name = processAttributes.name?.trim().slice(0, 100) || "Импортированный BPMN процесс";
  const nodes: ProcessGraphNode[] = [];
  const internalNodeIds = new Map<string, string>();
  const elementPattern = /<(?:[\w.-]+:)?(startEvent|endEvent|serviceTask|scriptTask|userTask|exclusiveGateway|parallelGateway|intermediateCatchEvent|callActivity|task)\b([^>]*?)(?:\/>|>([\s\S]*?)<\/(?:[\w.-]+:)?\1\s*>)/gu;
  for (const match of processBody.matchAll(elementPattern)) {
    const tag = match[1] ?? "";
    if (!SUPPORTED_TAGS.has(tag)) continue;
    const attrs = attributeMap(match[2] ?? "");
    const externalId = attrs.id?.trim();
    if (!externalId) continue;
    const id = (attrs["agat:id"]?.trim() || externalId).slice(0, 100);
    const type = inferredType(tag, attrs, match[3] ?? "");
    internalNodeIds.set(externalId, id);
    nodes.push({
      id,
      type,
      name: (attrs.name?.trim() || type).slice(0, 80),
      position: { x: 0, y: 0 },
      config: { ...defaultConfig(type, attrs), ...decodedConfig(attrs) },
    });
  }
  if (nodes.length === 0) throw new Error("BPMN не содержит поддерживаемых flow nodes");

  const positions = new Map<string, { x: number; y: number }>();
  const shapePattern = /<(?:[\w.-]+:)?BPMNShape\b([^>]*)>([\s\S]*?)<\/(?:[\w.-]+:)?BPMNShape\s*>/gu;
  for (const shape of xml.matchAll(shapePattern)) {
    const shapeAttrs = attributeMap(shape[1] ?? "");
    const bounds = /<(?:[\w.-]+:)?Bounds\b([^>]*)\/?\s*>/u.exec(shape[2] ?? "");
    const boundsAttrs = attributeMap(bounds?.[1] ?? "");
    const x = Number(boundsAttrs.x);
    const y = Number(boundsAttrs.y);
    if (shapeAttrs.bpmnElement && Number.isFinite(x) && Number.isFinite(y)) {
      positions.set(internalNodeIds.get(shapeAttrs.bpmnElement) ?? shapeAttrs.bpmnElement, { x, y });
    }
  }
  const fallbacks = fallbackPositions(nodes);
  for (const node of nodes) node.position = positions.get(node.id) ?? fallbacks.get(node.id)!;

  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const edges: ProcessGraph["edges"] = [];
  const flowPattern = /<(?:[\w.-]+:)?sequenceFlow\b([^>]*?)(?:\/>|>[\s\S]*?<\/(?:[\w.-]+:)?sequenceFlow\s*>)/gu;
  const branchIndexes = new Map<string, number>();
  for (const match of processBody.matchAll(flowPattern)) {
    const attrs = attributeMap(match[1] ?? "");
    const sourceId = attrs.sourceRef ? internalNodeIds.get(attrs.sourceRef) ?? attrs.sourceRef : "";
    const targetId = attrs.targetRef ? internalNodeIds.get(attrs.targetRef) ?? attrs.targetRef : "";
    if (!attrs.id || !sourceId || !targetId || !nodeById.has(sourceId) || !nodeById.has(targetId)) continue;
    const source = nodeById.get(sourceId)!;
    const index = branchIndexes.get(source.id) ?? 0;
    branchIndexes.set(source.id, index + 1);
    let branch = attrs["agat:branch"] as ProcessBranch | undefined;
    if (!(["default", "true", "false", "repeat", "exit"] as const).includes(branch as ProcessBranch)) {
      branch = source.type === "condition" ? (index === 0 ? "true" : "false")
        : source.type === "loop" ? (index === 0 ? "repeat" : "exit")
          : "default";
    }
    edges.push({
      id: (attrs["agat:id"]?.trim() || attrs.id).slice(0, 100),
      source: source.id,
      target: targetId,
      branch: branch ?? "default",
    });
  }

  const incomingCounts = new Map<string, number>();
  const incomingByNode = new Map<string, string[]>();
  const outgoingByNode = new Map<string, string[]>();
  for (const edge of edges) {
    incomingCounts.set(edge.target, (incomingCounts.get(edge.target) ?? 0) + 1);
    incomingByNode.set(edge.target, [...(incomingByNode.get(edge.target) ?? []), edge.source]);
    outgoingByNode.set(edge.source, [...(outgoingByNode.get(edge.source) ?? []), edge.target]);
  }
  // Some BPMN producers omit gatewayDirection. Infer convergence from standard flow topology.
  for (const node of nodes) {
    if (node.type === "parallel_fork" && (incomingCounts.get(node.id) ?? 0) >= 2 && (outgoingByNode.get(node.id)?.length ?? 0) <= 1) {
      node.type = "parallel_join";
    }
  }
  const canReach = (initialId: string, targetId: string): boolean => {
    const queue = [initialId];
    const seen = new Set<string>();
    while (queue.length > 0) {
      const current = queue.shift()!;
      if (current === targetId) return true;
      if (seen.has(current)) continue;
      seen.add(current);
      queue.push(...(outgoingByNode.get(current) ?? []));
    }
    return false;
  };
  const forks = nodes.filter((node) => node.type === "parallel_fork");
  for (const join of nodes.filter((node) => node.type === "parallel_join" && !node.config.forkId)) {
    const candidates = forks.filter((fork) => {
      const targets = outgoingByNode.get(fork.id) ?? [];
      const arrivals = incomingByNode.get(join.id) ?? [];
      if (targets.length < 2 || targets.length !== arrivals.length) return false;
      const matched = new Set<number>();
      const assign = (targetIndex: number, seen: Set<number>): boolean => {
        if (targetIndex >= targets.length) return true;
        for (let arrivalIndex = 0; arrivalIndex < arrivals.length; arrivalIndex += 1) {
          if (seen.has(arrivalIndex) || !canReach(targets[targetIndex]!, arrivals[arrivalIndex]!)) continue;
          seen.add(arrivalIndex);
          matched.add(arrivalIndex);
          if (assign(targetIndex + 1, seen)) return true;
          seen.delete(arrivalIndex);
          matched.delete(arrivalIndex);
        }
        return false;
      };
      return assign(0, new Set()) && matched.size === arrivals.length;
    });
    if (candidates.length === 1) join.config.forkId = candidates[0]!.id;
  }
  return { name, graph: { nodes, edges } };
}
