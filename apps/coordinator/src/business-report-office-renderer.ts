import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import PDFDocument from "pdfkit";
import { create as createFont } from "fontkit";
import { AlignmentType, BorderStyle, Document, ExternalHyperlink, Footer, HeadingLevel, Packer, PageNumber, Paragraph, Table, TableCell, TableRow, TextRun, VerticalAlign, WidthType } from "docx";
import { BUSINESS_REPORT_OFFICE_FONT_HASHES, BUSINESS_REPORT_OFFICE_LICENSE_SHA256, BUSINESS_REPORT_OFFICE_MAX_BYTES, BusinessReportOfficeError,
  officeDocument, officeText, type BusinessReportOfficeInput, type OfficeRun, type OfficeDocument } from "./business-report-office-document.js";

const fontRoot = new URL("../assets/fonts/", import.meta.url);
function fonts() {
  const assets = Object.fromEntries((["regular", "bold"] as const).map(style => {
    const bytes = readFileSync(new URL(`DejaVuSans${style === "bold" ? "-Bold" : ""}.ttf`, fontRoot));
    if (createHash("sha256").update(bytes).digest("hex") !== BUSINESS_REPORT_OFFICE_FONT_HASHES[style]) throw new BusinessReportOfficeError("font_unavailable", "Ресурсы шрифта экспорта не прошли проверку", 503);
    return [style, { bytes, font: createFont(bytes) }];
  })) as Record<"regular" | "bold", { bytes: Buffer; font: ReturnType<typeof createFont> }>;
  const license = readFileSync(new URL("../../../docs/licenses/DejaVuSans-LICENSE.txt", import.meta.url), "utf8");
  if (createHash("sha256").update(license).digest("hex") !== BUSINESS_REPORT_OFFICE_LICENSE_SHA256) throw new BusinessReportOfficeError("font_unavailable", "Лицензия шрифта экспорта не прошла проверку", 503);
  return { ...assets, license };
}
function verifyGlyphs(document: OfficeDocument, assets: ReturnType<typeof fonts>) {
  for (const block of document.blocks) for (const run of block.type === "table" ? block.rows.flat(2) : block.runs) {
    const font = assets[run.bold ? "bold" : "regular"].font;
    if ("fonts" in font) throw new BusinessReportOfficeError("font_unavailable", "Неподдерживаемый пакет шрифта", 503);
    for (const char of run.text) if (!/[\r\n\t]/u.test(char) && !font.hasGlyphForCodePoint(char.codePointAt(0)!)) {
      throw new BusinessReportOfficeError("unsupported_glyph", `Шрифт экспорта не содержит символ U+${char.codePointAt(0)!.toString(16).toUpperCase()}. Скачайте исходный Markdown.`);
    }
  }
}
function wordRuns(runs: OfficeRun[]) {
  return runs.flatMap<TextRun | ExternalHyperlink>(run => {
    const children = run.text.split("\n").flatMap((text, index) => [
      ...(index ? [new TextRun({ break: 1 })] : []), new TextRun({ text, bold: run.bold, italics: run.italic,
        font: "DejaVu Sans", ...(run.href ? { style: "Hyperlink" } : {}) }),
    ]);
    return run.href ? [new ExternalHyperlink({ link: run.href, children })] : children;
  });
}
async function docxBytes(tree: OfficeDocument, input: BusinessReportOfficeInput, assets: ReturnType<typeof fonts>): Promise<Buffer> {
  const border = { style: BorderStyle.SINGLE, color: "D9D9D9", size: 4 };
  const children = tree.blocks.map(block => {
    if (block.type === "table") return new Table({ width: { size: 100, type: WidthType.PERCENTAGE },
      rows: block.rows.map((row, index) => new TableRow({ tableHeader: index === 0, children: row.map((runs, column) => new TableCell({
        width: { size: row.length === 4 ? [22, 40, 17, 21][column]! : 100 / row.length, type: WidthType.PERCENTAGE },
        verticalAlign: VerticalAlign.CENTER, margins: { top: 100, bottom: 100, left: 120, right: 120 },
        shading: { fill: index === 0 ? "E8EDF2" : index % 2 ? "FFFFFF" : "F8FAFC" },
        borders: { top: border, bottom: border, left: border, right: border },
        children: [new Paragraph({ alignment: column >= 2 ? AlignmentType.CENTER : AlignmentType.LEFT,
          spacing: { before: 0, after: 0 }, children: wordRuns(runs.map(run => ({ ...run, bold: index === 0 || run.bold }))) })],
      })) })) });
    return new Paragraph({ children: wordRuns(block.runs), ...(block.type === "heading" ? {
      heading: block.level === 1 ? HeadingLevel.TITLE : block.level === 2 ? HeadingLevel.HEADING_1 : HeadingLevel.HEADING_2,
      keepNext: true, spacing: { before: block.level === 1 ? 0 : 240, after: 120 },
    } : { ...(block.bullet ? { bullet: { level: 0 } } : {}), indent: { left: block.quote * 240 }, spacing: { after: 120 } }) });
  });
  const doc = new Document({ creator: "АГАТ", title: "Внутренний отчёт", description: `Canonical Markdown SHA-256 ${input.canonicalSha256}`,
    customProperties: [{ name: "FontLicense", value: assets.license }],
    fonts: [{ name: "DejaVu Sans", data: assets.regular.bytes, bold: assets.bold.bytes }],
    styles: { default: { document: { run: { font: "DejaVu Sans", size: 23, color: "000000" }, paragraph: { spacing: { line: 276, after: 120 } } },
      title: { run: { color: "000000", size: 40, bold: true } }, heading1: { run: { color: "000000", size: 28, bold: true } }, heading2: { run: { color: "000000", size: 25, bold: true } } } },
    sections: [{ properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 1134, bottom: 1134, left: 1134, right: 1134 } } },
      footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.RIGHT,
        children: [new TextRun({ children: ["Страница ", PageNumber.CURRENT], size: 18, color: "444444" })] })] }) }, children }] });
  return Packer.toBuffer(doc);
}

