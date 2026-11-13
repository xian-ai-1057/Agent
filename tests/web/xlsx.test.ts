/** 預覽面板讀 Excel 活頁簿：工作表、共用文字、數字格式。 */

import JSZip from "jszip";
import { describe, expect, it } from "vitest";
import { previewKind } from "../../web/src/components/chat/filetype.ts";
import {
  columnIndex,
  columnName,
  formatNumber,
  parseFormat,
  readWorkbook,
} from "../../web/src/components/chat/xlsx.ts";

const WORKBOOK = `<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="營收 &amp; 成本" sheetId="1" r:id="rId1"/>
    <sheet name="備註" sheetId="2" state="hidden" r:id="rId2"/>
  </sheets>
</workbook>`;

const RELS = `<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="worksheet" Target="/xl/worksheets/sheet2.xml"/>
</Relationships>`;

const STRINGS = `<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <si><t>月份</t></si>
  <si><t>營收</t></si>
  <si><r><t>一</t></r><r><t xml:space="preserve">月 </t></r><rPh><t>イチ</t></rPh></si>
</sst>`;

// s=0 一般、s=1 日期（內建 14）、s=2 百分比（自訂 0.0%）、s=3 千分位兩位小數。
const STYLES = `<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <numFmts count="1"><numFmt numFmtId="164" formatCode="0.0%"/></numFmts>
  <cellXfs count="4">
    <xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="164"/><xf numFmtId="4"/>
  </cellXfs>
</styleSheet>`;

const SHEET1 = `<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="D1" t="inlineStr"><is><t>&lt;b&gt;不是 HTML</t></is></c></row>
    <row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" s="3"><v>1234567.891</v></c><c r="C2" s="2"><v>0.125</v></c></row>
    <row r="4"><c r="A4" s="1"><v>46337</v></c><c r="B4"><f>B2*2</f><v>0.30000000000000004</v></c><c r="C4" t="b"><v>1</v></c><c r="D4"><f>SUM(B2:B3)</f><v></v></c></row>
    <row r="5"><c r="A5"/></row>
  </sheetData>
</worksheet>`;

const SHEET2 = `<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData><row r="1"><c r="A1" t="str"><v>草稿</v></c></row></sheetData>
</worksheet>`;

async function workbook(sheet1 = SHEET1, sheet2 = SHEET2): Promise<Uint8Array> {
  const zip = new JSZip();
  zip.file("xl/workbook.xml", WORKBOOK);
  zip.file("xl/_rels/workbook.xml.rels", RELS);
  zip.file("xl/sharedStrings.xml", STRINGS);
  zip.file("xl/styles.xml", STYLES);
  zip.file("xl/worksheets/sheet1.xml", sheet1);
  zip.file("xl/worksheets/sheet2.xml", sheet2);
  return zip.generateAsync({ type: "uint8array" });
}

describe("readWorkbook", () => {
  it("reads every sheet with its text and formatted numbers", async () => {
    const [first, second] = await readWorkbook(await workbook());

    expect(first?.name).toBe("營收 & 成本");
    expect(first?.hidden).toBe(false);
    expect(first?.rows).toEqual([
      ["月份", "營收", "", "<b>不是 HTML"],
      ["一月 ", "1,234,567.89", "12.5%", ""],
      ["", "", "", ""],
      ["2026-11-11", "0.3", "TRUE", "=SUM(B2:B3)"],
    ]);
    expect(first?.truncated).toBe(false);
    expect(second).toMatchObject({
      name: "備註",
      hidden: true,
      rows: [["草稿"]],
    });
  });

  it("stops at the row and column limits", async () => {
    const [first] = await readWorkbook(await workbook(), {
      maxRows: 2,
      maxColumns: 2,
    });

    expect(first?.rows).toEqual([
      ["月份", "營收"],
      ["一月 ", "1,234,567.89"],
    ]);
    expect(first?.truncated).toBe(true);
  });

  it("reads files whose elements have a namespace prefix", async () => {
    const prefixed = SHEET2.replace(
      /<(\/?)(worksheet|sheetData|row|c|v)\b/g,
      "<$1x:$2",
    );
    const [, second] = await readWorkbook(await workbook(SHEET1, prefixed));

    expect(second?.rows).toEqual([["草稿"]]);
  });

  it("explains files that are not workbooks", async () => {
    const zip = new JSZip();
    zip.file("word/document.xml", "<w:document/>");
    const data = await zip.generateAsync({ type: "uint8array" });

    await expect(readWorkbook(data)).rejects.toThrow("workbook.xml");
  });
});

describe("number formats", () => {
  it.each([
    ["yyyy/mm/dd", { kind: "date", time: false }],
    ['[$-404]e"年"m"月"d"日"', { kind: "date", time: false }],
    ["yyyy-mm-dd hh:mm", { kind: "date", time: true }],
    ["h:mm:ss", { kind: "time" }],
    ["0.00%", { kind: "percent", decimals: 2 }],
    ["#,##0.0;[Red]-#,##0.0", { kind: "number", decimals: 1, thousands: true }],
    ['0" days"', { kind: "number", decimals: 0, thousands: false }],
    ["@", { kind: "general" }],
  ])("reads %s", (code, expected) => {
    expect(parseFormat(code)).toEqual(expected);
  });

  it("turns Excel serial numbers into dates and times", () => {
    expect(formatNumber(1, { kind: "date", time: false })).toBe("1900-01-01");
    expect(formatNumber(61, { kind: "date", time: false })).toBe("1900-03-01");
    expect(formatNumber(46337.5, { kind: "date", time: true })).toBe(
      "2026-11-11 12:00",
    );
    expect(formatNumber(0, { kind: "date", time: false }, true)).toBe(
      "1904-01-01",
    );
    expect(formatNumber(0.75, { kind: "time" })).toBe("18:00");
  });
});

describe("column names", () => {
  it.each([
    ["A1", 0, "A"],
    ["Z9", 25, "Z"],
    ["AA10", 26, "AA"],
    ["BC3", 54, "BC"],
  ])("%s is column %i (%s)", (ref, index, name) => {
    expect(columnIndex(ref)).toBe(index);
    expect(columnName(index)).toBe(name);
  });
});

describe("previewKind for Office files", () => {
  it.each([
    ["報告.docx", "word"],
    ["data/營收.XLSX", "sheet"],
    ["簡報.pptx", "slides"],
    ["舊版.doc", "binary"],
    ["舊版.xls", "binary"],
    ["舊版.ppt", "binary"],
  ])("%s → %s", (path, kind) => {
    expect(previewKind(path)).toBe(kind);
  });
});
