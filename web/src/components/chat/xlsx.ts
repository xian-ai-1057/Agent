/**
 * 讀取 Excel（.xlsx、.xlsm）裡每個工作表的儲存格文字，給預覽面板顯示成
 * 表格。
 *
 * xlsx 是一個 zip，裡面是 XML：workbook.xml 列出工作表、sharedStrings.xml
 * 放共用的文字、styles.xml 決定數字怎麼顯示（日期、百分比、千分位）。
 * 這裡只取顯示需要的部分（值與數字格式），不處理字型、顏色、合併儲存格
 * 與公式（公式顯示存檔時算好的結果）。不靠 DOMParser，所以在 Node
 * 的測試裡也能執行。
 */

import JSZip from "jszip";
import { columnIndex } from "./columns.ts";

export { columnIndex, columnName } from "./columns.ts";

export type Sheet = {
  name: string;
  /** 每一列的儲存格文字；空的儲存格是空字串。 */
  rows: string[][];
  /** 超過 maxRows 或 maxColumns，只讀了一部分。 */
  truncated: boolean;
  hidden: boolean;
};

export type ReadLimits = { maxRows: number; maxColumns: number };

const DEFAULT_LIMITS: ReadLimits = { maxRows: 1000, maxColumns: 100 };

// 內建的日期與時間格式（ECMA-376 18.8.30）。
const BUILTIN_DATE = new Set([14, 15, 16, 17, 22, 27, 30, 36, 50, 57]);
const BUILTIN_TIME = new Set([18, 19, 20, 21, 45, 46, 47]);
const BUILTIN_PERCENT: Record<number, number> = { 9: 0, 10: 2 };
const BUILTIN_THOUSANDS: Record<number, number> = { 3: 0, 4: 2 };
const BUILTIN_FIXED: Record<number, number> = { 2: 2 };

type Format =
  | { kind: "general" }
  | { kind: "date"; time: boolean }
  | { kind: "time" }
  | { kind: "percent"; decimals: number }
  | { kind: "number"; decimals: number; thousands: boolean };

export async function readWorkbook(
  data: ArrayBuffer | Uint8Array,
  limits: ReadLimits = DEFAULT_LIMITS,
): Promise<Sheet[]> {
  const zip = await JSZip.loadAsync(data);
  const text = async (path: string) =>
    unprefix((await zip.file(path)?.async("string")) ?? "");
  const workbook = await text("xl/workbook.xml");
  if (!workbook) throw new Error("這不是 Excel 活頁簿（找不到 workbook.xml）");
  const targets = relationships(await text("xl/_rels/workbook.xml.rels"));
  const strings = sharedStrings(await text("xl/sharedStrings.xml"));
  const formats = cellFormats(await text("xl/styles.xml"));
  const date1904 = /<workbookPr\b[^>]*\bdate1904="(1|true)"/.test(workbook);

  const sheets: Sheet[] = [];
  for (const tag of workbook.match(/<sheet\b[^>]*>/g) ?? []) {
    const attrs = attributes(tag);
    const id = attrs["r:id"] ?? attrs.id;
    const target = id ? targets[id] : undefined;
    if (!target) continue;
    const xml = await text(resolveTarget(target));
    sheets.push({
      name: attrs.name ?? "",
      hidden: attrs.state === "hidden" || attrs.state === "veryHidden",
      ...readSheet(xml, strings, formats, date1904, limits),
    });
  }
  return sheets;
}

function readSheet(
  xml: string,
  strings: string[],
  formats: Format[],
  date1904: boolean,
  limits: ReadLimits,
): { rows: string[][]; truncated: boolean } {
  const rows: string[][] = [];
  let truncated = false;
  let nextRow = 0;
  const rowPattern = /<row\b([^>]*?)(?:\/>|>([\s\S]*?)<\/row>)/g;
  for (const match of xml.matchAll(rowPattern)) {
    const rowAttrs = attributes(`<row ${match[1]}>`);
    const index = rowAttrs.r ? Number(rowAttrs.r) - 1 : nextRow;
    nextRow = index + 1;
    if (index >= limits.maxRows) {
      truncated = true;
      break;
    }
    const cells: string[] = [];
    let nextColumn = 0;
    const cellPattern = /<c\b([^>]*?)(?:\/>|>([\s\S]*?)<\/c>)/g;
    for (const cell of (match[2] ?? "").matchAll(cellPattern)) {
      const attrs = attributes(`<c ${cell[1]}>`);
      const column = attrs.r ? columnIndex(attrs.r) : nextColumn;
      nextColumn = column + 1;
      if (column >= limits.maxColumns) {
        truncated = true;
        continue;
      }
      const value = cellText(cell[2] ?? "", attrs, strings, formats, date1904);
      if (value === "") continue;
      while (cells.length < column) cells.push("");
      cells[column] = value;
    }
    while (rows.length < index) rows.push([]);
    rows[index] = cells;
  }
  // 最後的空白列不顯示。
  while (rows.at(-1)?.every((cell) => !cell)) rows.pop();
  const width = Math.max(0, ...rows.map((row) => row.length));
  return {
    rows: rows.map((row) => [...row, ...Array(width - row.length).fill("")]),
    truncated,
  };
}

