/**
 * 簡單的 CSV 解析：逗號分隔、雙引號包住的欄位可以含逗號與換行，
 * 兩個雙引號代表一個雙引號。只讀前 maxRows 列。
 */
export function parseCsv(
  text: string,
  maxRows = 200,
): { rows: string[][]; truncated: boolean } {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  let i = 0;
  // 去掉開頭的 BOM。
  const source = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;

  const endRow = () => {
    row.push(field);
    rows.push(row);
    row = [];
    field = "";
  };

  while (i < source.length) {
    const char = source[i];
    if (quoted) {
      if (char === '"') {
        if (source[i + 1] === '"') {
          field += '"';
          i += 2;
          continue;
        }
        quoted = false;
      } else {
        field += char;
      }
      i += 1;
      continue;
    }
    if (char === '"' && field === "") {
      quoted = true;
    } else if (char === ",") {
      row.push(field);
      field = "";
    } else if (char === "\n" || char === "\r") {
      if (char === "\r" && source[i + 1] === "\n") i += 1;
      endRow();
      if (rows.length > maxRows) break;
    } else {
      field += char;
    }
    i += 1;
  }
  if (field !== "" || row.length > 0) endRow();

  const truncated = rows.length > maxRows || i < source.length;
  return { rows: rows.slice(0, maxRows), truncated };
}
