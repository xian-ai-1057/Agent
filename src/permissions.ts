/**
 * 工具的執行權限。
 *
 * 判斷順序：deny 規則 → allow 規則 → 模式。
 *   ask:     需要同意的工具要先問使用者（預設）。
 *   bypass:  全部直接執行。
 *   dontAsk: 不問使用者，需要同意的工具一律拒絕（適合無人值守）。
 *
 * 「需要同意的工具」是指定義時標記 needsApproval 的工具，或名稱符合
 * ask 規則的工具。規則是工具名稱的萬用字元樣式，例如 "send_*"。
 */

import type { Tool } from "./tool.ts";
import type { ToolCall } from "./types.ts";

export const PERMISSION_MODES = ["ask", "bypass", "dontAsk"] as const;
export type PermissionMode = (typeof PERMISSION_MODES)[number];

/** 詢問使用者的函式，回傳 true 表示同意。 */
export type Approver = (call: ToolCall, target: Tool) => Promise<boolean>;

export interface Permissions {
  mode: PermissionMode;
  /** 不需詢問就能執行的工具名稱樣式。 */
  allow: string[];
  /** 一律拒絕的工具名稱樣式。 */
  deny: string[];
  /** 執行前要先詢問的工具名稱樣式。 */
  ask: string[];
  approver?: Approver;
}

export type Verdict = "allow" | "deny" | "ask";

/**
 * 建立權限設定，沒給的欄位補上預設值。
 *
 * @throws 模式名稱不存在時。
 */
export function definePermissions(
  options: Partial<Permissions> = {},
): Permissions {
  const mode = options.mode ?? "ask";
  if (!PERMISSION_MODES.includes(mode)) {
    const modes = PERMISSION_MODES.join("、");
    throw new Error(`權限模式必須是 ${modes} 之一：${mode}`);
  }
  return {
    mode,
    allow: options.allow ?? [],
    deny: options.deny ?? [],
    ask: options.ask ?? [],
    approver: options.approver,
  };
}

/** 判斷一個工具能不能執行（不詢問使用者）。 */
export function checkPermission(
  permissions: Permissions,
  target: Tool,
): Verdict {
  if (matches(target.name, permissions.deny)) return "deny";
  if (matches(target.name, permissions.allow)) return "allow";
  if (permissions.mode === "bypass") return "allow";
  const sensitive =
    target.needsApproval || matches(target.name, permissions.ask);
  if (!sensitive) return "allow";
  return permissions.mode === "dontAsk" ? "deny" : "ask";
}

/** 詢問使用者是否同意；沒有設定 approver 時視為不同意。 */
export async function askPermission(
  permissions: Permissions,
  call: ToolCall,
  target: Tool,
): Promise<boolean> {
  if (!permissions.approver) return false;
  return permissions.approver(call, target);
}

function matches(name: string, patterns: string[]): boolean {
  return patterns.some((pattern) => toRegExp(pattern).test(name));
}

/** 把萬用字元樣式（* 與 ?）轉成正規表示式。 */
function toRegExp(pattern: string): RegExp {
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&");
  const body = escaped.replace(/\*/g, ".*").replace(/\?/g, ".");
  return new RegExp(`^${body}$`);
}
