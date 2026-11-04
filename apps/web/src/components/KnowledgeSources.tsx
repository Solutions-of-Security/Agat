import React from "react";
import { createKnowledgeCitationResolver, knowledgeSourceHref, knowledgeSourceLocation } from "../knowledge";
import type { KnowledgeSource, Stage } from "../types";

export function KnowledgeLinkedOutput({ text, sources }: { text: string; sources: KnowledgeSource[] }) {
  return <pre>{createKnowledgeCitationResolver(sources)(text).map((part, index) => part.source
    ? <a key={index} href={knowledgeSourceHref(part.source)} title={part.source.provenance.documentName}>{part.text}</a>
    : part.text)}</pre>;
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
