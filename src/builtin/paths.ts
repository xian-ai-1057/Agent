/** 內建工具共用的路徑處理。 */

import { isAbsolute, relative, resolve } from "node:path";
import { ToolError } from "../tool.ts";
import type { RunContext } from "../types.ts";

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

/** 把文字切成行；結尾的換行不算多出一行，空檔案沒有任何一行。 */
export function splitLines(text: string): string[] {
  if (text === "") return [];
  const body = text.endsWith("\n") ? text.slice(0, -1) : text;
  return body.split("\n");
}