function cellText(
  inner: string,
  attrs: Record<string, string>,
  strings: string[],
  formats: Format[],
  date1904: boolean,
): string {
  const type = attrs.t ?? "n";
  if (type === "inlineStr") return richText(inner);
  const raw = /<v>([\s\S]*?)<\/v>/.exec(inner)?.[1];
  if (raw === undefined || raw === "") {
    // 沒有存算好的結果（例如 openpyxl 寫的公式）：顯示公式本身。
    const formula = /<f\b[^>]*>([\s\S]*?)<\/f>/.exec(inner)?.[1];
    return formula ? `=${decode(formula)}` : "";
  }
  const value = decode(raw);
  if (type === "s") return strings[Number(value)] ?? "";
  if (type === "b") return value === "1" ? "TRUE" : "FALSE";
  if (type === "str" || type === "e") return value;
  if (type === "d")
    return value.replace("T", " ").replace(/(:00)?\.000Z?$/, "");
  const number = Number(value);
  if (!Number.isFinite(number)) return value;
  const format = formats[Number(attrs.s ?? 0)] ?? { kind: "general" };
  return formatNumber(number, format, date1904);
}

/** 依儲存格的數字格式把數字轉成文字。 */
export function formatNumber(
  value: number,
  format: Format,
  date1904 = false,
): string {
  switch (format.kind) {
    case "date":
      return serialDate(value, date1904, format.time);
    case "time":
      return clock(value);
    case "percent":
      return `${(value * 100).toFixed(format.decimals)}%`;
    case "number":
      return value.toLocaleString("en-US", {
        minimumFractionDigits: format.decimals,
        maximumFractionDigits: format.decimals,
        useGrouping: format.thousands,
      });
    default:
      // 去掉浮點數的雜訊，例如 0.30000000000000004。
      return String(Number.parseFloat(value.toPrecision(15)));
  }
}

