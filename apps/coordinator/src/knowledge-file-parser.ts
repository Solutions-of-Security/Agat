import { unzipSync } from "fflate";
import { SaxesParser } from "saxes";
import type { KnowledgePageLocation } from "./types.js";

const MAX_TEXT = 2_000_000;
const MAX_XML_BYTES = 8 * 1024 * 1024;

export interface ExtractedKnowledgeFile {
  content: string;
  pages: KnowledgePageLocation[];
}

// Called in a bounded worker thread. No URLs, relationships or embedded objects
// from the document are fetched or executed.
export async function extractKnowledgeFile(bytes: Uint8Array, mediaType: string): Promise<ExtractedKnowledgeFile> {
  if (mediaType === "application/pdf") {
    if (Buffer.from(bytes.subarray(0, 5)).toString() !== "%PDF-") throw new Error("Некорректная сигнатура PDF");
    const { getDocument } = await import("pdfjs-dist/legacy/build/pdf.mjs");
    const task = getDocument({ data: bytes, useSystemFonts: false, disableFontFace: true,
      stopAtErrors: true, verbosity: 0 });
    try {
      const pdf = await task.promise;
      if (pdf.numPages > 500) throw new Error("PDF содержит больше 500 страниц");
      let content = "";
      const pages: KnowledgePageLocation[] = [];
      for (let number = 1; number <= pdf.numPages; number += 1) {
        const page = await pdf.getPage(number);
        const text = await page.getTextContent();
        const value = text.items.map((item) => "str" in item ? item.str + (item.hasEOL ? "\n" : " ") : "").join("").trim();
        if (number > 1) content += "\n\n";
        const start = content.length;
        content += value;
        if (content.length > MAX_TEXT) throw new Error("Извлечённый текст превышает 2 000 000 символов");
        pages.push({ pageNumber: number, charStart: start, charEnd: content.length });
        page.cleanup();
      }
      if (!content.trim()) throw new Error("PDF не содержит извлекаемого текста. Для сканов нужен OCR; загрузите документ с текстовым слоем");
      return { content, pages };
    } catch (error) {
      if (error instanceof Error && error.name === "PasswordException") throw new Error("PDF защищён паролем. Загрузите копию без пароля");
      throw error;
    } finally {
      await task.destroy();
    }
  }

  if (bytes[0] !== 0x50 || bytes[1] !== 0x4b) throw new Error("Некорректная сигнатура DOCX");
  const files = unzipSync(bytes, { filter: (file) => {
    if (file.name !== "word/document.xml") return false;
    if (file.originalSize > MAX_XML_BYTES) throw new Error("Текстовая часть DOCX превышает 8 МиБ");
    return true;
  } });
  const xml = files["word/document.xml"];
  if (!xml) throw new Error("В DOCX отсутствует word/document.xml");
  if (xml.byteLength > MAX_XML_BYTES) throw new Error("Текстовая часть DOCX превышает 8 МиБ");
  const parser = new SaxesParser({ xmlns: true });
  const wordNamespaces = new Set([
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "http://purl.oclc.org/ooxml/wordprocessingml/main",
  ]);
  let content = "";
  let inText = false;
  let documentFound = false;
  const append = (text: string) => {
    content += text;
    if (content.length > MAX_TEXT) throw new Error("Извлечённый текст превышает 2 000 000 символов");
  };
  parser.on("doctype", () => { throw new Error("DTD в DOCX не поддерживается"); });
  parser.on("opentag", (tag) => {
    if (!wordNamespaces.has(tag.uri)) return;
    if (tag.local === "document") documentFound = true;
    if (tag.local === "t") inText = true;
    if (tag.local === "tab") append("\t");
    if (tag.local === "br" || tag.local === "cr") append("\n");
  });
  parser.on("text", (text) => { if (inText) append(text); });
  parser.on("closetag", (tag) => {
    if (!wordNamespaces.has(tag.uri)) return;
    if (tag.local === "t") inText = false;
    if (tag.local === "p") append("\n\n");
  });
  parser.write(new TextDecoder("utf-8", { fatal: true }).decode(xml)).close();
  if (!documentFound) throw new Error("Некорректная структура DOCX");
  if (!content.trim()) throw new Error("DOCX не содержит извлекаемого текста");
  // DOCX has no reliable page numbers without a layout engine. Cite chunks.
  return { content: content.trim(), pages: [] };
}
