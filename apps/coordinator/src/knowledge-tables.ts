import { parse } from "csv-parse/sync";
import { unzipSync } from "fflate";
import { SaxesParser, type SaxesTagNS } from "saxes";

export const KNOWLEDGE_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
export const KNOWLEDGE_CSV_MEDIA_TYPE = "text/csv";
const MAX_ROWS = 10_000;
const MAX_COLUMNS = 256;
const MAX_CELLS = 100_000;
const MAX_CELL_TEXT = 32_000;
const MAX_TEXT = 2_000_000;
const MAX_XML = 8 * 1024 * 1024;
const MAIN_NAMESPACES = new Set(["http://schemas.openxmlformats.org/spreadsheetml/2006/main", "http://purl.oclc.org/ooxml/spreadsheetml/main"]);

function columnName(index: number): string {
  let name = "";
  for (let value = index + 1; value; value = Math.floor((value - 1) / 26)) name = String.fromCharCode(65 + (value - 1) % 26) + name;
  return name;
}

function outputBuilder() {
  const parts: string[] = [];
  let length = 0;
  let cells = 0;
  return {
    append(text: string) {
      length += text.length;
      if (length > MAX_TEXT) throw new Error("Извлечённая таблица превышает 2 000 000 символов");
      parts.push(text);
    },
    cell(sheet: string, address: string, type: string, value: string) {
      if (++cells > MAX_CELLS) throw new Error("Таблица содержит больше 100 000 ячеек");
      if (value.length > MAX_CELL_TEXT) throw new Error("Содержимое ячейки превышает 32 000 символов");
      this.append(`${JSON.stringify(sheet)}!${address} [${type}] = ${JSON.stringify(value)}\n`);
    },
    result() {
      if (!cells) throw new Error("Таблица не содержит извлекаемых значений");
      return { content: parts.join(""), pages: [] };
    },
  };
}

/** Determine the first nonempty logical record, excluding separators inside quotes. */
function csvDelimiter(text: string): string {
  const separators = [",", ";", "\t"];
  let counts = [0, 0, 0]; let quoted = false; let nonempty = false;
  for (let index = 0; index < Math.min(text.length, 64_000); index++) {
    const character = text[index]!;
    if (character === '"') {
      nonempty = true;
      if (quoted && text[index + 1] === '"') index++;
      else quoted = !quoted;
    } else if (!quoted && (character === "\n" || character === "\r")) {
      if (nonempty) break;
      counts = [0, 0, 0];
    } else {
      if (character.trim()) nonempty = true;
      if (!quoted) { const separator = separators.indexOf(character); if (separator >= 0) counts[separator]!++; }
    }
  }
  const max = Math.max(...counts);
  return separators[counts.indexOf(max)]!;
}

export function extractCsv(bytes: Uint8Array) {
  let text: string;
  try { text = new TextDecoder("utf-8", { fatal: true }).decode(bytes); }
  catch { throw new Error("CSV должен быть в UTF-8. Сохраните файл в UTF-8 и повторите загрузку"); }
  const out = outputBuilder();
  out.append("CSV: адреса обозначают логические строки, включая заголовок и пустые строки. Все значения — текст; формулы не исполняются.\n");
  let row = 0; let width: number | undefined;
  try {
    parse(text, { bom: true, delimiter: csvDelimiter(text), cast: false, relax_column_count: true,
      skip_empty_lines: false, max_record_size: 64_000,
      on_record(record: string[]) {
        if (++row > MAX_ROWS) throw new Error("CSV содержит больше 10 000 строк");
        if (record.length > MAX_COLUMNS) throw new Error("CSV содержит больше 256 столбцов");
        const blank = record.length === 1 && record[0] === "";
        if (!blank) {
          width ??= record.length;
          if (record.length !== width) throw new Error("CSV содержит строки с разным числом столбцов");
        }
        record.forEach((value, column) => { if (value !== "") out.cell("CSV", `${columnName(column)}${row}`, "text", value); });
        return null;
      },
    });
  } catch (error) {
    if (error instanceof Error && !error.message.startsWith("Invalid") && !("code" in error)) throw error;
    throw new Error("Некорректный CSV: проверьте разделители, кавычки и размер строки (до 64 000 символов)");
  }
  return out.result();
}

