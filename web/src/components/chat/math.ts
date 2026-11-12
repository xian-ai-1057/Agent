/** 數學式的前處理（純函式，方便測試）。 */

const MATH = /\$\$/;

/**
 * 模型常把整行公式寫成 `$$…$$` 放在同一行；Markdown 會把它當成行內
 * 公式而縮得很小。整行只有一個 $$…$$ 時改成獨立的區塊公式。程式碼
 * 區塊裡的內容不動。
 */
export function displayMath(text: string): string {
  if (!MATH.test(text)) return text;
  let fence: string | null = null;
  return text
    .split("\n")
    .map((line) => {
      const marker = line.match(/^[ \t>]*(`{3,}|~{3,})/)?.[1];
      if (marker) {
        if (fence === null) fence = marker;
        else if (marker.startsWith(fence)) fence = null;
        return line;
      }
      if (fence !== null) return line;
      const match = line.match(/^(\s*)\$\$([^$]+)\$\$\s*$/);
      if (!match) return line;
      const indent = match[1] ?? "";
      return `${indent}$$\n${indent}${(match[2] ?? "").trim()}\n${indent}$$`;
    })
    .join("\n");
}
