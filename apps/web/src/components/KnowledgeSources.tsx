import { knowledgeSourceHref, knowledgeSourceLocation } from "../knowledge";
import type { KnowledgeSource, Stage } from "../types";

export function KnowledgeLinkedOutput({ text, sources }: { text: string; sources: KnowledgeSource[] }) {
  return <pre>{text.split(/(\[K\d+\])/g).map((part, index) => {
    const matches = sources.filter((source) => `[${source.marker}]` === part);
    // Legacy runs may reuse K1 in different stages; never guess which source it means.
    return matches.length === 1 ? <a key={index} href={knowledgeSourceHref(matches[0]!)} title={matches[0]!.provenance.documentName}>{part}</a> : part;
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
import React from "react";
