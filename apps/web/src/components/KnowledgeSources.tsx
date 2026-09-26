import React from "react";
import { knowledgeSourceHref, knowledgeSourceLocation } from "../knowledge";
import type { KnowledgeSource, Stage } from "../types";

export function KnowledgeLinkedOutput({ text, sources }: { text: string; sources: KnowledgeSource[] }) {
  const unique = new Map<string, KnowledgeSource | null>();
  for (const source of sources) {
    // Legacy runs may reuse K1 in different stages; never guess its source.
    unique.set(source.marker, unique.has(source.marker) ? null : source);
  }
  const link = (label: string, marker: string, key: string) => {
    const source = unique.get(marker);
    return source ? <a key={key} href={knowledgeSourceHref(source)} title={source.provenance.documentName}>{label}</a> : label;
  };
  return <pre>{text.split(/(\[K\d+(?:[ \t]*[,/][ \t]*K\d+)*\])/g).map((part, index) => {
    if (/^\[K\d+\]$/.test(part)) return link(part, part.slice(1, -1), String(index));
    if (!/^\[K\d+(?:[ \t]*[,/][ \t]*K\d+)+\]$/.test(part)) return part;
    // Preserve the exact generated text and delimiters; each known marker has
    // its own destination. Malformed groups and ranges remain ordinary text.
    return <React.Fragment key={index}>{part.split(/(K\d+)/g).map((token, offset) =>
      /^K\d+$/.test(token) ? link(token, token, `${index}:${offset}`) : token)}</React.Fragment>;
  })}</pre>;
}

export function KnowledgeSources({ sources, stages }: { sources: KnowledgeSource[]; stages: Stage[] }) {
  if (!sources.length) return null;
  return <section className="knowledge-sources" aria-label="Источники результата">
    <h4>Источники Knowledge</h4>
    <p>Фрагменты, переданные модели. Откройте документ, чтобы проверить выводы отчёта.</p>
    {sources.map((source) => <details key={`${source.retrievalId}:${source.marker}`}>
      <summary><span>[{source.marker}] </span><a href={knowledgeSourceHref(source)}>{source.provenance.documentName} · {knowledgeSourceLocation(source)}</a>
        <small>Этап: {stages.find((stage) => stage.id === source.stageId)?.agent.name ?? source.stageId}</small></summary>
      <blockquote>{source.content ?? source.excerpt}</blockquote>
      <small>Символы {source.provenance.charStart}–{source.provenance.charEnd} · SHA-256 фрагмента: <code>{source.provenance.chunkSha256}</code></small>
    </details>)}
  </section>;
}