function attribute(tag: SaxesTagNS, local: string): string {
  return Object.values(tag.attributes).find((item) => item.local === local)?.value ?? "";
}
function xml(bytes: Uint8Array | undefined, handlers: { open?: (tag: SaxesTagNS) => void;
  close?: (tag: SaxesTagNS) => void; text?: (value: string) => void }) {
  if (!bytes) throw new Error("В XLSX отсутствует обязательная XML-часть");
  const parser = new SaxesParser({ xmlns: true });
  parser.on("doctype", () => { throw new Error("DTD в XLSX не поддерживается"); });
  if (handlers.open) parser.on("opentag", handlers.open);
  if (handlers.close) parser.on("closetag", handlers.close);
  if (handlers.text) { parser.on("text", handlers.text); parser.on("cdata", handlers.text); }
  parser.write(new TextDecoder("utf-8", { fatal: true }).decode(bytes)).close();
}

export function extractXlsx(bytes: Uint8Array) {
  if (bytes[0] !== 0x50 || bytes[1] !== 0x4b) throw new Error("Некорректная сигнатура XLSX");
  let expanded = 0; let entries = 0;
  const files = unzipSync(bytes, { filter(file) {
    if (++entries > 2_000) throw new Error("XLSX содержит больше 2000 ZIP-частей");
    if (/vbaproject|macrosheets\/|dialogsheets\//i.test(file.name)) throw new Error("Книги с макросами не поддерживаются. Загрузите XLSX только со значениями");
    if (!["xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/sharedStrings.xml"].includes(file.name)
      && !/^xl\/worksheets\/[A-Za-z0-9_-]+\.xml$/.test(file.name)) return false;
    expanded += file.originalSize;
    if (file.originalSize > MAX_XML || expanded > 32 * 1024 * 1024) throw new Error("XML-части XLSX превышают допустимый размер");
    return true;
  } });
  const sheets: Array<{ name: string; id: string; state: string }> = [];
  let workbookFound = false;
  xml(files["xl/workbook.xml"], { open(tag) {
    if (!MAIN_NAMESPACES.has(tag.uri)) return;
    if (tag.local === "workbook") workbookFound = true;
    if (tag.local === "sheet") {
      if (sheets.length >= 20) throw new Error("XLSX содержит больше 20 листов");
      const sheet = { name: attribute(tag, "name"), id: attribute(tag, "id"), state: attribute(tag, "state") || "visible" };
      if (!sheet.name || sheet.name.length > 100 || !sheet.id || sheets.some((item) => item.name === sheet.name || item.id === sheet.id)) {
        throw new Error("Некорректный или повторный лист XLSX");
      }
      sheets.push(sheet);
    }
  } });
  if (!workbookFound || !sheets.length) throw new Error("В XLSX отсутствуют листы");
  const relations = new Map<string, string>();
  xml(files["xl/_rels/workbook.xml.rels"], { open(tag) {
    if (tag.local !== "Relationship") return;
    const id = attribute(tag, "Id");
    if (relations.has(id)) throw new Error("Повторный relationship в XLSX");
    if (attribute(tag, "TargetMode") === "External") return;
    if (!attribute(tag, "Type").endsWith("/worksheet")) return;
    const target = attribute(tag, "Target");
    const resolved = target.startsWith("/xl/") ? target.slice(1) : `xl/${target}`;
    if (!/^xl\/worksheets\/[A-Za-z0-9_-]+\.xml$/.test(resolved)) throw new Error("Неподдерживаемый путь листа XLSX");
    relations.set(id, resolved);
  } });
  const strings: string[] = [];
  if (files["xl/sharedStrings.xml"]) {
    let item: string | null = null; let inText = false; let total = 0;
    xml(files["xl/sharedStrings.xml"], {
      open(tag) { if (MAIN_NAMESPACES.has(tag.uri)) {
        if (tag.local === "rPh") throw new Error("Фонетические аннотации XLSX не поддерживаются. Загрузите копию только со значениями");
        if (tag.local === "si") item = ""; if (tag.local === "t") inText = true;
      } },
      text(value) { if (item !== null && inText) { item += value; total += value.length;
        if (item.length > MAX_CELL_TEXT || total > MAX_TEXT) throw new Error("Таблица строк XLSX превышает лимит"); } },
      close(tag) { if (!MAIN_NAMESPACES.has(tag.uri)) return; if (tag.local === "t") inText = false;
        if (tag.local === "si") { if (strings.length >= MAX_CELLS) throw new Error("Таблица строк XLSX превышает лимит"); strings.push(item ?? ""); item = null; } },
    });
  }
  const out = outputBuilder();
  out.append("XLSX: исходные хранимые значения с адресами ячеек. Форматирование и числовые даты не интерпретируются; style — индекс исходного формата.\n");
  for (const sheet of sheets) {
    const target = relations.get(sheet.id);
    if (!target) throw new Error("Лист XLSX отсутствует, внешний или имеет неподдерживаемый тип");
    out.append(`Лист ${JSON.stringify(sheet.name)}; состояние: ${sheet.state}.\n`);
    const seen = new Set<string>();
    let cell: { address: string; type: string; style: string; value: string; inline: string } | null = null;
    let inValue = false; let inText = false; let worksheetFound = false;
    xml(files[target], {
      open(tag) {
        if (!MAIN_NAMESPACES.has(tag.uri)) return;
        if (tag.local === "worksheet") worksheetFound = true;
        if (tag.local === "f") throw new Error("XLSX содержит формулы; их кэш может быть устаревшим. Загрузите копию только со значениями");
        if (tag.local === "c") {
          const address = attribute(tag, "r");
          const match = /^([A-Z]{1,3})([1-9][0-9]{0,5})$/.exec(address);
          const column = match?.[1]?.split("").reduce((value, character) => value * 26 + character.charCodeAt(0) - 64, 0) ?? 0;
          if (!match || column > MAX_COLUMNS || Number(match[2]) > MAX_ROWS) throw new Error("Адрес ячейки XLSX выходит за лимит 10 000 строк / 256 столбцов");
          if (seen.has(address)) throw new Error("Повторный адрес ячейки XLSX");
          seen.add(address);
          if (seen.size > MAX_CELLS) throw new Error("XLSX содержит слишком много ячеек");
          cell = { address, type: attribute(tag, "t") || "n", style: attribute(tag, "s"), value: "", inline: "" };
        }
        if (tag.local === "v") inValue = true;
        if (tag.local === "t") inText = true;
      },
      text(value) { if (cell) { if (inValue) cell.value += value; if (inText) cell.inline += value;
        if (cell.value.length > MAX_CELL_TEXT || cell.inline.length > MAX_CELL_TEXT) throw new Error("Содержимое ячейки превышает 32 000 символов"); } },
      close(tag) {
        if (!MAIN_NAMESPACES.has(tag.uri)) return;
        if (tag.local === "v") inValue = false;
        if (tag.local === "t") inText = false;
        if (tag.local !== "c" || !cell) return;
        let value = cell.value;
        if (cell.type === "s") {
          if (!/^(0|[1-9][0-9]*)$/.test(value) || strings[Number(value)] === undefined) throw new Error("Некорректная ссылка на строку XLSX");
          value = strings[Number(value)]!;
        } else if (cell.type === "inlineStr") value = cell.inline;
        else if (cell.type === "b") { if (value !== "0" && value !== "1") throw new Error("Некорректный boolean XLSX"); value = value === "1" ? "true" : "false"; }
        else if (cell.type === "e") throw new Error("XLSX содержит ячейку с ошибкой. Исправьте книгу перед загрузкой");
        else if (!["n", "str", "d"].includes(cell.type)) throw new Error("Неподдерживаемый тип ячейки XLSX");
        if (cell.type === "n" && value !== "" && (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value)
          || !Number.isFinite(Number(value)))) throw new Error("Некорректное число XLSX");
        if (value !== "") out.cell(sheet.name, cell.address, `${cell.type}${cell.style ? `; style=${cell.style}` : ""}`, value);
        cell = null;
      },
    });
    if (!worksheetFound) throw new Error("Некорректная структура листа XLSX");
  }
  return out.result();
}