function serialDate(serial: number, date1904: boolean, time: boolean): string {
  // Excel 的 1900 系統把 1900 年當成閏年：序號 60 之後要少算一天。
  const days = date1904 ? serial + 1462 : serial < 60 ? serial + 1 : serial;
  const epoch = Date.UTC(1899, 11, 30);
  const date = new Date(epoch + Math.round(days * 86_400_000));
  const pad = (n: number) => String(n).padStart(2, "0");
  const day = `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
  if (!time) return day;
  return `${day} ${clock(serial)}`;
}

function clock(serial: number): string {
  const seconds = Math.round((serial - Math.floor(serial)) * 86_400) % 86_400;
  const pad = (n: number) => String(n).padStart(2, "0");
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return s ? `${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(h)}:${pad(m)}`;
}

/** styles.xml 的 cellXfs：儲存格的 s 屬性是這個清單的索引。 */
function cellFormats(xml: string): Format[] {
  const custom: Record<number, string> = {};
  for (const tag of xml.match(/<numFmt\b[^>]*>/g) ?? []) {
    const attrs = attributes(tag);
    custom[Number(attrs.numFmtId)] = attrs.formatCode ?? "";
  }
  const section = /<cellXfs\b[^>]*>([\s\S]*?)<\/cellXfs>/.exec(xml)?.[1] ?? "";
  return (section.match(/<xf\b[^>]*>/g) ?? []).map((tag) => {
    const id = Number(attributes(tag).numFmtId ?? 0);
    const code = custom[id];
    return code !== undefined ? parseFormat(code) : builtinFormat(id);
  });
}

function builtinFormat(id: number): Format {
  if (BUILTIN_DATE.has(id)) return { kind: "date", time: id === 22 };
  if (BUILTIN_TIME.has(id)) return { kind: "time" };
  const percent = BUILTIN_PERCENT[id];
  if (percent !== undefined) return { kind: "percent", decimals: percent };
  const grouped = BUILTIN_THOUSANDS[id];
  if (grouped !== undefined) {
    return { kind: "number", decimals: grouped, thousands: true };
  }
  const fixed = BUILTIN_FIXED[id];
  if (fixed !== undefined) {
    return { kind: "number", decimals: fixed, thousands: false };
  }
  return { kind: "general" };
}

/** 自訂格式（例如 yyyy/mm/dd、0.0%、#,##0.00）的大概意思。 */
export function parseFormat(code: string): Format {
  // 只看第一段（正數），去掉引號裡的文字、跳脫字元與 [紅色] 這類標記。
  const first = code.split(";")[0] ?? "";
  const plain = first
    .replace(/"[^"]*"/g, "")
    .replace(/\\./g, "")
    .replace(/\[[^\]]*\]/g, "")
    .toLowerCase();
  if (/[yd]/.test(plain)) {
    return { kind: "date", time: /h/.test(plain) };
  }
  if (/h|s/.test(plain) && /:/.test(plain)) return { kind: "time" };
  const decimals = /\.(0+)/.exec(plain)?.[1]?.length ?? 0;
  if (plain.includes("%")) return { kind: "percent", decimals };
  if (/[0#]/.test(plain)) {
    return { kind: "number", decimals, thousands: plain.includes(",") };
  }
  return { kind: "general" };
}

function sharedStrings(xml: string): string[] {
  return (xml.match(/<si\b[^>]*>[\s\S]*?<\/si>/g) ?? []).map(richText);
}

/** <si> 或 <is> 裡的文字：所有 <t> 接起來，不含注音（<rPh>）。 */
function richText(xml: string): string {
  const withoutPhonetic = xml.replace(/<rPh\b[\s\S]*?<\/rPh>/g, "");
  let out = "";
  for (const match of withoutPhonetic.matchAll(
    /<t\b[^>]*?(?:\/>|>([\s\S]*?)<\/t>)/g,
  )) {
    out += decode(match[1] ?? "");
  }
  return out;
}

function relationships(xml: string): Record<string, string> {
  const map: Record<string, string> = {};
  for (const tag of xml.match(/<Relationship\b[^>]*>/g) ?? []) {
    const attrs = attributes(tag);
    if (attrs.Id && attrs.Target) map[attrs.Id] = attrs.Target;
  }
  return map;
}

function resolveTarget(target: string): string {
  if (target.startsWith("/")) return target.slice(1);
  const parts = `xl/${target}`.split("/");
  const out: string[] = [];
  for (const part of parts) {
    if (part === "..") out.pop();
    else if (part && part !== ".") out.push(part);
  }
  return out.join("/");
}

/**
 * 有些程式（例如 .NET 的 Open XML SDK）寫成 <x:row>、<x:c>：去掉元素名稱
 * 的前綴，後面的比對才不用每個都考慮。屬性（例如 r:id）不受影響。
 */
function unprefix(xml: string): string {
  return xml.replace(/<(\/?)[A-Za-z][\w.-]*:(?=[A-Za-z])/g, "<$1");
}

function attributes(tag: string): Record<string, string> {
  const attrs: Record<string, string> = {};
  for (const match of tag.matchAll(/([\w:.-]+)\s*=\s*("([^"]*)"|'([^']*)')/g)) {
    const name = match[1];
    if (name) attrs[name] = decode(match[3] ?? match[4] ?? "");
  }
  return attrs;
}

const ENTITIES: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
};

function decode(text: string): string {
  return text
    .replace(/_x([0-9a-fA-F]{4})_/g, (_, hex) =>
      String.fromCharCode(Number.parseInt(hex, 16)),
    )
    .replace(/&(#x[0-9a-fA-F]+|#\d+|\w+);/g, (whole, name: string) => {
      if (name.startsWith("#x")) {
        return String.fromCodePoint(Number.parseInt(name.slice(2), 16));
      }
      if (name.startsWith("#")) {
        return String.fromCodePoint(Number.parseInt(name.slice(1), 10));
      }
      return ENTITIES[name] ?? whole;
    });
}
