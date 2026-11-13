/** 試算表的欄位名稱（A、B…Z、AA）。和 xlsx.ts 分開，畫面不必先載入讀檔的程式。 */

/** A1、BC12 這類位置的欄號（從 0 開始）。 */
export function columnIndex(ref: string): number {
  let index = 0;
  for (const char of ref.toUpperCase()) {
    const code = char.charCodeAt(0);
    if (code < 65 || code > 90) break;
    index = index * 26 + (code - 64);
  }
  return index - 1;
}

/** 欄號（從 0 開始）轉成 A、B…Z、AA。 */
export function columnName(index: number): string {
  let name = "";
  let n = index + 1;
  while (n > 0) {
    const rest = (n - 1) % 26;
    name = String.fromCharCode(65 + rest) + name;
    n = Math.floor((n - 1) / 26);
  }
  return name;
}
