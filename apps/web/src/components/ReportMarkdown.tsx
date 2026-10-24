import React, { useMemo, type ComponentProps } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { createKnowledgeCitationResolver, knowledgeSourceHref } from "../knowledge";
import type { KnowledgeSource } from "../types";
import { KnowledgeLinkedOutput } from "./KnowledgeSources";

interface MdNode { type: string; value?: string; url?: string; children?: MdNode[] }
function remarkKnowledgeCitations({ sources }: { sources: KnowledgeSource[] }) {
  const resolve = createKnowledgeCitationResolver(sources);
  return (tree: MdNode) => {
    const visit = (node: MdNode) => {
      if (!node.children || ["link", "linkReference", "code", "inlineCode", "html"].includes(node.type)) return;
      node.children = node.children.flatMap((child): MdNode[] => {
        if (child.type !== "text" || typeof child.value !== "string") { visit(child); return [child]; }
        return resolve(child.value).map((part) => part.source
          ? { type: "link", url: knowledgeSourceHref(part.source), children: [{ type: "text", value: part.text }] }
          : { type: "text", value: part.text });
      });
    };
    visit(tree);
  };
}

const allowedElements = ["p", "h1", "h2", "h3", "h4", "h5", "h6", "table", "thead", "tbody", "tr", "th", "td",
  "ul", "ol", "li", "blockquote", "pre", "code", "strong", "em", "del", "hr", "br", "a", "img", "sup"];

export function ReportMarkdown({ text, sources }: { text: string; sources: KnowledgeSource[] }) {
  const plugins = useMemo<NonNullable<ComponentProps<typeof Markdown>["remarkPlugins"]>>(
    () => [remarkGfm, [remarkKnowledgeCitations, { sources }]], [sources]);
  const allowedHrefs = useMemo(() => {
    const counts = new Map<string, number>();
    for (const source of sources) counts.set(source.marker, (counts.get(source.marker) ?? 0) + 1);
    return new Set(sources.filter((source) => counts.get(source.marker) === 1).map(knowledgeSourceHref));
  }, [sources]);
  if (new TextEncoder().encode(text).byteLength > 200_000) return <>
    <p role="status">Результат слишком велик для форматирования (до 200 000 байт). Исходный текст и скачивание доступны полностью.</p>
    <KnowledgeLinkedOutput text={text} sources={sources} />
  </>;
  return <div className="report-markdown">
      <Markdown remarkPlugins={plugins} skipHtml allowedElements={allowedElements} unwrapDisallowed components={{
        a: ({ href, children }) => {
          const target = href?.startsWith("/#") ? href.slice(1) : href;
          return target && allowedHrefs.has(target) ? <a href={target}>{children}</a> : <span>{children}</span>;
        },
        img: ({ alt }) => <span className="report-markdown__image">{alt || "Изображение из отчёта"}</span>,
        table: ({ children }) => <div className="report-markdown__table" role="region" aria-label="Таблица отчёта" tabIndex={0}><table>{children}</table></div>,
      }}>{text}</Markdown>
    </div>;
}
