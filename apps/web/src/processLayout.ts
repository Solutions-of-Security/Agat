import type { ProcessGraph, ProcessGraphEdge, ProcessGraphNode } from "./types";

type XYPosition = { x: number; y: number };

export function autoLayout(graph: ProcessGraph): ProcessGraph {
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


export function hasOverlappingSteps(graph: ProcessGraph): boolean {
  return graph.nodes.some((node, index) => graph.nodes.slice(index + 1).some((other) =>
    Math.abs(node.position.x - other.position.x) < 208
    && Math.abs(node.position.y - other.position.y) < 124));
}
