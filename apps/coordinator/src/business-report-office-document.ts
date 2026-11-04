import { createHash } from "node:crypto";
import MarkdownIt from "markdown-it";
type Token = ReturnType<InstanceType<typeof MarkdownIt>["parse"]>[number];

export const BUSINESS_REPORT_OFFICE_PROFILE = "business-report-office.v1";
export const BUSINESS_REPORT_OFFICE_MAX_BYTES = 10 * 1024 * 1024;
export const BUSINESS_REPORT_OFFICE_FONT_HASHES = {
  regular: "7da195a74c55bef988d0d48f9508bd5d849425c1770dba5d7bfc6ce9ed848954",
  bold: "e6476c1b80502924294eed40894c5b18e06c181444ca953e5334262df9c27724",
} as const;
export const BUSINESS_REPORT_OFFICE_LICENSE_SHA256 = "7a083b136e64d064794c3419751e5c7dd10d2f64c108fe5ba161eae5e5958a93";
export const BUSINESS_REPORT_OFFICE_RENDERER_SHA256 = createHash("sha256").update(JSON.stringify({
  profile: BUSINESS_REPORT_OFFICE_PROFILE, markdownIt: "15.0.3", docx: "9.14.0", pdfkit: "0.20.2", fontkit: "2.0.4",
  fonts: BUSINESS_REPORT_OFFICE_FONT_HASHES, fontLicense: BUSINESS_REPORT_OFFICE_LICENSE_SHA256, maxBytes: BUSINESS_REPORT_OFFICE_MAX_BYTES, maxPdfPages: 100,
})).digest("hex");
export type BusinessReportOfficeFormat = "docx" | "pdf";
export interface BusinessReportOfficeInput {
  format: BusinessReportOfficeFormat; markdown: string; canonicalSha256: string;
  canonicalArtifactId: string; origin: string; createdAt: string;
}
export interface OfficeRun { text: string; bold?: boolean; italic?: boolean; code?: boolean; href?: string }
export type OfficeBlock = { type: "heading"; level: number; runs: OfficeRun[] }
  | { type: "paragraph"; runs: OfficeRun[]; bullet: boolean; quote: number }
  | { type: "table"; rows: OfficeRun[][][] };
export interface OfficeDocument { blocks: OfficeBlock[]; documentSha256: string }
export class BusinessReportOfficeError extends Error {
  constructor(readonly code: string, message: string, readonly status = 400) { super(message); this.name = "BusinessReportOfficeError"; }
}
const unsupported = () => new BusinessReportOfficeError("unsupported_document", "Содержание отчёта не поддерживается этим профилем экспорта. Исходный Markdown доступен для скачивания.");
export function normalizeOfficeFormat(raw: unknown): BusinessReportOfficeFormat {
  if (raw !== "docx" && raw !== "pdf") throw new BusinessReportOfficeError("invalid_format", "Выберите DOCX или PDF");
  return raw;
}
export function normalizeOfficeOrigin(raw: unknown): string {
  if (typeof raw !== "string" || raw.length > 2048) throw unsupported();
  let url: URL;
  try { url = new URL(raw); } catch { throw unsupported(); }
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.pathname !== "/" || url.search || url.hash) throw unsupported();
  return url.origin;
}
export const officeText = (runs: OfficeRun[]) => runs.map(run => run.text).join("");

function inline(tokens: Token[], origin: string): OfficeRun[] {
  const runs: OfficeRun[] = [];
  let bold = 0, italic = 0, href: string | undefined;
  for (const token of tokens) {
    if (token.type === "strong_open") { bold++; continue; }
    if (token.type === "strong_close") { bold--; continue; }
    if (token.type === "em_open") { italic++; continue; }
    if (token.type === "em_close") { italic--; continue; }
    if (token.type === "link_open") {
      if (href) throw unsupported();
      const target = new URL(String(token.attrGet("href") ?? ""), origin);
      if (target.origin !== origin || target.pathname !== "/" || !target.hash.startsWith("#knowledge?")) throw unsupported();
      href = target.href; continue;
    }
    if (token.type === "link_close") { href = undefined; continue; }
    const text = token.type === "softbreak" || token.type === "hardbreak" ? "\n"
      : token.type === "text" || token.type === "code_inline" ? token.content : null;
    if (text === null || bold < 0 || italic < 0) throw unsupported();
    if (/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/u.test(text)) throw unsupported();
    runs.push({ text, ...(bold ? { bold: true } : {}), ...(italic ? { italic: true } : {}),
      ...(token.type === "code_inline" ? { code: true } : {}), ...(href ? { href } : {}) });
  }
  if (bold || italic || href) throw unsupported();
  return runs;
}

