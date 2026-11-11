/**
 * 內建的檔案工具（toolset 名稱：files）。
 *
 * 所有路徑都限制在專案根目錄（context.workdir）底下。
 *
 * 設計參考 Claude Code 的 Read / Write / Edit / Glob / Grep：
 *   - read_file：讀整個檔案，或用 offset / limit 只讀其中幾行。
 *   - edit_file：精準取代一段文字，不必為了改一行重寫整個檔案。
 *   - find_files、search_files：依檔名或內容找檔案（見 search.ts）。
 */

import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { z } from "zod";
import { type Tool, ToolError, tool } from "../tool.ts";
import { resolvePath, splitLines } from "./paths.ts";
import { findFilesTool, searchFilesTool } from "./search.ts";

export { resolvePath, splitLines } from "./paths.ts";

export const MAX_READ_CHARS = 100_000;

function truncate(text: string): string {
  if (text.length <= MAX_READ_CHARS) return text;
  const omitted = text.length - MAX_READ_CHARS;
  return `${text.slice(0, MAX_READ_CHARS)}\n[檔案過長，後面還有 ${omitted} 個字元沒有顯示]`;
}

/** 依 offset（從 1 起算）與 limit 取出部分行，並註明範圍。 */
function sliceLines(text: string, offset = 1, limit?: number): string {
  const lines = splitLines(text);
  if (offset > Math.max(lines.length, 1)) {
    throw new ToolError(`檔案只有 ${lines.length} 行，offset 超出範圍`);
  }
  const end =
    limit === undefined
      ? lines.length
      : Math.min(lines.length, offset - 1 + limit);
  const selected = lines.slice(offset - 1, end).join("\n");
  if (offset === 1 && end === lines.length) return selected;
  return `${selected}\n[第 ${offset}–${end} 行，共 ${lines.length} 行]`;
}

export const readFileTool = tool({
  name: "read_file",
  description:
    "讀取專案資料夾裡的文字檔。檔案很長時，用 offset 與 limit 只讀需要的那幾行。",
  parameters: z.object({
    path: z.string().describe("相對於專案根目錄的檔案路徑"),
    offset: z
      .number()
      .int()
      .min(1)
      .optional()
      .describe("從第幾行開始讀（從 1 起算），不寫表示從第一行"),
    limit: z
      .number()
      .int()
      .min(1)
      .optional()
      .describe("最多讀幾行，不寫表示讀到檔案結尾"),
  }),
  execute: async ({ path, offset, limit }, context) => {
    const text = await readFile(resolvePath(context, path), "utf8");
    if (offset === undefined && limit === undefined) return truncate(text);
    return truncate(sliceLines(text, offset, limit));
  },
});

export const listDirTool = tool({
  name: "list_dir",
  description: "列出專案資料夾裡某個目錄的內容。",
  parameters: z.object({
    path: z.string().default(".").describe("相對於專案根目錄的目錄路徑"),
  }),
  execute: async ({ path }, context) => {
    const entries = await readdir(resolvePath(context, path), {
      withFileTypes: true,
    });
    return entries
      .map((entry) => (entry.isDirectory() ? `${entry.name}/` : entry.name))
      .sort();
  },
});

export const writeFileTool = tool({
  name: "write_file",
  description:
    "把文字寫入專案資料夾裡的檔案，檔案已存在時會覆寫。只改檔案的一部分時用 edit_file。",
  parameters: z.object({
    path: z.string().describe("相對於專案根目錄的檔案路徑"),
    content: z.string().describe("要寫入的完整內容"),
  }),
  needsApproval: true,
  execute: async ({ path, content }, context) => {
    const target = resolvePath(context, path);
    await mkdir(dirname(target), { recursive: true });
    await writeFile(target, content, "utf8");
    return `已寫入 ${path}（${content.length} 個字元）`;
  },
});

/** 計算 needle 在 text 裡不重疊出現的次數。 */
function countOccurrences(text: string, needle: string): number {
  return text.split(needle).length - 1;
}

export const editFileTool = tool({
  name: "edit_file",
  description:
    "把檔案裡的一段文字換成新的文字。old_text 必須和檔案內容逐字相同" +
    "（含縮排與換行），而且只出現一次；要全部取代時設定 replace_all。" +
    "修改前先用 read_file 看過檔案。",
  parameters: z.object({
    path: z.string().describe("相對於專案根目錄的檔案路徑"),
    old_text: z.string().describe("要被取代的原文，必須逐字相同"),
    new_text: z.string().describe("取代後的新文字"),
    replace_all: z
      .boolean()
      .default(false)
      .describe("是否取代所有出現的地方，預設只取代唯一的一處"),
  }),
  needsApproval: true,
  execute: async ({ path, old_text, new_text, replace_all }, context) => {
    if (old_text === "") throw new ToolError("old_text 不能是空字串");
    if (old_text === new_text) {
      throw new ToolError("old_text 與 new_text 相同，沒有需要修改的地方");
    }
    const target = resolvePath(context, path);
    const text = await readFile(target, "utf8");
    const count = countOccurrences(text, old_text);
    if (count === 0) {
      throw new ToolError(
        `${path} 裡找不到 old_text，請先用 read_file 確認原文（含縮排與換行）`,
      );
    }
    if (count > 1 && !replace_all) {
      throw new ToolError(
        `old_text 在 ${path} 裡出現 ${count} 次：請加上前後文讓它只出現一次，` +
          "或設定 replace_all 取代全部",
      );
    }
    const updated = replace_all
      ? text.split(old_text).join(new_text)
      : text.replace(old_text, () => new_text);
    await writeFile(target, updated, "utf8");
    return `已修改 ${path}（取代 ${replace_all ? count : 1} 處）`;
  },
});

export const fileTools: Tool[] = [
  readFileTool,
  listDirTool,
  writeFileTool,
  editFileTool,
  findFilesTool,
  searchFilesTool,
];
