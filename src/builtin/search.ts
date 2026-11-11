/**
 * 依檔名或內容找檔案：find_files（類似 glob）與 search_files（類似
 * grep）。屬於內建的 files toolset。
 *
 * 樣式規則（Python 版相同）：
 *   - `*` 比對檔名裡任意字元（不跨資料夾），`?` 比對一個字元，
 *     `**` 比對任意層資料夾，`{ts,tsx}` 比對其中一個。
 *   - 不含 `/` 的樣式只比對檔名，所以 `*.py` 會找到任何一層的 .py 檔；
 *     含 `/` 的樣式比對相對於搜尋目錄的完整路徑，例如 `src/**\/*.ts`。
 *
 * 一律略過 .git、node_modules、.harness 等不需要看的資料夾，也不會
 * 跟著符號連結走出專案資料夾。
 */

import { readdir, readFile, stat } from "node:fs/promises";
import { join, relative } from "node:path";
import { z } from "zod";
import { ToolError, tool } from "../tool.ts";
import type { RunContext } from "../types.ts";
import { resolvePath, splitLines } from "./paths.ts";

/** 永遠略過的資料夾名稱。 */
export const IGNORED_DIRS = new Set([
  ".git",
  ".hg",
  ".svn",
  ".harness",
  ".venv",
  "venv",
  "node_modules",
  "__pycache__",
  ".mypy_cache",
  ".pytest_cache",
  ".ruff_cache",
]);
/** 最多檢查幾個檔案，避免在超大的資料夾裡跑太久。 */
export const MAX_WALK_FILES = 20_000;
export const MAX_FIND_RESULTS = 200;
export const MAX_SEARCH_MATCHES = 100;
/** 超過這個大小的檔案不搜尋內容。 */
export const MAX_SEARCH_FILE_BYTES = 1_000_000;
const MAX_LINE_CHARS = 300;

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** 把 glob 樣式轉成比對相對路徑（以 / 分隔）的正規表示式。 */
export function globToRegExp(pattern: string): RegExp {
  let body = "";
  let index = 0;
  while (index < pattern.length) {
    const char = pattern[index] as string;
    if (pattern.startsWith("**/", index)) {
      body += "(?:.*/)?";
      index += 3;
    } else if (pattern.startsWith("**", index)) {
      body += ".*";
      index += 2;
    } else if (char === "*") {
      body += "[^/]*";
      index += 1;
    } else if (char === "?") {
      body += "[^/]";
      index += 1;
    } else if (char === "{" && pattern.indexOf("}", index) > index) {
      const end = pattern.indexOf("}", index);
      const options = pattern.slice(index + 1, end).split(",");
      body += `(?:${options.map(escapeRegExp).join("|")})`;
      index = end + 1;
    } else {
      body += escapeRegExp(char);
      index += 1;
    }
  }
  return new RegExp(`^${body}$`);
}

/** 建立判斷相對路徑是否符合樣式的函式。 */
export function globMatcher(pattern: string): (path: string) => boolean {
  const regexp = globToRegExp(pattern);
  if (pattern.includes("/")) return (path) => regexp.test(path);
  return (path) => regexp.test(path.slice(path.lastIndexOf("/") + 1));
}

interface Walked {
  /** 相對於搜尋目錄的路徑，以 / 分隔，依名稱排序。 */
  files: string[];
  /** 檔案太多、沒有全部檢查完。 */
  truncated: boolean;
}

/** 列出目錄底下所有的檔案（略過 IGNORED_DIRS 與符號連結）。 */
export async function walkFiles(directory: string): Promise<Walked> {
  const files: string[] = [];
  const visit = async (current: string): Promise<boolean> => {
    const entries = await readdir(current, { withFileTypes: true });
    entries.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
    for (const entry of entries) {
      const full = join(current, entry.name);
      if (entry.isDirectory()) {
        if (IGNORED_DIRS.has(entry.name)) continue;
        if (!(await visit(full))) return false;
      } else if (entry.isFile()) {
        if (files.length >= MAX_WALK_FILES) return false;
        files.push(relative(directory, full).split("\\").join("/"));
      }
    }
    return true;
  };
  const complete = await visit(directory);
  return { files, truncated: !complete };
}

/** 把相對於搜尋目錄的路徑換成相對於專案根目錄的路徑。 */
function fromRoot(context: RunContext, directory: string, path: string) {
  const full = relative(resolvePath(context, "."), join(directory, path));
  return full.split("\\").join("/");
}