/** One presentation tree for both formats, built only from the checksum-verified canonical Markdown. */
export function officeDocument(input: BusinessReportOfficeInput): OfficeDocument {
  const origin = normalizeOfficeOrigin(input.origin);
  if (typeof input.markdown !== "string" || Buffer.byteLength(input.markdown) > 800_000
    || !input.markdown.startsWith("# Внутренний отчёт\n") || !/^[a-f0-9]{64}$/.test(input.canonicalSha256)
    || createHash("sha256").update(input.markdown).digest("hex") !== input.canonicalSha256
    || !/^[a-f0-9-]{36}$/.test(input.canonicalArtifactId) || !Number.isFinite(Date.parse(input.createdAt))) throw unsupported();
  const tokens = new MarkdownIt({ html: false, linkify: false, typographer: false }).parse(input.markdown, {});
  const blocks: OfficeBlock[] = [];
  let quote = 0;
  const lists: boolean[] = [];
  let bullet = false;
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i]!;
    if (token.type === "blockquote_open") { quote++; continue; }
    if (token.type === "blockquote_close") { quote--; continue; }
    if (token.type === "bullet_list_open") { lists.push(true); continue; }
    if (token.type === "bullet_list_close") { lists.pop(); bullet = false; continue; }
    if (token.type === "list_item_open") { bullet = true; continue; }
    if (token.type === "list_item_close") { bullet = false; continue; }
    if (token.type === "paragraph_open" || token.type === "heading_open") {
      const content = tokens[++i]; const close = tokens[++i];
      if (content?.type !== "inline" || !close || close.type !== (token.type === "heading_open" ? "heading_close" : "paragraph_close")) throw unsupported();
      const runs = inline(content.children ?? [], origin);
      if (token.type === "heading_open") {
        const level = Number(token.tag.slice(1)); if (level < 1 || level > 3) throw unsupported();
        blocks.push({ type: "heading", level, runs });
      } else { blocks.push({ type: "paragraph", runs, bullet, quote }); bullet = false; }
    } else if (token.type === "table_open") {
      const rows: OfficeRun[][][] = [];
      let row: OfficeRun[][] | null = null, closed = false;
      for (i++; i < tokens.length; i++) {
        const cell = tokens[i]!;
        if (cell.type === "table_close") { closed = true; break; }
        if (cell.type === "tr_open") row = [];
        else if (cell.type === "tr_close") { if (!row?.length) throw unsupported(); rows.push(row); row = null; }
        else if (cell.type === "th_open" || cell.type === "td_open") {
          const content = tokens[++i], close = tokens[++i];
          if (!row || content?.type !== "inline" || close?.type !== (cell.type === "th_open" ? "th_close" : "td_close")) throw unsupported();
          row.push(inline(content.children ?? [], origin));
        } else if (!["thead_open", "thead_close", "tbody_open", "tbody_close"].includes(cell.type)) throw unsupported();
      }
      if (!closed || !rows.length || rows.length > 501 || rows[0]!.length > 8 || rows.some(row => row.length !== rows[0]!.length)) throw unsupported();
      blocks.push({ type: "table", rows });
    } else throw unsupported();
    if (blocks.length > 10_000 || lists.length > 4 || quote > 4 || quote < 0) throw unsupported();
  }
  if (lists.length || quote) throw unsupported();
  blocks.push({ type: "heading", level: 2, runs: [{ text: "Сведения об экспорте" }] },
    { type: "paragraph", bullet: false, quote: 0, runs: [{ text: `Исходный артефакт: ${input.canonicalArtifactId}\nSHA-256 исходного Markdown: ${input.canonicalSha256}\nПрофиль экспорта: ${BUSINESS_REPORT_OFFICE_PROFILE}\nSHA-256 renderer: ${BUSINESS_REPORT_OFFICE_RENDERER_SHA256}\nАдрес АГАТ для ссылок: ${origin}` }] });
  return { blocks, documentSha256: createHash("sha256").update(JSON.stringify(blocks)).digest("hex") };
}