async function pdfBytes(tree: OfficeDocument, input: BusinessReportOfficeInput, assets: ReturnType<typeof fonts>): Promise<Buffer> {
  const info = { Title: "Внутренний отчёт", Creator: "АГАТ", FontLicense: assets.license,
    Subject: `Canonical Markdown SHA-256 ${input.canonicalSha256}`, CreationDate: new Date(input.createdAt), ModDate: new Date(input.createdAt) };
  const doc = new PDFDocument({ size: "A4", margin: 54, bufferPages: true, info });
  doc.registerFont("Regular", assets.regular.bytes); doc.registerFont("Bold", assets.bold.bytes);
  let pages = 1;
  doc.on("pageAdded", () => { if (++pages > 100) throw new BusinessReportOfficeError("too_many_pages", "PDF превышает 100 страниц. Скачайте исходный Markdown или сократите отчёт."); });
  const chunks: Buffer[] = []; let size = 0;
  const complete = new Promise<Buffer>((resolve, reject) => {
    doc.on("data", chunk => { size += chunk.length; if (size > BUSINESS_REPORT_OFFICE_MAX_BYTES) doc.destroy(new BusinessReportOfficeError("export_too_large", "Файл экспорта превышает 10 МиБ")); else chunks.push(chunk); });
    doc.once("end", () => resolve(Buffer.concat(chunks))); doc.once("error", reject);
  });
  const left = 54, width = doc.page.width - 108;
  const paragraph = (runs: OfficeRun[], options: { size?: number; indent?: number; bullet?: boolean; heading?: boolean } = {}) => {
    const fontSize = options.size ?? 11;
    if (options.heading && doc.y > doc.page.height - 54 - 75) doc.addPage();
    doc.x = left + (options.indent ?? 0); doc.fontSize(fontSize).fillColor("000000");
    const values = options.bullet ? [{ text: "• " }, ...runs] : runs;
    if (!values.length) return;
    values.forEach((run, index) => {
      doc.font(options.heading || run.bold ? "Bold" : "Regular");
      doc.text(run.text, { width: width - (options.indent ?? 0), lineGap: 2, continued: index < values.length - 1, ...(run.href ? { link: run.href, underline: true } : { link: null, underline: false }) });
    });
    doc.moveDown(options.heading ? 0.45 : 0.6); doc.x = left;
  };
  const table = (rows: OfficeRun[][][]) => {
    const widths = rows[0]!.map((_, index) => width * (rows[0]!.length === 4 ? [0.22, 0.40, 0.17, 0.21][index]! : 1 / rows[0]!.length));
    const draw = (row: OfficeRun[][], header: boolean, index: number) => {
      doc.font(header ? "Bold" : "Regular").fontSize(10);
      const height = Math.max(...row.map((runs, col) => doc.heightOfString(officeText(runs), { width: widths[col]! - 16, lineGap: 2 }))) + 20;
      if (height > doc.page.height - 108 - 80) throw new BusinessReportOfficeError("table_row_too_large", "Строка таблицы не помещается на странице PDF. Скачайте исходный Markdown.");
      if (doc.y + height > doc.page.height - 54) { doc.addPage(); if (!header) draw(rows[0]!, true, 0); }
      const y = doc.y; let x = left;
      row.forEach((runs, col) => {
        doc.save().lineWidth(0.5).rect(x, y, widths[col]!, height).fillAndStroke(header ? "#E8EDF2" : index % 2 ? "#FFFFFF" : "#F8FAFC", "#D9D9D9").restore();
        doc.font(header ? "Bold" : "Regular").fontSize(10).fillColor("000000")
          .text(officeText(runs), x + 8, y + 10, { width: widths[col]! - 16, height: height - 16, lineGap: 2, align: col >= 2 ? "center" : "left" });
        x += widths[col]!;
      });
      doc.x = left; doc.y = y + height;
    };
    rows.forEach((row, index) => draw(row, index === 0, index)); doc.moveDown(0.7);
  };
  try {
    for (const block of tree.blocks) {
      if (block.type === "table") table(block.rows);
      else if (block.type === "heading") paragraph(block.runs, { heading: true, size: block.level === 1 ? 21 : 14 });
      else paragraph(block.runs, { bullet: block.bullet, indent: block.quote * 12 });
    }
    const range = doc.bufferedPageRange();
    for (let i = 0; i < range.count; i++) {
      doc.switchToPage(i);
      // The footer lives below the body margin. Without this temporary override,
      // PDFKit flows it onto a new page even when lineBreak is false.
      const bottom = doc.page.margins.bottom;
      doc.page.margins.bottom = 0;
      doc.font("Regular").fontSize(9).fillColor("444444")
        .text(`Страница ${i + 1} из ${range.count}`, left, doc.page.height - 32, { lineBreak: false, width, align: "right" });
      doc.page.margins.bottom = bottom;
    }
    doc.end(); return await complete;
  } catch (error) { doc.destroy(); void complete.catch(() => {}); throw error; }
}

export async function renderOfficeBytes(input: BusinessReportOfficeInput) {
  const tree = officeDocument(input); const assets = fonts(); verifyGlyphs(tree, assets);
  const bytes = input.format === "docx" ? await docxBytes(tree, input, assets) : await pdfBytes(tree, input, assets);
  if (bytes.byteLength > BUSINESS_REPORT_OFFICE_MAX_BYTES) throw new BusinessReportOfficeError("export_too_large", "Файл экспорта превышает 10 МиБ");
  return { bytes, documentSha256: tree.documentSha256 };
}
