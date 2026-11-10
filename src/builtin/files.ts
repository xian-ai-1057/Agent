/**
 * 內建的檔案工具（toolset 名稱：files）。
 *
 * 所有路徑都限制在專案根目錄（context.workdir）底下。
 */

import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { dirname, isAbsolute, relative, resolve } from "node:path";
import { z } from "zod";
import { type Tool, ToolError, tool } from "../tool.ts";
import type { RunContext } from "../types.ts";

export const MAX_READ_CHARS = 100_000;

/**
 * 把相對路徑轉成專案根目錄底下的絕對路徑。
 *
 * @throws 路徑跑到專案根目錄之外時。
 */
export function resolvePath(context: RunContext, path: string): string {
  const root = resolve(context.workdir);
  const target = resolve(root, path);
  const fromRoot = relative(root, target);
  if (fromRoot.startsWith("..") || isAbsolute(fromRoot)) {
    throw new ToolError(`路徑超出專案資料夾：${path}`);
  }
  return target;
}

export const readFileTool = tool({
  name: "read_file",
  description: "讀取專案資料夾裡的文字檔。",
  parameters: z.object({
    path: z.string().describe("相對於專案根目錄的檔案路徑"),
  }),
  execute: async ({ path }, context) => {
    const text = await readFile(resolvePath(context, path), "utf8");
    if (text.length <= MAX_READ_CHARS) return text;
    const omitted = text.length - MAX_READ_CHARS;
    return `${text.slice(0, MAX_READ_CHARS)}\n[檔案過長，後面還有 ${omitted} 個字元沒有顯示]`;
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
  description: "把文字寫入專案資料夾裡的檔案，檔案已存在時會覆寫。",
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

export const fileTools: Tool[] = [readFileTool, listDirTool, writeFileTool];