async function searchRoot(context: RunContext, path: string) {
  const directory = resolvePath(context, path);
  const info = await stat(directory).catch(() => undefined);
  if (!info?.isDirectory()) throw new ToolError(`找不到資料夾：${path}`);
  return directory;
}

export const findFilesTool = tool({
  name: "find_files",
  description:
    "用 glob 樣式依檔名找檔案，例如 *.py、src/**/*.ts、test_*.{py,md}。" +
    "不含 / 的樣式比對任何一層的檔名。回傳相對於專案根目錄的路徑。",
  parameters: z.object({
    pattern: z.string().min(1).describe("glob 樣式"),
    path: z
      .string()
      .default(".")
      .describe("從哪個資料夾開始找，相對於專案根目錄"),
  }),
  execute: async ({ pattern, path }, context) => {
    const directory = await searchRoot(context, path);
    const matches = globMatcher(pattern);
    const walked = await walkFiles(directory);
    const found = walked.files.filter(matches);
    if (found.length === 0) return `沒有符合 ${pattern} 的檔案`;
    const shown = found
      .slice(0, MAX_FIND_RESULTS)
      .map((item) => fromRoot(context, directory, item));
    const notes: string[] = [];
    if (found.length > MAX_FIND_RESULTS) {
      notes.push(
        `[共 ${found.length} 個，只顯示前 ${MAX_FIND_RESULTS} 個，請縮小範圍]`,
      );
    }
    if (walked.truncated) {
      notes.push(`[資料夾裡的檔案太多，只檢查了前 ${MAX_WALK_FILES} 個]`);
    }
    return [...shown, ...notes].join("\n");
  },
});

function compilePattern(pattern: string, ignoreCase: boolean): RegExp {
  try {
    return new RegExp(pattern, ignoreCase ? "i" : "");
  } catch (error) {
    throw new ToolError(`正規表示式有誤：${(error as Error).message}`);
  }
}

function clip(line: string): string {
  const trimmed = line.trimEnd();
  if (trimmed.length <= MAX_LINE_CHARS) return trimmed;
  return `${trimmed.slice(0, MAX_LINE_CHARS)}…`;
}

export const searchFilesTool = tool({
  name: "search_files",
  description:
    "用正規表示式搜尋檔案內容，回傳「路徑:行號: 內容」。可以用 glob 只搜尋" +
    "某些檔案（例如 *.py）。適合找函式定義、設定值、錯誤訊息出現的位置。",
  parameters: z.object({
    pattern: z.string().min(1).describe("正規表示式，例如 def\\s+load_"),
    path: z
      .string()
      .default(".")
      .describe("從哪個資料夾開始搜尋，相對於專案根目錄"),
    glob: z
      .string()
      .optional()
      .describe("只搜尋符合這個 glob 樣式的檔案，例如 *.ts"),
    ignore_case: z.boolean().default(false).describe("是否忽略大小寫"),
  }),
  execute: async ({ pattern, path, glob, ignore_case }, context) => {
    const regexp = compilePattern(pattern, ignore_case);
    const directory = await searchRoot(context, path);
    const wanted = glob ? globMatcher(glob) : () => true;
    const walked = await walkFiles(directory);
    const results: string[] = [];
    let total = 0;
    for (const file of walked.files.filter(wanted)) {
      const full = join(directory, file);
      if ((await stat(full)).size > MAX_SEARCH_FILE_BYTES) continue;
      const buffer = await readFile(full);
      // 有 NUL 字元的視為二進位檔，不搜尋。
      if (buffer.subarray(0, 8000).includes(0)) continue;
      const lines = splitLines(buffer.toString("utf8"));
      for (const [index, line] of lines.entries()) {
        if (!regexp.test(line)) continue;
        total += 1;
        if (results.length < MAX_SEARCH_MATCHES) {
          const where = fromRoot(context, directory, file);
          results.push(`${where}:${index + 1}: ${clip(line)}`);
        }
      }
    }
    if (total === 0) return `沒有找到符合 ${pattern} 的內容`;
    if (total > MAX_SEARCH_MATCHES) {
      results.push(
        `[共 ${total} 筆，只顯示前 ${MAX_SEARCH_MATCHES} 筆，請縮小範圍]`,
      );
    }
    if (walked.truncated) {
      results.push(`[資料夾裡的檔案太多，只檢查了前 ${MAX_WALK_FILES} 個]`);
    }
    return results.join("\n");
  },
});
