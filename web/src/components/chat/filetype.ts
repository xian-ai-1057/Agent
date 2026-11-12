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

/** 在回覆底下顯示成縮圖的圖片格式。 */
const THUMBNAIL = ["png", "jpg", "jpeg", "gif", "webp"];

export function isThumbnail(path: string): boolean {
  return THUMBNAIL.includes(extensionOf(path));
}

/** 有通訊協定（http:、data:、mailto:…）的網址，或 //host 開頭的網址。 */
const EXTERNAL = /^([a-z][a-z0-9+.-]*:|\/\/)/i;

/**
 * 沙盒容器裡的工作目錄（sandbox.py 的 CONTAINER_WORKDIR）與它在專案裡
 * 預設的位置（DEFAULT_WORKSPACE）。harness.yaml 改了 sandbox.workspace
 * 時，前端不知道新的位置，只對應預設的。
 */
const CONTAINER_WORKDIR = "/workspace/";
const PROJECT_WORKSPACE = "workspace/";

/**
 * 回覆裡圖片的 src 對應到專案裡的路徑（相對於專案根目錄），可以交給
 * fileUrl 讀取。外部網址、data: 與其他絕對路徑回傳 undefined，照原樣
 * 顯示。容器裡的 /workspace/x 對應到專案的 workspace/x；開頭的 ./ 拿掉。
 */
export function projectPath(src: string): string | undefined {
  let path = src.trim();
  if (!path || EXTERNAL.test(path) || path.startsWith("#")) return undefined;
  path = path.split(/[?#]/)[0] ?? "";
  try {
    path = decodeURI(path);
  } catch {
    // 不是合法的百分比編碼時照原樣使用。
  }
  if (path.startsWith(CONTAINER_WORKDIR)) {
    path = PROJECT_WORKSPACE + path.slice(CONTAINER_WORKDIR.length);
  } else if (path.startsWith("/")) {
    return undefined;
  }
  while (path.startsWith("./")) path = path.slice(2);
  return path || undefined;
}
