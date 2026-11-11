/** 依副檔名判斷檔案要怎麼預覽。 */

export type PreviewKind =
  | "image"
  | "pdf"
  | "markdown"
  | "csv"
  | "html"
  | "text"
  | "binary";

// SVG 不在這裡：伺服器把它當純文字送（裡面可以有程式），所以顯示原始碼。
const IMAGE = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "avif"];

/** 副檔名對應到 highlight.js 的語言；空字串表示純文字。 */
const TEXT_LANGUAGES: Record<string, string> = {
  txt: "",
  log: "",
  text: "",
  env: "",
  ini: "ini",
  cfg: "ini",
  conf: "ini",
  toml: "ini",
  json: "json",
  jsonl: "json",
  py: "python",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  jsx: "javascript",
  ts: "typescript",
  tsx: "typescript",
  yaml: "yaml",
  yml: "yaml",
  css: "css",
  scss: "scss",
  sql: "sql",
  xml: "xml",
  svg: "xml",
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  java: "java",
  go: "go",
  rs: "rust",
  rb: "ruby",
  php: "php",
  c: "c",
  h: "c",
  cpp: "cpp",
  cs: "csharp",
  kt: "kotlin",
  swift: "swift",
  r: "r",
  tsv: "",
  dockerfile: "dockerfile",
};

export function extensionOf(path: string): string {
  const name = path.split("/").at(-1) ?? "";
  if (/^dockerfile$/i.test(name)) return "dockerfile";
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

export function fileName(path: string): string {
  return path.split("/").at(-1) || path;
}

export function previewKind(path: string): PreviewKind {
  const ext = extensionOf(path);
  if (IMAGE.includes(ext)) return "image";
  if (ext === "pdf") return "pdf";
  if (ext === "md" || ext === "markdown") return "markdown";
  if (ext === "csv") return "csv";
  if (ext === "html" || ext === "htm") return "html";
  if (ext in TEXT_LANGUAGES) return "text";
  // 沒有副檔名的檔案（README、Makefile）多半是文字。
  if (ext === "") return "text";
  return "binary";
}

/** 程式碼區塊要用的語言；html 也當成原始碼顯示。 */
export function languageOf(path: string): string {
  const ext = extensionOf(path);
  if (ext === "html" || ext === "htm") return "xml";
  return TEXT_LANGUAGES[ext] ?? "";
}

/** 檔案卡片上的小標籤，例如 PDF、DOCX。 */
export function badgeOf(path: string): string {
  const ext = extensionOf(path);
  return ext ? ext.slice(0, 4).toUpperCase() : "FILE";
}
