/**
 * 內建的 toolsets，agent 設定的 tools 欄位可以直接用名稱引用。
 *
 *   files  讀取、搜尋、寫入、修改專案資料夾裡的檔案
 *   shell  執行命令列指令（需要使用者同意）
 *   plan   列出並更新多步驟工作的計畫
 *   web    讀取網頁（需要使用者同意）
 *
 * 內建工具不會自動給 agent：agent 沒寫 tools 欄位時只會拿到專案自己的
 * 工具，要用內建工具得明確列出 toolset 名稱。專案裡有同名的 toolset 時，
 * 以專案的為準。
 */

import type { Tool } from "../tool.ts";
import { fileTools } from "./files.ts";
import { planTools } from "./plan.ts";
import { shellTools } from "./shell.ts";
import { webTools } from "./web.ts";

export const BUILTIN_TOOLSETS: Readonly<Record<string, Tool[]>> = {
  files: fileTools,
  shell: shellTools,
  plan: planTools,
  web: webTools,
};

/** 判斷某個 toolset 是不是內建的那一個（而不是專案裡同名的 toolset）。 */
export function isBuiltinToolset(name: string, tools: Tool[]): boolean {
  return BUILTIN_TOOLSETS[name] === tools;
}

export { fileTools, planTools, shellTools, webTools };
