import type { ProcessGraph, ProcessVersionDiff, ProcessVersionDiffEntry } from "./types.js";

interface ProcessDocument {
  name: string;
  description: string;
  graph: ProcessGraph;
}

function canonical(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  return `{${Object.entries(value as Record<string, unknown>)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, field]) => `${JSON.stringify(key)}:${canonical(field)}`)
    .join(",")}}`;
}

function changed(before: unknown, after: unknown): boolean {
  return canonical(before) !== canonical(after);
}

export function diffProcessDocuments(input: {
  processId: string;
  from: number | "draft";
  to: number | "draft";
  before: ProcessDocument;
  after: ProcessDocument;
}): ProcessVersionDiff {
  const entries: ProcessVersionDiffEntry[] = [];
  if (input.before.name !== input.after.name || input.before.description !== input.after.description
    || changed(input.before.graph.requiredTools ?? [], input.after.graph.requiredTools ?? [])
    || changed(input.before.graph.requiredKnowledgeCollectionIds ?? [], input.after.graph.requiredKnowledgeCollectionIds ?? [])
    || changed(input.before.graph.mcpToolAllowlist, input.after.graph.mcpToolAllowlist)
    || changed(input.before.graph.allowPartialStart, input.after.graph.allowPartialStart)) {
    entries.push({
      kind: "metadata_changed",
      id: input.processId,
      before: { name: input.before.name, description: input.before.description, requiredTools: input.before.graph.requiredTools ?? [], requiredKnowledgeCollectionIds: input.before.graph.requiredKnowledgeCollectionIds ?? [], mcpToolAllowlist: input.before.graph.mcpToolAllowlist, allowPartialStart: input.before.graph.allowPartialStart },
      after: { name: input.after.name, description: input.after.description, requiredTools: input.after.graph.requiredTools ?? [], requiredKnowledgeCollectionIds: input.after.graph.requiredKnowledgeCollectionIds ?? [], mcpToolAllowlist: input.after.graph.mcpToolAllowlist, allowPartialStart: input.after.graph.allowPartialStart },
    });
  }

  const beforeNodes = new Map(input.before.graph.nodes.map((node) => [node.id, node]));
  const afterNodes = new Map(input.after.graph.nodes.map((node) => [node.id, node]));
  for (const [id, node] of beforeNodes) {
    const next = afterNodes.get(id);
    if (!next) entries.push({ kind: "node_removed", id, before: node, after: null });
    else if (changed(node, next)) entries.push({ kind: "node_changed", id, before: node, after: next });
  }
  for (const [id, node] of afterNodes) {
    if (!beforeNodes.has(id)) entries.push({ kind: "node_added", id, before: null, after: node });
  }

  const beforeEdges = new Map(input.before.graph.edges.map((edge) => [edge.id, edge]));
  const afterEdges = new Map(input.after.graph.edges.map((edge) => [edge.id, edge]));
  for (const [id, edge] of beforeEdges) {
    const next = afterEdges.get(id);
    if (!next || changed(edge, next)) entries.push({ kind: "edge_removed", id, before: edge, after: null });
  }
  for (const [id, edge] of afterEdges) {
    const previous = beforeEdges.get(id);
    if (!previous || changed(previous, edge)) entries.push({ kind: "edge_added", id, before: null, after: edge });
  }

  const count = (kind: ProcessVersionDiffEntry["kind"]): number => entries.filter((entry) => entry.kind === kind).length;
  return {
    processId: input.processId,
    from: input.from,
    to: input.to,
    summary: {
      addedNodes: count("node_added"),
      removedNodes: count("node_removed"),
      changedNodes: count("node_changed"),
      addedEdges: count("edge_added"),
      removedEdges: count("edge_removed"),
      metadataChanged: count("metadata_changed") > 0,
    },
    entries,
  };
}
